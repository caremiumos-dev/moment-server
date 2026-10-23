"""Moment API endpoints, matching the paths declared in ``MomentHttp``."""

import json
import secrets
import datetime as dt
from pathlib import Path

from flask import Blueprint, current_app, g, request
from sqlalchemy import and_, desc as sql_desc, or_

from app.api_common import (
    ERROR_INVALID_PARAM,
    ERROR_NOT_FOUND,
    authenticate,
    fail,
    ok,
)
from app.models import (
    Comment,
    Friendship,
    Like,
    Moment,
    MomentVisibility,
    MomentTemplate,
    Topic,
    User,
    get_session,
    get_setting,
    millis,
    utcnow,
)
from app.media import absolute_resource, media_url
from app.serializers import (
    serialize_comment,
    serialize_like,
    serialize_moment,
    serialize_template,
    serialize_topic,
)

api = Blueprint("api", __name__)

DEFAULT_PAGE_SIZE = 20

# ``FriendsVisibleBean.type`` values used by the watch client.
VISIBLE_ALL_FRIENDS = 0
VISIBLE_SELECTED_FRIENDS = 1
VISIBLE_ALL_EXCEPT_SELECTED = 2
VISIBLE_SELF_ONLY = 3
VALID_VISIBILITY_TYPES = {
    VISIBLE_ALL_FRIENDS,
    VISIBLE_SELECTED_FRIENDS,
    VISIBLE_ALL_EXCEPT_SELECTED,
    VISIBLE_SELF_ONLY,
}
MAX_VISIBILITY_IDS = 500


def dt_from_millis(value):
    """Convert the client's millisecond timestamps into naive UTC datetimes."""
    return dt.datetime.fromtimestamp(value / 1000.0, dt.timezone.utc).replace(tzinfo=None)


def _body():
    return request.get_json(silent=True) or {}


def _new_id(prefix=""):
    return f"{prefix}{secrets.token_hex(16)}"


def _visibility_values(payload, author, accept_type_alias=False):
    """Validate the visibility payload and return ``(type, watch_ids)``.

    ``None`` as the first tuple item means the request is invalid.  Partial
    ranges must contain at least one friend.  Full and self-only ranges ignore
    any stale lookup ids supplied by an older client.
    """
    raw_permission_type = payload.get("permissionType")
    if raw_permission_type is None and accept_type_alias:
        raw_permission_type = payload.get("type", 0)
    try:
        permission_type = int(raw_permission_type or 0)
    except (TypeError, ValueError):
        return None, []
    if permission_type not in VALID_VISIBILITY_TYPES:
        return None, []

    raw_ids = payload.get("lookupIds")
    if raw_ids is None:
        raw_ids = payload.get("friends")
    if isinstance(raw_ids, str):
        try:
            raw_ids = json.loads(raw_ids)
        except (TypeError, ValueError):
            raw_ids = []
    if not isinstance(raw_ids, list):
        raw_ids = []

    watch_ids = []
    seen = set()
    for raw_id in raw_ids:
        watch_id = str(raw_id or "").strip()
        if not watch_id or watch_id == author.watch_id or watch_id in seen:
            continue
        seen.add(watch_id)
        watch_ids.append(watch_id)
        if len(watch_ids) >= MAX_VISIBILITY_IDS:
            break

    if permission_type in (VISIBLE_SELECTED_FRIENDS, VISIBLE_ALL_EXCEPT_SELECTED):
        if not watch_ids:
            return None, []
    else:
        watch_ids = []
    return permission_type, watch_ids


def _replace_moment_visibilities(session, moment, watch_ids):
    """Replace the selected watch ids for a moment while preserving order."""
    session.query(MomentVisibility).filter_by(moment_id=moment.moment_id).delete(
        synchronize_session=False
    )
    for watch_id in watch_ids:
        session.add(MomentVisibility(moment_id=moment.moment_id, watch_id=watch_id))


