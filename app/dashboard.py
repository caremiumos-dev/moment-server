"""Administration dashboard: sign in, add and manage watch accounts."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.api_common import normalize_chip_id
from app.auth import (
    current_user,
    end_session,
    login_required,
    revoke_tokens,
    start_session,
)
from app.models import (
    AuthToken,
    Comment,
    Like,
    Moment,
    ReviewRequest,
    User,
    get_session,
    utcnow,
)

dashboard = Blueprint("dashboard", __name__)


@dashboard.get("/")
def index():
    if current_user() is None:
        return redirect(url_for("dashboard.login"))
    return redirect(url_for("dashboard.home"))


@dashboard.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        db = get_session()
        try:
            user = db.query(User).filter_by(username=username).one_or_none()
            if user is None or not user.check_password(password):
                flash("用户名或密码不正确", "error")
            elif user.is_banned:
                flash("该账号已被封禁", "error")
            elif not user.is_active:
                flash("该账号已被停用", "error")
            elif not user.is_admin:
                flash("该账号没有管理权限", "error")
            else:
                start_session(user)
                return redirect(request.args.get("next") or url_for("dashboard.home"))
        finally:
            db.close()
    return render_template("login.html")


@dashboard.post("/logout")
def logout():
    end_session()
    return redirect(url_for("dashboard.login"))


@dashboard.get("/dashboard")
@login_required
def home():
    db = get_session()
    try:
        users = db.query(User).order_by(User.id).all()
        rows = []
        for user in users:
            rows.append(
                {
                    "user": user,
                    "moment_count": db.query(Moment).filter_by(user_id=user.id, deleted=False).count(),
                    "comment_count": db.query(Comment).filter_by(user_id=user.id, deleted=False).count(),
                    "like_count": db.query(Like).filter_by(user_id=user.id).count(),
                    "active_tokens": db.query(AuthToken)
                    .filter(AuthToken.user_id == user.id, AuthToken.revoked.is_(False))
                    .count(),
                    "pending_reviews": db.query(ReviewRequest)
                    .filter(ReviewRequest.user_id == user.id, ReviewRequest.handled.is_(False))
                    .count(),
                }
            )
        total_moments = db.query(Moment).filter(Moment.deleted.is_(False)).count()
        total_comments = db.query(Comment).filter(Comment.deleted.is_(False)).count()
        total_likes = db.query(Like).count()
        reviews = (
            db.query(ReviewRequest)
            .filter(ReviewRequest.handled.is_(False))
            .order_by(ReviewRequest.create_time.desc())
            .limit(50)
            .all()
        )
        return render_template(
            "dashboard.html",
            rows=rows,
            me=current_user(),
            total_moments=total_moments,
            total_comments=total_comments,
            total_likes=total_likes,
            reviews=reviews,
        )
    finally:
        db.close()


def _checkbox(form, name, default=False):
    """Read a checkbox that may be paired with a hidden fallback value.

    The edit form submits a hidden ``off`` before the real checkbox so an
    unchecked box still reaches the server; ``on`` anywhere in the list wins.
    """
    values = form.getlist(name)
    if not values:
        return default
    return "on" in values


@dashboard.post("/users/create")
@login_required
def create_user():
    form = request.form
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    watch_id = (form.get("watch_id") or "").strip()
    display_name = (form.get("display_name") or "").strip()
    if not username or not password or not watch_id:
        flash("用户名、密码、watchId 都不能为空", "error")
        return redirect(url_for("dashboard.home"))
    db = get_session()
    try:
        if db.query(User).filter_by(username=username).one_or_none() is not None:
            flash(f"用户名 {username} 已存在", "error")
            return redirect(url_for("dashboard.home"))
        if db.query(User).filter_by(watch_id=watch_id).one_or_none() is not None:
            flash(f"watchId {watch_id} 已被占用", "error")
            return redirect(url_for("dashboard.home"))
        chip_id = normalize_chip_id(form.get("chip_id"))
        if chip_id and db.query(User).filter(User.chip_id == chip_id).one_or_none() is not None:
            flash(f"chipId {chip_id} 已绑定给其它账号", "error")
            return redirect(url_for("dashboard.home"))
        user = User(
            username=username,
            watch_id=watch_id,
            display_name=display_name,
            chip_id=chip_id,
            is_admin=_checkbox(form, "is_admin"),
            is_active=_checkbox(form, "is_active", default=True),
        )
        user.set_password(password)
        db.add(user)
        db.commit()
        flash(f"已创建账号 {username}", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/users/<int:user_id>/update")
@login_required
def update_user(user_id):
    form = request.form
    db = get_session()
    try:
        user = db.get(User, user_id)
        if user is None:
            flash("账号不存在", "error")
            return redirect(url_for("dashboard.home"))
        user.display_name = (form.get("display_name") or "").strip()
        new_password = form.get("password") or ""
        if new_password:
            user.set_password(new_password)
        # Binding a chip id pins the account to one watch; an empty value means
        # "any device that presents this watch id is accepted".
        if "chip_id" in form:
            chip_id = normalize_chip_id(form.get("chip_id"))
            if chip_id:
                clash = (
                    db.query(User)
                    .filter(User.chip_id == chip_id, User.id != user.id)
                    .one_or_none()
                )
                if clash is not None:
                    flash(f"chipId {chip_id} 已绑定给 {clash.username}", "error")
                    return redirect(url_for("dashboard.home"))
            user.chip_id = chip_id
        # The template submits hidden "off" values first, so an unchecked box
        # still reaches us and can actually revoke the flag.
        if "is_admin" in form:
            user.is_admin = _checkbox(form, "is_admin")
        if "is_active" in form:
            user.is_active = _checkbox(form, "is_active", default=True)
            if not user.is_active:
                revoke_tokens(db, user)
        db.commit()
        flash(f"已更新账号 {user.username}", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/users/<int:user_id>/toggle")
@login_required
def toggle_user(user_id):
    db = get_session()
    try:
        user = db.get(User, user_id)
        if user is None:
            flash("账号不存在", "error")
        elif user.id == current_user().id:
            flash("不能停用当前登录的账号", "error")
        else:
            user.is_active = not user.is_active
            if not user.is_active:
                revoke_tokens(db, user)
            db.commit()
            flash(f"{user.username} 已{'启用' if user.is_active else '停用'}", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/users/<int:user_id>/revoke")
@login_required
def revoke_user_tokens(user_id):
    db = get_session()
    try:
        user = db.get(User, user_id)
        if user is None:
            flash("账号不存在", "error")
        else:
            count = revoke_tokens(db, user)
            flash(f"已撤销 {user.username} 的 {count} 个 token", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/users/<int:user_id>/ban")
@login_required
def ban_user(user_id):
    """Suspend an account.

    A banned account answers ``000007`` on every call, which the watch renders
    as the "your account has been suspended" prompt, and it cannot log in.
    """
    db = get_session()
    try:
        user = db.get(User, user_id)
        if user is None:
            flash("账号不存在", "error")
        elif user.id == current_user().id:
            flash("不能封禁当前登录的账号", "error")
        else:
            user.is_banned = True
            user.banned_at = utcnow()
            # Drop live sessions so the watch re-authenticates immediately.
            revoke_tokens(db, user)
            db.commit()
            flash(f"{user.username} 已被封禁", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/users/<int:user_id>/unban")
@login_required
def unban_user(user_id):
    db = get_session()
    try:
        user = db.get(User, user_id)
        if user is None:
            flash("账号不存在", "error")
        else:
            user.is_banned = False
            user.banned_at = None
            db.commit()
            flash(f"已解除 {user.username} 的封禁", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/reviews/<int:review_id>/handled")
@login_required
def mark_review_handled(review_id):
    db = get_session()
    try:
        review = db.get(ReviewRequest, review_id)
        if review is None:
            flash("复核申请不存在", "error")
        else:
            review.handled = True
            db.commit()
            flash("已标记为处理完毕", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/users/<int:user_id>/bind-seen-chip")
@login_required
def bind_seen_chip(user_id):
    """Bind the most recent chip id this account was seen with."""
    db = get_session()
    try:
        user = db.get(User, user_id)
        if user is None:
            flash("账号不存在", "error")
        elif not user.last_chip_id:
            flash(f"{user.username} 还没有上报过 chipId", "error")
        else:
            clash = (
                db.query(User)
                .filter(User.chip_id == user.last_chip_id, User.id != user.id)
                .one_or_none()
            )
            if clash is not None:
                flash(f"chipId {user.last_chip_id} 已绑定给 {clash.username}", "error")
            else:
                user.chip_id = user.last_chip_id
                db.commit()
                flash(f"已把 {user.username} 绑定到 {user.chip_id}", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/users/<int:user_id>/unbind-chip")
@login_required
def unbind_chip(user_id):
    """Drop the chip-id binding so the account works on any watch again."""
    db = get_session()
    try:
        user = db.get(User, user_id)
        if user is None:
            flash("账号不存在", "error")
        else:
            user.chip_id = ""
            db.commit()
            flash(f"已解除 {user.username} 的设备绑定", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/users/<int:user_id>/delete")
@login_required
def delete_user(user_id):
    db = get_session()
    try:
        user = db.get(User, user_id)
        if user is None:
            flash("账号不存在", "error")
        elif user.id == current_user().id:
            flash("不能删除当前登录的账号", "error")
        else:
            db.delete(user)
            db.commit()
            flash(f"已删除账号 {user.username}", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.home"))


@dashboard.post("/moments/<int:moment_id>/delete")
@login_required
def delete_moment(moment_id):
    db = get_session()
    try:
        moment = db.get(Moment, moment_id)
        if moment is None:
            flash("动态不存在", "error")
        else:
            moment.deleted = True
            db.commit()
            flash("动态已删除", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.moments"))


@dashboard.get("/moments")
@login_required
def moments():
    db = get_session()
    try:
        page = max(int(request.args.get("page") or 1), 1)
        page_size = 20
        query = (
            db.query(Moment)
            .filter(Moment.deleted.is_(False))
            .order_by(Moment.create_time.desc())
        )
        total = query.count()
        rows = query.offset((page - 1) * page_size).limit(page_size).all()
        total_page = (total + page_size - 1) // page_size
        return render_template(
            "moments.html",
            moments=rows,
            me=current_user(),
            page=page,
            total_page=total_page,
            total=total,
        )
    finally:
        db.close()
