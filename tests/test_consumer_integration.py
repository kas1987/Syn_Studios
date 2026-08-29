import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from integrations.query_catalog import CatalogQueryError, discover, load_catalog, select_exact


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


if __name__ == "__main__":
    unittest.main()