def _visibility_condition(session, viewer):
    """SQL condition selecting moments that ``viewer`` is allowed to read.

    Type 0 and type 2 require a server-side friendship with the author.  The
    selected watch ids for type 1/2 need not be server friends, which keeps the
    client's provider fallback usable when its address book contains a contact
    that the dashboard has not registered.
    """
    friend_authors = session.query(Friendship.friend_user_id).filter(
        Friendship.user_id == viewer.id
    )
    is_selected = (
        session.query(MomentVisibility.id)
        .filter(
            MomentVisibility.moment_id == Moment.moment_id,
            MomentVisibility.watch_id == viewer.watch_id,
        )
        .exists()
    )
    return or_(
        Moment.user_id == viewer.id,
        and_(Moment.permission_type == VISIBLE_ALL_FRIENDS,
             Moment.user_id.in_(friend_authors)),
        and_(Moment.permission_type == VISIBLE_SELECTED_FRIENDS, is_selected),
        and_(Moment.permission_type == VISIBLE_ALL_EXCEPT_SELECTED,
             Moment.user_id.in_(friend_authors), ~is_selected),
    )


def _can_view_moment(session, moment, viewer):
    """Python equivalent of :func:`_visibility_condition` for one moment."""
    if moment is None or viewer is None:
        return False
    if moment.user_id == viewer.id:
        return True
    if moment.permission_type == VISIBLE_SELECTED_FRIENDS:
        return (
            session.query(MomentVisibility.id)
            .filter_by(moment_id=moment.moment_id, watch_id=viewer.watch_id)
            .first()
            is not None
        )
    if moment.permission_type not in (VISIBLE_ALL_FRIENDS, VISIBLE_ALL_EXCEPT_SELECTED):
        return False
    is_friend = (
        session.query(Friendship.id)
        .filter_by(user_id=viewer.id, friend_user_id=moment.user_id)
        .first()
        is not None
    )
    if not is_friend:
        return False
    if moment.permission_type == VISIBLE_ALL_FRIENDS:
        return True
    excluded = (
        session.query(MomentVisibility.id)
        .filter_by(moment_id=moment.moment_id, watch_id=viewer.watch_id)
        .first()
    )
    return excluded is None


def _visible_moment(session, moment_id, viewer):
    moment = (
        session.query(Moment)
        .filter(Moment.moment_id == moment_id, Moment.deleted.is_(False))
        .one_or_none()
    )
    return moment if _can_view_moment(session, moment, viewer) else None


@api.post("/moment/public")
@authenticate()
def publish_moment():
    """Publish a moment. Also serves ``PublishMomentTask.RequestValues``."""
    from flask import g

    payload = _body()
    session = get_session()
    try:
        permission_type, lookup_ids = _visibility_values(payload, g.current_user)
        if permission_type is None:
            return fail(ERROR_INVALID_PARAM, "invalid visibility range")
        topic_list = payload.get("topicList")
        moment = Moment(
            moment_id=_new_id(),
            user_id=g.current_user.id,
            type=int(payload.get("type") or 3),
            content=payload.get("content") or "",
            resource=payload.get("resource") or "",
            resource_id=int(payload.get("resourceId") or 0),
            location=payload.get("location") or "",
            emotion_id=int(payload.get("emotionId") or 0),
            permission_type=permission_type,
            topic_list_json=json.dumps(topic_list, ensure_ascii=False) if topic_list else "",
            create_time=utcnow(),
        )
        session.add(moment)
        session.flush()
        _replace_moment_visibilities(session, moment, lookup_ids)
        session.commit()
        return ok(serialize_moment(moment, g.current_user.id))
    finally:
        session.close()


@api.post("/moment/delete")
@authenticate()
def delete_moment():
    from flask import g

    payload = _body()
    session = get_session()
    try:
        moment = session.query(Moment).filter_by(moment_id=payload.get("momentId")).one_or_none()
        if moment is None:
            return fail(ERROR_NOT_FOUND, "moment not found")
        if moment.user_id != g.current_user.id and not g.current_user.is_admin:
            return fail(ERROR_INVALID_PARAM, "not the owner")
        moment.deleted = True
        session.commit()
        return ok({"result": 1})
    finally:
        session.close()


