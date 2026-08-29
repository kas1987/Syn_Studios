import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.assemble_template_releases import AssemblyRefused, assemble
from scripts.validate_library import validate_repository


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class ReleaseAssemblyTests(unittest.TestCase):
    actor_arguments = {
        "builder_id": "release:builder",
        "builder_name": "Release Builder",
        "sanitizer_id": "release:sanitizer",
        "sanitizer_name": "Sanitization Reviewer",
        "technical_id": "release:technical",
        "technical_name": "Technical Validator",
        "conductor_id": "release:conductor",
        "conductor_name": "Release Conductor",
    }

    def make_root(self, temporary):
        root = Path(temporary)
        for relative in ("schemas", "library", "examples/blueprints", "evidence/reports", "evidence/template-releases", "tests/fixtures"):
            source = ROOT / relative
            if source.exists():
                shutil.copytree(source, root / relative)
        catalog = json.loads((root / "library/catalog.json").read_text(encoding="utf-8"))
        for index, entry in enumerate(catalog["templates"], 1):
            descriptor_path = root / entry["descriptor"]
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            descriptor["release_status"] = "released"
            contract = descriptor["render_contract"]
            if contract["required"]:
                release_id = f"REL-{index:04d}"
                old_pdf = root / contract["expected_pdf_path"]
                old_pattern = contract["expected_page_image_pattern"]
                release_root = root / "evidence/template-releases" / release_id
                render_root = release_root / "render"
                render_root.mkdir(parents=True, exist_ok=True)
                already_release_owned = old_pdf.resolve() == release_root.resolve() or release_root.resolve() in old_pdf.resolve().parents
                pdf = old_pdf if already_release_owned else render_root / "render.pdf"
                if not already_release_owned:
                    shutil.copy2(old_pdf, pdf)
                pages = []
                for page in range(1, contract["expected_page_count"] + 1):
                    source = root / old_pattern.format(page=page)
                    target = source if already_release_owned else render_root / f"page-{page}.png"
                    if not already_release_owned:
                        shutil.copy2(source, target)
                    pages.append(target)
                if not already_release_owned:
                    contract["evidence_manifest"] = f"evidence/template-releases/{release_id}/render-manifest.json"
                    contract["expected_pdf_path"] = pdf.relative_to(root).as_posix()
                    contract["expected_page_image_pattern"] = (render_root / "page-{page}.png").relative_to(root).as_posix()
                write_json(descriptor_path, descriptor)
                asset = descriptor["native_assets"][0]
                outputs = [pdf, *pages]
                write_json(root / contract["evidence_manifest"], {
                    "schema_version": "1.0.0",
                    "templates": {descriptor["template_id"]: {
                        "asset_path": asset["path"], "asset_sha256": asset["sha256"],
                        "page_count": contract["expected_page_count"], "sheet_names": contract["expected_sheet_names"],
                        "rendered_outputs": [{"path": path.relative_to(root).as_posix(), "sha256": sha256(path)} for path in outputs],
                    }},
                })
            else:
                write_json(descriptor_path, descriptor)
        return root

    def add_reviews(self, root, *, terra_actor="reviewer:terra", sol_actor="reviewer:sol"):
        catalog = json.loads((root / "library/catalog.json").read_text(encoding="utf-8"))
        for index, entry in enumerate(catalog["templates"], 1):
            release_id = f"REL-{index:04d}"
            descriptor_path = root / entry["descriptor"]
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            descriptor_hash = sha256(descriptor_path)
            asset_hashes = [item["sha256"] for item in descriptor["native_assets"]]
            release_root = root / "evidence/template-releases" / release_id
            for lane, record_type, verdict, actor_id, actor_name in (
                ("terra", "terra_review", "USABILITY_PASS", terra_actor, "Terra Reviewer"),
                ("sol", "sol_review", "INTEGRITY_PASS", sol_actor, "Sol Reviewer"),
            ):
                proof = release_root / "proofs" / f"{lane}.txt"
                proof.parent.mkdir(parents=True, exist_ok=True)
                proof.write_text(
                    f"release_id={release_id}\ntemplate_id={entry['template_id']}\ncategory=provenance\n"
                    + "".join(f"native_asset_sha256={value}\n" for value in asset_hashes)
                    + f"observation=Independent {lane} review verified the frozen descriptor and proof set.\n",
                    encoding="utf-8",
                )
                write_json(release_root / f"{lane}.json", {
                    "schema_version": "1.0.0", "record_id": f"EVID-{release_id}-{lane.upper()}",
                    "record_type": record_type, "release_id": release_id, "template_id": entry["template_id"],
                    "version": entry["version"], "descriptor_sha256": descriptor_hash,
                    "native_asset_sha256s": asset_hashes, "verdict": verdict,
                    "actor_id": actor_id, "actor": actor_name,
                    "observations": [f"Independent {lane} review inspected the exact frozen template hashes."],
                    "artifacts": [{"path": proof.relative_to(root).as_posix(), "sha256": sha256(proof), "media_type": "text/plain", "category": "provenance"}],
                    "summary": f"Independent {lane} review passed for this frozen template version.",
                })

    def assemble(self, root, **overrides):
        arguments = dict(self.actor_arguments)
        arguments.update(overrides)
        return assemble(root, approved=True, **arguments)

    def test_refuses_without_exact_terra_and_sol_records_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary)
            catalog_before = (root / "library/catalog.json").read_bytes()
            with self.assertRaisesRegex(AssemblyRefused, "missing required terra review"):
                self.assemble(root)
            self.assertEqual((root / "library/catalog.json").read_bytes(), catalog_before)
            self.assertFalse((root / "library/releases").exists())

    def test_refuses_review_hash_drift_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary)
            self.add_reviews(root)
            terra_path = root / "evidence/template-releases/REL-0001/terra.json"
            terra = json.loads(terra_path.read_text(encoding="utf-8"))
            terra["descriptor_sha256"] = "0" * 64
            write_json(terra_path, terra)
            with self.assertRaisesRegex(AssemblyRefused, "descriptor_sha256 does not match"):
                self.assemble(root)
            self.assertFalse((root / "library/releases").exists())

    def test_refuses_review_proof_hash_drift_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary)
            self.add_reviews(root)
            proof = root / "evidence/template-releases/REL-0001/proofs/terra.txt"
            proof.write_text(proof.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
            with self.assertRaisesRegex(AssemblyRefused, "hash does not match its proof artifact"):
                self.assemble(root)
            self.assertFalse((root / "library/releases").exists())

    def test_refuses_normalized_actor_reuse_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary)
            self.add_reviews(root)
            with self.assertRaisesRegex(AssemblyRefused, "actor aliases"):
                self.assemble(root, conductor_id=" REVIEWER:TERRA ")
            self.assertFalse((root / "library/releases").exists())

    def test_requires_explicit_conductor_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary)
            self.add_reviews(root)
            with self.assertRaisesRegex(AssemblyRefused, "explicit conductor approval"):
                assemble(root, approved=False, **self.actor_arguments)

    def test_happy_path_is_deterministic_preserves_reviews_and_validates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary)
            self.add_reviews(root)
            review_paths = sorted((root / "evidence/template-releases").glob("REL-*/terra.json")) + sorted((root / "evidence/template-releases").glob("REL-*/sol.json"))
            review_bytes = {path: path.read_bytes() for path in review_paths}
            first = self.assemble(root)
            first_bytes = {path.relative_to(root): path.read_bytes() for path in first}
            second = self.assemble(root)
            second_bytes = {path.relative_to(root): path.read_bytes() for path in second}
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(review_bytes, {path: path.read_bytes() for path in review_paths})
            self.assertEqual(sorted(path.name for path in (root / "library/releases").glob("*.json")), ["REL-0001.template.json", "REL-0002.template.json", "REL-0003.template.json"])
            catalog = json.loads((root / "library/catalog.json").read_text(encoding="utf-8"))
            self.assertTrue(all(entry["release_status"] == "released" and "release_record" in entry for entry in catalog["templates"]))
            findings, _ = validate_repository(root)
            self.assertEqual(findings, [], "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
