"""Account authentication helpers.

The watch client never sends a username or password to the moment service: it
already knows which watch account it belongs to and puts that id into the
plaintext ``Base-Request-Param`` header (see ``HttpHelper.buildBaseRequestParamJson``
in the app).  So the normal authentication path is "resolve ``accountId`` to a
row in ``users``" and that is handled by :func:`app.api_common.authenticate`.

This module adds two extras on top of that:

* ``POST /moment/auth/login`` / ``POST /moment/auth/logout`` for tooling and for
  clients that want a revocable bearer token instead of the watch id.
* the shared helpers the dashboard uses to keep its own session.
"""

import datetime as dt

from flask import Blueprint, redirect, request, session, url_for
from functools import wraps

from app.api_common import (
    ERROR_ACCOUNT_DISABLED,
    ERROR_AUTH_FAILED,
    ERROR_INVALID_PARAM,
    ERROR_TOKEN_EXPIRED,
    fail,
    fail_banned,
    normalize_chip_id,
    ok,
)
from app.models import AuthToken, User, get_session, utcnow

auth = Blueprint("auth", __name__)

# How long an issued bearer token stays valid.
TOKEN_LIFETIME_DAYS = 30

SESSION_USER_KEY = "dashboard_user_id"


def issue_token(session, user, device_id=""):
    """Create and persist a fresh bearer token for ``user``."""
    token = AuthToken(
        token=AuthToken.new_token(),
        user_id=user.id,
        device_id=device_id or "",
        created_at=utcnow(),
        expires_at=utcnow() + dt.timedelta(days=TOKEN_LIFETIME_DAYS),
    )
    session.add(token)
    user.last_login_at = utcnow()
    session.commit()
    return token


def revoke_tokens(session, user):
    """Invalidate every token the user currently holds."""
    count = (
        session.query(AuthToken)
        .filter(AuthToken.user_id == user.id, AuthToken.revoked.is_(False))
        .update({AuthToken.revoked: True}, synchronize_session=False)
    )
    session.commit()
    return count


def login_required(view):
    """Dashboard guard: bounce anonymous visitors to the login form."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("dashboard.login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def current_user():
    """Return the signed-in dashboard user, or ``None``."""
    user_id = session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    db = get_session()
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            session.pop(SESSION_USER_KEY, None)
            return None
        # Detach so the caller can keep using the object after the session closes.
        db.expunge(user)
        return user
    finally:
        db.close()


def start_session(user):
    session.clear()
    session[SESSION_USER_KEY] = user.id
    session.permanent = True


def end_session():
    session.clear()


@auth.post("/moment/auth/login")
def watch_login():
    """Username/password login that returns a revocable token."""
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if not username or not password:
        return fail(ERROR_INVALID_PARAM, "username and password are required")
    db = get_session()
    try:
        user = db.query(User).filter_by(username=username).one_or_none()
        if user is None or not user.check_password(password):
            return fail(ERROR_INVALID_PARAM, "invalid username or password")
        if user.is_banned:
            # Same envelope the watch shows as "account suspended".
            return fail_banned()
        if not user.is_active:
            return fail(ERROR_ACCOUNT_DISABLED, "account disabled")
        chip_id = normalize_chip_id(payload.get("chipId"))
        if user.chip_id and chip_id and chip_id != user.chip_id:
            return fail(ERROR_AUTH_FAILED, "device does not match this account")
        if not user.chip_id and chip_id and user.last_chip_id != chip_id:
            user.last_chip_id = chip_id
        token = issue_token(db, user, device_id=payload.get("deviceId") or "")
        return ok(
            {
                "token": token.token,
                "watchId": user.watch_id,
                "watchName": user.effective_name,
                "expiresAt": int(token.expires_at.timestamp() * 1000),
            }
        )
    finally:
        db.close()


@auth.post("/moment/auth/logout")
def watch_logout():
    """Revoke the bearer token supplied in the ``authId`` header."""
    value = request.headers.get("authId") or (request.get_json(silent=True) or {}).get("token")
    if not value:
        return fail(ERROR_INVALID_PARAM, "token is required")
    db = get_session()
    try:
        token = db.query(AuthToken).filter_by(token=value).one_or_none()
        if token is None or not token.is_valid:
            return fail(ERROR_TOKEN_EXPIRED, "token expired")
        token.revoked = True
        db.commit()
        return ok("1")
    finally:
        db.close()
