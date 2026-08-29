import json
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock
from pathlib import Path

from integrations.query_catalog import (
    _atomic_rename_no_replace,
    _canonical_consumer_ids,
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
        self.assertEqual(operations["validate"]["outcomes"], ["pass", "error"])
        self.assertEqual(operations["validate"]["returns"], "exact_release_identity_and_bound_hashes")
        self.assertEqual(
            operations["validate"]["candidate_artifact_validation"],
            "owned_by_consumer_package_review_workflow",
        )

    def test_required_consumers_and_modes_are_declared(self):
        consumers = {item["consumer_id"]: item for item in self.profile["consumers"]}
        expected = {
            "anna",
            "holodeck-file-generation",
            "human-artifact-realism",
        }
        self.assertEqual(set(consumers), expected)
        self.assertEqual(
            consumers["anna"]["modes"],
            ["discover", "select", "instantiate", "validate"],
        )
        self.assertIn("artifact_map", consumers["holodeck-file-generation"]["modes"])
        self.assertEqual(
            consumers["human-artifact-realism"]["modes"],
            ["discover", "select", "validate", "plan", "create", "audit"],
        )

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
        self.root = Path(self.temp_directory.name)
        (self.root / "integrations").mkdir()
        shutil.copy2(PROFILE_PATH, self.root / "integrations/consumer-profile.v1.json")
        validator_path = self.root / "scripts/validate_library.py"
        validator_path.parent.mkdir()
        validator_path.write_text("def validate_repository(root):\n    return [], 1\n", encoding="utf-8")
        self.catalog_path = self.root / "library/catalog.json"
        self.catalog_path.parent.mkdir()
        self.catalog_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "catalog_id": "syn-studios-artifact-library",
                    "templates": [
                        {
                            "kind": "artifact_template",
                            "template_id": "TMPL-0002",
                            "version": "1.0.0",
                            "name": "Draft memo",
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
                            "kind": "artifact_template",
                            "template_id": "TMPL-0001",
                            "version": "1.2.3",
                            "name": "Released workbook",
                            "artifact_type": "xlsx",
                            "blueprint_id": "BP-0001",
                            "authority": "supporting",
                            "lifecycle": "reviewed",
                            "descriptor": "library/templates/TMPL-0001/template.json",
                            "native_assets": ["library/templates/TMPL-0001/workbook.xlsx"],
                            "supported_consumers": ["anna", "holodeck-file-generation"],
                            "capabilities": ["recalculate", "render", "metadata"],
                            "release_status": "released",
                            "release_record": {"path": "library/releases/REL-0001.template.json", "sha256": "0" * 64},
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.catalog = load_catalog(self.catalog_path)

    def test_profile_operation_drift_is_rejected_at_runtime(self):
        profile_path = self.root / "integrations/consumer-profile.v1.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["interface"]["operations"].remove("discover")
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(CatalogQueryError, "canonical resolver contract"):
            _canonical_consumer_ids(self.root)

    def test_profile_consumer_mode_drift_is_rejected_at_runtime(self):
        profile_path = self.root / "integrations/consumer-profile.v1.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        next(item for item in profile["consumers"] if item["consumer_id"] == "anna")["modes"] = []
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(CatalogQueryError, "nonempty unique strings"):
            _canonical_consumer_ids(self.root)

    def test_invoked_operation_must_be_allowed_for_consumer(self):
        profile_path = self.root / "integrations/consumer-profile.v1.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        next(item for item in profile["consumers"] if item["consumer_id"] == "anna")["modes"].remove("discover")
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(CatalogQueryError, "does not allow anna mode: discover"):
            discover(self.catalog, consumer_id="anna", repository_root=self.root)

    def test_discover_filters_and_excludes_unreleased_entries(self):
        matches = discover(
            self.catalog,
            consumer_id="holodeck-file-generation",
            artifact_type="xlsx",
            capabilities=["recalculate", "render"],
            repository_root=self.root,
        )
        self.assertEqual([(item["template_id"], item["version"]) for item in matches], [("TMPL-0001", "1.2.3")])
        self.assertEqual(discover(self.catalog, consumer_id="anna", artifact_type="docx", repository_root=self.root), [])

    def test_select_requires_exact_released_version(self):
        base = {"catalog": self.catalog, "template_id": "TMPL-0001", "consumer_id": "anna", "repository_root": self.root}
        selected = select_exact(**base, version="1.2.3")
        self.assertEqual(selected["blueprint_id"], "BP-0001")
        self.assertEqual(selected["descriptor"], "library/templates/TMPL-0001/template.json")
        self.assertEqual(selected["native_assets"], ["library/templates/TMPL-0001/workbook.xlsx"])
        self.assertIsNone(select_exact(**base, version="9.9.9"))
        with self.assertRaisesRegex(CatalogQueryError, "floating versions are forbidden"):
            select_exact(**base, version="latest")

    def test_cli_returns_machine_readable_json(self):
        command = [
            sys.executable,
            str(ROOT / "integrations" / "query_catalog.py"),
            "--root",
            str(self.root),
            "discover",
            "--consumer-id",
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
            "--root",
            str(self.root),
            "select",
            "--consumer-id",
            "anna",
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
        self.assertEqual(discover(candidate_catalog, consumer_id="anna", repository_root=self.root), [])
        self.assertIsNone(select_exact(candidate_catalog, template_id="TMPL-0001", version="1.2.3", consumer_id="anna", repository_root=self.root))

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
        (self.root / "integrations").mkdir(parents=True)
        shutil.copy2(PROFILE_PATH, self.root / "integrations/consumer-profile.v1.json")
        validator_path = self.root / "scripts/validate_library.py"
        validator_path.parent.mkdir(parents=True)
        validator_path.write_text(
            "from pathlib import Path\n"
            "import json\n"
            "def validate_repository(root):\n"
            "    marker = Path(root) / '.canonical-findings.json'\n"
            "    findings = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else []\n"
            "    return findings, 1\n",
            encoding="utf-8",
        )

        self.asset = self.root / "library/templates/TMPL-0001/1.2.3/workbook.xlsx"
        self.asset.parent.mkdir(parents=True)
        with zipfile.ZipFile(self.asset, "w") as package:
            package.writestr("[Content_Types].xml", "<Types/>")
            package.writestr("xl/workbook.xml", "<workbook/>")
        asset_hash = _file_hash(self.asset)
        self.descriptor_path = self.asset.parent / "template.json"
        self.blueprint_path = self.root / "examples/blueprints/BP-0001.internal-close-workbook.json"
        self.procedures = {
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
            self.blueprint_path,
            {
                "schema_version": "2.0.0",
                "blueprint_id": "BP-0001",
                "archetype": "close_workbook",
                "name": "Internal close workbook",
                "artifact_type": "xlsx",
                "producer": "senior accountant",
                "purpose": "Reconcile authorized source activity to the ledger.",
                "lifecycle": "reviewed",
                "authority": {"primary_class": "supporting", "governing_scope": "internal analysis", "non_governing_scope": "external approval"},
                "foundation_lineage": [{"card_id": "FOUND-0001", "card_sha256": "0" * 64, "reviewed_source_sha256": "1" * 64, "use_mode": "reviewed_pattern", "patterns_used": ["reconciliation layers"], "prohibited_content_acknowledged": True, "transformation_boundary": "No source facts are reused."}],
                "medium": "Native Excel workbook",
                "source_boundary": {"authorized_inputs": ["approved world facts"], "excluded_inputs": ["private source answers"], "conflict_resolution": "Authoritative package sources control."},
                "footprint": {"target": "eight worksheets", "natural_depth": ["source rows", "mapping rows"], "source_owned_rationale": "A close workflow naturally preserves source and reconciliation layers."},
                "handling_history": {"mode": "none", "justification": "The reusable template has no handling history."},
                "complexity_layers": [{"layer": "core", "features": ["reconciliation"]}, {"layer": "operational_depth", "features": ["source population"]}],
                "prohibited": ["private world facts"],
                "proof_gates": [
                    {"category": category, "applicable": True, "procedure": procedure}
                    for category, procedure in self.procedures.items()
                ],
            },
        )
        descriptor = {
            "schema_version": "1.0.0",
            "template_id": "TMPL-0001",
            "version": "1.2.3",
            "name": "Released workbook",
            "artifact_type": "xlsx",
            "authority": "supporting",
            "lifecycle": "reviewed",
            "lineage": {"blueprint_id": "BP-0001", "foundation_ids": ["FOUND-0001"], "lineage_source": "examples/blueprints/BP-0001.internal-close-workbook.json#foundation_lineage", "method": "Built from scratch using only the reviewed abstract blueprint pattern."},
            "release_status": "released",
            "producer": {"role": "senior accountant", "department": "finance"},
            "native_assets": [{"path": self.asset.relative_to(self.root).as_posix(), "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "sha256": asset_hash}],
            "knowledge_and_authority_constraints": ["Questions cannot resolve themselves."],
            "prohibited_content": ["answer keys", "private world facts"],
            "supported_consumers": ["anna", "holodeck-file-generation"],
            "capabilities": ["recalculate", "render"],
            "purpose": "Reconcile authorized source activity to the ledger.",
            "slots": ["organization_name"],
            "slot_contract": {"token_format": "{{slot_name}}", "required": True, "instantiated_artifact_policy": "reject unresolved tokens", "value_source": "authorized world facts only"},
            "generation_notes": ["Populate source layers before reconciliation."],
            "proof_expectations": [{"id": "render-all", "capability": "render", "required": True, "description": "Render and inspect every worksheet."}],
        }
        _write_json(self.descriptor_path, descriptor)
        descriptor_hash = _file_hash(self.descriptor_path)

        self.evidence_paths = {}
        def evidence(name, record_type, verdict, actor_id, categories=None):
            artifact_categories = categories or ["provenance"]
            artifacts = []
            for category in artifact_categories:
                proof = self.root / "evidence/template-releases/REL-0001/proofs" / f"{name}-{category}.txt"
                proof.parent.mkdir(parents=True, exist_ok=True)
                proof.write_text(
                    f"REL-0001 TMPL-0001 {category} {asset_hash} observed output for {name}.\n",
                    encoding="utf-8",
                )
                artifacts.append({"path": proof.relative_to(self.root).as_posix(), "sha256": _file_hash(proof), "media_type": "text/plain", "category": category})
            record = {
                "schema_version": "1.0.0", "record_id": f"EVID-RECORD-{name.upper()}",
                "record_type": record_type, "release_id": "REL-0001", "template_id": "TMPL-0001", "version": "1.2.3",
                "descriptor_sha256": descriptor_hash, "native_asset_sha256s": [asset_hash], "verdict": verdict,
                "actor_id": actor_id, "actor": name.title(), "observations": [f"Observed concrete output for the {name} gate."],
                "artifacts": artifacts, "summary": f"Typed evidence for the {name} release gate.",
            }
            if categories is not None:
                record["categories"] = categories
                record["procedures"] = {category: self.procedures[category] for category in categories}
            path = self.root / "evidence/template-releases/REL-0001" / f"{name}.json"
            _write_json(path, record)
            self.evidence_paths[name] = path
            return {"record_path": path.relative_to(self.root).as_posix(), "record_sha256": _file_hash(path)}

        sanitization = evidence("sanitization", "sanitization", "SANITIZATION_PASS", "reviewer:sanitization")
        terra = evidence("terra", "terra_review", "USABILITY_PASS", "reviewer:terra")
        sol = evidence("sol", "sol_review", "INTEGRITY_PASS", "reviewer:sol")
        conductor = evidence("conductor", "conductor_approval", "APPROVED", "reviewer:conductor")
        technical = evidence("technical", "technical_validation", "VALIDATION_PASS", "runner:validation", sorted(self.procedures))
        release = {
            "schema_version": "2.0.0",
            "release_id": "REL-0001",
            "template_id": "TMPL-0001",
            "version": "1.2.3",
            "status": "released",
            "descriptor": {"path": self.descriptor_path.relative_to(self.root).as_posix(), "sha256": descriptor_hash},
            "native_assets": [{"path": self.asset.relative_to(self.root).as_posix(), "sha256": asset_hash}],
            "blueprint": {
                "blueprint_id": "BP-0001",
                "path": "examples/blueprints/BP-0001.internal-close-workbook.json",
                "sha256": _file_hash(self.blueprint_path),
            },
            "sanitization": {"evidence": sanitization, "foundation_card_ids": ["FOUND-0001"]},
            "reviews": {"terra": terra, "sol": sol},
            "conductor_approval": conductor,
            "evidence": {category: technical for category in self.procedures},
        }
        self.release_path = self.root / "library/releases/REL-0001.workbook.json"
        _write_json(self.release_path, release)

        self.catalog_entry = {
            "kind": "artifact_template",
            "template_id": "TMPL-0001",
            "version": "1.2.3",
            "name": "Released workbook",
            "artifact_type": "xlsx",
            "blueprint_id": "BP-0001",
            "authority": "supporting",
            "lifecycle": "reviewed",
            "descriptor": "library/templates/TMPL-0001/1.2.3/template.json",
            "native_assets": ["library/templates/TMPL-0001/1.2.3/workbook.xlsx"],
            "supported_consumers": ["anna", "holodeck-file-generation"],
            "capabilities": ["recalculate", "render"],
            "release_status": "released",
            "release_record": {"path": self.release_path.relative_to(self.root).as_posix(), "sha256": _file_hash(self.release_path)},
        }
        self.catalog_path = self.root / "library/catalog.json"
        _write_json(
            self.catalog_path,
            {
                "schema_version": "1.0.0",
                "catalog_id": "syn-studios-artifact-library",
                "discovery_fields": ["artifact_type", "authority", "lifecycle", "capabilities", "supported_consumers", "blueprint_id", "release_status"],
                "templates": [self.catalog_entry],
            },
        )

    def _set_canonical_findings(self, *findings):
        _write_json(self.root / ".canonical-findings.json", list(findings))

    def _add_secondary_asset(self, *, bind_evidence):
        secondary = self.asset.parent / "secondary.txt"
        secondary.write_bytes(b"authorized secondary template asset")
        secondary_binding = {"path": secondary.relative_to(self.root).as_posix(), "sha256": _file_hash(secondary)}
        descriptor = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
        descriptor["native_assets"].append({**secondary_binding, "media_type": "text/plain"})
        _write_json(self.descriptor_path, descriptor)
        descriptor_hash = _file_hash(self.descriptor_path)

        release = json.loads(self.release_path.read_text(encoding="utf-8"))
        release["descriptor"]["sha256"] = descriptor_hash
        release["native_assets"].append(secondary_binding)
        asset_hashes = [item["sha256"] for item in release["native_assets"]]
        for path in self.evidence_paths.values():
            record = json.loads(path.read_text(encoding="utf-8"))
            record["descriptor_sha256"] = descriptor_hash
            if bind_evidence:
                record["native_asset_sha256s"] = asset_hashes
            _write_json(path, record)

        def reference(name):
            path = self.evidence_paths[name]
            return {"record_path": path.relative_to(self.root).as_posix(), "record_sha256": _file_hash(path)}

        release["sanitization"]["evidence"] = reference("sanitization")
        release["reviews"] = {"terra": reference("terra"), "sol": reference("sol")}
        release["conductor_approval"] = reference("conductor")
        release["evidence"] = {category: reference("technical") for category in self.procedures}
        _write_json(self.release_path, release)
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        catalog["templates"][0]["native_assets"].append(secondary_binding["path"])
        catalog["templates"][0]["release_record"]["sha256"] = _file_hash(self.release_path)
        _write_json(self.catalog_path, catalog)
        return secondary

    def _refresh_evidence_reference(self, name):
        path = self.evidence_paths[name]
        reference = {"record_path": path.relative_to(self.root).as_posix(), "record_sha256": _file_hash(path)}
        release = json.loads(self.release_path.read_text(encoding="utf-8"))
        if name == "technical":
            release["evidence"] = {category: reference for category in self.procedures}
        elif name in {"terra", "sol"}:
            release["reviews"][name] = reference
        elif name == "conductor":
            release["conductor_approval"] = reference
        else:
            release["sanitization"]["evidence"] = reference
        _write_json(self.release_path, release)
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        catalog["templates"][0]["release_record"]["sha256"] = _file_hash(self.release_path)
        _write_json(self.catalog_path, catalog)

    def test_control_compatible_release_fixture_is_accepted(self):
        catalog = load_catalog(self.catalog_path)
        result = validate_release(self.root, catalog["templates"][0])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["descriptor_sha256"], _file_hash(self.descriptor_path))

    def test_operations_fail_closed_without_canonical_validator(self):
        (self.root / "scripts/validate_library.py").unlink()
        with self.assertRaisesRegex(CatalogQueryError, "canonical library validator is unavailable"):
            discover(
                load_catalog(self.catalog_path),
                consumer_id="anna",
                repository_root=self.root,
            )

    def test_canonical_validator_system_exit_uses_error_contract(self):
        (self.root / "scripts/validate_library.py").write_text("raise SystemExit(2)\n", encoding="utf-8")
        with self.assertRaisesRegex(CatalogQueryError, "terminated with exit code 2"):
            discover(load_catalog(self.catalog_path), consumer_id="anna", repository_root=self.root)

    def test_discovery_rejects_forged_in_memory_catalog(self):
        catalog = load_catalog(self.catalog_path)
        catalog["templates"][0]["name"] = "Forged display name"
        with self.assertRaisesRegex(CatalogQueryError, "exactly match the canonical"):
            discover(catalog, consumer_id="anna", repository_root=self.root)

    def test_descriptor_producer_and_knowledge_drift_invalidates_release(self):
        descriptor = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
        descriptor["producer"] = {"role": "outside counsel", "department": "legal"}
        descriptor["knowledge_and_authority_constraints"] = ["May invent governing conclusions."]
        _write_json(self.descriptor_path, descriptor)
        with self.assertRaisesRegex(CatalogQueryError, "hash does not match"):
            validate_release(self.root, load_catalog(self.catalog_path)["templates"][0])

    def test_secondary_asset_omitted_from_typed_evidence_is_rejected(self):
        self._add_secondary_asset(bind_evidence=False)
        self._set_canonical_findings("typed evidence is not bound to every native asset")
        with self.assertRaisesRegex(CatalogQueryError, "canonical library validation failed"):
            validate_release(self.root, load_catalog(self.catalog_path)["templates"][0])

    def test_secondary_asset_drift_is_rejected(self):
        secondary = self._add_secondary_asset(bind_evidence=True)
        secondary.write_bytes(b"drifted after release")
        with self.assertRaisesRegex(CatalogQueryError, "hash does not match"):
            validate_release(self.root, load_catalog(self.catalog_path)["templates"][0])

    def test_typed_review_actor_independence_is_enforced(self):
        self._set_canonical_findings("Terra, Sol, and conductor identities must be independent")
        with self.assertRaisesRegex(CatalogQueryError, "canonical library validation failed"):
            validate_release(self.root, load_catalog(self.catalog_path)["templates"][0])

    def test_typed_technical_procedure_must_match_blueprint(self):
        self._set_canonical_findings("technical procedure does not match the blueprint")
        with self.assertRaisesRegex(CatalogQueryError, "canonical library validation failed"):
            validate_release(self.root, load_catalog(self.catalog_path)["templates"][0])

    def test_later_target_collision_has_zero_partial_copy_side_effects(self):
        secondary = self._add_secondary_asset(bind_evidence=True)
        output = self.package_root / "working_world"
        output.mkdir()
        existing = output / secondary.name
        existing.write_bytes(b"preserve existing")
        with self.assertRaisesRegex(CatalogQueryError, "new output location"):
            instantiate(
                root=self.root,
                catalog=load_catalog(self.catalog_path),
                template_id="TMPL-0001",
                version="1.2.3",
                consumer_id="anna",
                package_root=self.package_root,
                output_location=Path("working_world"),
                manifest_approved=True,
                write_authorized=True,
                source_authorized=True,
                provenance_reference="manifest.md#workbook",
                materialize=True,
            )
        self.assertFalse((output / self.asset.name).exists())
        self.assertEqual(existing.read_bytes(), b"preserve existing")
        self.assertEqual(list(self.package_root.glob(".working_world.syn-studios-*")), [])

    def test_second_copy_failure_leaves_no_committed_or_staged_output(self):
        self._add_secondary_asset(bind_evidence=True)
        real_copy = shutil.copy2
        copy_count = 0

        def fail_second_copy(source, target):
            nonlocal copy_count
            copy_count += 1
            if copy_count == 2:
                raise OSError("injected second-copy failure")
            return real_copy(source, target)

        with mock.patch("integrations.query_catalog.shutil.copy2", side_effect=fail_second_copy):
            with self.assertRaisesRegex(CatalogQueryError, "failed before commit"):
                instantiate(
                    root=self.root,
                    catalog=load_catalog(self.catalog_path),
                    template_id="TMPL-0001",
                    version="1.2.3",
                    consumer_id="anna",
                    package_root=self.package_root,
                    output_location=Path("working_world"),
                    manifest_approved=True,
                    write_authorized=True,
                    source_authorized=True,
                    provenance_reference="manifest.md#workbook",
                    materialize=True,
                )
        self.assertFalse((self.package_root / "working_world").exists())
        self.assertEqual(list(self.package_root.glob(".working_world.syn-studios-*")), [])

    def test_target_appearance_during_commit_is_not_overwritten(self):
        output = self.package_root / "working_world"

        def race_winner(_staging, target):
            target.mkdir()
            (target / "owned-by-racer.txt").write_text("preserve", encoding="utf-8")
            raise FileExistsError("injected target race")

        with mock.patch("integrations.query_catalog._atomic_rename_no_replace", side_effect=race_winner):
            with self.assertRaisesRegex(CatalogQueryError, "failed before commit"):
                instantiate(
                    root=self.root,
                    catalog=load_catalog(self.catalog_path),
                    template_id="TMPL-0001",
                    version="1.2.3",
                    consumer_id="anna",
                    package_root=self.package_root,
                    output_location=Path("working_world"),
                    manifest_approved=True,
                    write_authorized=True,
                    source_authorized=True,
                    provenance_reference="manifest.md#workbook",
                    materialize=True,
                )
        self.assertEqual((output / "owned-by-racer.txt").read_text(encoding="utf-8"), "preserve")
        self.assertEqual(list(self.package_root.glob(".working_world.syn-studios-*")), [])

    def test_atomic_publish_primitive_rejects_existing_target(self):
        source = self.package_root / "staged"
        target = self.package_root / "published"
        source.mkdir()
        target.mkdir()
        (target / "existing.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaises(OSError):
            _atomic_rename_no_replace(source, target)
        self.assertTrue(source.is_dir())
        self.assertEqual((target / "existing.txt").read_text(encoding="utf-8"), "preserve")

    def test_full_discover_select_instantiate_plan_validate_trajectory(self):
        catalog = load_catalog(self.catalog_path)
        discovered = discover(
            catalog,
            consumer_id="anna",
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
            consumer_id="anna",
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
            consumer_id="anna",
            producer_role="senior accountant",
            medium="Native Excel workbook",
            authority="supporting",
            required_allowed_knowledge=["Questions cannot resolve themselves."],
            prohibited_knowledge=["private world facts"],
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
        self.assertEqual(
            handoff["binding"]["selection_constraints"],
            {
                "authority": "supporting",
                "producer_role": "senior accountant",
                "medium": "Native Excel workbook",
                "capabilities": [],
                "required_allowed_knowledge": ["Questions cannot resolve themselves."],
                "prohibited_knowledge": ["private world facts"],
            },
        )

    def test_explicit_materialization_copies_without_mutating_template(self):
        catalog = load_catalog(self.catalog_path)
        source = self.root / self.catalog_entry["native_assets"][0]
        before = _file_hash(source)
        handoff = instantiate(
            root=self.root,
            catalog=catalog,
            template_id="TMPL-0001",
            version="1.2.3",
            consumer_id="anna",
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
        base = {"catalog": catalog, "template_id": "TMPL-0001", "version": "1.2.3", "consumer_id": "anna", "repository_root": self.root}
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

    def test_consumer_ids_are_canonical_and_case_sensitive(self):
        catalog = load_catalog(self.catalog_path)
        with self.assertRaisesRegex(CatalogQueryError, "canonical consumer profile ID"):
            discover(catalog, consumer_id="ANNA", repository_root=self.root)
        self.assertIsNone(
            select_exact(
                catalog,
                template_id="TMPL-0001",
                version="1.2.3",
                consumer_id="human-artifact-realism",
                repository_root=self.root,
            )
        )
        catalog["templates"][0]["supported_consumers"] = ["ANNA"]
        _write_json(self.catalog_path, catalog)
        with self.assertRaisesRegex(CatalogQueryError, "catalog supported_consumers"):
            discover(catalog, consumer_id="anna", repository_root=self.root)

    def test_instantiation_requires_and_reapplies_provenance_constraints(self):
        catalog = load_catalog(self.catalog_path)
        common = {
            "root": self.root,
            "catalog": catalog,
            "template_id": "TMPL-0001",
            "version": "1.2.3",
            "consumer_id": "anna",
            "package_root": self.package_root,
            "output_location": Path("working"),
            "manifest_approved": True,
            "write_authorized": True,
            "source_authorized": True,
        }
        with self.assertRaisesRegex(CatalogQueryError, "nonempty provenance_reference"):
            instantiate(**common, provenance_reference="")
        mismatches = (
            {"producer_role": "outside counsel"},
            {"medium": "Native Word memorandum"},
            {"authority": "authoritative"},
            {"required_allowed_knowledge": ["May invent conclusions."]},
            {"prohibited_knowledge": ["unlisted secret"]},
            {"capabilities": ["unsupported-capability"]},
        )
        for mismatch in mismatches:
            with self.subTest(mismatch=mismatch):
                with self.assertRaisesRegex(CatalogQueryError, "exact released template selection not found"):
                    instantiate(
                        **common,
                        provenance_reference="manifest.md#workbook",
                        **mismatch,
                    )

    def test_instantiation_enforces_authority_and_output_containment(self):
        catalog = load_catalog(self.catalog_path)
        common = {
            "root": self.root,
            "catalog": catalog,
            "template_id": "TMPL-0001",
            "version": "1.2.3",
            "consumer_id": "anna",
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

    def test_instantiation_rejects_symlink_or_junction_escape(self):
        outside = self.package_root.parent / "outside"
        outside.mkdir()
        link = self.package_root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        common = {
            "root": self.root,
            "catalog": load_catalog(self.catalog_path),
            "template_id": "TMPL-0001",
            "version": "1.2.3",
            "consumer_id": "anna",
            "package_root": self.package_root,
            "output_location": Path("linked/working"),
            "provenance_reference": "manifest.md#workbook",
            "manifest_approved": True,
            "write_authorized": True,
            "source_authorized": True,
        }
        with self.assertRaisesRegex(CatalogQueryError, "escapes package root"):
            instantiate(**common)

    def test_validate_detects_native_file_drift(self):
        catalog = load_catalog(self.catalog_path)
        (self.root / self.catalog_entry["native_assets"][0]).write_bytes(b"tampered")
        with self.assertRaisesRegex(CatalogQueryError, "hash does not match"):
            validate_release(self.root, catalog["templates"][0])

    def test_cli_uses_root_canonical_catalog_by_default(self):
        real_catalog = ROOT / "library/catalog.json"
        real_validator = ROOT / "scripts/validate_library.py"
        smoke_root = ROOT if real_catalog.is_file() and real_validator.is_file() else self.root
        command = [
            sys.executable,
            str(ROOT / "integrations/query_catalog.py"),
            "--root",
            str(smoke_root),
            "discover",
            "--consumer-id",
            "anna",
            "--artifact-type",
            "xlsx",
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        catalog = load_catalog(smoke_root / "library/catalog.json")
        expected_count = sum(
            entry.get("release_status") == "released"
            and entry.get("artifact_type") == "xlsx"
            and "anna" in entry.get("supported_consumers", [])
            for entry in catalog["templates"]
        )
        self.assertEqual(json.loads(completed.stdout)["count"], expected_count)


if __name__ == "__main__":
    unittest.main()
