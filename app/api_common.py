"""Helpers that reproduce the watch client's HTTP contract.

The client treats every response as ``{"code": ..., "data": ..., "desc": ...}``
and only accepts ``000001`` as success (see ``NetBaseResult``).  Requests carry
the account identity inside the plaintext ``Base-Request-Param`` header, so the
server can authenticate without touching any encryption code.
"""

import base64
import json
from functools import wraps

from flask import g, jsonify, request

SUCCESS = "000001"
ERROR_INVALID_PARAM = "000002"
ERROR_TOKEN_EXPIRED = "000003"
ERROR_ACCOUNT_DISABLED = "000004"
ERROR_NOT_FOUND = "000005"
ERROR_FORBIDDEN = "000006"
# ``HttpAuthManager.AUTH_ERROR_CODE``: the client broadcasts "auth failed" and
# the launcher re-authenticates the watch.
ERROR_AUTH_FAILED = "000016"

BASE_REQUEST_PARAM_HEADER = "Base-Request-Param"
AUTH_ID_HEADER = "authId"

# ``WatchDevice.getChipId()`` falls back to this literal when
# ``ro.boot.xtc.chipid`` is unreadable, so it must not be treated as an identity.
UNKNOWN_CHIP_ID = "unkown"


def normalize_chip_id(value):
    """Return the chip id, or ``""`` when the client could not read one."""
    text = (value or "").strip()
    if not text or text.lower() == UNKNOWN_CHIP_ID:
        return ""
    return text


def ok(data=None, desc=""):
    """Success envelope understood by ``HttpRxJavaCallback``."""
    return jsonify({"code": SUCCESS, "data": data, "desc": desc})


def fail(code, desc=""):
    """Failure envelope; the client surfaces ``desc``/``watchTips`` to the user."""
    return jsonify({"code": code, "data": None, "desc": desc, "watchTips": desc})


def parse_base_request_param(raw):
    """Decode ``Base-Request-Param`` which is plain JSON, or AES-of-JSON.

    Only the unencrypted form is supported: the caller passes the header value
    verbatim and we read the account fields straight out of it.
    """
    if not raw:
        return None
    text = raw.strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    # Tolerate a base64-wrapped JSON payload; anything still opaque is rejected.
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return None


def request_identity():
    """Extract the account identity the client advertises on every call."""
    param = parse_base_request_param(request.headers.get(BASE_REQUEST_PARAM_HEADER))
    if not param:
        return None
    account_id = param.get("accountId") or param.get("watchId")
    return {
        "account_id": account_id,
        "device_id": param.get("deviceId", ""),
        "mac": param.get("mac", ""),
        # ``token`` is the hardware chip id, not a session token.
        "chip_id": normalize_chip_id(param.get("token")),
        "raw": param,
    }


def authenticate(required=True):
    """Resolve ``g.current_user`` from the request identity."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            from app.models import AuthToken, User, get_session

            g.current_user = None
            g.identity = request_identity()
            session = get_session()
            try:
                account_id = g.identity["account_id"] if g.identity else None
                if account_id:
                    user = session.query(User).filter_by(watch_id=account_id).one_or_none()
                    if user is not None:
                        if not user.is_active:
                            return fail(ERROR_ACCOUNT_DISABLED, "account disabled")
                        chip_id = g.identity["chip_id"]
                        if user.chip_id:
                            # Bound account: only the registered watch may use it.
                            if chip_id != user.chip_id:
                                return fail(ERROR_AUTH_FAILED, "device does not match this account")
                        elif chip_id:
                            # Unbound: remember the first device so the admin can
                            # bind it from the dashboard with one click.
                            if user.last_chip_id != chip_id:
                                user.last_chip_id = chip_id
                                session.commit()
                        g.current_user = user
                if g.current_user is None:
                    token_value = request.headers.get(AUTH_ID_HEADER)
                    if token_value:
                        token = session.query(AuthToken).filter_by(token=token_value).one_or_none()
                        if token is not None and not token.user.is_active:
                            return fail(ERROR_ACCOUNT_DISABLED, "account disabled")
                        if token is not None and token.is_valid:
                            g.current_user = token.user
                if required and g.current_user is None:
                    return fail(ERROR_TOKEN_EXPIRED, "account not found or token expired")
                return view(*args, **kwargs)
            finally:
                session.close()

        return wrapper

    return decorator
