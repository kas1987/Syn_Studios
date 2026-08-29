import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.validate_library import SCHEMA_NAMES, validate_repository


ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LibraryControlPlaneTests(unittest.TestCase):
    def make_minimal_root(self, temporary):
        root = Path(temporary)
        for name in SCHEMA_NAMES:
            target = root / "schemas" / f"{name}.schema.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "schemas" / target.name, target)
        card_target = root / "library/foundations/FOUND-0001.json"
        card_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "library/foundations/FOUND-0001.json", card_target)
        blueprint_target = root / "examples/blueprints/BP-0001.internal-close-workbook.json"
        blueprint_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "examples/blueprints/BP-0001.internal-close-workbook.json", blueprint_target)
        fixture_root = root / "examples/blueprints/fixtures"
        fixture_root.mkdir(parents=True, exist_ok=True)
        for name in ("close-workbook.positive.json", "close-workbook.anti.json"):
            shutil.copy2(ROOT / "examples/blueprints/fixtures" / name, fixture_root / name)
        return root, card_target, blueprint_target

    def assert_finding(self, root, fragment):
        findings, _ = validate_repository(root)
        self.assertTrue(any(fragment in item for item in findings), "\n".join(findings))

    def make_release(self, root, blueprint_path):
        blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
        asset = root / "library/templates/TMPL-0001/1.0.0/workbook.xlsx"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"authorized synthetic workbook template")
        asset_binding = {"path": asset.relative_to(root).as_posix(), "sha256": file_hash(asset)}
        descriptor = {
            "schema_version": "1.0.0",
            "template_id": "TMPL-0001",
            "version": "1.0.0",
            "artifact_type": "xlsx",
            "blueprint_id": "BP-0001",
            "blueprint_sha256": file_hash(blueprint_path),
            "native_assets": [asset_binding],
            "supported_consumers": ["anna-holodeck-bridge"],
            "capabilities": ["recalculate", "render"],
        }
        descriptor_path = asset.parent / "template.json"
        write_json(descriptor_path, descriptor)
        descriptor_binding = {"path": descriptor_path.relative_to(root).as_posix(), "sha256": file_hash(descriptor_path)}

        def evidence(name, record_type, verdict, actor, categories=None, procedures=None):
            record = {
                "schema_version": "1.0.0",
                "record_id": f"EVID-RECORD-{name.upper()}",
                "record_type": record_type,
                "release_id": "REL-0001",
                "template_id": "TMPL-0001",
                "version": "1.0.0",
                "descriptor_sha256": descriptor_binding["sha256"],
                "native_asset_sha256s": [asset_binding["sha256"]],
                "verdict": verdict,
                "actor": actor,
                "summary": f"Typed evidence for the {name} release gate.",
            }
            if categories is not None:
                record["categories"] = categories
            if procedures is not None:
                record["procedures"] = procedures
            path = root / "evidence/template-releases/REL-0001" / f"{name}.json"
            write_json(path, record)
            return {"record_path": path.relative_to(root).as_posix(), "record_sha256": file_hash(path)}

        sanitization = evidence("sanitization", "sanitization", "SANITIZATION_PASS", "Sanitization reviewer")
        terra = evidence("terra", "terra_review", "USABILITY_PASS", "Terra reviewer")
        sol = evidence("sol", "sol_review", "INTEGRITY_PASS", "Sol reviewer")
        conductor = evidence("conductor", "conductor_approval", "APPROVED", "Conductor")
        procedures = {gate["category"]: gate["procedure"] for gate in blueprint["proof_gates"]}
        technical = evidence("technical", "technical_validation", "VALIDATION_PASS", "Validation runner", sorted(procedures), procedures)
        release = {
            "schema_version": "2.0.0",
            "release_id": "REL-0001",
            "template_id": "TMPL-0001",
            "version": "1.0.0",
            "status": "released",
            "descriptor": descriptor_binding,
            "native_assets": [asset_binding],
            "blueprint": {"blueprint_id": "BP-0001", "path": blueprint_path.relative_to(root).as_posix(), "sha256": file_hash(blueprint_path)},
            "sanitization": {"evidence": sanitization, "foundation_card_ids": ["FOUND-0001"]},
            "reviews": {"terra": terra, "sol": sol},
            "conductor_approval": conductor,
            "evidence": {category: technical for category in sorted(g["category"] for g in blueprint["proof_gates"])},
        }
        release_path = root / "library/releases/REL-0001.template.json"
        write_json(release_path, release)
        return release_path, release, descriptor_path, descriptor

    def make_catalog(self, root, release_path, release, descriptor):
        blueprint = json.loads((root / release["blueprint"]["path"]).read_text(encoding="utf-8"))
        catalog = {
            "schema_version": "1.0.0",
            "catalog_id": "syn-studios-artifact-library",
            "templates": [{
                "template_id": release["template_id"],
                "version": release["version"],
                "artifact_type": descriptor["artifact_type"],
                "blueprint_id": release["blueprint"]["blueprint_id"],
                "authority": blueprint["authority"]["primary_class"],
                "lifecycle": blueprint["lifecycle"],
                "descriptor": release["descriptor"],
                "native_assets": release["native_assets"],
                "supported_consumers": descriptor["supported_consumers"],
                "capabilities": descriptor["capabilities"],
                "release_status": "released",
                "release_record": {"path": release_path.relative_to(root).as_posix(), "sha256": file_hash(release_path)},
            }],
        }
        path = root / "library/catalog.json"
        write_json(path, catalog)
        return path, catalog

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

    def test_blueprint_rejects_duplicate_layers_and_omitted_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["complexity_layers"][1]["layer"] = "core"
            blueprint["proof_gates"] = [gate for gate in blueprint["proof_gates"] if gate["category"] != "leakage"]
            write_json(blueprint_path, blueprint)
            findings, _ = validate_repository(root)
            self.assertIn("layer names must be unique", "\n".join(findings))
            self.assertIn("must contain each required category exactly once", "\n".join(findings))

    def test_fixture_mapping_is_canonical_and_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _ = self.make_minimal_root(temporary)
            positive = root / "examples/blueprints/fixtures/close-workbook.positive.json"
            fixture = json.loads(positive.read_text(encoding="utf-8"))
            fixture["archetype"] = "invented_nonproduction_type"
            write_json(positive, fixture)
            self.assert_finding(root, "must match the base blueprint archetype")
            positive.unlink()
            self.assert_finding(root, "requires exactly one pass and one fail fixture")

    def test_typed_release_and_catalog_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            self.make_catalog(root, release_path, release, descriptor)
            self.assertEqual(validate_repository(root)[0], [])

    def test_fake_readme_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, _ = self.make_release(root, blueprint_path)
            readme = root / "README.md"
            readme.write_text("unrelated prose", encoding="utf-8")
            release["reviews"]["terra"] = {"record_path": "README.md", "record_sha256": file_hash(readme)}
            write_json(release_path, release)
            self.assert_finding(root, "path must be under evidence/template-releases")

    def test_distinct_review_records_and_lane_verdicts_are_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, _ = self.make_release(root, blueprint_path)
            release["reviews"]["sol"] = release["reviews"]["terra"]
            write_json(release_path, release)
            self.assert_finding(root, "record paths must be distinct")
            terra_path = root / release["reviews"]["terra"]["record_path"]
            terra = json.loads(terra_path.read_text(encoding="utf-8"))
            terra["verdict"] = "INTEGRITY_PASS"
            write_json(terra_path, terra)
            release["reviews"]["terra"]["record_sha256"] = file_hash(terra_path)
            write_json(release_path, release)
            self.assert_finding(root, "'USABILITY_PASS' was expected")

    def test_direct_blueprint_hash_sabotage_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, _ = self.make_release(root, blueprint_path)
            release["blueprint"]["sha256"] = "0" * 64
            write_json(release_path, release)
            self.assert_finding(root, "hash does not match")

    def test_catalog_released_entry_requires_exact_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            catalog_path, catalog = self.make_catalog(root, release_path, release, descriptor)
            del catalog["templates"][0]["release_record"]
            write_json(catalog_path, catalog)
            self.assert_finding(root, "release_record")

    def test_descriptor_id_hash_and_unbound_assets_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, descriptor_path, descriptor = self.make_release(root, blueprint_path)
            extra = descriptor_path.parent / "unbound.csv"
            extra.write_text("secret,world,facts", encoding="utf-8")
            descriptor["template_id"] = "TMPL-9999"
            descriptor["native_assets"].append({"path": extra.relative_to(root).as_posix(), "sha256": file_hash(extra)})
            write_json(descriptor_path, descriptor)
            release["descriptor"]["sha256"] = file_hash(descriptor_path)
            write_json(release_path, release)
            findings, _ = validate_repository(root)
            rendered = "\n".join(findings)
            self.assertIn("descriptor.template_id", rendered)
            self.assertIn("must exactly match release native assets", rendered)

    def test_catalog_mismatched_id_descriptor_and_asset_hashes_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            catalog_path, catalog = self.make_catalog(root, release_path, release, descriptor)
            entry = catalog["templates"][0]
            entry["template_id"] = "TMPL-9999"
            entry["descriptor"]["sha256"] = "0" * 64
            entry["native_assets"][0]["sha256"] = "1" * 64
            write_json(catalog_path, catalog)
            findings, _ = validate_repository(root)
            rendered = "\n".join(findings)
            self.assertIn("template_id: does not match release", rendered)
            self.assertIn("descriptor: does not match release", rendered)
            self.assertIn("native_assets: do not match release", rendered)

    def test_malformed_unhashable_ids_report_without_crashing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, card_path, blueprint_path = self.make_minimal_root(temporary)
            card = json.loads(card_path.read_text(encoding="utf-8"))
            card["card_id"] = ["FOUND-0001"]
            write_json(card_path, card)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["blueprint_id"] = {"bad": "id"}
            blueprint["foundation_lineage"][0]["card_id"] = ["FOUND-0001"]
            write_json(blueprint_path, blueprint)
            findings, _ = validate_repository(root)
            self.assertTrue(findings)
            self.assertIn("is not of type 'string'", "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
