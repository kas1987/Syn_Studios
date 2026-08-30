import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integrations.query_catalog import CatalogQueryError, diversity_fingerprint, recommend
from scripts.validate_library import validate_repository


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def requirements(**overrides):
    value = {
        "schema_version": "1.0.0",
        "consumer_id": "anna",
        "compatibility": {},
        "facets": {},
        "recent_window": 5,
    }
    value.update(overrides)
    return value


class SubmissionProfileValidatorTests(unittest.TestCase):
    def copy_repository(self, temporary: str) -> Path:
        target = Path(temporary) / "repository"
        shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        return target

    def test_profiles_are_valid_and_raw_artifact_bytes_are_rejected(self):
        findings, _ = validate_repository(ROOT)
        self.assertEqual(findings, [])
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(temporary)
            (root / "library/submissions/SUB-005/copied-source.xlsx").write_bytes(b"submission bytes")
            findings, _ = validate_repository(root)
            self.assertIn("raw artifact bytes are forbidden", "\n".join(findings))

    def test_schema_owns_required_cross_sector_vocabulary(self):
        schema = json.loads((ROOT / "schemas/submission-profile.schema.json").read_text(encoding="utf-8"))
        definitions = schema["$defs"]
        self.assertTrue({"public_company", "private_company", "individual"}.issubset(definitions["organization_form"]["enum"]))
        self.assertTrue({"fasb_asc", "irs", "internal_policy", "none"}.issubset(definitions["authority_family"]["enum"]))
        self.assertTrue({"statutory", "other_approved"}.issubset(definitions["reporting_basis"]["enum"]))

    def test_schema_valid_facets_must_still_join_the_bound_blueprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(temporary)
            path = root / "library/submissions/SUB-005/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["templates"][0]["facets"]["authority_class"] = ["authoritative"]
            profile["templates"][0]["diversity_fingerprint"] = diversity_fingerprint(
                profile["templates"][0], root
            )
            write_json(path, profile)
            findings, _ = validate_repository(root)
            self.assertIn("must exactly match blueprint authority class", "\n".join(findings))

    def test_profile_hash_joins_are_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(temporary)
            path = root / "library/submissions/SUB-006/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["templates"][0]["foundation_cards"][0]["sha256"] = "0" * 64
            write_json(path, profile)
            findings, _ = validate_repository(root)
            self.assertIn("hash does not match", "\n".join(findings))

    def test_diversity_fingerprint_is_derived(self):
        for path in sorted((ROOT / "library/submissions").glob("SUB-*/profile.json")):
            profile = json.loads(path.read_text(encoding="utf-8"))
            for template in profile["templates"]:
                self.assertEqual(
                    template["diversity_fingerprint"],
                    diversity_fingerprint(template, ROOT),
                )

    def test_diversity_fingerprint_excludes_foundation_lineage_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(temporary)
            profile = json.loads(
                (root / "library/submissions/SUB-005/profile.json").read_text(encoding="utf-8")
            )
            original = profile["templates"][0]
            alternate = json.loads(json.dumps(original))
            source_card = root / original["foundation_cards"][0]["path"]
            alternate_card = root / "library/foundations/FOUND-0099.json"
            card = json.loads(source_card.read_text(encoding="utf-8"))
            card["card_id"] = "FOUND-0099"
            write_json(alternate_card, card)
            alternate["foundation_cards"][0]["card_id"] = "FOUND-0099"
            alternate["foundation_cards"][0]["path"] = "library/foundations/FOUND-0099.json"
            for invariant in alternate["pattern_invariants"]:
                invariant["foundation_card_id"] = "FOUND-0099"

            self.assertEqual(
                diversity_fingerprint(original, root),
                diversity_fingerprint(alternate, root),
            )

    def test_diversity_fingerprint_uses_unique_semantic_ids_not_pattern_wording(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(temporary)
            profile = json.loads(
                (root / "library/submissions/SUB-005/profile.json").read_text(encoding="utf-8")
            )
            original = profile["templates"][0]
            changed = json.loads(json.dumps(original))
            original_fingerprint = diversity_fingerprint(original, root)
            card_path = root / original["foundation_cards"][0]["path"]
            card = json.loads(card_path.read_text(encoding="utf-8"))
            card["reuse"]["patterns"][0] = "A reviewed paraphrase of the same module-separated source-population structure."
            write_json(card_path, card)
            changed["pattern_invariants"].append(dict(changed["pattern_invariants"][0]))

            self.assertEqual(
                original_fingerprint,
                diversity_fingerprint(changed, root),
            )
            changed["pattern_invariants"][0]["semantic_pattern_id"] = (
                "controlled_record_lifecycle_distribution_and_retention"
            )
            self.assertNotEqual(original_fingerprint, diversity_fingerprint(changed, root))

    def test_profile_rejects_duplicate_semantic_pattern_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(temporary)
            path = root / "library/submissions/SUB-005/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            invariants = profile["templates"][0]["pattern_invariants"]
            invariants[1]["semantic_pattern_id"] = invariants[0]["semantic_pattern_id"]
            profile["templates"][0]["diversity_fingerprint"] = diversity_fingerprint(
                profile["templates"][0], root
            )
            write_json(path, profile)

            findings, _ = validate_repository(root)

            self.assertIn("must be unique within one profiled template", "\n".join(findings))

    def test_semantic_pattern_id_must_match_its_reviewed_meaning_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(temporary)
            path = root / "library/submissions/SUB-006/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            template = profile["templates"][0]
            template["pattern_invariants"][0]["semantic_pattern_id"] = (
                "controlled_record_lifecycle_distribution_and_retention"
            )
            template["diversity_fingerprint"] = diversity_fingerprint(template, root)
            write_json(path, profile)

            findings, _ = validate_repository(root)

            self.assertIn("does not match its reviewed semantic binding", "\n".join(findings))

    @unittest.skipUnless(
        sys.platform == "win32" and hasattr(Path, "is_junction"),
        "NTFS junction proof",
    )
    def test_submission_profile_junction_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(temporary)
            profile_directory = root / "library/submissions/SUB-006"
            held = root / "held-SUB-006"
            outside = Path(temporary) / "outside-SUB-006"
            shutil.copytree(profile_directory, outside)
            profile_directory.rename(held)
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(profile_directory), str(outside)],
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                held.rename(profile_directory)
                self.skipTest(f"junction creation unavailable: {completed.stderr.strip()}")
            try:
                findings, _ = validate_repository(root)
                self.assertIn("symbolic links or junctions", "\n".join(findings))
                with self.assertRaisesRegex(CatalogQueryError, "canonical library validation failed"):
                    recommend(requirements(), [], repository_root=root)
            finally:
                profile_directory.rmdir()
                held.rename(profile_directory)

    def test_profile_prose_cannot_smuggle_source_locator_or_identity(self):
        unsafe_values = ("Sub_005/source/workbook.xlsx", "Acme Source Company")
        for unsafe in unsafe_values:
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temporary:
                root = self.copy_repository(temporary)
                path = root / "library/submissions/SUB-005/profile.json"
                profile = json.loads(path.read_text(encoding="utf-8"))
                profile["templates"][0]["transformation_obligations"][0] = unsafe
                write_json(path, profile)

                findings, _ = validate_repository(root)

                self.assertTrue(findings)
                if unsafe.startswith("Sub_"):
                    self.assertIn("private or absolute source locators are forbidden", "\n".join(findings))

    def test_conflicting_diversity_dimensions_are_not_broadened(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(temporary)
            path = root / "library/submissions/SUB-005/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            template = profile["templates"][0]
            template["diversity_dimensions"]["medium"] = "eml_native"
            template["diversity_fingerprint"] = diversity_fingerprint(template, root)
            write_json(path, profile)
            findings, _ = validate_repository(root)
            self.assertIn("must agree with the controlled medium facet", "\n".join(findings))


class RecommendationInterfaceTests(unittest.TestCase):
    def call(self, request=None, history=None, root=ROOT):
        with mock.patch("integrations.query_catalog._canonical_validate_repository"):
            return recommend(request or requirements(), history or [], repository_root=root)

    def test_unprofiled_releases_remain_outside_recommendation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repository"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
            path = root / "library/submissions/SUB-005/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["templates"] = [item for item in profile["templates"] if item["template_id"] != "TMPL-0003"]
            write_json(path, profile)
            result = self.call(root=root)
            self.assertEqual(result["count"], 2)
            self.assertNotIn("TMPL-0003", {item["template_id"] for item in result["recommendations"]})

    def test_only_reviewed_profiles_are_recommendation_eligible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repository"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
            path = root / "library/submissions/SUB-005/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["status"] = "deprecated"
            write_json(path, profile)
            result = self.call(root=root)
            self.assertEqual([item["template_id"] for item in result["recommendations"]], ["TMPL-0002"])
            self.assertEqual(result["recommendations"][0]["profile_status"], "reviewed")

    def test_selectable_unprofiled_history_is_tolerated_without_invented_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repository"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
            path = root / "library/submissions/SUB-005/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["templates"] = [item for item in profile["templates"] if item["template_id"] != "TMPL-0003"]
            write_json(path, profile)
            result = self.call(
                history=[{"template_id": "TMPL-0003", "version": "1.0.0"}],
                root=root,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["unprofiled_history_count_considered"], 1)
            self.assertTrue(all(item["lineage_reuse_count"] == 0 for item in result["recommendations"]))

    def test_ranking_is_deterministic_and_penalizes_submission_lineage(self):
        history = [{"template_id": "TMPL-0001", "version": "1.0.0"}]
        first = self.call(history=history)
        second = self.call(history=history)
        self.assertEqual(first, second)
        self.assertEqual([item["template_id"] for item in first["recommendations"]], ["TMPL-0002", "TMPL-0003"])
        self.assertIn("submission_lineage_repeated", first["recommendations"][1]["reason_codes"])

    def test_consecutive_exact_reuse_is_blocked_by_default(self):
        request = requirements(facets={"artifact_family": "close_workbook"})
        history = [{"template_id": "TMPL-0001", "version": "1.0.0"}]
        result = self.call(request, history)
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["reason_codes"], ["reuse_policy_blocked_all_candidates"])

    def test_recent_exact_reuse_requires_a_material_plan(self):
        request = requirements(facets={"artifact_family": "close_workbook"})
        history = [
            {"template_id": "TMPL-0001", "version": "1.0.0"},
            {"template_id": "TMPL-0002", "version": "1.0.0"},
        ]
        self.assertEqual(self.call(request, history)["status"], "no_match")
        request["material_transformation_plan"] = {
            "target": history[0],
            "change_kinds": ["substantive_footprint"],
        }
        result = self.call(request, history)
        self.assertEqual(result["recommendations"][0]["template_id"], "TMPL-0001")
        recommendation = result["recommendations"][0]
        self.assertIn("exact_reuse_material_transformation_planned", recommendation["reason_codes"])
        self.assertEqual(recommendation["material_transformation"]["status"], "planned_not_validated")
        self.assertEqual(recommendation["material_transformation"]["plan"], request["material_transformation_plan"])
        self.assertEqual(
            recommendation["material_transformation"]["completion_owner"],
            "consumer_candidate_artifact_validation",
        )

    def test_cosmetic_only_plan_does_not_qualify(self):
        target = {"template_id": "TMPL-0001", "version": "1.0.0"}
        request = requirements(
            facets={"artifact_family": "close_workbook"},
            material_transformation_plan={"target": target, "change_kinds": ["rename", "recolor", "layout_polish"]},
        )
        history = [target, {"template_id": "TMPL-0002", "version": "1.0.0"}]
        self.assertEqual(self.call(request, history)["status"], "no_match")

    def test_fingerprint_reuse_is_reported_and_penalized(self):
        target = {"template_id": "TMPL-0003", "version": "1.0.0"}
        request = requirements(
            material_transformation_plan={"target": target, "change_kinds": ["document_composition"]}
        )
        history = [target, {"template_id": "TMPL-0001", "version": "1.0.0"}]
        result = self.call(request, history)
        repeated = next(item for item in result["recommendations"] if item["template_id"] == "TMPL-0003")
        self.assertEqual(repeated["fingerprint_reuse_count"], 1)
        self.assertIn("diversity_fingerprint_repeated", repeated["reason_codes"])
        self.assertLess(
            next(item["rank"] for item in result["recommendations"] if item["template_id"] == "TMPL-0002"),
            repeated["rank"],
        )

    def test_consecutive_exact_reuse_stays_blocked_with_a_material_plan(self):
        target = {"template_id": "TMPL-0001", "version": "1.0.0"}
        request = requirements(
            material_transformation_plan={"target": target, "change_kinds": ["producer_workflow"]},
            facets={"artifact_family": "close_workbook"},
        )
        result = self.call(request, [target])
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["reason_codes"], ["reuse_policy_blocked_all_candidates"])

    def test_last_resort_override_surface_is_rejected(self):
        target = {"template_id": "TMPL-0001", "version": "1.0.0"}
        request = requirements(
            no_compatible_alternative_override={
                "target": target,
                "rationale": "unsupported",
                "evidence_references": ["does-not-exist#x"],
            },
        )
        with self.assertRaisesRegex(CatalogQueryError, "Additional properties are not allowed"):
            self.call(request, [target])

    def test_unknown_history_identity_fails_input_validation(self):
        with self.assertRaisesRegex(CatalogQueryError, "unknown selectable template identity"):
            self.call(history=[{"template_id": "TMPL-9999", "version": "1.0.0"}])

    def test_request_is_closed_and_has_no_caller_score(self):
        request = requirements(score=99)
        with self.assertRaisesRegex(CatalogQueryError, "Additional properties are not allowed"):
            self.call(request)

    def test_all_three_consumers_receive_profiled_exact_versions(self):
        for consumer_id in ("anna", "holodeck-file-generation", "human-artifact-realism"):
            with self.subTest(consumer_id=consumer_id):
                result = self.call(requirements(consumer_id=consumer_id))
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["count"], 3)
                self.assertTrue(all(item["next_operation"] == "select" for item in result["recommendations"]))

    def test_public_gaap_fasb_facets_resolve_without_collapsing_dimensions(self):
        request = requirements(
            facets={
                "organization_form": "public_company",
                "reporting_basis": "us_gaap",
                "authority_family": "fasb_asc",
            }
        )
        result = self.call(request)
        self.assertEqual({item["template_id"] for item in result["recommendations"]}, {"TMPL-0001", "TMPL-0002"})

    def test_controlled_sector_authority_and_basis_contexts_are_compilable(self):
        contexts = (
            {"organization_form": "nonprofit", "reporting_basis": "us_gaap", "authority_family": "fasb_asc"},
            {"organization_form": "private_company", "reporting_basis": "tax_basis", "authority_family": "irs", "business_function": "tax"},
            {"organization_form": "public_company", "reporting_basis": "regulatory_basis", "authority_family": "sec"},
            {"organization_form": "government", "reporting_basis": "statutory", "authority_family": "gasb"},
        )
        for facets in contexts:
            with self.subTest(facets=facets):
                result = self.call(requirements(facets=facets))
                self.assertEqual(result["status"], "ok")
                self.assertEqual({item["template_id"] for item in result["recommendations"]}, {"TMPL-0001", "TMPL-0002"})
                self.assertTrue(all(item["profile_status"] == "reviewed" for item in result["recommendations"]))

    def test_each_sector_and_authority_facet_independently_filters_candidates(self):
        cases = (
            ({"organization_form": "individual"}, {"TMPL-0003"}),
            ({"reporting_basis": "cash_basis"}, {"TMPL-0001"}),
            ({"authority_family": "none"}, {"TMPL-0003"}),
            ({"authority_class": "contextual"}, {"TMPL-0003"}),
        )
        for facets, expected in cases:
            with self.subTest(facets=facets):
                result = self.call(requirements(facets=facets))
                self.assertEqual(result["status"], "ok")
                self.assertEqual(
                    {item["template_id"] for item in result["recommendations"]},
                    expected,
                )

        authoritative = self.call(requirements(facets={"authority_class": "authoritative"}))
        self.assertEqual(authoritative["status"], "no_match")
        self.assertEqual(authoritative["reason_codes"], ["no_compatible_profiled_template"])

    def test_recommend_cli_is_a_thin_json_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            requirements_path = Path(temporary) / "requirements.json"
            recent_path = Path(temporary) / "recent.json"
            write_json(requirements_path, requirements(consumer_id="holodeck-file-generation"))
            write_json(recent_path, [])
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "integrations/query_catalog.py"),
                    "recommend",
                    "--requirements",
                    str(requirements_path),
                    "--recent-usage",
                    str(recent_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["operation"], "recommend")
            self.assertEqual(result["count"], 3)


if __name__ == "__main__":
    unittest.main()