@api.post("/moment/search")
@authenticate()
def search_moment():
    """Return moments, mirroring how ``MomentHttpServiceProxy`` fills the body.

    ``MomentPresenter`` -> ``getMomentsFromNet(count, 1, watchId, endTime)`` ->
    ``searchMoment(begin=0, end=endTime-1, friend=1, from=0, size=count, ...)``.
    So ``size`` is the page size, ``friend`` is the page number (always 1), and
    paging happens by moving the ``end`` bound backwards in time.  The query is
    filtered by the viewer's server-side friendships and the moment visibility
    rule before paging, so hidden moments never contribute to ``total``.
    """
    payload = _body()
    size = int(payload.get("size") or DEFAULT_PAGE_SIZE)
    if size <= 0 or size > 200:
        size = DEFAULT_PAGE_SIZE
    end = int(payload.get("end") or 0)
    begin = int(payload.get("begin") or 0)
    session = get_session()
    try:
        query = session.query(Moment).filter(
            Moment.deleted.is_(False), _visibility_condition(session, g.current_user)
        )
        if end > 0:
            query = query.filter(Moment.create_time <= dt_from_millis(end))
        if begin > 0:
            query = query.filter(Moment.create_time > dt_from_millis(begin))
        query = query.order_by(sql_desc(Moment.create_time))
        total = query.count()
        moments = query.limit(size).all()
        return ok(
            {
                "watchMoments": [
                    serialize_moment(moment, g.current_user.id) for moment in moments
                ],
                "adverts": [],
                "likeNotify": None,
                "maxLikeNum": 0,
                "total": total,
            }
        )
    finally:
        session.close()


@api.post("/moment/like")
@authenticate()
def like_moment():
    from flask import g

    payload = _body()
    session = get_session()
    try:
        moment = _visible_moment(session, payload.get("momentId"), g.current_user)
        if moment is None:
            return fail(ERROR_NOT_FOUND, "moment not found")
        existing = (
            session.query(Like)
            .filter_by(moment_id=moment.moment_id, user_id=g.current_user.id)
            .one_or_none()
        )
        if existing is None:
            session.add(
                Like(
                    moment_id=moment.moment_id,
                    user_id=g.current_user.id,
                    emotion_id=int(payload.get("emotionId") or 0),
                    create_time=utcnow(),
                )
            )
            session.commit()
        return ok(
            {
                "momentId": moment.moment_id,
                "momentWatchId": moment.author.watch_id,
                "watchId": g.current_user.watch_id,
                "watchName": g.current_user.effective_name,
                "emotionId": int(payload.get("emotionId") or 0),
                "createTime": int(utcnow().timestamp() * 1000),
            }
        )
    finally:
        session.close()


@api.post("/moment/like/cancel")
@authenticate()
def cancel_like():
    from flask import g

    payload = _body()
    session = get_session()
    try:
        deleted = (
            session.query(Like)
            .filter_by(moment_id=payload.get("momentId"), user_id=g.current_user.id)
            .delete()
        )
        session.commit()
        return ok("1" if deleted else "0")
    finally:
        session.close()


@api.post("/moment/like/search")
@authenticate()
def search_like():
    payload = _body()
    moment_ids = payload.get("momentIds") or []
    session = get_session()
    try:
        visible_ids = []
        if moment_ids:
            visible_ids = [
                row.moment_id
                for row in session.query(Moment).filter(
                    Moment.moment_id.in_(moment_ids), Moment.deleted.is_(False),
                    _visibility_condition(session, g.current_user),
                )
            ]
        likes = session.query(Like).filter(Like.moment_id.in_(visible_ids)).all() if visible_ids else []
        grouped = {}
        for like in likes:
            grouped.setdefault(like.moment_id, []).append(serialize_like(like))
        return ok({"momentLikeMap": grouped})
    finally:
        session.close()


@api.post("/moment/comment")
@authenticate()
def comment_moment():
    from flask import g

    payload = _body()
    session = get_session()
    try:
        moment = _visible_moment(session, payload.get("momentId"), g.current_user)
        if moment is None:
            return fail(ERROR_NOT_FOUND, "moment not found")
        comment = Comment(
            comment_id=_new_id(),
            moment_id=moment.moment_id,
            user_id=g.current_user.id,
            content=payload.get("comment") or "",
            reply_comment_id=payload.get("replyId") or "",
            create_time=utcnow(),
        )
        # A reply names the watch id of the comment it answers, so store the
        # matching row to reproduce ``CommentBean.replyWatchId``/``replyWatchName``.
        reply_watch_id = payload.get("replyId") or ""
        if reply_watch_id:
            parent = (
                session.query(Comment)
                .join(User, Comment.user_id == User.id)
                .filter(Comment.moment_id == moment.moment_id, User.watch_id == reply_watch_id)
                .order_by(sql_desc(Comment.create_time))
                .first()
            )
            if parent is not None:
                comment.parent_id = parent.id
                comment.reply_comment_id = parent.comment_id
        session.add(comment)
        session.commit()
        return ok({"result": "1", "comment": serialize_comment(comment), "tips": ""})
    finally:
        session.close()


