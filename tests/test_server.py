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


def base_param(watch_id, device_id="dev-1"):
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
            "token": "chip-1",
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

    def test_health(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

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
        login = self.client.post(
            "/login", data={"username": "admin", "password": "admin123"}, follow_redirects=True
        )
        self.assertEqual(login.status_code, 200)

        # 2. Add a watch account.
        created = self.client.post(
            "/users/create",
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
        self.client.post("/login", data={"username": "admin", "password": "admin123"})
        self.client.post(
            "/users/create",
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
        self.client.post(f"/users/{user_id}/toggle", follow_redirects=True)

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
        self.client.post("/login", data={"username": "admin", "password": "admin123"})
        self.client.post(
            "/users/create",
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
        self.client.post(
            f"/users/{user_id}/update",
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
        self.client.post("/login", data={"username": "admin", "password": "admin123"})
        self.client.post(
            "/users/create",
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
        self.client.post(f"/moments/{moment_pk}/delete")

        listed = self.client.post(
            "/moment/search", json={"size": 50}, headers=headers
        ).get_json()
        self.assertEqual(
            [m for m in listed["data"]["watchMoments"] if m["content"] == "to be removed"], []
        )

    def test_reply_marks_comment_as_reply(self):
        self.client.post("/login", data={"username": "admin", "password": "admin123"})
        self.client.post(
            "/users/create",
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


if __name__ == "__main__":
    unittest.main()
