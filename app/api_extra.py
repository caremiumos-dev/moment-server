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
from app.models import Like, Moment, ReviewRequest, User, get_session, millis, utcnow

extra = Blueprint("extra", __name__)

# ``PersonalState.statusId`` — the personal page treats 0 as "no active state".
_NO_STATE = 0


def _body():
    return request.get_json(silent=True) or {}


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
        "icon": "",
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
    """Friend lists are not modelled, so removal is a no-op success."""
    return ok("1")


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
