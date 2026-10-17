"""Administration dashboard: sign in, add and manage watch accounts."""

import os
import secrets
import time
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

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
    Friendship,
    Like,
    Moment,
    MomentTemplate,
    ReviewRequest,
    Setting,
    Topic,
    User,
    get_session,
    get_setting,
    set_setting,
    utcnow,
)

dashboard = Blueprint("dashboard", __name__)


def _csrf_token():
    """Per-session token embedded in every dashboard form."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@dashboard.app_context_processor
def _inject_csrf():
    return {"csrf_token": _csrf_token()}


@dashboard.before_request
def _check_csrf():
    """Reject state-changing dashboard posts without the session token.

    The watch API is authenticated by its own header and does not use browser
    cookies, so this guard only applies to the admin dashboard forms.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    expected = session.get("csrf_token")
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        return "CSRF token missing or invalid", 400
    return None


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


@dashboard.get("/templates")
@login_required
def templates():
    """心情/状态模板管理：对应发布页的模板列表。"""
    db = get_session()
    try:
        rows = (
            db.query(MomentTemplate)
            .order_by(MomentTemplate.type, MomentTemplate.sort_value.desc(), MomentTemplate.id)
            .all()
        )
        return render_template("templates.html", rows=rows, me=current_user())
    finally:
        db.close()


