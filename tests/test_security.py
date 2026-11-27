import json
import pathlib
import sys
import unittest

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
import tunnel_and_auth  # noqa: E402


class SecurityTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(server.app)
        cls.tok = server.config["token"]
        cls.hdr = {"Authorization": f"Bearer {cls.tok}"}

    def test_cf_ip_requires_cf_ray(self) -> None:
        req_no_ray = type(
            "Req", (), {"client": type("C", (), {"host": "127.0.0.1"})(), "headers": {"cf-connecting-ip": "203.0.113.5"}}
        )()
        self.assertEqual(server.client_ip(req_no_ray), "127.0.0.1")

        req_with_ray = type(
            "Req",
            (),
            {
                "client": type("C", (), {"host": "127.0.0.1"})(),
                "headers": {"cf-connecting-ip": "203.0.113.5", "cf-ray": "abc123"},
            },
        )()
        self.assertEqual(server.client_ip(req_with_ray), "203.0.113.5")

    def test_upload_read_download_and_block_executable(self) -> None:
        r_bad = self.client.post("/api/upload", files={"file": ("evil.py", b"print(1)")}, headers=self.hdr)
        self.assertEqual(r_bad.status_code, 400)

        r_up = self.client.post("/api/upload", files={"file": ("sample.txt", b"hello-upload")}, headers=self.hdr)
        self.assertEqual(r_up.status_code, 200)
        up = r_up.json()
        self.assertTrue(up["rel_path"].startswith("_uploads/"))

        r_read = self.client.get("/api/file", params={"path": up["rel_path"]}, headers=self.hdr)
        self.assertEqual(r_read.status_code, 200)
        self.assertEqual(r_read.json()["content"], "hello-upload")

        r_dl = self.client.get("/api/download", params={"path": up["rel_path"]}, headers=self.hdr)
        self.assertEqual(r_dl.status_code, 200)
        self.assertEqual(r_dl.content, b"hello-upload")

        pathlib.Path(up["path"]).unlink(missing_ok=True)

    def test_pairing_cookie_compare_before_clear(self) -> None:
        code, cookie = server.hub.issue_pairing_code()
        self.assertFalse(server.hub.consume_pairing_code(code, "wrong-cookie"))
        # Code must still be valid after a wrong cookie attempt
        self.assertTrue(server.hub.consume_pairing_code(code, cookie))
        # Second use must fail
        self.assertFalse(server.hub.consume_pairing_code(code, cookie))

    def test_status_consistency_and_remote_minimization(self) -> None:
        r_local = self.client.get("/api/status", headers=self.hdr)
        self.assertEqual(r_local.status_code, 200)
        self.assertIn(r_local.json()["agy_mode"], ("accept-edits", "plan"))

        r_remote = self.client.get(
            "/api/status", headers={**self.hdr, "cf-connecting-ip": "203.0.113.8", "cf-ray": "ray1"}
        )
        self.assertEqual(r_remote.status_code, 200)
        for hidden in ("owner_email", "workspace", "tunnel_url"):
            self.assertNotIn(hidden, r_remote.json())

    def test_path_and_cid_traversal_blocked(self) -> None:
        self.assertEqual(
            self.client.get("/api/file", params={"path": "../../Windows/win.ini"}, headers=self.hdr).status_code, 403
        )
        self.assertEqual(
            self.client.post("/api/chats/select", json={"conversation_id": "../bad"}, headers=self.hdr).status_code, 400
        )

    def test_static_index_no_url_token(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('urlParams.get("token")', html)

    def test_ws_auth_and_rate_limit(self) -> None:
        with self.client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "auth", "token": self.tok}))
            msg = json.loads(ws.receive_text())
            self.assertEqual(msg["type"], "history")

    def test_supabase_cache_invalidate(self) -> None:
        cli = tunnel_and_auth.SupabaseAuthClient()
        cli._cache["t1"] = (9999999999, {"email": "a@b.com"})
        cli.invalidate("t1")
        self.assertNotIn("t1", cli._cache)


if __name__ == "__main__":
    unittest.main()
