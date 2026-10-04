#!/usr/bin/env python3
"""DataSeek backend API + admin server.

Zero external dependencies: standard-library HTTP server over the SQLite
database. Read endpoints are public; every mutating/admin endpoint requires the
X-Admin-Token header and writes an audit_log entry.

Run:  .venv/bin/python backend/api.py --port 8787
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from v2 import config, query  # noqa: E402

WEB_DIR = ROOT / "web"
ADMIN_TOKEN = os.environ.get("DATASEEK_ADMIN_TOKEN") or ""
_REPROCESS_LOCK = threading.Lock()
_ALLOWED_EDIT = {"name", "canonical_name", "short_description", "primary_category",
                 "subcategory", "resource_type", "confidence", "license", "developer",
                 "platforms", "tags"}


def db_rw() -> sqlite3.Connection:
    db = sqlite3.connect(config.DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def ensure_wal() -> None:
    with sqlite3.connect(config.DB_PATH, timeout=30) as db:
        db.execute("PRAGMA journal_mode=WAL")


def audit(db: sqlite3.Connection, entity_id, task_id, action, old, new, reason, actor="admin") -> None:
    db.execute(
        "INSERT INTO audit_log(timestamp,entity_id,task_id,actor,action,old_value,new_value,reason) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (config.now(), entity_id, task_id, actor, action,
         json.dumps(old) if old is not None else None,
         json.dumps(new) if new is not None else None, reason))


class Handler(BaseHTTPRequestHandler):
    server_version = "DataSeek/2.0"

    def log_message(self, fmt, *args):  # quieter logs
        if os.environ.get("DATASEek_VERBOSE"):
            super().log_message(fmt, *args)

    # ---- helpers ----
    def _json(self, obj, status=200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _authorized(self) -> bool:
        token = self.headers.get("X-Admin-Token", "")
        return bool(ADMIN_TOKEN) and secrets.compare_digest(token, ADMIN_TOKEN)

    def _require_admin(self) -> bool:
        if not self._authorized():
            self._json({"error": "admin authorization required"}, 401)
            return False
        return True

    # ---- routing ----
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self._file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            if path == "/admin":
                return self._file(ROOT / "backend" / "admin.html", "text/html; charset=utf-8")
            if path == "/styles.css":
                return self._file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            if path == "/app.js":
                return self._file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            if path == "/data/dataseek.json":
                return self._file(WEB_DIR / "data" / "dataseek.json", "application/json")
            if path == "/api/stats":
                db = query.connect()
                try:
                    return self._json(query.statistics(db))
                finally:
                    db.close()
            if path == "/api/categories":
                db = query.connect()
                try:
                    return self._json(query.statistics(db)["category_breakdown"])
                finally:
                    db.close()
            if path == "/api/search":
                db = query.connect()
                try:
                    results = query.search(
                        db, (qs.get("q") or [""])[0],
                        category=(qs.get("category") or [None])[0],
                        resource_type=(qs.get("type") or [None])[0],
                        verified_only=(qs.get("verified") or ["0"])[0] == "1",
                        limit=int((qs.get("limit") or ["100"])[0]))
                    return self._json({"count": len(results), "results": results})
                finally:
                    db.close()
            if path == "/api/resources":
                db = query.connect()
                try:
                    return self._json(query.all_resources(db))
                finally:
                    db.close()
            if path.startswith("/api/resources/"):
                return self._resource(path.rsplit("/", 1)[-1])
            if path == "/api/records":
                db = query.connect()
                try:
                    return self._json(query.all_records(db))
                finally:
                    db.close()
            if path.startswith("/api/records/"):
                return self._record(path.rsplit("/", 1)[-1])
            if path == "/api/audit":
                if not self._require_admin():
                    return
                db = query.connect()
                try:
                    rows = [dict(r) for r in db.execute(
                        "SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT 200")]
                    return self._json(rows)
                finally:
                    db.close()
            return self._json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _resource(self, entity_id: str):
        db = query.connect()
        try:
            row = db.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
            if not row:
                return self._json({"error": "resource not found"}, 404)
            rec = query.resource_record(db, row)
            rec["evidence"] = [dict(r) for r in db.execute(
                "SELECT kind,label,content,created_at FROM evidence WHERE entity_id=? "
                "ORDER BY evidence_id", (entity_id,))]
            return self._json(rec)
        finally:
            db.close()

    def _record(self, task_id: str):
        db = query.connect()
        try:
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                return self._json({"error": "record not found"}, 404)
            return self._json(query.record_detail(db, row))
        finally:
            db.close()

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/admin/"):
            if not self._require_admin():
                return
            body = self._body()
            try:
                if path == "/api/admin/reprocess":
                    return self._reprocess(body.get("task_id"))
                if path.endswith("/merge"):
                    return self._merge(path.split("/")[3], body.get("into"))
                if path.endswith("/restore"):
                    return self._restore(path.split("/")[3])
                if path.endswith("/edit"):
                    return self._edit(path.split("/")[3], body)
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        return self._json({"error": "not found"}, 404)

    def do_DELETE(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/admin/resource/"):
            if not self._require_admin():
                return
            return self._soft_delete(parsed.path.rsplit("/", 1)[-1])
        return self._json({"error": "not found"}, 404)

    # ---- admin actions ----
    def _reprocess(self, task_id):
        if not task_id or not (ROOT / "database" / "dataseek.sqlite3").is_file():
            return self._json({"error": "task_id required"}, 400)
        if not _REPROCESS_LOCK.acquire(blocking=False):
            return self._json({"error": "a reprocess is already running"}, 409)
        try:
            from v2.pipeline import process_task
            result = process_task(task_id, use_vision=True)
            db = db_rw()
            audit(db, result.get("entity_id"), task_id, "REPROCESS_OCR", None, result,
                  "Admin-triggered v2 multi-OCR reprocess")
            db.commit()
            db.close()
            return self._json(result)
        finally:
            _REPROCESS_LOCK.release()

    def _merge(self, entity_id, into):
        if not into:
            return self._json({"error": "target 'into' required"}, 400)
        db = db_rw()
        try:
            src = db.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
            dst = db.execute("SELECT * FROM entities WHERE entity_id=? AND is_invalid=0", (into,)).fetchone()
            if not src or not dst:
                return self._json({"error": "source or target resource not found"}, 404)
            db.execute("INSERT OR IGNORE INTO entity_images(entity_id,task_id,evidence) "
                       "SELECT ?, task_id, evidence FROM entity_images WHERE entity_id=?",
                       (into, entity_id))
            db.execute("UPDATE tasks SET entity_id=? WHERE entity_id=?", (into, entity_id))
            db.execute("DELETE FROM entity_images WHERE entity_id=?", (entity_id,))
            db.execute("UPDATE entities SET is_invalid=1, deleted_at=?, deleted_by='admin', "
                       "deletion_reason=? WHERE entity_id=?",
                       (config.now(), f"merged into {into}", entity_id))
            audit(db, into, None, "MERGE_RESOURCE", {"from": entity_id}, {"into": into},
                  "Admin merge; source retained for audit")
            db.commit()
            return self._json({"merged": entity_id, "into": into})
        finally:
            db.close()

    def _restore(self, entity_id):
        db = db_rw()
        try:
            db.execute("UPDATE entities SET is_invalid=0, deleted_at=NULL, deleted_by=NULL, "
                       "deletion_reason=NULL WHERE entity_id=?", (entity_id,))
            audit(db, entity_id, None, "RESTORE_RESOURCE", {"is_invalid": 1}, {"is_invalid": 0},
                  "Admin restore")
            db.commit()
            return self._json({"restored": entity_id})
        finally:
            db.close()

    def _soft_delete(self, entity_id):
        db = db_rw()
        try:
            row = db.execute("SELECT entity_id FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
            if not row:
                return self._json({"error": "resource not found"}, 404)
            db.execute("UPDATE entities SET is_invalid=1, deleted_at=?, deleted_by='admin', "
                       "deletion_reason='admin soft delete' WHERE entity_id=?", (config.now(), entity_id))
            audit(db, entity_id, None, "DELETE_RESOURCE", {"is_invalid": 0}, {"is_invalid": 1},
                  "Admin soft delete")
            db.commit()
            return self._json({"deleted": entity_id})
        finally:
            db.close()

    def _edit(self, entity_id, body):
        fields = {k: v for k, v in body.items() if k in _ALLOWED_EDIT}
        if not fields:
            return self._json({"error": "no editable fields supplied", "allowed": sorted(_ALLOWED_EDIT)}, 400)
        db = db_rw()
        try:
            row = db.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
            if not row:
                return self._json({"error": "resource not found"}, 404)
            if isinstance(fields.get("tags"), list):
                fields["tags"] = json.dumps(fields["tags"])
            sets = ", ".join(f"{k}=?" for k in fields)
            db.execute(f"UPDATE entities SET {sets}, updated_at=? WHERE entity_id=?",
                       (*fields.values(), config.now(), entity_id))
            audit(db, entity_id, None, "EDIT_RESOURCE",
                  {k: row[k] for k in fields}, fields, "Admin edit")
            db.commit()
            return self._json({"updated": entity_id, "fields": list(fields)})
        finally:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    global ADMIN_TOKEN
    if not ADMIN_TOKEN:
        ADMIN_TOKEN = secrets.token_urlsafe(24)
        token_file = ROOT / "backend" / ".admin_token"
        token_file.write_text(ADMIN_TOKEN, encoding="utf-8")
        os.chmod(token_file, 0o600)
        print(f"Generated admin token in {token_file} (not committed).", file=sys.stderr)
    ensure_wal()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"DataSeek API on http://{args.host}:{args.port}  (admin at /admin)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
