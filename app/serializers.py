"""Convert ORM rows into the JSON shapes the watch client deserializes."""

import json

from app.models import millis


def _topic_list(raw):
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


def serialize_user(user):
    return {
        "watchId": user.watch_id,
        "watchName": user.effective_name,
        "name": user.effective_name,
        "icon": "",
    }


def serialize_moment(moment):
    """One entry of ``Moments.watchMoments``.

    ``BeanConverterUtil.convertToDbMoment`` reads ``checked``/``likeTotal``/
    ``watchId``/``emotionId``/``topicList`` and the list UI reads the display
    fields (``name``/``iconPath``/``location``/``scaleType``), so all of them
    are filled in here.
    """
    comments = [comment for comment in moment.comments if not comment.deleted]
    return {
        "id": moment.id,
        "momentId": moment.moment_id,
        "lookupId": "",
        "parentId": "",
        "watchId": moment.author.watch_id,
        "watchName": moment.author.effective_name,
        "name": moment.author.effective_name,
        "iconPath": "",
        "type": moment.type,
        "content": moment.content,
        "resource": moment.resource,
        "resourceId": moment.resource_id,
        "createTime": millis(moment.create_time),
        "emotionId": moment.emotion_id,
        "location": moment.location,
        "permissionType": moment.permission_type,
        "topicList": _topic_list(moment.topic_list_json),
        "topicListJson": moment.topic_list_json or "",
        "likeTotal": len(moment.likes) if moment.likes is not None else 0,
        "enableLike": True,
        "checked": True,
        "skiped": False,
        "previewed": False,
        "official": 0,
        "top": 0,
        "topExpireTime": 0,
        "scaleType": 0,
        "supportComment": 1,
        "commentsPageNum": 1,
        "commentsPageSize": len(comments),
        "commentsTotalCount": len(comments),
        "commentsTotalPage": 1 if comments else 0,
        "comments": [serialize_comment(comment) for comment in comments],
    }


def serialize_comment(comment):
    """One entry of ``SearchCommentResponse.comments``.

    ``CommentBean`` carries both the comment author and, for replies, the
    ``replyWatchId``/``replyWatchName`` pair; ``BeanConverterUtil`` uses those
    to decide whether a row is a plain comment (type 1) or a reply (type 2).
    """
    parent = comment.parent
    return {
        "commentId": comment.comment_id,
        "momentId": comment.moment_id,
        "momentWatchId": comment.moment.author.watch_id,
        "watchId": comment.author.watch_id,
        "watchName": comment.author.effective_name,
        "comment": comment.content,
        "createTime": millis(comment.create_time),
        "replyId": parent.author.watch_id if parent else "",
        "replyName": parent.author.effective_name if parent else "",
        "replyWatchId": parent.author.watch_id if parent else "",
        "replyWatchName": parent.author.effective_name if parent else "",
        "replyCommentId": comment.reply_comment_id or "",
        "parentWatchId": comment.moment.author.watch_id,
        "advertId": "",
        "type": 2 if parent else 1,
        "mediaType": 1,
        "checked": True,
        "deleted": comment.deleted,
    }


def serialize_like(like):
    """One value of ``PraiseResponse.momentLikeMap``.

    ``MomentLike.createTime`` is a ``java.util.Date``, which Gson parses from
    the same millisecond long used everywhere else in the protocol.
    """
    return {
        "id": like.id,
        "momentId": like.moment_id,
        "momentWatchId": like.moment.author.watch_id,
        "watchId": like.user.watch_id,
        "watchName": like.user.effective_name,
        "emotionId": like.emotion_id,
        "createTime": millis(like.create_time),
    }
