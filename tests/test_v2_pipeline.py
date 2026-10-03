from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from v2 import consensus, taxonomy  # noqa: E402
from v2.schema import ensure_v2_schema  # noqa: E402


def engine(name, text, confidence, error=None, **extra):
    return {"engine": name, "engine_version": "test", "preprocessing": "original",
            "text": text, "confidence": confidence, "boxes": [], "error": error,
            "latency_s": 0.0, **extra}


class ConsensusTests(unittest.TestCase):
    def test_strong_engine_with_weak_second_is_not_conflicting(self):
        rec = consensus.reconcile([
            engine("rapidocr", "Higgsfield upgrade 30% off MCP create", 94.0),
            engine("tesseract", "@@@ xx gibberish", 48.0),
            engine("vision", "1. VISIBLE_TEXT: Higgsfield\n3. PRODUCT_NAME: Higgsfield", None),
        ])
        self.assertEqual(rec["status"], "OCR_GOOD")
        self.assertEqual(rec["quality_level"], 2)
        self.assertIn("Higgsfield", rec["final_text"])

    def test_two_credible_engines_that_disagree_are_conflicting(self):
        rec = consensus.reconcile([
            engine("rapidocr", "alpha beta gamma delta epsilon", 90.0),
            engine("tesseract", "zzz yyy xxx www vvv uuu", 88.0),
        ])
        self.assertEqual(rec["status"], "OCR_CONFLICTING")
        self.assertLess(rec["agreement"], 0.35)

    def test_no_text_is_unreadable(self):
        rec = consensus.reconcile([engine("rapidocr", "", None)])
        self.assertEqual(rec["status"], "OCR_UNREADABLE")
        self.assertEqual(rec["quality_level"], 0)

    def test_engine_error_is_ignored(self):
        rec = consensus.reconcile([
            engine("rapidocr", "hello world", 90.0),
            engine("tesseract", "x", 10.0, error="boom"),
        ])
        self.assertNotIn("tesseract", rec["engines"])

    def test_final_text_prefers_highest_confidence_engine(self):
        rec = consensus.reconcile([
            engine("rapidocr", "short", 95.0),
            engine("tesseract", "a much longer but lower confidence string", 60.0),
        ])
        self.assertEqual(rec["final_text"], "short")


class ExtractionTests(unittest.TestCase):
    def test_urls_and_domains(self):
        urls = consensus.extract_urls("visit https://github.com/foo/bar and example.com now")
        values = {u["url"].lower() for u in urls}
        self.assertTrue(any("github.com/foo/bar" in v for v in values))
        self.assertIn("example.com", values)

    def test_github_candidates(self):
        pairs = consensus.github_candidates("see github.com/smittix/intercept for details")
        self.assertIn(("smittix", "intercept"), pairs)

    def test_vision_product_name(self):
        text = "2. URLS: none\n3. PRODUCT_NAME: OmniVoice\n5. INFERRED: none"
        self.assertEqual(consensus.vision_product_name(text), "OmniVoice")
        self.assertIsNone(consensus.vision_product_name("PRODUCT_NAME: unclear"))

    def test_classify_type_falls_back(self):
        self.assertEqual(consensus.classify_type("GitHub - foo/bar README", None), "GITHUB")
        self.assertEqual(consensus.classify_type("random app ui", "APP_UI"), "APP_UI")


class TaxonomyTests(unittest.TestCase):
    def test_signal_intelligence_is_security_not_ai(self):
        cls = taxonomy.classify("Signal Intelligence Platform with rtl_433 sensors", None, "Repository")
        self.assertEqual(cls["primary_category"], "Security")
        self.assertEqual(cls["subcategory"], "OSINT")

    def test_ocr_is_documents(self):
        cls = taxonomy.classify("an OCR tool that reads text from images", None, "Repository")
        self.assertEqual(cls["primary_category"], "Documents")
        self.assertEqual(cls["subcategory"], "OCR")

    def test_browser_extension(self):
        cls = taxonomy.classify("a chrome extension for tab management", None, "Extension")
        self.assertEqual(cls["subcategory"], "Browser Extension")

    def test_repository_defaults_to_development(self):
        cls = taxonomy.classify("a general purpose utility", None, "Repository")
        self.assertEqual(cls["primary_category"], "Development")

    def test_every_rule_targets_a_valid_subcategory(self):
        for _pattern, root, sub in taxonomy.RULES:
            self.assertIn(root, taxonomy.TAXONOMY)
            self.assertIn(sub, taxonomy.TAXONOMY[root])


class SchemaTests(unittest.TestCase):
    def test_v2_schema_is_idempotent_and_additive(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = sqlite3.connect(Path(tmp) / "t.sqlite3")
            db.execute("CREATE TABLE tasks(task_id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE entities(entity_id TEXT PRIMARY KEY)")
            ensure_v2_schema(db)
            ensure_v2_schema(db)  # must not raise
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for expected in ("ocr_runs", "ocr_regions", "ocr_consensus", "urls",
                             "categories", "tags", "evidence", "duplicate_links",
                             "processing_errors", "vision_runs"):
                self.assertIn(expected, tables)
            cols = {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
            self.assertIn("processing_version", cols)
            self.assertIn("quality_level", cols)
            # Root taxonomy seeded.
            roots = {r[0] for r in db.execute("SELECT name FROM categories WHERE parent_id IS NULL")}
            self.assertIn("AI", roots)
            self.assertIn("Security", roots)


if __name__ == "__main__":
    unittest.main()
