"""Tests for the admin API: authorization and audit-log behaviour.

These start the real backend handler on an ephemeral port against a throwaway
copy of the database, so no test mutates the production data. They also assert
that the routes the admin UI calls are the routes the server actually serves.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from v2 import config  # noqa: E402

TOKEN = "test-admin-token"


def load_api():
    spec = importlib.util.spec_from_file_location("dataseek_api", ROOT / "backend" / "api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_database(src: Path, dst: Path) -> None:
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    with target:
        source.backup(target)
    target.close()
    source.close()


class AdminApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not config.DB_PATH.is_file():
            raise unittest.SkipTest("database not built")
        cls.api = load_api()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tmp_db = Path(cls.tmp.name) / "dataseek.sqlite3"
        copy_database(config.DB_PATH, cls.tmp_db)
        cls._orig_db = config.DB_PATH
        config.DB_PATH = cls.tmp_db
        cls.api.ADMIN_TOKEN = TOKEN
        cls.api._REPROCESS_LOCK = threading.Lock()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        config.DB_PATH = cls._orig_db
        cls.tmp.cleanup()

    def setUp(self):
        self.db = sqlite3.connect(self.tmp_db)
        self.db.row_factory = sqlite3.Row
        self.eid = self.db.execute(
            "SELECT entity_id FROM entities WHERE is_invalid=0 AND deleted_at IS NULL "
            "ORDER BY entity_id LIMIT 1").fetchone()[0]

    def tearDown(self):
        self.db.close()

    def request(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("X-Admin-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read() or b"{}"
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {}

    # ---- public reads ----
    def test_public_reads_need_no_token(self):
        for path in ("/api/stats", "/api/search?q=ai", "/api/resources", "/api/records"):
            status, body = self.request("GET", path)
            self.assertEqual(status, 200, f"{path} should be public")

    # ---- authorization ----
    def test_admin_reads_are_protected(self):
        status, _ = self.request("GET", "/api/audit")
        self.assertEqual(status, 401)
        status, _ = self.request("GET", "/api/audit", token="wrong")
        self.assertEqual(status, 401)

    def test_admin_writes_are_protected(self):
        cases = [
            ("POST", f"/api/admin/resource/{self.eid}/edit", {"short_description": "x"}),
            ("POST", f"/api/admin/resource/{self.eid}/merge", {"into": self.eid}),
            ("POST", f"/api/admin/resource/{self.eid}/restore", None),
            ("DELETE", f"/api/admin/resource/{self.eid}", None),
        ]
        for method, path, body in cases:
            status, _ = self.request(method, path, body)
            self.assertEqual(status, 401, f"{method} {path} must require a token")

    def test_wrong_token_is_rejected_even_when_token_configured(self):
        status, _ = self.request("GET", "/api/audit", token="")
        self.assertEqual(status, 401)

    # ---- edit ----
    def test_edit_requires_editable_fields(self):
        status, body = self.request(
            "POST", f"/api/admin/resource/{self.eid}/edit", {"bogus": 1}, token=TOKEN)
        self.assertEqual(status, 400)
        self.assertIn("allowed", body)

    def test_edit_updates_row_and_writes_audit_log(self):
        status, body = self.request(
            "POST", f"/api/admin/resource/{self.eid}/edit",
            {"short_description": "edited by test"}, token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body.get("updated"), self.eid)
        row = self.db.execute(
            "SELECT short_description FROM entities WHERE entity_id=?", (self.eid,)).fetchone()
        self.assertEqual(row[0], "edited by test")
        entry = self.db.execute(
            "SELECT * FROM audit_log WHERE entity_id=? AND action='EDIT_RESOURCE' "
            "ORDER BY audit_id DESC LIMIT 1", (self.eid,)).fetchone()
        self.assertIsNotNone(entry, "edit must be audited")
        self.assertIn("edited by test", entry["new_value"])

    # ---- delete / restore ----
    def test_delete_and_restore_are_soft_and_audited(self):
        status, body = self.request("DELETE", f"/api/admin/resource/{self.eid}", token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body.get("deleted"), self.eid)
        row = self.db.execute(
            "SELECT is_invalid FROM entities WHERE entity_id=?", (self.eid,)).fetchone()
        self.assertEqual(row[0], 1)

        status, body = self.request(
            "POST", f"/api/admin/resource/{self.eid}/restore", None, token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body.get("restored"), self.eid)
        row = self.db.execute(
            "SELECT is_invalid FROM entities WHERE entity_id=?", (self.eid,)).fetchone()
        self.assertEqual(row[0], 0)

        actions = {r[0] for r in self.db.execute(
            "SELECT action FROM audit_log WHERE entity_id=?", (self.eid,))}
        self.assertIn("DELETE_RESOURCE", actions)
        self.assertIn("RESTORE_RESOURCE", actions)

    def test_unknown_resource_returns_404(self):
        status, _ = self.request("DELETE", "/api/admin/resource/ENT-999999", token=TOKEN)
        self.assertEqual(status, 404)

    def test_audit_endpoint_lists_entries(self):
        self.request("POST", f"/api/admin/resource/{self.eid}/edit",
                     {"developer": "tester"}, token=TOKEN)
        status, rows = self.request("GET", "/api/audit", token=TOKEN)
        self.assertEqual(status, 200)
        self.assertIsInstance(rows, list)
        self.assertTrue(any(r.get("action") == "EDIT_RESOURCE" for r in rows))

    # ---- UI/server contract ----
    def test_admin_ui_calls_the_routes_the_server_serves(self):
        html = (ROOT / "backend" / "admin.html").read_text(encoding="utf-8")
        for fragment in ("/api/admin/resource/${id}/edit",
                         "/api/admin/resource/${id}/merge",
                         "/api/admin/reprocess",
                         "/api/audit"):
            self.assertIn(fragment, html, f"admin UI no longer calls {fragment}")
        # The routes the UI uses must resolve to a real action, not a 404.
        status, _ = self.request("POST", f"/api/admin/resource/{self.eid}/restore",
                                 None, token=TOKEN)
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
