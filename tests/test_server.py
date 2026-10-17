"""End-to-end checks for the self-hosted moment server.

Run with:  python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402


def base_param(watch_id, device_id="dev-1", chip_id="chip-1"):
    """The plaintext identity header the watch client sends."""
    return json.dumps(
        {
            "accountId": watch_id,
            "appId": "2",
            "deviceId": device_id,
            "imFlag": "1",
            "mac": "00:11:22:33:44:55",
            "program": "watch",
            "registId": 1,
            "requestId": "req-1",
            "timestamp": "2026-10-16 12:00:00",
            "token": chip_id,
        }
    )


class MomentServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_dir = tempfile.mkdtemp(prefix="moment-media-")
        db_path = os.path.join(tempfile.mkdtemp(prefix="moment-db-"), "test.db")
        cls.app = create_app(
            overrides={
                "TESTING": True,
                "DATABASE_URL": f"sqlite:///{db_path}",
                "MEDIA_DIR": cls.media_dir,
                "SECRET_KEY": "test",
            }
        )

    def setUp(self):
        self.client = self.app.test_client()

    def post_form(self, path, data=None, **kwargs):
        """Submit a dashboard form with the CSRF token the guard requires."""
        with self.client.session_transaction() as session:
            token = session.get("csrf_token")
        if not token:
            # Any dashboard GET populates the session token.
            self.client.get("/login")
            with self.client.session_transaction() as session:
                token = session.get("csrf_token")
        payload = dict(data or {})
        payload.setdefault("csrf_token", token)
        return self.client.post(path, data=payload, **kwargs)

    def test_health(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_readiness_checks_the_database(self):
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_production_refuses_the_default_secret(self):
        # Call the guard directly: building a whole app here would replace the
        # module-level engine the other tests share.
        from flask import Flask

        from app import _check_secret

        probe = Flask(__name__)
        probe.config.update(
            {"SECRET_KEY": "moment-dev-secret-change-me", "ALLOW_INSECURE_DEFAULTS": False}
        )
        with self.assertRaises(RuntimeError):
            _check_secret(probe)

        probe.config["SECRET_KEY"] = "a-real-production-secret"
        _check_secret(probe)  # must not raise

    def test_friend_list_admin_and_fallback_endpoint(self):
        """The server friend list backs the client's provider fallback."""
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form(
            "/users/create",
            data={"username": "alice", "password": "pw", "watch_id": "W2011", "is_active": "on",
                  "display_name": "小红"},
        )
        self.post_form(
            "/users/create",
            data={"username": "bob", "password": "pw", "watch_id": "W2012", "is_active": "on",
                  "display_name": "小明"},
        )
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            alice_id = db.query(User).filter_by(watch_id="W2011").one().id
            db.close()

        # Nobody has friends yet, so the fallback returns an empty list.
        headers = {"Base-Request-Param": base_param("W2011")}
        empty = self.client.post("/extra/getFriendList", json={"watchId": "W2011"},
                                 headers=headers).get_json()
        self.assertEqual(empty["code"], "000001")
        self.assertEqual(empty["data"], [])

        self.post_form(
            f"/friends/{alice_id}/add",
            data={"friend_watch_id": "W2012", "remark": "同学"},
        )

        listing = self.client.post("/extra/getFriendList", json={"watchId": "W2011"},
                                   headers=headers).get_json()["data"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["watchId"], "W2012")
        # The remark wins over the account's own display name.
        self.assertEqual(listing[0]["name"], "同学")
        self.assertEqual(listing[0]["friendIcon"], "")

        # Adding writes both directions, so the other side sees it too.
        other = self.client.post(
            "/extra/getFriendList", json={"watchId": "W2012"},
            headers={"Base-Request-Param": base_param("W2012")},
        ).get_json()["data"]
        self.assertEqual([entry["watchId"] for entry in other], ["W2011"])
        # Without a remark the friend's own display name is used.
        self.assertEqual(other[0]["name"], "小红")

        # Removal clears both directions again.
        self.post_form(
            f"/friends/{alice_id}/remove", data={"friend_watch_id": "W2012"}
        )
        after = self.client.post("/extra/getFriendList", json={"watchId": "W2011"},
                                 headers=headers).get_json()["data"]
        self.assertEqual(after, [])

    def test_dashboard_avatar_is_served_in_friend_data(self):
        """Dashboard avatar uploads become absolute URLs in every client shape."""
        import io

        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form(
            "/users/create",
            data={"username": "avatar-user", "password": "pw", "watch_id": "WAV1",
                  "is_active": "on", "display_name": "头像用户"},
        )
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user_id = db.query(User).filter_by(watch_id="WAV1").one().id
            db.close()
        self.post_form(
            f"/users/{user_id}/update",
            data={"display_name": "头像用户", "is_active": "on",
                  "avatar": (io.BytesIO(b"fake-png"), "avatar.png")},
            content_type="multipart/form-data",
        )
        headers = {"Base-Request-Param": base_param("WAV1")}
        info = self.client.post("/smartwatch/geniusAccount/watchGetInfo", json={},
                                headers=headers).get_json()["data"]
        self.assertTrue(info["icon"].startswith("http://localhost/media/avatar_"))

        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            admin_id = db.query(User).filter_by(username="admin").one().id
            db.close()
        self.post_form(f"/friends/{admin_id}/add", data={"friend_watch_id": "WAV1"})
        friend_headers = {"Base-Request-Param": base_param("admin")}
        listing = self.client.post("/extra/getFriendList", json={"watchId": "admin"},
                                   headers=friend_headers).get_json()["data"]
        entry = next(row for row in listing if row["watchId"] == "WAV1")
        self.assertIn("/media/avatar_", entry["friendIcon"])

        avatars = self.client.post(
            "/extra/getFriendAvatar", json={"watchIds": ["WAV1", "missing"]},
            headers=headers,
        ).get_json()["data"]
        self.assertEqual(len(avatars), 1)
        self.assertEqual(avatars[0]["watchId"], "WAV1")
        self.assertIn("/media/avatar_", avatars[0]["friendIcon"])

    def test_search_like_flag_is_per_requesting_account(self):
        """A liked friend moment must stay liked after the next feed refresh."""
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form(
            "/users/create",
            data={"username": "like-owner", "password": "pw", "watch_id": "WLIKE1",
                  "is_active": "on"},
        )
        self.post_form(
            "/users/create",
            data={"username": "like-guest", "password": "pw", "watch_id": "WLIKE2",
                  "is_active": "on"},
        )
        owner_headers = {"Base-Request-Param": base_param("WLIKE1")}
        guest_headers = {"Base-Request-Param": base_param("WLIKE2")}
        moment = self.client.post(
            "/moment/public", json={"type": 3, "content": "like test"},
            headers=owner_headers,
        ).get_json()["data"]
        self.client.post("/moment/like", json={"momentId": moment["momentId"]},
                         headers=guest_headers)
        listing = self.client.post(
            "/moment/search", json={"size": 50}, headers=guest_headers
        ).get_json()["data"]["watchMoments"]
        listed = next(row for row in listing if row["momentId"] == moment["momentId"])
        self.assertFalse(listed["enableLike"])
        self.assertEqual(listed["likeTotal"], 1)

        owner_view = self.client.post(
            "/moment/search", json={"size": 50}, headers=owner_headers
        ).get_json()["data"]["watchMoments"]
        owner_listed = next(row for row in owner_view if row["momentId"] == moment["momentId"])
        self.assertTrue(owner_listed["enableLike"])

    def test_friend_list_requires_a_known_account(self):
        response = self.client.post(
            "/extra/getFriendList", json={"watchId": "W9999"},
            headers={"Base-Request-Param": base_param("W9999")},
        ).get_json()
        self.assertEqual(response["code"], "000003")

    def test_comment_delete_routes_used_for_short_watch_ids(self):
        """Comments on 32-char account ids go through advertComment/deleteComment."""
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form(
            "/users/create",
            data={"username": "owner", "password": "pw", "watch_id": "W3011", "is_active": "on"},
        )
        self.post_form(
            "/users/create",
            data={"username": "guest", "password": "pw", "watch_id": "W3012", "is_active": "on"},
        )
        owner_headers = {"Base-Request-Param": base_param("W3011")}
        guest_headers = {"Base-Request-Param": base_param("W3012")}

        moment = self.client.post(
            "/moment/public", json={"type": 3, "content": "hello"}, headers=owner_headers
        ).get_json()["data"]
        posted = self.client.post(
            "/moment/comment",
            json={"momentId": moment["momentId"], "comment": "hi"},
            headers=guest_headers,
        ).get_json()["data"]["comment"]
        comment_id = posted["commentId"]

        # The watch uses this endpoint whenever the moment's watchId is short.
        response = self.client.post(
            "/moment/advertComment/deleteComment",
            json={"commentId": comment_id},
            headers=guest_headers,
        ).get_json()
        self.assertEqual(response["code"], "000001")

        remaining = self.client.post(
            "/moment/getMomentCommentAll",
            json={"momentId": moment["momentId"]},
            headers=owner_headers,
        ).get_json()["data"]["comments"]
        self.assertNotIn(comment_id, [c["commentId"] for c in remaining])

    def test_dashboard_posts_require_csrf_token(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        # A valid session without the token is still rejected.
        response = self.client.post(
            "/users/create",
            data={"username": "no-csrf", "password": "pw", "watch_id": "WCSRF"},
        )
        self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            self.assertIsNone(db.query(User).filter_by(username="no-csrf").one_or_none())
            db.close()

    def test_dashboard_login_page(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("管理员登录", response.get_data(as_text=True))

    def test_api_rejects_unknown_account(self):
        response = self.client.post(
            "/moment/search",
            json={"from": 0, "size": 20},
            headers={"Base-Request-Param": base_param("nobody")},
        )
        self.assertEqual(response.get_json()["code"], "000003")

    def test_admin_creates_user_then_client_can_post(self):
        # 1. Sign in to the dashboard with the bootstrap administrator.
        login = self.post_form("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=True
        )
        self.assertEqual(login.status_code, 200)

        # 2. Add a watch account.
        created = self.post_form("/users/create",
            data={
                "username": "kid",
                "password": "kid-pass",
                "watch_id": "W1001",
                "display_name": "小明",
                "is_active": "on",
            },
            follow_redirects=True,
        )
        self.assertIn("已创建账号", created.get_data(as_text=True))

        # 3. The watch publishes a moment using only the plaintext header.
        headers = {"Base-Request-Param": base_param("W1001")}
        published = self.client.post(
            "/moment/public",
            json={"type": 3, "content": "hello", "topicList": [{"topicName": "周末"}]},
            headers=headers,
        ).get_json()
        self.assertEqual(published["code"], "000001")
        moment_id = published["data"]["momentId"]
        self.assertTrue(moment_id)

        # 4. It shows up in the shared list.
        listed = self.client.post(
            "/moment/search",
            json={"begin": 0, "end": 0, "friend": 1, "from": 0, "size": 20},
            headers=headers,
        ).get_json()
        self.assertEqual(listed["code"], "000001")
        self.assertEqual(len(listed["data"]["watchMoments"]), 1)
        self.assertEqual(listed["data"]["watchMoments"][0]["content"], "hello")
        self.assertTrue(listed["data"]["watchMoments"][0]["createTime"] > 0)

        # 5. Likes and comments round-trip.
        liked = self.client.post(
            "/moment/like",
            json={"momentId": moment_id, "emotionId": 1},
            headers=headers,
        ).get_json()
        self.assertEqual(liked["code"], "000001")
        likes = self.client.post(
            "/moment/like/search", json={"momentIds": [moment_id]}, headers=headers
        ).get_json()
        self.assertEqual(len(likes["data"]["momentLikeMap"][moment_id]), 1)

        commented = self.client.post(
            "/moment/comment",
            json={"momentId": moment_id, "comment": "nice"},
            headers=headers,
        ).get_json()
        self.assertEqual(commented["data"]["result"], "1")

        # 6. The used topic is echoed back by the topic config endpoint.
        topics = self.client.get(
            "/moment-service/topic/recommend/list", headers=headers
        ).get_json()
        self.assertEqual(topics["data"]["config"]["maxTopicCount"], 2)
        self.assertEqual(topics["data"]["topicList"][0]["topicName"], "周末")

    def test_disabled_account_is_locked_out(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={
                "username": "blocked",
                "password": "pw",
                "watch_id": "W2002",
                "is_active": "on",
            },
        )
        # Find the new user id and disable it.
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user = db.query(User).filter_by(watch_id="W2002").one()
            user_id = user.id
            db.close()
        self.post_form(f"/users/{user_id}/toggle", follow_redirects=True)

        response = self.client.post(
            "/moment/search",
            json={},
            headers={"Base-Request-Param": base_param("W2002")},
        )
        self.assertEqual(response.get_json()["code"], "000004")

    def test_login_endpoint_issues_token(self):
        response = self.client.post(
            "/moment/auth/login", json={"username": "admin", "password": "admin123"}
        ).get_json()
        self.assertEqual(response["code"], "000001")
        self.assertTrue(response["data"]["token"])

        # The token can be used instead of the watch-id header.
        listed = self.client.post(
            "/moment/search",
            json={},
            headers={"authId": response["data"]["token"]},
        ).get_json()
        self.assertEqual(listed["code"], "000001")

    def test_edit_user_can_revoke_admin_and_disable(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={
                "username": "editor",
                "password": "pw",
                "watch_id": "W3003",
                "is_admin": "on",
                "is_active": "on",
            },
        )
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user_id = db.query(User).filter_by(watch_id="W3003").one().id
            db.close()

        # Unchecking the boxes must actually clear the flags.
        self.post_form(f"/users/{user_id}/update",
            data={"display_name": "改名", "is_admin": "off", "is_active": "off"},
        )
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user = db.get(User, user_id)
            self.assertFalse(user.is_admin)
            self.assertFalse(user.is_active)
            self.assertEqual(user.display_name, "改名")
            db.close()

    def test_delete_moment_hides_it_from_the_feed(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "poster", "password": "pw", "watch_id": "W4004", "is_active": "on"},
        )
        headers = {"Base-Request-Param": base_param("W4004")}
        published = self.client.post(
            "/moment/public", json={"type": 3, "content": "to be removed"}, headers=headers
        ).get_json()["data"]

        with self.app.app_context():
            from app.models import Moment, get_session

            db = get_session()
            moment_pk = db.query(Moment).filter_by(moment_id=published["momentId"]).one().id
            db.close()
        self.post_form(f"/moments/{moment_pk}/delete")

        listed = self.client.post(
            "/moment/search", json={"size": 50}, headers=headers
        ).get_json()
        self.assertEqual(
            [m for m in listed["data"]["watchMoments"] if m["content"] == "to be removed"], []
        )

    def test_reply_marks_comment_as_reply(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "talker", "password": "pw", "watch_id": "W5005", "is_active": "on"},
        )
        headers = {"Base-Request-Param": base_param("W5005")}
        moment_id = self.client.post(
            "/moment/public", json={"type": 3, "content": "聊天"}, headers=headers
        ).get_json()["data"]["momentId"]

        plain = self.client.post(
            "/moment/comment", json={"momentId": moment_id, "comment": "一楼"}, headers=headers
        ).get_json()["data"]["comment"]
        self.assertEqual(plain["type"], 1)
        self.assertEqual(plain["momentWatchId"], "W5005")

        reply = self.client.post(
            "/moment/comment",
            json={"momentId": moment_id, "comment": "回复一楼", "replyId": "W5005"},
            headers=headers,
        ).get_json()["data"]["comment"]
        self.assertEqual(reply["type"], 2)
        self.assertEqual(reply["replyWatchId"], "W5005")
        self.assertEqual(reply["replyCommentId"], plain["commentId"])

        page = self.client.post(
            "/moment/getMomentComment",
            json={"momentId": moment_id, "pageNum": 1, "pageSize": 20},
            headers=headers,
        ).get_json()["data"]
        self.assertEqual(page["totalCount"], 2)
        self.assertEqual(page["totalPage"], 1)

    def test_bound_account_rejects_other_devices(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={
                "username": "bound",
                "password": "pw",
                "watch_id": "W6006",
                "chip_id": "CHIP-AAA",
                "is_active": "on",
            },
        )
        # The bound watch is accepted.
        ok_response = self.client.post(
            "/moment/search",
            json={},
            headers={"Base-Request-Param": base_param("W6006", chip_id="CHIP-AAA")},
        ).get_json()
        self.assertEqual(ok_response["code"], "000001")
        # Any other chip id is rejected with the client's auth-error code.
        bad_response = self.client.post(
            "/moment/search",
            json={},
            headers={"Base-Request-Param": base_param("W6006", chip_id="CHIP-BBB")},
        ).get_json()
        self.assertEqual(bad_response["code"], "000016")

    def test_unbound_account_remembers_last_chip_id(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "loose", "password": "pw", "watch_id": "W7007", "is_active": "on"},
        )
        headers = {"Base-Request-Param": base_param("W7007", chip_id="CHIP-SEEN")}
        listed = self.client.post("/moment/search", json={}, headers=headers).get_json()
        self.assertEqual(listed["code"], "000001")

        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user = db.query(User).filter_by(watch_id="W7007").one()
            self.assertEqual(user.chip_id, "")
            self.assertEqual(user.last_chip_id, "CHIP-SEEN")
            user_id = user.id
            db.close()

        # "unknown" is what WatchDevice reports when the property is unreadable,
        # so it must not be stored as an identity.
        self.client.post(
            "/moment/search",
            json={},
            headers={"Base-Request-Param": base_param("W7007", chip_id="unkown")},
        )
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user = db.get(User, user_id)
            self.assertEqual(user.last_chip_id, "CHIP-SEEN")
            db.close()

        # Binding the seen chip id locks the account to that watch.
        self.post_form(f"/users/{user_id}/bind-seen-chip")
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            self.assertEqual(db.get(User, user_id).chip_id, "CHIP-SEEN")
            db.close()
        rejected = self.client.post(
            "/moment/search",
            json={},
            headers={"Base-Request-Param": base_param("W7007", chip_id="CHIP-OTHER")},
        ).get_json()
        self.assertEqual(rejected["code"], "000016")

        # Unbinding restores access for any device.
        self.post_form(f"/users/{user_id}/unbind-chip")
        accepted = self.client.post(
            "/moment/search",
            json={},
            headers={"Base-Request-Param": base_param("W7007", chip_id="CHIP-OTHER")},
        ).get_json()
        self.assertEqual(accepted["code"], "000001")

    def test_photo_upload_round_trip(self):
        """Token request -> upload -> download url -> fetch the bytes back."""
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "shooter", "password": "pw", "watch_id": "W8008", "is_active": "on"},
        )
        headers = {"Base-Request-Param": base_param("W8008")}

        token = self.client.post(
            "/moment/file/pic/transfer",
            json={"format": ".webp", "watchId": "W8008"},
            headers=headers,
        ).get_json()["data"]
        key = token["source"]["key"]
        self.assertTrue(key.endswith(".webp"))
        self.assertEqual(token["smallPic"], None)
        self.assertEqual(token["uploadToken"], f"local:{key}")

        # Uploading with the wrong token is rejected.
        bad = self.client.post(
            "/moment/file/upload",
            data=b"payload",
            headers={"Upload-Key": key, "Upload-Token": "local:someone-else"},
        )
        self.assertEqual(bad.get_json()["code"], "000002")

        uploaded = self.client.post(
            "/moment/file/upload",
            data=b"image-bytes",
            headers={"Upload-Key": key, "Upload-Token": token["uploadToken"]},
        ).get_json()
        self.assertEqual(uploaded["code"], "000001")
        self.assertEqual(uploaded["data"]["size"], len(b"image-bytes"))

        # The download endpoint hands back a url that really serves the file.
        resource = self.client.post(
            "/moment/file/download", json={"key": key}, headers=headers
        ).get_json()["data"]["urls"][0]
        self.assertTrue(resource["downloadUrl"].endswith(f"/media/{key}"))
        self.assertGreater(resource["urlDeadline"], 0)
        fetched = self.client.get(f"/media/{key}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.get_data(), b"image-bytes")
        fetched.close()

    def test_photo_upload_batch_and_path_traversal(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "batch", "password": "pw", "watch_id": "W9009", "is_active": "on"},
        )
        headers = {"Base-Request-Param": base_param("W9009")}
        tokens = self.client.post(
            "/moment/file/pics/transfer",
            json=[{"format": ".jpg"}, {"format": ".png"}],
            headers=headers,
        ).get_json()["data"]
        self.assertEqual(len(tokens), 2)
        self.assertTrue(tokens[0]["source"]["key"].endswith(".jpg"))
        self.assertTrue(tokens[1]["source"]["key"].endswith(".png"))
        self.assertNotEqual(tokens[0]["source"]["key"], tokens[1]["source"]["key"])

        # A key with a path separator must never be accepted.
        escaped = self.client.post(
            "/moment/file/upload",
            data=b"x",
            headers={"Upload-Key": "../evil.txt", "Upload-Token": "local:../evil.txt"},
        ).get_json()
        self.assertEqual(escaped["code"], "000002")

    def test_video_transfer_returns_both_keys(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "filmer", "password": "pw", "watch_id": "W1010", "is_active": "on"},
        )
        headers = {"Base-Request-Param": base_param("W1010")}
        data = self.client.post(
            "/moment/file/video/transfer",
            json={"format": ".mp4", "iconFormat": ".webp", "watchId": "W1010"},
            headers=headers,
        ).get_json()["data"]
        self.assertTrue(data["source"]["key"].endswith(".mp4"))
        self.assertTrue(data["icon"]["key"].endswith(".webp"))
        self.assertNotEqual(data["source"]["key"], data["icon"]["key"])
        self.assertEqual(data["uploadToken"], f"local:{data['source']['key']}")
        self.assertEqual(data["iconUploadToken"], f"local:{data['icon']['key']}")

    def test_extra_endpoints_used_by_the_personal_page(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "personal", "password": "pw", "watch_id": "W1111", "is_active": "on"},
        )
        headers = {"Base-Request-Param": base_param("W1111")}

        info = self.client.post(
            "/smartwatch/geniusAccount/watchGetInfo", json={}, headers=headers
        ).get_json()
        self.assertEqual(info["code"], "000001")
        self.assertEqual(info["data"]["watchId"], "W1111")

        unite = self.client.post(
            "/social-service/personalInfo/getPersonalInfoUnite", json={}, headers=headers
        ).get_json()["data"]
        self.assertEqual(unite["geniusAccount"]["watchId"], "W1111")
        self.assertEqual(unite["simpleMedal"]["medals"], [])
        self.assertEqual(unite["socializeUser"]["statusId"], 0)

        grade = self.client.get(
            "/operation/exp/getWatchScoreLevelInfo/W1111/model", headers=headers
        ).get_json()["data"]
        self.assertEqual(grade["level"], 1)
        self.assertGreater(grade["timeStamp"], 0)

        for path in [
            "/social-service/medal/simple",
            "/social-service/personalInfo/getPersonalInfo",
            "/social-service/personalInfo/like",
            "/watchfriend/delFriendByWatch",
            "/social-service/prerogative/getpersonal",
            "/social-service/convention/home",
            "/social-service/convention/content",
            "/social-service/violation/initViolationInfo",
            "/social-service/inform/content",
            "/social-service/violation/searchMomentRiskRecord",
            "/social-service/violation/uploadDelayCount",
            "/social-service/violation/uploadBanRecord",
            "/oauth/openAccount/getOpenId",
            "/route-service/route/getRouteInfo",
        ]:
            with self.subTest(path=path):
                response = self.client.post(path, json={}, headers=headers).get_json()
                self.assertEqual(response["code"], "000001", path)

        whitelist = self.client.get(
            "/social-service/config/getWhiteH5", headers=headers
        ).get_json()
        self.assertEqual(whitelist["data"], [])
        resources = self.client.get(
            "/social-service/prerogative/getResource", headers=headers
        ).get_json()
        self.assertEqual(resources["data"], [])
        h5 = self.client.get(
            "/smartwatch/server/getWatchH5Url/com.xtc.moment", headers=headers
        ).get_json()
        self.assertEqual(h5["data"], [])

    def test_template_admin_feeds_the_mood_and_state_picker(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        headers = {"Base-Request-Param": base_param("admin")}

        # Nothing configured yet: the picker would be empty.
        empty = self.client.post(
            "/moment/template", json={"from": 0, "size": 100, "updateId": 0}, headers=headers
        ).get_json()["data"]
        self.assertEqual(empty["resources"], [])
        self.assertEqual(empty["total"], 0)

        self.post_form("/templates/create",
            data={
                "type": "0",
                "content": "今天真开心",
                "resource": "https://example.com/happy.png",
                "sort_value": "5",
                "enabled": "on",
            },
        )
        self.post_form("/templates/create",
            data={
                "type": "1",
                "content": "正在学习",
                "resource": "https://example.com/study.png",
                "sort_value": "3",
                "enabled": "on",
            },
        )

        listing = self.client.post(
            "/moment/template", json={"from": 0, "size": 100, "updateId": 0}, headers=headers
        ).get_json()["data"]
        self.assertEqual(listing["total"], 2)
        by_type = {item["type"]: item for item in listing["resources"]}
        self.assertEqual(by_type[0]["content"], "今天真开心")
        self.assertEqual(by_type[1]["content"], "正在学习")
        # The watch upserts on resourceId and refuses a template without both
        # resource and content, so both must be present and distinct.
        self.assertEqual(len({item["resourceId"] for item in listing["resources"]}), 2)
        for item in listing["resources"]:
            self.assertTrue(item["resource"])
            self.assertTrue(item["content"])
            self.assertGreater(item["updateTime"], 0)

        incremental = self.client.post(
            "/moment/template",
            json={"from": 0, "size": 100, "updateId": listing["updateId"]},
            headers=headers,
        ).get_json()["data"]
        self.assertEqual(incremental["resources"], [])

        # A disabled template disappears from the list the watch caches.
        template_id = by_type[0]["id"]
        self.post_form(f"/templates/{template_id}/update",
            data={"type": "0", "content": "今天真开心", "resource": "https://example.com/happy.png",
                  "enabled": "off"},
        )
        after = self.client.post(
            "/moment/template", json={"from": 0, "size": 100, "updateId": 0}, headers=headers
        ).get_json()["data"]
        self.assertEqual(after["total"], 1)
        self.assertEqual(after["resources"][0]["type"], 1)

    def test_topic_admin_feeds_the_bracket_list(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        headers = {"Base-Request-Param": base_param("admin")}

        empty = self.client.get("/moment-service/topic/recommend/list", headers=headers).get_json()["data"]
        # Other tests in this class publish moments, so user topics may already
        # exist; only the configured official list must start out empty.
        self.assertEqual([t for t in empty["topicList"] if t["topicType"] == 0], [])
        self.assertEqual(empty["config"]["maxTopicCount"], 2)

        self.post_form("/topics/config", data={"max_topic_count": "3", "name_max_length": "16",
                                                 "official_color": "#FFEEAA", "user_color": "#CCEEFF"})
        self.post_form("/topics/create", data={"topic_name": "每日打卡", "color": "#FFEEAA",
                                                 "sort_value": "10", "topic_type": "0", "enabled": "on"})
        self.post_form("/topics/create", data={"topic_name": "我的周末", "color": "#FFEEAA",
                                                 "sort_value": "5", "topic_type": "0", "enabled": "on"})

        config = self.client.get("/moment-service/topic/recommend/list", headers=headers).get_json()["data"]
        self.assertEqual(config["config"]["maxTopicCount"], 3)
        self.assertEqual(config["config"]["topicNameMaxLength"], 16)
        official = [topic for topic in config["topicList"] if topic["topicType"] == 0]
        self.assertEqual([topic["topicName"] for topic in official], ["每日打卡", "我的周末"])
        self.assertEqual(config["topicList"][0]["color"], "#FFEEAA")
        self.assertEqual(config["topicList"][0]["topicType"], 0)

        # A topic the user typed themselves is offered back as a user topic,
        # without shadowing the configured official entry of the same name.
        self.client.post(
            "/moment/public",
            json={"type": 3, "content": "hi", "topicList": [{"topicName": "每日打卡"}, {"topicName": "随手拍"}]},
            headers=headers,
        )
        after = self.client.get("/moment-service/topic/recommend/list", headers=headers).get_json()["data"]
        names = [topic["topicName"] for topic in after["topicList"]]
        self.assertEqual(names.count("每日打卡"), 1)
        self.assertIn("随手拍", names)
        user_topic = next(topic for topic in after["topicList"] if topic["topicName"] == "随手拍")
        self.assertEqual(user_topic["topicType"], 1)
        self.assertEqual(user_topic["color"], "#CCEEFF")

    def test_banned_account_gets_the_suspend_envelope(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "banned", "password": "pw", "watch_id": "W1212", "is_active": "on"},
        )
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user_id = db.query(User).filter_by(watch_id="W1212").one().id
            db.close()

        headers = {"Base-Request-Param": base_param("W1212")}
        # Before the ban the account works normally.
        self.assertEqual(
            self.client.post("/moment/search", json={}, headers=headers).get_json()["code"],
            "000001",
        )

        self.post_form(f"/users/{user_id}/ban")
        # Every authenticated call now answers 000007 with the exact strings the
        # watch matches on when it shows the "account suspended" prompt.
        for path in ["/moment/search", "/moment/public", "/moment/isBannedToPost"]:
            with self.subTest(path=path):
                response = self.client.post(path, json={}, headers=headers).get_json()
                self.assertEqual(response["code"], "000007", path)
                self.assertEqual(response["desc"], "request parameter is invalid.")
                self.assertEqual(response["watchTips"], "Your account has been suspended")
                self.assertIsNone(response["data"])

        # The optional-token endpoints are gated too.
        self.assertEqual(
            self.client.get("/moment-service/topic/recommend/list", headers=headers).get_json()["code"],
            "000007",
        )

    def test_banned_account_cannot_log_in(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "noluck", "password": "pw", "watch_id": "W1313", "is_active": "on"},
        )
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user_id = db.query(User).filter_by(watch_id="W1313").one().id
            db.close()
        self.post_form(f"/users/{user_id}/ban")

        login = self.client.post(
            "/moment/auth/login", json={"username": "noluck", "password": "pw"}
        ).get_json()
        self.assertEqual(login["code"], "000007")
        self.assertEqual(login["watchTips"], "Your account has been suspended")

    def test_unban_restores_access(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "temp", "password": "pw", "watch_id": "W1414", "is_active": "on"},
        )
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user_id = db.query(User).filter_by(watch_id="W1414").one().id
            db.close()

        headers = {"Base-Request-Param": base_param("W1414")}
        self.post_form(f"/users/{user_id}/ban")
        self.assertEqual(
            self.client.post("/moment/search", json={}, headers=headers).get_json()["code"],
            "000007",
        )
        self.post_form(f"/users/{user_id}/unban")
        self.assertEqual(
            self.client.post("/moment/search", json={}, headers=headers).get_json()["code"],
            "000001",
        )

    def test_review_request_is_recorded_and_visible(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "appealer", "password": "pw", "watch_id": "W1515", "is_active": "on"},
        )
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user_id = db.query(User).filter_by(watch_id="W1515").one().id
            db.close()
        self.post_form(f"/users/{user_id}/ban")

        # A banned account can still file an appeal: this endpoint is the whole
        # reason the dialog exists, so it must not answer 000007.
        headers = {"Base-Request-Param": base_param("W1515")}
        filed = self.client.post(
            "/extra/review-request",
            json={"watchId": "W1515", "reason": "我没有违规", "contact": "12345678"},
            headers=headers,
        ).get_json()
        self.assertEqual(filed["code"], "000001")

        with self.app.app_context():
            from app.models import ReviewRequest, get_session

            db = get_session()
            review = db.query(ReviewRequest).filter_by(watch_id="W1515").one()
            self.assertEqual(review.reason, "我没有违规")
            self.assertEqual(review.contact, "12345678")
            self.assertEqual(review.user_id, user_id)
            self.assertFalse(review.handled)
            review_id = review.id
            db.close()

        # The dashboard lists it and can mark it handled.
        page = self.client.get("/dashboard")
        self.assertIn("我没有违规", page.get_data(as_text=True))
        self.post_form(f"/reviews/{review_id}/handled")
        with self.app.app_context():
            from app.models import ReviewRequest, get_session

            db = get_session()
            self.assertTrue(db.get(ReviewRequest, review_id).handled)
            db.close()
        self.assertNotIn("我没有违规", self.client.get("/dashboard").get_data(as_text=True))

    def test_dashboard_renders_the_ban_controls(self):
        self.post_form("/login", data={"username": "admin", "password": "admin123"})
        self.post_form("/users/create",
            data={"username": "shown", "password": "pw", "watch_id": "W1616", "is_active": "on"},
        )
        page = self.client.get("/dashboard").get_data(as_text=True)
        # The ban button goes through the in-page modal rather than a native
        # confirm(), and the modal carries the exact text the watch shows.
        self.assertIn("data-ban-form", page)
        self.assertIn('id="banned-modal"', page)
        self.assertIn("封禁通知", page)
        self.assertIn("申请复核", page)
        self.assertIn("下载您所发布过的动态", page)
        self.assertIn("banned.js", page)

        # After banning, the row flips to the unban action.
        with self.app.app_context():
            from app.models import User, get_session

            db = get_session()
            user_id = db.query(User).filter_by(watch_id="W1616").one().id
            db.close()
        self.post_form(f"/users/{user_id}/ban")
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("已封禁", page)
        self.assertIn("解除封禁", page)


if __name__ == "__main__":
    unittest.main()
