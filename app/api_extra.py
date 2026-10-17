"""The remaining endpoints the watch calls outside ``MomentHttp``.

These live on the gateway/default host rather than ``/moment``.  They back the
personal page, the privilege (background / like-emotion) resources, the
convention pages, the report flow, the H5 url list and the open-id exchange.
Shapes follow the client beans one for one; see the notes on each view.
"""

from flask import Blueprint, request

from app.api_common import (
    authenticate,
    ok,
)
from app.media import absolute_resource
from app.models import (
    Friendship,
    Like,
    Moment,
    ReviewRequest,
    User,
    get_session,
    millis,
    utcnow,
)

extra = Blueprint("extra", __name__)


def _body():
    """Request JSON, or an empty dict."""
    return request.get_json(silent=True) or {}


# ``PersonalState.statusId`` — the personal page treats 0 as "no active state".
_NO_STATE = 0


@extra.post("/smartwatch/geniusAccount/watchGetInfo")
@authenticate()
def watch_get_info():
    """``PersonalInfoResponse`` for the watch's own account."""
    from flask import g

    return ok(_personal_info(g.current_user))


def _personal_info(user):
    return {
        "watchId": user.watch_id,
        "watchName": user.effective_name,
        "mobileId": "",
        "bindNumber": user.chip_id or "",
        "geniusNumber": "",
        "score": 0,
        "friends": 0,
        "contacts": 0,
        "level": 1,
        "icon": absolute_resource(user.avatar),
        "name": user.effective_name,
        "gender": 3,
        "birthday": 0,
        "authId": 0,
    }


@extra.post("/social-service/personalInfo/getPersonalInfo")
@authenticate()
def get_signature_and_like_rule():
    """``PersonalInfoAndLikeRuleResponse``: signature plus today's like quota."""
    return ok(
        {
            "signature": "",
            "likeRule": [],
            "likeLimit": 0,
            "likes": 0,
            "restLikes": 0,
            "fuzzyLikes": "",
            "likesLimitNumber": 0,
        }
    )


@extra.post("/social-service/personalInfo/getPersonalInfoUnite")
@authenticate()
def get_personal_info_unite():
    """Everything the personal page needs in one call."""
    from flask import g

    user = g.current_user
    session = get_session()
    try:
        likes = session.query(Like).filter_by(user_id=user.id).count()
        likes_received = (
            session.query(Like)
            .join(Moment, Like.moment_id == Moment.moment_id)
            .filter(Moment.user_id == user.id)
            .count()
        )
        return ok(
            {
                "simpleMedal": {"watchId": user.watch_id, "medals": []},
                "personalInfo": {
                    "signature": "",
                    "likeRule": [],
                    "likeLimit": 0,
                    "likes": likes,
                    "restLikes": 0,
                    "fuzzyLikes": "",
                    "likesLimitNumber": 0,
                },
                "geniusAccount": _personal_info(user),
                "socializeUser": {
                    "statusId": _NO_STATE,
                    "url": "",
                    "desc": "",
                    "expireTime": 0,
                    "likes": likes_received,
                    "watchID": user.watch_id,
                    "socialId": "",
                    "isliked": False,
                },
            }
        )
    finally:
        session.close()


@extra.post("/social-service/medal/simple")
@authenticate()
def simple_medal():
    from flask import g

    return ok({"watchId": g.current_user.watch_id, "medals": []})


@extra.post("/social-service/personalInfo/like")
@authenticate()
def like_personal_state():
    """Acknowledged; the personal page only needs a success code."""
    return ok("1")


@extra.post("/watchfriend/delFriendByWatch")
@authenticate()
def delete_friend():
    """Remove one friendship edge for the caller."""
    from flask import g

    payload = _body()
    target_watch_id = (payload.get("friendWatchId") or payload.get("watchId") or "").strip()
    if not target_watch_id:
        return ok("1")
    session = get_session()
    try:
        target = session.query(User).filter_by(watch_id=target_watch_id).one_or_none()
        if target is not None:
            session.query(Friendship).filter(
                Friendship.user_id == g.current_user.id,
                Friendship.friend_user_id == target.id,
            ).delete(synchronize_session=False)
            session.commit()
        return ok("1")
    finally:
        session.close()


@extra.post("/extra/getFriendList")
@authenticate()
def get_friend_list():
    """好友列表，供客户端在本地通讯录 provider 拿不到数据时回退使用。

    返回一个 ``Friend`` 数组：``watchId`` / ``name`` / ``friendIcon``，与
    客户端 ``com.xtc.moment.module.bean.Friend`` 一一对应。备注名优先于
    账号昵称；没有头像时 ``friendIcon`` 为空字符串，客户端会回退到默认头像。
    """
    from flask import g

    session = get_session()
    try:
        rows = (
            session.query(Friendship)
            .filter(Friendship.user_id == g.current_user.id)
            .order_by(Friendship.remark, Friendship.create_time)
            .all()
        )
        return ok(
            [
                {
                    "watchId": row.friend_user.watch_id,
                    "name": row.remark or row.friend_user.effective_name,
                    "friendIcon": absolute_resource(row.friend_user.avatar),
                }
                for row in rows
                if row.friend_user is not None and row.friend_user.is_active
            ]
        )
    finally:
        session.close()
    return ok("1")