@api.post("/moment/deleteComment")
@authenticate()
def delete_comment():
    from flask import g

    payload = _body()
    session = get_session()
    try:
        comment = session.query(Comment).filter_by(comment_id=payload.get("commentId")).one_or_none()
        if comment is None:
            return fail(ERROR_NOT_FOUND, "comment not found")
        # A comment may be deleted by its author, or by the author of the
        # moment it belongs to (the original app allows moderating your own
        # post's comment section), plus admins.
        moment = session.query(Moment).filter_by(moment_id=comment.moment_id).one_or_none()
        is_comment_author = comment.user_id == g.current_user.id
        is_moment_author = moment is not None and moment.user_id == g.current_user.id
        if not (is_comment_author or is_moment_author or g.current_user.is_admin):
            return fail(ERROR_INVALID_PARAM, "not the owner")
        comment.deleted = True
        session.commit()
        return ok({"code": "000001", "data": None, "desc": ""})
    finally:
        session.close()


@api.post("/moment/getMomentComment")
@authenticate()
def get_moment_comment():
    payload = _body()
    page_num = int(payload.get("pageNum") or 1)
    page_size = int(payload.get("pageSize") or DEFAULT_PAGE_SIZE)
    session = get_session()
    try:
        if _visible_moment(session, payload.get("momentId"), g.current_user) is None:
            return fail(ERROR_NOT_FOUND, "moment not found")
        query = (
            session.query(Comment)
            .filter_by(moment_id=payload.get("momentId"))
            .filter(Comment.deleted.is_(False))
            .order_by(sql_desc(Comment.create_time))
        )
        total = query.count()
        comments = query.offset((page_num - 1) * page_size).limit(page_size).all()
        total_page = (total + page_size - 1) // page_size if page_size else 0
        return ok(
            {
                "comments": [serialize_comment(comment) for comment in comments],
                "pageNum": page_num,
                "pageSize": page_size,
                "totalCount": total,
                "totalPage": total_page,
            }
        )
    finally:
        session.close()


@api.post("/moment/getMomentCommentAll")
@authenticate()
def get_moment_comment_all():
    payload = _body()
    session = get_session()
    try:
        if _visible_moment(session, payload.get("momentId"), g.current_user) is None:
            return fail(ERROR_NOT_FOUND, "moment not found")
        comments = (
            session.query(Comment)
            .filter_by(moment_id=payload.get("momentId"))
            .filter(Comment.deleted.is_(False))
            .order_by(sql_desc(Comment.create_time))
            .all()
        )
        return ok(
            {
                "comments": [serialize_comment(comment) for comment in comments],
                "pageNum": 1,
                "pageSize": len(comments),
                "totalCount": len(comments),
                "totalPage": 1,
            }
        )
    finally:
        session.close()


@api.post("/moment/isBannedToPost")
@authenticate(required=False)
def is_banned_to_post():
    return ok({"bannedToPost": False})


@api.post("/moment/permission/search")
@authenticate()
def permission_search():
    """Return the visibility scope of moments owned by the caller."""
    payload = _body()
    raw_ids = payload.get("momentIds") if isinstance(payload, dict) else None
    moment_ids = []
    seen = set()
    for raw_id in raw_ids if isinstance(raw_ids, list) else []:
        moment_id = str(raw_id or "").strip()
        if moment_id and moment_id not in seen:
            seen.add(moment_id)
            moment_ids.append(moment_id)
            if len(moment_ids) >= 200:
                break
    if not moment_ids:
        return ok([])
    session = get_session()
    try:
        moments = (
            session.query(Moment)
            .filter(Moment.moment_id.in_(moment_ids), Moment.deleted.is_(False))
            .all()
        )
        moment_map = {moment.moment_id: moment for moment in moments}
        visibility_rows = (
            session.query(MomentVisibility)
            .filter(MomentVisibility.moment_id.in_(moment_ids))
            .order_by(MomentVisibility.id)
            .all()
        )
        ids_by_moment = {}
        for row in visibility_rows:
            ids_by_moment.setdefault(row.moment_id, []).append(row.watch_id)
        result = []
        for moment_id in moment_ids:
            moment = moment_map.get(moment_id)
            if moment is None:
                continue
            if moment.user_id != g.current_user.id and not g.current_user.is_admin:
                continue
            result.append(
                {
                    "type": moment.permission_type,
                    "permissionType": moment.permission_type,
                    "friends": ids_by_moment.get(moment_id, []),
                    "lookupIds": ids_by_moment.get(moment_id, []),
                    "momentId": moment_id,
                }
            )
        return ok(result)
    finally:
        session.close()


