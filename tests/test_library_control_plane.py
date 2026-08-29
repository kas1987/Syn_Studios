import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.validate_library import validate_repository


ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LibraryControlPlaneTests(unittest.TestCase):
    def make_minimal_root(self, temporary):
        root = Path(temporary)
        for name in ("foundation-card", "artifact-blueprint", "template-release"):
            target = root / "schemas" / f"{name}.schema.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "schemas" / target.name, target)
        card_target = root / "library/foundations/FOUND-0001.json"
        card_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "library/foundations/FOUND-0001.json", card_target)
        blueprint_target = root / "examples/blueprints/BP-0001.internal-close-workbook.json"
        blueprint_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "examples/blueprints/BP-0001.internal-close-workbook.json", blueprint_target)
        return root, card_target, blueprint_target

    def assert_finding(self, root, fragment):
        findings, _ = validate_repository(root)
        self.assertTrue(any(fragment in item for item in findings), "\n".join(findings))

    def test_updated_repository_records_and_fixtures_pass(self):
        findings, count = validate_repository(ROOT)
        self.assertEqual(findings, [])
        self.assertGreaterEqual(count, 35)

    def test_foundation_cannot_self_promote_to_template_ready(self):
        schema = json.loads((ROOT / "schemas/foundation-card.schema.json").read_text(encoding="utf-8"))
        card = json.loads((ROOT / "library/foundations/FOUND-0001.json").read_text(encoding="utf-8"))
        card["status"] = "template_ready"
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(card)))

    def test_blueprint_rejects_stale_foundation_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["foundation_lineage"][0]["card_sha256"] = "0" * 64
            write_json(blueprint_path, blueprint)
            self.assert_finding(root, "does not match current foundation card bytes")

    def test_candidate_foundation_cannot_feed_blueprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, card_path, blueprint_path = self.make_minimal_root(temporary)
            card = json.loads(card_path.read_text(encoding="utf-8"))
            card["status"] = "candidate"
            write_json(card_path, card)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["foundation_lineage"][0]["card_sha256"] = file_hash(card_path)
            write_json(blueprint_path, blueprint)
            self.assert_finding(root, "candidate foundation cards cannot feed blueprints")

    def test_blueprint_rejects_duplicate_or_out_of_order_layers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["complexity_layers"][1]["layer"] = "core"
            write_json(blueprint_path, blueprint)
            self.assert_finding(root, "layer names must be unique")

    def test_blueprint_rejects_arbitrary_footprint_and_missing_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["footprint"]["source_owned_rationale"] = "Hit quota."
            del blueprint["authority"]["non_governing_scope"]
            write_json(blueprint_path, blueprint)
            findings, _ = validate_repository(root)
            rendered = "\n".join(findings)
            self.assertIn("source_owned_rationale", rendered)
            self.assertIn("non_governing_scope", rendered)

    def test_blueprint_rejects_omitted_render_and_leakage_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["proof_gates"] = [gate for gate in blueprint["proof_gates"] if gate["category"] not in {"render", "leakage"}]
            write_json(blueprint_path, blueprint)
            self.assert_finding(root, "must contain each required category exactly once")

    def test_fixture_pairs_cover_every_production_archetype(self):
        fixtures = [json.loads(path.read_text(encoding="utf-8")) for path in (ROOT / "examples/blueprints/fixtures").glob("*.json")]
        by_archetype = {}
        for fixture in fixtures:
            by_archetype.setdefault(fixture["archetype"], set()).add(fixture["expected"])
        self.assertEqual(len(by_archetype), 9)
        self.assertTrue(all(expectations == {"pass", "fail"} for expectations in by_archetype.values()))

    def test_release_requires_real_files_and_same_hash_independent_reviews(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            template_path = root / "library/templates/native/sample.xlsx"
            template_path.parent.mkdir(parents=True, exist_ok=True)
            template_path.write_bytes(b"authorized synthetic template fixture")
            evidence_path = root / "evidence/proof.txt"
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text("independent validation evidence", encoding="utf-8")
            template_hash = file_hash(template_path)
            evidence_hash = file_hash(evidence_path)
            blueprint_data = json.loads(blueprint_path.read_text(encoding="utf-8"))
            evidence = {
                gate["category"]: {
                    "status": "pass", "template_sha256": template_hash,
                    "blueprint_procedure": gate["procedure"],
                    "record_path": "evidence/proof.txt", "record_sha256": evidence_hash,
                }
                for gate in blueprint_data["proof_gates"]
            }
            release = {
                "schema_version": "1.0.0", "release_id": "REL-0001", "version": "1.0.0", "status": "released",
                "template": {"path": "library/templates/native/sample.xlsx", "sha256": template_hash, "artifact_type": "xlsx"},
                "blueprint": {"blueprint_id": "BP-0001", "path": "examples/blueprints/BP-0001.internal-close-workbook.json", "sha256": file_hash(blueprint_path)},
                "sanitization": {"record_path": "evidence/proof.txt", "record_sha256": evidence_hash, "template_sha256": template_hash, "foundation_card_ids": ["FOUND-0001"]},
                "reviews": {
                    "terra": {"reviewer": "Terra reviewer", "verdict": "pass", "template_sha256": template_hash, "record_path": "evidence/proof.txt", "record_sha256": evidence_hash},
                    "sol": {"reviewer": "Sol reviewer", "verdict": "pass", "template_sha256": template_hash, "record_path": "evidence/proof.txt", "record_sha256": evidence_hash}
                },
                "conductor_approval": {"approver": "Conductor", "decision": "approved", "template_sha256": template_hash, "record_path": "evidence/proof.txt", "record_sha256": evidence_hash},
                "evidence": evidence
            }
            release_path = root / "library/releases/REL-0001.sample.json"
            write_json(release_path, release)
            self.assertEqual(validate_repository(root)[0], [])
            release["reviews"]["sol"]["template_sha256"] = "0" * 64
            release["reviews"]["sol"]["reviewer"] = "Terra reviewer"
            write_json(release_path, release)
            findings, _ = validate_repository(root)
            rendered = "\n".join(findings)
            self.assertIn("same-hash binding failed", rendered)
            self.assertIn("identities must be independent", rendered)

    def test_malformed_release_reports_findings_without_crashing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _ = self.make_minimal_root(temporary)
            write_json(
                root / "library/releases/REL-0001.malformed.json",
                {
                    "schema_version": "1.0.0", "release_id": "REL-0001", "version": "1.0.0",
                    "status": "released", "template": [], "blueprint": "broken",
                    "sanitization": 3, "reviews": [], "conductor_approval": None,
                    "evidence": ["not", "an", "object"],
                },
            )
            findings, _ = validate_repository(root)
            self.assertTrue(findings)
            self.assertTrue(any("is not of type 'object'" in finding for finding in findings))


if __name__ == "__main__":
    unittest.main()