@extra.post("/extra/getFriendAvatar")
@authenticate()
def get_friend_avatars():
    """Return server-set avatars for a batch of friend watch ids.

    The client normally reads contacts from the system provider.  A friend
    added only on the self-hosted server has no provider row, so this endpoint
    lets the client overlay the server avatar without replacing the provider
    as the source of names and friendship data.
    """
    from flask import g

    payload = _body()
    watch_ids = payload.get("watchIds") if isinstance(payload, dict) else None
    if not isinstance(watch_ids, list):
        watch_ids = []
    watch_ids = [str(value).strip() for value in watch_ids if str(value).strip()]
    if not watch_ids:
        return ok([])
    # Cap the request to keep a malformed client from generating an unbounded
    # SQL IN clause; the watch's contact page size is far below this limit.
    watch_ids = list(dict.fromkeys(watch_ids))[:500]
    session = get_session()
    try:
        rows = (
            session.query(User)
            .filter(User.watch_id.in_(watch_ids), User.is_active.is_(True))
            .all()
        )
        return ok(
            [
                {"watchId": row.watch_id, "friendIcon": absolute_resource(row.avatar)}
                for row in rows
                if row.avatar
            ]
        )
    finally:
        session.close()


@extra.get("/operation/exp/getWatchScoreLevelInfo/<watch_id>/<model>")
@authenticate()
def watch_score_level_info(watch_id, model):
    """``PersonalGradeResultBean``: a level-1 account with no exp yet."""
    return ok(
        {
            "timeStamp": millis(utcnow()),
            "score": 0,
            "levelGap": 0,
            "level": 1,
            "costScore": 0,
            "nextLevelGap": 0,
            "getExp": 0,
            "scoreForExpNum": 0,
            "restScoreOfExpNum": 0,
        }
    )


@extra.post("/social-service/prerogative/getpersonal")
@authenticate()
def prerogative_personal():
    """No background/like-emotion privileges are granted."""
    return ok({"emotions": [], "packages": []})


@extra.get("/social-service/prerogative/getResource")
@authenticate()
def prerogative_resource():
    return ok([])


@extra.post("/social-service/convention/home")
@authenticate()
def convention_home():
    """``CommunityConversationResponse``; empty catalog means no chapters."""
    return ok({"catalog": [], "content": "", "imageUrl": "", "language": ""})


@extra.post("/social-service/convention/content")
@authenticate()
def convention_content():
    """``CommunityDetailResponse`` list for the requested chapter."""
    return ok([])


@extra.get("/social-service/config/getWhiteH5")
@authenticate(required=False)
def white_h5():
    """H5 whitelist; an empty list lets the built-in verification run."""
    return ok([])


@extra.get("/smartwatch/server/getWatchH5Url/<package_name>")
@authenticate(required=False)
def watch_h5_urls(package_name):
    """``DbWebUrl`` list backing the H5 entries; none are configured."""
    return ok([])


@extra.post("/oauth/openAccount/getOpenId")
@authenticate()
def get_open_id():
    """``RespAccountInfo``; the watch id doubles as the open id."""
    from flask import g

    payload = _body()
    return ok(
        {
            "appId": int(payload.get("appId") or 2),
            "authId": 0,
            "openId": g.current_user.watch_id,
        }
    )


@extra.post("/route-service/route/getRouteInfo")
@authenticate(required=False)
def route_info():
    """Route config.

    With a build-time base url there is nothing to route, so the response is
    empty and ``HttpDomainConfigProvider.selectConfig`` simply finds no match.
    """
    return ok({"dataCenterCode": "", "envs": []})


@extra.post("/social-service/violation/initViolationInfo")
@authenticate()
def init_violation_info():
    """``ViolationInfoBean``: the account is in good standing."""
    return ok(
        {
            "startTime": "",
            "expireTime": "",
            "delayCount": 0,
            "status": 0,
            "type": 0,
            "disableSendDeadLine": 0,
        }
    )


@extra.post("/social-service/inform/content")
@authenticate()
def inform_content():
    """``BannerNetBean`` for the report entry."""
    return ok({"contentVos": []})


@extra.post("/social-service/violation/searchMomentRiskRecord")
@authenticate()
def search_moment_risk_record():
    return ok({"contentVos": []})


@extra.post("/social-service/violation/uploadDelayCount")
@authenticate()
def upload_delay_count():
    return ok("1")


@extra.post("/social-service/violation/uploadBanRecord")
@authenticate()
def upload_ban_record():
    return ok("1")


@extra.post("/extra/review-request")
@authenticate(required=False, allow_banned=True)
def review_request():
    """Appeal submitted from the "your account has been suspended" dialog.

    Not gated by ``authenticate(required=True)``: the whole point is that a
    banned account can still reach it, and ``authenticate`` would answer
    ``000007`` first.
    """
    from flask import g

    payload = _body()
    watch_id = payload.get("watchId") or (g.identity or {}).get("account_id") or ""
    session = get_session()
    try:
        user = session.query(User).filter_by(watch_id=watch_id).one_or_none() if watch_id else None
        session.add(
            ReviewRequest(
                watch_id=watch_id,
                user_id=user.id if user is not None else None,
                reason=(payload.get("reason") or "").strip(),
                contact=(payload.get("contact") or "").strip(),
                create_time=utcnow(),
            )
        )
        session.commit()
        return ok("1")
    finally:
        session.close()
