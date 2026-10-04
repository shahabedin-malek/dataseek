"""Contract tests for the published site data bundle.

The frontend and the exported bundles are a contract: the resource page derives
its "Same organization" and "Shared technologies" sections entirely from these
fields, so they must always be present and well-formed. These tests also guard
the public payload against leaking internal provenance fields and against
emitting broken (scheme-less) GitHub links.

They run against the real built bundles when present and are skipped otherwise.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = ("site/data/dataseek.json", "web/data/dataseek.json")

# A real repository link: https://github.com/<owner>/<repo>
GITHUB_REPO_RE = re.compile(r"^https?://github\.com/[^/\s]+/[^/\s?#]+$")

# Field names that must never appear in the published payload.
FORBIDDEN_KEYS = ("screenshot_type", "screenshots", "filename",
                  "sha256", "perceptual_hash", "absolute_source_path")

# Fields the resource page relies on for metadata-only relationships.
RELATIONSHIP_FIELDS = ("entity_id", "name", "developer", "technologies")


def load_bundles() -> dict[str, dict]:
    out = {}
    for rel in BUNDLES:
        path = ROOT / rel
        if path.is_file():
            out[rel] = json.loads(path.read_text(encoding="utf-8"))
    return out


class SiteDataContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundles = load_bundles()
        if not cls.bundles:
            raise unittest.SkipTest("site data bundle not built")

    def test_bundle_shape(self):
        for rel, data in self.bundles.items():
            self.assertIsInstance(data.get("resources"), list, rel)
            self.assertIsInstance(data.get("records"), list, rel)
            self.assertIsInstance(data.get("statistics"), dict, rel)
            self.assertGreater(len(data["resources"]), 0, rel)

    def test_every_resource_exposes_relationship_fields(self):
        for rel, data in self.bundles.items():
            for r in data["resources"]:
                for field in RELATIONSHIP_FIELDS:
                    self.assertIn(field, r, f"{rel}: {r.get('entity_id')} missing {field}")
                dev = r["developer"]
                self.assertTrue(dev is None or isinstance(dev, str),
                                f"{rel}: developer must be str or null")
                if isinstance(dev, str):
                    self.assertEqual(dev, dev.strip(),
                                     f"{rel}: developer of {r['entity_id']} is padded")
                self.assertIsInstance(r["technologies"], list,
                                      f"{rel}: technologies must be a list")
                for tech in r["technologies"]:
                    self.assertIsInstance(tech, str)
                    self.assertTrue(tech.strip(),
                                    f"{rel}: empty technology on {r['entity_id']}")

    def test_no_bare_github_urls(self):
        # 'github.com' / 'https://github.com' (no owner/repo) is not a repository
        # and would render as a broken or root link.
        for rel, data in self.bundles.items():
            for r in data["resources"]:
                gh = r.get("github_url")
                if gh is None:
                    continue
                self.assertNotIn(gh.rstrip("/").lower(),
                                 ("github.com", "http://github.com", "https://github.com"),
                                 f"{rel}: {r['entity_id']} has a bare GitHub URL")
                self.assertRegex(gh, GITHUB_REPO_RE,
                                 f"{rel}: {r['entity_id']} github_url not a repo URL: {gh!r}")

    def test_resource_urls_are_non_empty_strings(self):
        for rel, data in self.bundles.items():
            for r in data["resources"]:
                for url in r.get("urls") or []:
                    self.assertIsInstance(url, str)
                    self.assertTrue(url.strip(), f"{rel}: blank URL on {r['entity_id']}")
                    self.assertEqual(url, url.strip(),
                                     f"{rel}: padded URL on {r['entity_id']}")

    def test_payload_has_no_forbidden_keys(self):
        for rel, data in self.bundles.items():
            blob = json.dumps(data).lower()
            for key in FORBIDDEN_KEYS:
                self.assertNotIn(f'"{key}"', blob, f"{rel} exposes internal field {key!r}")

    def test_records_do_not_leak_local_provenance(self):
        for rel, data in self.bundles.items():
            for rec in data.get("records", []):
                for key in ("sha256", "perceptual_hash", "filename",
                            "absolute_source_path"):
                    self.assertNotIn(key, rec, f"{rel}: record {rec.get('task_id')} leaks {key}")

    def test_relationships_are_self_consistent(self):
        """Mirror the frontend's relationship rules and assert their invariants."""
        for rel, data in self.bundles.items():
            resources = data["resources"]
            by_id = {r["entity_id"]: r for r in resources}

            def same_organization(r):
                org = (r.get("developer") or "").strip().lower()
                if not org:
                    return []
                return [x["entity_id"] for x in resources
                        if x["entity_id"] != r["entity_id"]
                        and (x.get("developer") or "").strip().lower() == org]

            def shared_technologies(r):
                tech = {t.lower() for t in (r.get("technologies") or [])}
                if not tech:
                    return []
                return [x["entity_id"] for x in resources
                        if x["entity_id"] != r["entity_id"]
                        and any(t.lower() in tech for t in (x.get("technologies") or []))]

            for r in resources:
                eid = r["entity_id"]
                org_related = same_organization(r)
                self.assertNotIn(eid, org_related, f"{rel}: {eid} relates to itself")
                for other in org_related:
                    self.assertEqual(
                        (by_id[other].get("developer") or "").strip().lower(),
                        (r.get("developer") or "").strip().lower(),
                        f"{rel}: {eid}/{other} do not share an organization")

                tech_related = shared_technologies(r)
                self.assertNotIn(eid, tech_related, f"{rel}: {eid} relates to itself")
                mine = {t.lower() for t in (r.get("technologies") or [])}
                for other in tech_related:
                    theirs = {t.lower() for t in (by_id[other].get("technologies") or [])}
                    self.assertTrue(mine & theirs,
                                    f"{rel}: {eid}/{other} share no technology")


if __name__ == "__main__":
    unittest.main()