@api.post("/moment/permission/update")
@authenticate()
def permission_update():
    payload = _body()
    moment_id = str(payload.get("momentId") or "").strip()
    if not moment_id:
        return fail(ERROR_INVALID_PARAM, "momentId is required")
    session = get_session()
    try:
        moment = session.query(Moment).filter_by(moment_id=moment_id).one_or_none()
        if moment is None or moment.deleted:
            return fail(ERROR_NOT_FOUND, "moment not found")
        if moment.user_id != g.current_user.id and not g.current_user.is_admin:
            return fail(ERROR_INVALID_PARAM, "not the owner")
        permission_type, lookup_ids = _visibility_values(
            payload, moment.author, accept_type_alias=True
        )
        if permission_type is None:
            return fail(ERROR_INVALID_PARAM, "invalid visibility range")
        moment.permission_type = permission_type
        _replace_moment_visibilities(session, moment, lookup_ids)
        session.commit()
        return ok("1")
    finally:
        session.close()


@api.post("/moment/doLbsStar")
@authenticate()
def do_lbs_star():
    return ok("1")


@api.post("/moment/template")
@authenticate()
def moment_template():
    """心情/状态模板列表，对应客户端 ``DbTemplate``。

    客户端进入发布页时会用 ``updateId=0`` 拉全量并写进本地
    ``moment_template`` 表；带上 ``updateId`` 时只回传之后更新的行。
    ``id`` 必须是每行唯一的正整数，因为客户端把它当作本地主键。
    """
    payload = _body()
    try:
        update_id = int(payload.get("updateId") or 0)
    except (TypeError, ValueError):
        update_id = 0
    session = get_session()
    try:
        rows = (
            session.query(MomentTemplate)
            .filter(MomentTemplate.enabled.is_(True))
            .order_by(MomentTemplate.sort_value.desc(), MomentTemplate.id)
            .all()
        )
        resources = []
        for row in rows:
            item = serialize_template(row)
            item["resource"] = absolute_resource(item["resource"])
            resources.append(item)
        newest = max((millis(row.update_time) for row in rows), default=0)
        if update_id:
            changed = [item for item in resources if item["updateTime"] > update_id]
        else:
            changed = resources
        return ok({"resources": changed, "total": len(resources), "updateId": newest})
    finally:
        session.close()


@api.post("/moment/file/video/transfer")
@authenticate()
def video_transfer():
    """Video upload tokens, including the thumbnail pair.

    ``PublishService.getUploadVideoToken`` reads ``source``/``uploadToken`` for
    the mp4 and ``icon``/``iconUploadToken`` for the webp cover, then hands both
    to the uploader with the keys returned here.
    """
    from flask import g

    payload = _body()
    video_key = _new_media_key(g.current_user.watch_id, payload.get("format") or ".mp4")
    icon_key = _new_media_key(g.current_user.watch_id, payload.get("iconFormat") or ".webp")
    return ok(
        {
            "type": "1",
            "zone": _ZONE,
            "wangSuUrl": "",
            "uploadToken": _upload_token(video_key),
            "uploadTokenDeadLine": _deadline(),
            "iconUploadToken": _upload_token(icon_key),
            "iconUploadTokenDeadLine": _deadline(),
            "source": _resource(video_key),
            "icon": _resource(icon_key),
            "transfer": _resource(video_key),
            "customParamMap": {},
        }
    )


def _safe_key(key):
    """Reject keys that would escape the media directory."""
    if not key:
        return None
    name = Path(key).name
    if name != key or name in (".", ".."):
        return None
    return name


