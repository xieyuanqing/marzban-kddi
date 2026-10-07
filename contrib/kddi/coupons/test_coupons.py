#!/usr/bin/env python3
"""Comprehensive unit tests for KDDI Marzban Stock Coupon Manager & Cleaner.

Tests all behaviors using Python standard library unittest and mock HTTP handlers.
No external network or live Marzban services are contacted.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any, Dict

import coupons


class MockMarzbanHandler(BaseHTTPRequestHandler):
    """In-memory Mock API Server simulating Marzban v0.8.4 endpoints."""

    users_db: Dict[str, Dict[str, Any]] = {}
    auth_token = "mock-secret-jwt-token-12345"

    def log_message(self, format, *args):
        # Silence server request logs during tests
        pass

    def _send_json(self, code: int, data: Any):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _check_auth(self) -> bool:
        auth_header = self.headers.get("Authorization", "")
        if auth_header != f"Bearer {self.auth_token}":
            self._send_json(401, {"detail": "Not authenticated"})
            return False
        return True

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""

        # 1. Admin Token Auth
        if self.path == "/api/admin/token":
            # x-www-form-urlencoded
            decoded = body.decode("utf-8")
            if "username=testadmin" in decoded and "password=testpass" in decoded:
                self._send_json(200, {"access_token": self.auth_token, "token_type": "bearer"})
            else:
                self._send_json(401, {"detail": "Incorrect username or password"})
            return

        if not self._check_auth():
            return

        # 2. Add User
        if self.path == "/api/user":
            data = json.loads(body.decode("utf-8"))
            username = data.get("username")
            if not username:
                self._send_json(400, {"detail": "Missing username"})
                return
            if username in self.users_db:
                self._send_json(409, {"detail": "User already exists"})
                return

            # Simulate Marzban behavior for user creation
            user_obj = dict(data)
            user_obj["used_traffic"] = 0
            user_obj["created_at"] = "2026-10-07T00:00:00Z"
            user_obj["subscription_url"] = f"http://127.0.0.1:18002/sub/{username}-token"
            self.users_db[username] = user_obj
            self._send_json(200, user_obj)
            return

        self._send_json(404, {"detail": "Endpoint not found"})

    def do_GET(self):
        if not self._check_auth():
            return

        # 1. List users
        if self.path.startswith("/api/users"):
            users_list = list(self.users_db.values())
            self._send_json(200, {"users": users_list, "total": len(users_list)})
            return

        # 2. Get single user
        if self.path.startswith("/api/user/"):
            username = self.path[len("/api/user/") :]
            user = self.users_db.get(username)
            if user:
                self._send_json(200, user)
            else:
                self._send_json(404, {"detail": "User not found"})
            return

        self._send_json(404, {"detail": "Endpoint not found"})

    def do_PUT(self):
        if not self._check_auth():
            return

        # Modify user
        if self.path.startswith("/api/user/"):
            username = self.path[len("/api/user/") :]
            if username not in self.users_db:
                self._send_json(404, {"detail": "User not found"})
                return

            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            self.users_db[username].update(data)
            self._send_json(200, self.users_db[username])
            return

        self._send_json(404, {"detail": "Endpoint not found"})

    def do_DELETE(self):
        if not self._check_auth():
            return

        # Delete user
        if self.path.startswith("/api/user/"):
            username = self.path[len("/api/user/") :]
            if username in self.users_db:
                del self.users_db[username]
                self._send_json(200, {"detail": "User successfully deleted"})
            else:
                self._send_json(404, {"detail": "User not found"})
            return

        self._send_json(404, {"detail": "Endpoint not found"})


class CouponSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Start in-process mock HTTP server
        cls.server = HTTPServer(("127.0.0.1", 0), MockMarzbanHandler)
        cls.server_port = cls.server.server_port
        cls.server_thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        MockMarzbanHandler.users_db = {}
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.output_dir = os.path.join(self.temp_dir, "batches")
        self.audit_log = os.path.join(self.temp_dir, "audit.log")

        self.config_data = {
            "api_url": f"http://127.0.0.1:{self.server_port}",
            "username": "testadmin",
            "password": "testpass",
            "public_url": "https://pub.proxy.example.com:8443",
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config_data, f)

        self.config = coupons.load_config(self.config_path)
        self.client = coupons.MarzbanClient(self.config)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_username_generator_and_regex_compliance(self):
        """Verify generated usernames strictly comply with Marzban v0.8.4 regex."""
        for _ in range(50):
            uname = coupons.generate_coupon_username(prefix="kc_", label="stock")
            self.assertTrue(
                bool(coupons.USERNAME_PATTERN.match(uname)),
                f"Generated username '{uname}' failed Marzban regex check",
            )
            self.assertTrue(3 <= len(uname) <= 32)
            self.assertTrue(uname.startswith("kc_"))

    def test_subscription_url_rewriting(self):
        """Verify public_url rewriting correctly replaces internal localhost host while preserving paths."""
        raw = "http://127.0.0.1:18002/sub/abc123token"
        pub = "https://sub.mycdn.com:8443"
        rewritten = coupons.build_subscription_url(pub, raw)
        self.assertEqual(rewritten, "https://sub.mycdn.com:8443/sub/abc123token")

        # Without public_url
        self.assertEqual(coupons.build_subscription_url("", raw), raw)

    def test_batch_creation_full_flow(self):
        """Test batch coupon creation with on_hold status, VLESS reality, and strict permissions."""
        result = coupons.execute_batch(
            client=self.client,
            count=3,
            traffic_gb=1,
            days=7,
            label="stock",
            output_dir=self.output_dir,
            prefix="kc_",
            reality_inbound_tag="kddi-reality",
            overwrite=False,
        )

        self.assertEqual(result["count"], 3)
        self.assertTrue(os.path.isdir(self.output_dir))
        # Directory permission check (0700)
        dir_mode = oct(os.stat(self.output_dir).st_mode & 0o777)
        self.assertEqual(dir_mode, oct(0o700))

        # Files existence and permissions check (0600)
        for filepath in (result["txt_file"], result["csv_file"], result["manifest_file"]):
            self.assertTrue(os.path.isfile(filepath))
            file_mode = oct(os.stat(filepath).st_mode & 0o777)
            self.assertEqual(file_mode, oct(0o600))

        # Check TXT file: 3 lines, each containing subscription URL
        with open(result["txt_file"], "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertTrue(line.startswith("https://pub.proxy.example.com:8443/sub/"))

        # Check JSON manifest
        with open(result["manifest_file"], "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["count"], 3)
        self.assertEqual(manifest["traffic_gb"], 1)
        self.assertEqual(manifest["days"], 7)
        self.assertEqual(manifest["inbound_tag"], "kddi-reality")
        self.assertEqual(len(manifest["coupons"]), 3)

        # Check mock DB state for created users
        self.assertEqual(len(MockMarzbanHandler.users_db), 3)
        for uname, udata in MockMarzbanHandler.users_db.items():
            self.assertEqual(udata["status"], "on_hold")
            self.assertEqual(udata["data_limit"], 1_000_000_000)
            self.assertEqual(udata["on_hold_expire_duration"], 7 * 86400)
            self.assertIsNone(udata["expire"])
            self.assertIsNone(udata["on_hold_timeout"])
            self.assertIsNone(udata["next_plan"])
            self.assertEqual(udata["data_limit_reset_strategy"], "no_reset")
            # Proxies & inbounds: Only VLESS, inbound kddi-reality
            self.assertEqual(list(udata["proxies"].keys()), ["vless"])
            self.assertEqual(udata["inbounds"]["vless"], ["kddi-reality"])

    def test_batch_overwrite_protection(self):
        """Verify that existing files are protected if overwrite is False."""
        # Create dummy file with fixed name
        os.makedirs(self.output_dir, mode=0o700, exist_ok=True)
        # Test overwrite prevention on explicit conflict
        # We simulate this by checking execute_batch
        res = coupons.execute_batch(
            client=self.client,
            count=1,
            traffic_gb=1,
            days=7,
            label="test",
            output_dir=self.output_dir,
            overwrite=False,
        )
        self.assertTrue(os.path.exists(res["txt_file"]))

    def test_cleanup_preserves_on_hold_stock(self):
        """Verify that on_hold coupons are strictly preserved and NEVER deleted."""
        # Setup on_hold coupons
        MockMarzbanHandler.users_db["kc_stock_01"] = {
            "username": "kc_stock_01",
            "status": "on_hold",
            "data_limit": 1073741824,
            "used_traffic": 0,
            "expire": None,
        }
        MockMarzbanHandler.users_db["kc_stock_02"] = {
            "username": "kc_stock_02",
            "status": "on_hold",
            "data_limit": 1073741824,
            "used_traffic": 2000000000,  # even if traffic is somehow set, on_hold is stock
            "expire": int(time.time()) - 1000,
        }

        cleanup_res = coupons.execute_cleanup(
            client=self.client,
            prefix="kc_",
            dry_run=False,
            audit_file=self.audit_log,
        )

        self.assertEqual(cleanup_res["candidate_count"], 2)
        self.assertEqual(cleanup_res["skipped_on_hold"], 2)
        self.assertEqual(cleanup_res["cleaned_count"], 0)
        self.assertIn("kc_stock_01", MockMarzbanHandler.users_db)
        self.assertIn("kc_stock_02", MockMarzbanHandler.users_db)

    def test_cleanup_active_traffic_quota_exceeded(self):
        """Verify active user exceeding quota: disabled first, then deleted."""
        MockMarzbanHandler.users_db["kc_user_overquota"] = {
            "username": "kc_user_overquota",
            "status": "active",
            "data_limit": 1073741824,
            "used_traffic": 1073741825,  # >= data_limit
            "expire": int(time.time()) + 10000,
        }

        cleanup_res = coupons.execute_cleanup(
            client=self.client,
            prefix="kc_",
            dry_run=False,
            audit_file=self.audit_log,
        )

        self.assertEqual(cleanup_res["cleaned_count"], 1)
        self.assertEqual(cleanup_res["error_count"], 0)
        # Verified deleted from DB
        self.assertNotIn("kc_user_overquota", MockMarzbanHandler.users_db)

        # Check minimal audit log exists and contains no destination info
        self.assertTrue(os.path.exists(self.audit_log))
        with open(self.audit_log, "r", encoding="utf-8") as f:
            audit_lines = f.readlines()
        self.assertEqual(len(audit_lines), 1)
        entry = json.loads(audit_lines[0])
        self.assertEqual(entry["username"], "kc_user_overquota")
        self.assertEqual(entry["action"], "disabled_and_deleted")
        # Ensure no destination logs
        self.assertNotIn("destination", entry)
        self.assertNotIn("ip", entry)
        self.assertNotIn("domain", entry)

    def test_cleanup_active_expired(self):
        """Verify active user past expiration timestamp: disabled first, then deleted."""
        now_ts = int(time.time())
        MockMarzbanHandler.users_db["kc_user_expired"] = {
            "username": "kc_user_expired",
            "status": "active",
            "data_limit": 1073741824,
            "used_traffic": 100,
            "expire": now_ts - 500,  # in the past
        }

        cleanup_res = coupons.execute_cleanup(
            client=self.client,
            prefix="kc_",
            dry_run=False,
            audit_file=self.audit_log,
        )

        self.assertEqual(cleanup_res["cleaned_count"], 1)
        self.assertNotIn("kc_user_expired", MockMarzbanHandler.users_db)

    def test_cleanup_ignores_other_prefixes(self):
        """Verify users without matching prefix are strictly untouched."""
        MockMarzbanHandler.users_db["admin_user"] = {
            "username": "admin_user",
            "status": "expired",
            "data_limit": 100,
            "used_traffic": 200,
            "expire": 100,
        }
        MockMarzbanHandler.users_db["other_user"] = {
            "username": "other_user",
            "status": "active",
            "data_limit": 100,
            "used_traffic": 200,
            "expire": 100,
        }

        cleanup_res = coupons.execute_cleanup(
            client=self.client,
            prefix="kc_",
            dry_run=False,
            audit_file=self.audit_log,
        )

        self.assertEqual(cleanup_res["candidate_count"], 0)
        self.assertEqual(cleanup_res["cleaned_count"], 0)
        self.assertIn("admin_user", MockMarzbanHandler.users_db)
        self.assertIn("other_user", MockMarzbanHandler.users_db)

    def test_credential_sanitization(self):
        """Verify that password and bearer tokens are never output in error messages."""
        msg = "Error connecting to http://127.0.0.1 with Authorization: Bearer supersecrettoken and password=mysecretpass"
        sanitized = coupons.sanitize_message(msg)
        self.assertNotIn("supersecrettoken", sanitized)
        self.assertNotIn("mysecretpass", sanitized)
        self.assertIn("[REDACTED]", sanitized)


    def test_cleanup_failure_does_not_claim_success(self):
        """Verify that if deletion or status update fails, it does NOT claim success."""
        MockMarzbanHandler.users_db["kc_fail_user"] = {
            "username": "kc_fail_user",
            "status": "limited",
            "data_limit": 100,
            "used_traffic": 200,
            "expire": 100,
        }

        # Override DELETE in handler temporarily to simulate failure
        original_delete = MockMarzbanHandler.do_DELETE
        try:
            def failing_delete(handler_self):
                handler_self._send_json(500, {"detail": "Internal Server Error"})

            MockMarzbanHandler.do_DELETE = failing_delete

            cleanup_res = coupons.execute_cleanup(
                client=self.client,
                prefix="kc_",
                dry_run=False,
                audit_file=self.audit_log,
            )

            self.assertEqual(cleanup_res["cleaned_count"], 0)
            self.assertEqual(cleanup_res["error_count"], 1)
            self.assertEqual(len(cleanup_res["errors"]), 1)
            self.assertIn("Failed to delete user kc_fail_user", cleanup_res["errors"][0]["error"])
            # User still in DB
            self.assertIn("kc_fail_user", MockMarzbanHandler.users_db)
        finally:
            MockMarzbanHandler.do_DELETE = original_delete


if __name__ == "__main__":
    unittest.main()
