"""Consistency tests for the database, derived exports and the final audit.

Two layers:
  1. The live database must agree with its own derived views (statistics,
     resources, records) and every resource must have provenance.
  2. The audit must actually *detect* drift: on a minimal consistent fixture it
     reports zero hard errors, and removing a required artifact flips it to a
     hard error. This stops the audit from silently passing on broken state.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from v2 import config, query  # noqa: E402
from v2.schema import ensure_v2_schema  # noqa: E402


def load_audit():
    spec = importlib.util.spec_from_file_location("dataseek_audit", ROOT / "scripts" / "audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LiveConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not config.DB_PATH.is_file():
            raise unittest.SkipTest("database not built")
        cls.db = query.connect()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_statistics_match_direct_counts(self):
        stats = query.statistics(self.db)
        tasks = self.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        entities = self.db.execute(
            "SELECT COUNT(*) FROM entities WHERE is_invalid=0 AND deleted_at IS NULL"
        ).fetchone()[0]
        self.assertEqual(stats["records"], tasks)
        self.assertEqual(stats["resources"], entities)

    def test_every_resource_has_provenance_and_matching_source_count(self):
        resources = query.all_resources(self.db)
        self.assertEqual(len(resources), query.statistics(self.db)["resources"])
        for r in resources:
            self.assertGreaterEqual(r["source_count"], 1,
                                    f"{r['entity_id']} has no source records")
            self.assertEqual(r["source_count"], len(r["records"]))
            self.assertTrue(r["primary_category"], f"{r['entity_id']} has no category")

    def test_records_are_unique_and_addressable(self):
        records = query.all_records(self.db)
        ids = [r["task_id"] for r in records]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(len(ids), 0)

    def test_search_returns_role_appropriate_results(self):
        for term in ("ai", "ocr", "github"):
            hits = query.search(self.db, term, limit=10)
            self.assertIsInstance(hits, list)

    def test_final_audit_passes_on_live_state(self):
        audit = load_audit()
        with contextlib.redirect_stdout(io.StringIO()):
            code = audit.main()
        self.assertEqual(code, 0, "the live audit must report 0 hard errors")


class AuditDetectsDriftTests(unittest.TestCase):
    """Build a minimal consistent project and prove the audit notices breakage."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "a.jpg").write_bytes(b"source")
        self.images = self.root / "images"
        self.images.mkdir()
        self.ocr = self.root / "ocr"
        self.ocr.mkdir()
        self.exports = self.root / "exports"
        self.exports.mkdir()
        self.db_path = self.root / "db.sqlite3"
        self._build_db()
        self._build_exports()
        self._patch()

    def tearDown(self):
        for name, old in self._saved.items():
            setattr(config, name, old)
        self.audit.ROOT = self._audit_root
        self.tmp.cleanup()

    def _patch(self):
        self.audit = load_audit()
        self._audit_root = self.audit.ROOT
        self._saved = {}
        for name, value in (("DB_PATH", self.db_path), ("EXPORTS", self.exports),
                            ("MARKDOWN_IMAGES", self.images), ("OCR_DIR", self.ocr)):
            self._saved[name] = getattr(config, name)
            setattr(config, name, value)
        self._saved["source_root"] = config.source_root
        config.source_root = lambda: self.source  # type: ignore[assignment]
        self.audit.ROOT = self.root

    def _build_db(self):
        db = sqlite3.connect(self.db_path)
        db.executescript(
            "CREATE TABLE tasks(task_id TEXT PRIMARY KEY, source_filename TEXT, "
            "entity_id TEXT, duplicate_of TEXT, status TEXT);"
            "CREATE TABLE entities(entity_id TEXT PRIMARY KEY, name TEXT, "
            "is_invalid INTEGER DEFAULT 0, deleted_at TEXT, primary_category TEXT);"
            "CREATE TABLE entity_images(entity_id TEXT, task_id TEXT, evidence TEXT);")
        ensure_v2_schema(db)
        db.execute("INSERT INTO tasks(task_id,source_filename,entity_id,status,"
                   "processing_version,v2_status) VALUES(?,?,?,?,?,?)",
                   ("IMG-0001", "a.jpg", "ENT-000001", "COMPLETED",
                    config.PROCESSING_VERSION, "COMPLETED"))
        db.execute("INSERT INTO entities(entity_id,name,is_invalid,primary_category) "
                   "VALUES(?,?,0,?)", ("ENT-000001", "Thing", "AI"))
        db.execute("INSERT INTO entity_images(entity_id,task_id) VALUES(?,?)",
                   ("ENT-000001", "IMG-0001"))
        db.execute("INSERT INTO ocr_consensus(task_id,status,updated_at) VALUES(?,?,?)",
                   ("IMG-0001", "OCR_GOOD", config.now()))
        db.commit()
        db.close()

    def _build_exports(self):
        (self.images / "IMG-0001.md").write_text("# IMG-0001", encoding="utf-8")
        task_ocr = self.ocr / "IMG-0001"
        task_ocr.mkdir(parents=True)
        (task_ocr / "consensus.json").write_text("{}", encoding="utf-8")
        (self.exports / "ALL_SCREENSHOTS.md").write_text(
            "# All\n\n## IMG-0001 — a.jpg\n", encoding="utf-8")
        (self.exports / "ALL_RESOURCES.md").write_text(
            "# All resources\n\n## Thing (`ENT-000001`)\n", encoding="utf-8")
        (self.exports / "search_index.json").write_text(json.dumps(
            {"records": [{"task_id": "IMG-0001"}],
             "resources": [{"entity_id": "ENT-000001"}]}), encoding="utf-8")

    def audit_code(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.audit.main()

    def test_consistent_fixture_passes(self):
        self.assertEqual(self.audit_code(), 0)

    def test_missing_record_markdown_is_a_hard_error(self):
        (self.images / "IMG-0001.md").unlink()
        self.assertEqual(self.audit_code(), 1)

    def test_missing_ocr_evidence_is_a_hard_error(self):
        (self.ocr / "IMG-0001" / "consensus.json").unlink()
        self.assertEqual(self.audit_code(), 1)

    def test_untracked_source_file_is_a_hard_error(self):
        (self.source / "b.jpg").write_bytes(b"extra")
        self.assertEqual(self.audit_code(), 1)

    def test_missing_provenance_is_a_hard_error(self):
        db = sqlite3.connect(self.db_path)
        db.execute("DELETE FROM entity_images")
        db.commit()
        db.close()
        self.assertEqual(self.audit_code(), 1)

    def test_export_database_mismatch_is_a_hard_error(self):
        (self.exports / "ALL_SCREENSHOTS.md").write_text("# All\n", encoding="utf-8")
        self.assertEqual(self.audit_code(), 1)


if __name__ == "__main__":
    unittest.main()
