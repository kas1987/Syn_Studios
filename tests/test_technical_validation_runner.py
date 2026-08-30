import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.run_template_technical_validation import ValidationFailed, run, sha256


ROOT = Path(__file__).resolve().parents[1]
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class TechnicalValidationRunnerTests(unittest.TestCase):
    actor_id = "syn-validation-runner-2026-08-29"
    actor = "Syn Studios validation runner"

    def copy_release_inputs(self, directory: str) -> Path:
        root = Path(directory).resolve()
        for relative in ("library/releases", "library/templates", "library/foundations", "examples/blueprints"):
            shutil.copytree(ROOT / relative, root / relative)
        evidence = root / "evidence/template-releases"
        for source in sorted((ROOT / "evidence/template-releases").glob("REL-*")):
            target = evidence / source.name
            target.mkdir(parents=True)
            manifest = source / "render-manifest.json"
            if manifest.is_file():
                shutil.copy2(manifest, target / manifest.name)
            if (source / "render").is_dir():
                shutil.copytree(source / "render", target / "render")
        return root

    def release(self, root: Path, release_id: str) -> tuple[Path, dict, Path, dict, Path]:
        release_path = root / f"library/releases/{release_id}.template.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        descriptor_path = root / release["descriptor"]["path"]
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        asset_path = root / descriptor["native_assets"][0]["path"]
        return release_path, release, descriptor_path, descriptor, asset_path

    def rebind_asset(self, root: Path, release_id: str) -> None:
        release_path, release, descriptor_path, descriptor, asset_path = self.release(root, release_id)
        asset_hash = sha256(asset_path)
        descriptor["native_assets"][0]["sha256"] = asset_hash
        write_json(descriptor_path, descriptor)
        release["descriptor"]["sha256"] = sha256(descriptor_path)
        release["native_assets"][0]["sha256"] = asset_hash
        write_json(release_path, release)
        manifest_relative = descriptor["render_contract"].get("evidence_manifest")
        if manifest_relative:
            manifest_path = root / manifest_relative
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["templates"][descriptor["template_id"]]["asset_sha256"] = asset_hash
            write_json(manifest_path, manifest)

    def assert_refused_without_results(self, root: Path) -> None:
        with self.assertRaises(ValidationFailed):
            run(root, self.actor_id, self.actor, write=True)
        self.assertEqual(list(root.glob("evidence/template-releases/REL-*/technical-results/*.json")), [])

    def test_real_release_inputs_dry_run_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            paths = run(root, self.actor_id, self.actor)
            self.assertEqual(len(paths), 24)
            self.assertTrue(all(not path.exists() for path in paths))

    def test_write_creates_24_deterministic_category_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            paths = run(root, self.actor_id, self.actor, write=True)
            self.assertEqual(len(paths), 24)
            first = {path.relative_to(root).as_posix(): path.read_bytes() for path in paths}
            paths = run(root, self.actor_id, self.actor, write=True)
            second = {path.relative_to(root).as_posix(): path.read_bytes() for path in paths}
            self.assertEqual(first, second)
            for path in paths:
                result = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(result["actor_id"], self.actor_id)
                self.assertTrue(result["checks"])
                self.assertTrue(all(item["id"].startswith(result["category"] + ":") for item in result["checks"]))

    def test_invalid_render_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            png = root / "evidence/template-releases/REL-0002/render/internal-controller-memo-page-3.png"
            png.write_bytes(b"not a PNG")
            self.assert_refused_without_results(root)

    def test_missing_formula_cache_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")
            with zipfile.ZipFile(asset) as package:
                members = {info.filename: package.read(info.filename) for info in package.infolist()}
            worksheet = ET.fromstring(members["xl/worksheets/sheet8.xml"])
            formula_cell = next(cell for cell in worksheet.findall(f".//{{{MAIN}}}c") if cell.find(f"{{{MAIN}}}f") is not None)
            formula_cell.remove(formula_cell.find(f"{{{MAIN}}}v"))
            members["xl/worksheets/sheet8.xml"] = ET.tostring(worksheet, encoding="utf-8", xml_declaration=True)
            with zipfile.ZipFile(asset, "w") as package:
                for name, payload in members.items():
                    package.writestr(name, payload)
            self.rebind_asset(root, "REL-0001")
            self.assert_refused_without_results(root)

    def test_external_link_surface_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")
            with zipfile.ZipFile(asset, "a") as package:
                package.writestr("xl/externalLinks/externalLink1.xml", "<externalLink/>")
            self.rebind_asset(root, "REL-0001")
            self.assert_refused_without_results(root)

    def test_prohibited_leakage_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            asset.write_bytes(asset.read_bytes() + b"\nX-Note: private grading residue\n")
            self.rebind_asset(root, "REL-0003")
            self.assert_refused_without_results(root)

    def test_stale_foundation_provenance_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            card = root / "library/foundations/FOUND-0001.json"
            card.write_bytes(card.read_bytes() + b"\n")
            self.assert_refused_without_results(root)


if __name__ == "__main__":
    unittest.main()