@api.post("/moment/file/pic/transfer")
@authenticate()
def photo_upload_token():
    """Hand out the key/token pair for a single picture upload."""
    from flask import g

    payload = _body()
    key = _new_media_key(g.current_user.watch_id, payload.get("format") or ".webp")
    return ok(_photo_token(key))


@api.post("/moment/file/pics/transfer")
@authenticate()
def photo_upload_tokens():
    """Batch variant of :func:`photo_upload_token`."""
    from flask import g

    payload = _body() or []
    if not isinstance(payload, list):
        return fail(ERROR_INVALID_PARAM, "expected a list of photo token params")
    return ok(
        [
            _photo_token(_new_media_key(g.current_user.watch_id, (entry or {}).get("format") or ".webp"))
            for entry in payload
        ]
    )


@api.post("/moment/file/upload")
def upload_media():
    """Store one uploaded file.

    Authentication is the upload token itself (``local:<key>``) rather than the
    request headers, because the client uploads with a bare HTTP client that
    does not run the app's signing interceptors.
    """
    key = _safe_key(request.headers.get("Upload-Key") or request.args.get("key"))
    token = request.headers.get("Upload-Token") or request.args.get("token")
    if key is None:
        return fail(ERROR_INVALID_PARAM, "invalid upload key")
    if token != _upload_token(key):
        return fail(ERROR_INVALID_PARAM, "invalid upload token")
    payload = request.get_data()
    if not payload:
        return fail(ERROR_INVALID_PARAM, "empty upload body")
    media_dir = Path(current_app.config["MEDIA_DIR"])
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / key).write_bytes(payload)
    return ok({"key": key, "size": len(payload), "url": media_url(key)})


@api.post("/moment/file/download")
@authenticate()
def file_download_url():
    key = _safe_key((_body() or {}).get("key"))
    if key is None:
        return fail(ERROR_INVALID_PARAM, "invalid key")
    return ok({"urls": [_resource(key)]})


@api.post("/moment/file/batchDownload")
@authenticate()
def file_batch_download_url():
    keys = (_body() or {}).get("keys") or []
    urls = []
    for key in keys:
        safe = _safe_key(key)
        if safe is not None:
            urls.append(_resource(safe))
    return ok({"urls": urls})


_ZONE = "self"
# The client refreshes a url once ``now > urlDeadline``, so hand out a real
# deadline instead of 0 to avoid a needless extra round trip per image.
_URL_LIFETIME_MILLIS = 24 * 60 * 60 * 1000


def _deadline():
    return int(utcnow().timestamp() * 1000) + _URL_LIFETIME_MILLIS


def _new_media_key(watch_id, suffix):
    """A unique, path-safe object key that keeps the client's extension."""
    suffix = (suffix or "").strip()
    if not suffix.startswith("."):
        suffix = f".{suffix}" if suffix else ""
    return f"{watch_id}_{secrets.token_hex(16)}{suffix}"


def _upload_token(key):
    """Token the client echoes back on upload; validated by :func:`upload_media`."""
    return f"local:{key}"


def _resource(key):
    """A ``CloudFileResource`` pointing at our own ``/media`` route."""
    return {
        "key": key,
        "downloadUrl": media_url(key),
        "urlDeadline": _deadline(),
        "width": 320,
        "height": 360,
    }


def _photo_token(key):
    """A ``PhotoTokenVo`` whose ``source``/``smallPic`` point at our media route."""
    return {
        "type": "1",
        "zone": _ZONE,
        "wangSuUrl": "",
        "uploadToken": _upload_token(key),
        "uploadTokenDeadLine": _deadline(),
        "source": _resource(key),
        # No separate thumbnail: the widget falls back to ``source`` when
        # ``smallPic`` is absent, which avoids serving the image twice.
        "smallPic": None,
        "customParamMap": {},
    }


@api.post("/moment/vlog/gift/search")
@authenticate()
def search_gift():
    return ok({"gifts": [], "total": 0})


@api.post("/moment/vlog/gift/sendgift")
@authenticate()
def send_gift():
    return ok({"code": "000001", "data": None, "desc": ""})


@api.post("/moment/advertComment/getAllAdvert")
@authenticate()
def get_all_advert():
    return ok({"advertVoList": []})


