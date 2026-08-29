import json
import hashlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from integrations.query_catalog import (
    CatalogQueryError,
    discover,
    instantiate,
    load_catalog,
    select_exact,
    validate_release,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "integrations" / "consumer-profile.v1.json"


class ConsumerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    def test_profile_is_machine_readable_and_stable(self):
        self.assertEqual(self.profile["schema_version"], "1.0.0")
        self.assertEqual(self.profile["profile_id"], "syn-studios-consumer")
        self.assertEqual(self.profile["status"], "stable")
        self.assertEqual(
            self.profile["interface"]["operations"],
            ["discover", "select", "instantiate", "validate"],
        )
        self.assertEqual(self.profile["interface"]["resolver"], "integrations/query_catalog.py")
        self.assertEqual(
            self.profile["interface"]["resolver_modes"],
            ["discover", "select", "instantiate", "validate"],
        )
        self.assertTrue((ROOT / self.profile["interface"]["resolver"]).is_file())

    def test_all_reference_paths_exist(self):
        for relative in self.profile["interface"]["reference_paths"]:
            self.assertTrue((ROOT / relative).exists(), relative)

        # These are canonical integration targets produced by the library release slice.
        self.assertEqual(self.profile["interface"]["catalog_path"], "library/catalog.json")
        self.assertEqual(self.profile["interface"]["release_evidence_root"], "library/releases")

    def test_operations_define_a_release_safe_handoff(self):
        operations = self.profile["operations"]
        self.assertEqual(set(operations), {"discover", "select", "instantiate", "validate"})
        self.assertIn("release_status_is_released", operations["select"]["requires"])
        self.assertEqual(operations["select"]["no_match_behavior"], "return_constraints_and_stop")
        self.assertIn("consumer_write_authorization", operations["instantiate"]["requires"])
        self.assertIn("library/templates", operations["instantiate"]["must_not_write"])
        self.assertFalse(operations["validate"]["side_effects"])

    def test_required_consumers_and_modes_are_declared(self):
        consumers = {item["consumer_id"]: item for item in self.profile["consumers"]}
        expected = {
            "anna-holodeck-bridge",
            "holodeck-synthetic-data",
            "holodeck-synthetic-data-explore-world",
            "human-artifact-realism",
        }
        self.assertEqual(set(consumers), expected)
        self.assertEqual(
            consumers["anna-holodeck-bridge"]["modes"],
            ["discover", "select", "instantiate", "validate"],
        )
        self.assertIn("artifact_map", consumers["holodeck-synthetic-data-explore-world"]["modes"])
        self.assertEqual(consumers["human-artifact-realism"]["modes"], ["plan", "create", "audit"])

    def test_holodeck_mapping_preserves_package_ownership(self):
        mapping = self.profile["holodeck_mapping"]
        required = {
            "provenance_card.author_or_source",
            "provenance_card.source_knows",
            "provenance_card.source_does_not_know",
            "provenance_card.tool_or_system",
            "provenance_card.realistic_imperfections",
            "provenance_card.generation_approach",
            "provenance_card.realism_checks",
            "provenance_card.difficulty_preservation",
            "world_facts",
            "seed_conventions",
        }
        self.assertTrue(required.issubset(mapping))
        self.assertEqual(mapping["world_facts"]["persistence"], "package_only")
        self.assertEqual(mapping["world_facts"]["template_storage"], "forbidden")
        self.assertFalse(self.profile["data_classes"]["persist_package_only_under_library"])

    def test_exploration_outputs_cover_artifact_map_and_world_brief(self):
        compatibility = self.profile["exploration_compatibility"]
        self.assertIn("authority", compatibility["artifact_map"]["fields"])
        self.assertIn("dependencies", compatibility["artifact_map"]["fields"])
        self.assertIn("observed_or_inferred", compatibility["artifact_map"]["fields"])
        self.assertIn("authoritative_sources", compatibility["world_brief"]["sections"])
        self.assertIn("evidence_index", compatibility["world_brief"]["sections"])
        self.assertTrue(compatibility["world_brief"]["facts_remain_package_owned"])

    def test_profile_embeds_no_private_paths_hashes_or_world_values(self):
        text = PROFILE_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(text, re.compile(r"[A-Za-z]:[\\/]"))
        self.assertNotRegex(text, re.compile(r"(?i)(?:^|[^a-f0-9])[a-f0-9]{64}(?:[^a-f0-9]|$)"))
        self.assertNotIn("source.locator", text)
        self.assertNotIn("key_numbers", text)

    def test_skill_routes_consumer_operations_to_owning_contract(self):
        text = (ROOT / "skill" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("### Consume", text)
        for operation in ("discover", "select", "instantiate", "validate"):
            self.assertIn(f"`{operation}`", text)
        self.assertIn("../docs/INTEGRATIONS.md", text)
        self.assertIn("../integrations/consumer-profile.v1.json", text)


class CatalogResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.catalog_path = Path(self.temp_directory.name) / "catalog.json"
        self.catalog_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "catalog_id": "syn-studios-artifact-library",
                    "templates": [
                        {
                            "template_id": "TMPL-0002",
                            "version": "1.0.0",
                            "artifact_type": "docx",
                            "blueprint_id": "BP-0002",
                            "authority": "supporting",
                            "lifecycle": "working",
                            "descriptor": "library/templates/TMPL-0002/template.json",
                            "native_assets": ["library/templates/TMPL-0002/memo.docx"],
                            "supported_consumers": ["holodeck-file-generation"],
                            "capabilities": ["render", "metadata"],
                            "release_status": "draft",
                        },
                        {
                            "template_id": "TMPL-0001",
                            "version": "1.2.3",
                            "artifact_type": "xlsx",
                            "blueprint_id": "BP-0001",
                            "authority": "supporting",
                            "lifecycle": "reviewed",
                            "descriptor": "library/templates/TMPL-0001/template.json",
                            "native_assets": ["library/templates/TMPL-0001/workbook.xlsx"],
                            "supported_consumers": ["anna", "holodeck-file-generation"],
                            "capabilities": ["recalculate", "render", "metadata"],
                            "release_status": "released",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.catalog = load_catalog(self.catalog_path)

    def test_discover_filters_and_excludes_unreleased_entries(self):
        matches = discover(
            self.catalog,
            artifact_type="xlsx",
            consumers=["holodeck-file-generation"],
            capabilities=["recalculate", "render"],
        )
        self.assertEqual([(item["template_id"], item["version"]) for item in matches], [("TMPL-0001", "1.2.3")])
        self.assertEqual(discover(self.catalog, artifact_type="docx"), [])

    def test_select_requires_exact_released_version(self):
        selected = select_exact(self.catalog, template_id="TMPL-0001", version="1.2.3")
        self.assertEqual(selected["blueprint_id"], "BP-0001")
        self.assertEqual(selected["descriptor"], "library/templates/TMPL-0001/template.json")
        self.assertEqual(selected["native_assets"], ["library/templates/TMPL-0001/workbook.xlsx"])
        self.assertIsNone(select_exact(self.catalog, template_id="TMPL-0001", version="9.9.9"))
        with self.assertRaisesRegex(CatalogQueryError, "floating versions are forbidden"):
            select_exact(self.catalog, template_id="TMPL-0001", version="latest")

    def test_cli_returns_machine_readable_json(self):
        command = [
            sys.executable,
            str(ROOT / "integrations" / "query_catalog.py"),
            "--catalog",
            str(self.catalog_path),
            "discover",
            "--consumer",
            "anna",
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)

    def test_cli_rejects_floating_version_with_json_error(self):
        command = [
            sys.executable,
            str(ROOT / "integrations" / "query_catalog.py"),
            "--catalog",
            str(self.catalog_path),
            "select",
            "--template-id",
            "TMPL-0001",
            "--version",
            "latest",
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stderr)["status"], "error")

    def test_invalid_authority_class_is_rejected(self):
        self.catalog["templates"][1]["authority"] = "looks-important"
        self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf-8")
        with self.assertRaisesRegex(CatalogQueryError, "unsupported authority class"):
            load_catalog(self.catalog_path)

    def test_traversal_in_released_paths_is_rejected(self):
        self.catalog["templates"][1]["descriptor"] = "../private/template.json"
        self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf-8")
        with self.assertRaisesRegex(CatalogQueryError, "safe repository-relative path"):
            load_catalog(self.catalog_path)

    def test_release_status_is_enforced(self):
        self.catalog["templates"][1]["release_status"] = "candidate"
        self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf-8")
        candidate_catalog = load_catalog(self.catalog_path)
        self.assertEqual(discover(candidate_catalog), [])
        self.assertIsNone(select_exact(candidate_catalog, template_id="TMPL-0001", version="1.2.3"))

    def test_windows_drive_and_unc_paths_are_rejected(self):
        for unsafe in (r"C:\private\template.json", r"\\server\share\template.json", r"\rooted\template.json"):
            with self.subTest(unsafe=unsafe):
                self.catalog["templates"][1]["descriptor"] = unsafe
                self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf-8")
                with self.assertRaisesRegex(CatalogQueryError, "safe repository-relative path"):
                    load_catalog(self.catalog_path)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FullConsumerTrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        temporary_root = Path(self.temporary.name)
        self.root = temporary_root / "repo"
        self.package_root = temporary_root / "package"
        self.package_root.mkdir()

        asset = self.root / "library/templates/TMPL-0001/1.2.3/workbook.xlsx"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"authorized fact-free template")
        asset_hash = _file_hash(asset)
        descriptor = {
            "template_id": "TMPL-0001",
            "version": "1.2.3",
            "artifact_type": "xlsx",
            "authority": "supporting",
            "lifecycle": "reviewed",
            "lineage": {"blueprint_id": "BP-0001", "foundation_ids": ["FOUND-0001"]},
            "release_status": "released",
            "producer": {"role": "senior accountant"},
            "native_assets": [
                {
                    "path": "library/templates/TMPL-0001/1.2.3/workbook.xlsx",
                    "sha256": asset_hash,
                }
            ],
            "knowledge_and_authority_constraints": ["Questions cannot resolve themselves."],
            "prohibited_content": ["answer keys", "private world facts"],
            "supported_consumers": ["anna", "holodeck-file-generation"],
            "capabilities": ["recalculate", "render"],
        }
        _write_json(self.root / "library/templates/TMPL-0001/1.2.3/template.json", descriptor)

        blueprint_path = self.root / "examples/blueprints/BP-0001.internal-close-workbook.json"
        procedures = {
            category: f"Verify {category.replace('_', ' ')} against the selected bytes."
            for category in (
                "core_integrity",
                "render",
                "metadata",
                "computational",
                "provenance",
                "leakage",
                "authority_separation",
                "anti_filler",
            )
        }
        _write_json(
            blueprint_path,
            {
                "blueprint_id": "BP-0001",
                "medium": "Native Excel workbook",
                "proof_gates": [
                    {"category": category, "applicable": True, "procedure": procedure}
                    for category, procedure in procedures.items()
                ],
            },
        )
        proof_path = self.root / "library/releases/evidence/proof.txt"
        proof_path.parent.mkdir(parents=True)
        proof_path.write_text("same-hash validation evidence", encoding="utf-8")
        proof_hash = _file_hash(proof_path)
        bound_record = {
            "template_sha256": asset_hash,
            "record_path": "library/releases/evidence/proof.txt",
            "record_sha256": proof_hash,
        }
        release = {
            "release_id": "REL-0001",
            "version": "1.2.3",
            "status": "released",
            "template": {
                "path": "library/templates/TMPL-0001/1.2.3/workbook.xlsx",
                "sha256": asset_hash,
                "artifact_type": "xlsx",
            },
            "blueprint": {
                "blueprint_id": "BP-0001",
                "path": "examples/blueprints/BP-0001.internal-close-workbook.json",
                "sha256": _file_hash(blueprint_path),
            },
            "sanitization": {
                **bound_record,
                "foundation_card_ids": ["FOUND-0001"],
            },
            "reviews": {
                "terra": {**bound_record, "reviewer": "Terra reviewer", "verdict": "pass"},
                "sol": {**bound_record, "reviewer": "Sol reviewer", "verdict": "pass"},
            },
            "conductor_approval": {
                **bound_record,
                "approver": "Conductor",
                "decision": "approved",
            },
            "evidence": {
                category: {**bound_record, "status": "pass", "blueprint_procedure": procedures[category]}
                for category in procedures
            },
        }
        _write_json(self.root / "library/releases/REL-0001.workbook.json", release)

        self.catalog_entry = {
            "template_id": "TMPL-0001",
            "version": "1.2.3",
            "artifact_type": "xlsx",
            "blueprint_id": "BP-0001",
            "authority": "supporting",
            "lifecycle": "reviewed",
            "descriptor": "library/templates/TMPL-0001/1.2.3/template.json",
            "native_assets": ["library/templates/TMPL-0001/1.2.3/workbook.xlsx"],
            "supported_consumers": ["anna", "holodeck-file-generation"],
            "capabilities": ["recalculate", "render"],
            "release_status": "released",
        }
        self.catalog_path = self.root / "library/catalog.json"
        _write_json(
            self.catalog_path,
            {
                "schema_version": "1.0.0",
                "catalog_id": "syn-studios-artifact-library",
                "templates": [self.catalog_entry],
            },
        )

    def test_full_discover_select_instantiate_plan_validate_trajectory(self):
        catalog = load_catalog(self.catalog_path)
        discovered = discover(
            catalog,
            producer_role="senior accountant",
            medium="Native Excel workbook",
            authority="supporting",
            required_allowed_knowledge=["Questions cannot resolve themselves."],
            prohibited_knowledge=["private world facts"],
            repository_root=self.root,
        )
        self.assertEqual(len(discovered), 1)
        selected = select_exact(
            catalog,
            template_id="TMPL-0001",
            version="1.2.3",
            producer_role="senior accountant",
            medium="Native Excel workbook",
            repository_root=self.root,
        )
        validation = validate_release(self.root, selected)
        self.assertEqual(validation["status"], "pass")
        handoff = instantiate(
            root=self.root,
            catalog=catalog,
            template_id="TMPL-0001",
            version="1.2.3",
            package_root=self.package_root,
            output_location=Path("working_world"),
            manifest_approved=True,
            write_authorized=True,
            source_authorized=True,
            world_fact_keys=["close_period_end", "organization_name"],
            provenance_reference="manifest.md#workbook",
        )
        self.assertEqual(handoff["mode"], "plan")
        self.assertFalse(handoff["template_bytes_mutated"])
        self.assertFalse((self.package_root / "working_world").exists())
        self.assertNotIn("world_fact_values", handoff["binding"])

    def test_explicit_materialization_copies_without_mutating_template(self):
        catalog = load_catalog(self.catalog_path)
        source = self.root / self.catalog_entry["native_assets"][0]
        before = _file_hash(source)
        handoff = instantiate(
            root=self.root,
            catalog=catalog,
            template_id="TMPL-0001",
            version="1.2.3",
            package_root=self.package_root,
            output_location=Path("working_world"),
            manifest_approved=True,
            write_authorized=True,
            source_authorized=True,
            provenance_reference="manifest.md#workbook",
            materialize=True,
        )
        self.assertEqual(handoff["mode"], "materialized_copy")
        self.assertEqual(_file_hash(source), before)
        self.assertEqual(len(handoff["materialized_assets"]), 1)

    def test_selection_rejects_producer_medium_provenance_and_authority_mismatch(self):
        catalog = load_catalog(self.catalog_path)
        base = {"catalog": catalog, "template_id": "TMPL-0001", "version": "1.2.3", "repository_root": self.root}
        cases = (
            {"producer_role": "lawyer"},
            {"medium": "Native Word memorandum"},
            {"authority": "authoritative"},
            {"required_allowed_knowledge": ["Knows the answer."]},
            {"prohibited_knowledge": ["unlisted secret"]},
        )
        for mismatch in cases:
            with self.subTest(mismatch=mismatch):
                self.assertIsNone(select_exact(**base, **mismatch))

    def test_instantiation_enforces_authority_and_output_containment(self):
        catalog = load_catalog(self.catalog_path)
        common = {
            "root": self.root,
            "catalog": catalog,
            "template_id": "TMPL-0001",
            "version": "1.2.3",
            "package_root": self.package_root,
            "provenance_reference": "manifest.md#workbook",
            "manifest_approved": True,
            "write_authorized": True,
            "source_authorized": True,
        }
        with self.assertRaisesRegex(CatalogQueryError, "authorization"):
            instantiate(**{**common, "write_authorized": False}, output_location=Path("working"))
        with self.assertRaisesRegex(CatalogQueryError, "escapes package root"):
            instantiate(**common, output_location=Path("../outside"))

    def test_validate_detects_native_file_drift(self):
        catalog = load_catalog(self.catalog_path)
        (self.root / self.catalog_entry["native_assets"][0]).write_bytes(b"tampered")
        with self.assertRaisesRegex(CatalogQueryError, "hash does not match"):
            validate_release(self.root, catalog["templates"][0])

    def test_cli_uses_root_canonical_catalog_by_default(self):
        command = [
            sys.executable,
            str(ROOT / "integrations/query_catalog.py"),
            "--root",
            str(self.root),
            "discover",
            "--artifact-type",
            "xlsx",
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(completed.stdout)["count"], 1)


if __name__ == "__main__":
    unittest.main()