@dashboard.post("/templates/create")
@login_required
def create_template():
    form = request.form
    content = (form.get("content") or "").strip()
    if not content:
        flash("文案不能为空", "error")
        return redirect(url_for("dashboard.templates"))
    resource, error = _save_template_icon(request.files.get("icon"), form.get("resource"))
    if error:
        flash(error, "error")
        return redirect(url_for("dashboard.templates"))
    if not resource:
        flash("心情/状态必须带一个图标，否则发布页会直接退出", "error")
        return redirect(url_for("dashboard.templates"))
    db = get_session()
    try:
        resource_id = _next_resource_id(db)
        template = MomentTemplate(
            type=int(form.get("type") or 0),
            sub_type=int(form.get("sub_type") or 0),
            content=content,
            resource=resource,
            resource_id=resource_id,
            sort_value=int(form.get("sort_value") or 0),
            enabled=_checkbox(form, "enabled", default=True),
        )
        db.add(template)
        db.commit()
        flash(f"已添加模板「{content}」", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.templates"))


@dashboard.post("/templates/<int:template_id>/update")
@login_required
def update_template(template_id):
    form = request.form
    db = get_session()
    try:
        template = db.get(MomentTemplate, template_id)
        if template is None:
            flash("模板不存在", "error")
            return redirect(url_for("dashboard.templates"))
        resource, error = _save_template_icon(request.files.get("icon"), form.get("resource"))
        if error:
            flash(error, "error")
            return redirect(url_for("dashboard.templates"))
        template.content = (form.get("content") or "").strip() or template.content
        template.type = int(form.get("type") or 0)
        template.sub_type = int(form.get("sub_type") or 0)
        template.sort_value = int(form.get("sort_value") or 0)
        template.enabled = _checkbox(form, "enabled", default=True)
        if resource:
            template.resource = resource
        template.update_time = utcnow()
        db.commit()
        flash("已保存模板", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.templates"))


@dashboard.post("/templates/<int:template_id>/delete")
@login_required
def delete_template(template_id):
    db = get_session()
    try:
        template = db.get(MomentTemplate, template_id)
        if template is not None:
            db.delete(template)
            db.commit()
            flash("已删除模板", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.templates"))


def _next_resource_id(db):
    """The watch upserts by ``resourceId``, so it must never repeat."""
    largest = db.query(MomentTemplate).order_by(MomentTemplate.resource_id.desc()).first()
    return (largest.resource_id if largest else 0) + 1


def _save_template_icon(upload, resource_text):
    """Store an uploaded icon (or a pasted url) and return the resource string.

    ``media:<file>`` is expanded to a full url when the template list is
    served, because only then is the request host known.
    """
    resource_text = (resource_text or "").strip()
    if upload is None or not upload.filename:
        return resource_text, None
    suffix = Path(secure_filename(upload.filename)).suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return None, "只支持 png/jpg/webp/gif 图标"
    media_dir = Path(current_app.config["MEDIA_DIR"])
    media_dir.mkdir(parents=True, exist_ok=True)
    name = f"template_{secrets.token_hex(8)}{suffix}"
    upload.save(os.fspath(media_dir / name))
    return f"media:{name}", None


@dashboard.get("/topics")
@login_required
def topics():
    """「小括号」推荐话题与功能配置管理。"""
    db = get_session()
    try:
        rows = db.query(Topic).order_by(Topic.sort_value.desc(), Topic.id).all()
        config = {
            "maxTopicCount": get_setting(db, "topic.maxTopicCount", "2"),
            "nameMaxLength": get_setting(db, "topic.nameMaxLength", "12"),
            "officialColor": get_setting(db, "topic.officialColor", "#FFFFFFCC"),
            "userColor": get_setting(db, "topic.userColor", "#FFFFFFCC"),
        }
        return render_template("topics.html", rows=rows, config=config, me=current_user())
    finally:
        db.close()


@dashboard.post("/topics/create")
@login_required
def create_topic():
    form = request.form
    name = (form.get("topic_name") or "").strip()
    if not name:
        flash("话题名不能为空", "error")
        return redirect(url_for("dashboard.topics"))
    db = get_session()
    try:
        if db.query(Topic).filter_by(topic_name=name).one_or_none() is not None:
            flash(f"话题「{name}」已存在", "error")
            return redirect(url_for("dashboard.topics"))
        db.add(
            Topic(
                topic_name=name,
                color=(form.get("color") or "#FFFFFFCC").strip(),
                jump_url=(form.get("jump_url") or "").strip(),
                sort_value=int(form.get("sort_value") or 0),
                topic_type=int(form.get("topic_type") or 0),
                enabled=_checkbox(form, "enabled", default=True),
            )
        )
        db.commit()
        flash(f"已添加话题「{name}」", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.topics"))


@dashboard.post("/topics/<int:topic_id>/update")
@login_required
def update_topic(topic_id):
    form = request.form
    db = get_session()
    try:
        topic = db.get(Topic, topic_id)
        if topic is None:
            flash("话题不存在", "error")
            return redirect(url_for("dashboard.topics"))
        topic.color = (form.get("color") or topic.color).strip()
        topic.jump_url = (form.get("jump_url") or "").strip()
        topic.sort_value = int(form.get("sort_value") or 0)
        topic.topic_type = int(form.get("topic_type") or 0)
        topic.enabled = _checkbox(form, "enabled", default=True)
        db.commit()
        flash("已保存话题", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.topics"))


@dashboard.post("/topics/<int:topic_id>/delete")
@login_required
def delete_topic(topic_id):
    db = get_session()
    try:
        topic = db.get(Topic, topic_id)
        if topic is not None:
            db.delete(topic)
            db.commit()
            flash("已删除话题", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.topics"))


@dashboard.post("/topics/config")
@login_required
def save_topic_config():
    form = request.form
    db = get_session()
    try:
        set_setting(db, "topic.maxTopicCount", max(int(form.get("max_topic_count") or 1), 1))
        set_setting(db, "topic.nameMaxLength", max(int(form.get("name_max_length") or 1), 1))
        set_setting(db, "topic.officialColor", (form.get("official_color") or "#FFFFFFCC").strip())
        set_setting(db, "topic.userColor", (form.get("user_color") or "#FFFFFFCC").strip())
        db.commit()
        flash("已保存话题配置", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.topics"))


@dashboard.get("/friends")
@login_required
def friends():
    """好友关系管理：每个账号维护自己的好友列表。"""
    db = get_session()
    try:
        users = db.query(User).order_by(User.id).all()
        rows = []
        for user in users:
            entries = (
                db.query(Friendship)
                .filter(Friendship.user_id == user.id)
                .order_by(Friendship.create_time)
                .all()
            )
            rows.append({"user": user, "friends": entries})
        return render_template("friends.html", rows=rows, me=current_user())
    finally:
        db.close()


@dashboard.post("/friends/<int:user_id>/add")
@login_required
def add_friend(user_id):
    """Add a two-way friendship between two existing accounts."""
    form = request.form
    target_watch_id = (form.get("friend_watch_id") or "").strip()
    remark = (form.get("remark") or "").strip()
    db = get_session()
    try:
        user = db.get(User, user_id)
        target = db.query(User).filter_by(watch_id=target_watch_id).one_or_none()
        if user is None or target is None:
            flash("账号不存在", "error")
            return redirect(url_for("dashboard.friends"))
        if user.id == target.id:
            flash("不能把自己加为好友", "error")
            return redirect(url_for("dashboard.friends"))
        existing = (
            db.query(Friendship)
            .filter_by(user_id=user.id, friend_user_id=target.id)
            .one_or_none()
        )
        if existing is not None:
            existing.remark = remark
            flash("好友已存在，已更新备注", "ok")
        else:
            _add_friendship(db, user, target, remark)
            flash(f"已添加好友 {target.effective_name}", "ok")
        db.commit()
    finally:
        db.close()
    return redirect(url_for("dashboard.friends"))


@dashboard.post("/friends/<int:user_id>/remove")
@login_required
def remove_friend(user_id):
    """Remove both directions of a friendship."""
    target_watch_id = (request.form.get("friend_watch_id") or "").strip()
    db = get_session()
    try:
        user = db.get(User, user_id)
        target = db.query(User).filter_by(watch_id=target_watch_id).one_or_none()
        if user is not None and target is not None:
            db.query(Friendship).filter(
                Friendship.user_id.in_([user.id, target.id]),
                Friendship.friend_user_id.in_([user.id, target.id]),
            ).delete(synchronize_session=False)
            db.commit()
            flash("已移除好友", "ok")
    finally:
        db.close()
    return redirect(url_for("dashboard.friends"))


def _add_friendship(db, user, target, remark=""):
    """Create both directed edges unless they already exist."""
    for owner, other, text in ((user, target, remark), (target, user, "")):
        row = (
            db.query(Friendship)
            .filter_by(user_id=owner.id, friend_user_id=other.id)
            .one_or_none()
        )
        if row is None:
            db.add(Friendship(user_id=owner.id, friend_user_id=other.id, remark=text))