@api.post("/moment/advertComment/getAdvertComment")
@authenticate()
def get_advert_comment():
    return ok(
        {
            "comments": [],
            "pageNum": int(_body().get("pageNum") or 1),
            "pageSize": DEFAULT_PAGE_SIZE,
            "totalCount": 0,
            "totalPage": 0,
        }
    )


@api.post("/moment/advertComment/comment")
@authenticate()
def comment_advert():
    return ok({"result": "1", "comment": None, "tips": ""})


@api.post("/moment/advertComment/deleteComment")
@authenticate()
def delete_advert_comment():
    """Delete a comment by id.

    The watch routes deletions here whenever the moment's ``watchId`` is
    shorter than 40 characters, which it treats as an "official" moment.  A
    self-hosted account id is 32 hex characters, so every comment posted on a
    friend's moment ends up on this endpoint rather than
    ``/moment/deleteComment``.  It must therefore really delete the row:
    answering success without touching the database makes the comment
    reappear on the next refresh.
    """
    from flask import g

    payload = _body()
    session = get_session()
    try:
        comment = session.query(Comment).filter_by(comment_id=payload.get("commentId")).one_or_none()
        if comment is None:
            return ok({"code": "000001", "data": None, "desc": ""})
        moment = session.query(Moment).filter_by(moment_id=comment.moment_id).one_or_none()
        is_comment_author = comment.user_id == g.current_user.id
        is_moment_author = moment is not None and moment.user_id == g.current_user.id
        if is_comment_author or is_moment_author or g.current_user.is_admin:
            comment.deleted = True
            session.commit()
        return ok({"code": "000001", "data": None, "desc": ""})
    finally:
        session.close()


@api.post("/moment/refreshos")
@authenticate()
def report_moment():
    return ok("1")


@api.get("/moment-service/topic/recommend/list")
@authenticate(required=False)
def topic_recommend_list():
    """话题推荐列表：后台配置的官方话题 + 用户已用过的话题。

    客户端只用 ``config.maxTopicCount`` 限制一条动态能挂几个话题，
    用 ``topicList`` 渲染可选列表，所以两者都必须非空。
    """
    session = get_session()
    try:
        official = (
            session.query(Topic)
            .filter(Topic.enabled.is_(True))
            .order_by(Topic.sort_value.desc(), Topic.id)
            .all()
        )
        seen = {topic.topic_name for topic in official}
        topics = [serialize_topic(topic) for topic in official]

        # Topics users typed themselves are offered back as user topics.
        counts = {}
        for (raw,) in session.query(Moment.topic_list_json).filter(Moment.deleted.is_(False)):
            if not raw:
                continue
            try:
                entries = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(entries, list):
                continue
            for entry in entries:
                name = (entry or {}).get("topicName")
                if name:
                    counts[name] = counts.get(name, 0) + 1
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            if name in seen:
                continue
            topics.append(
                {
                    "topicName": name,
                    "color": get_setting(session, "topic.userColor", "#FFFFFFCC"),
                    "jumpUrl": "",
                    "sortValue": count,
                    "topicType": 1,
                }
            )
        return ok(
            {
                "config": {
                    "maxTopicCount": int(get_setting(session, "topic.maxTopicCount", "2")),
                    "topicNameMaxLength": int(get_setting(session, "topic.nameMaxLength", "12")),
                    "officialColor": get_setting(session, "topic.officialColor", "#FFFFFFCC"),
                    "userColor": get_setting(session, "topic.userColor", "#FFFFFFCC"),
                },
                "topicList": topics,
            }
        )
    finally:
        session.close()


@api.get("/social-service/warmReminder/getWarmReminderConfig")
@authenticate(required=False)
def reminder_config():
    """No warm reminders: an empty list and a zero warm time."""
    return ok({"WarmReminderList": [], "warmTime": 0})


@api.post("/social-service/inform/submit")
@authenticate()
def submit_report():
    return ok("1")


@api.post("/social-service/inform/state")
@authenticate()
def report_state():
    return ok({"state": 0})


@api.get("/moment/user/info")
@authenticate()
def user_info():
    from flask import g

    return ok(
        {
            "watchId": g.current_user.watch_id,
            "watchName": g.current_user.effective_name,
            "isAdmin": g.current_user.is_admin,
        }
    )
