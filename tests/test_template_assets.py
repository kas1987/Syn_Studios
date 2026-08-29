import hashlib
import json
import re
import tempfile
import unittest
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "library/catalog.json"
TOKEN = re.compile(rb"\{\{([a-zA-Z0-9_]+)\}\}")


def load_json(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tokens_in_asset(path):
    if path.suffix.lower() in {".xlsx", ".docx"}:
        with zipfile.ZipFile(path) as archive:
            payload = b" ".join(
                archive.read(name)
                for name in archive.namelist()
                if name.endswith(".xml")
            )
    elif path.suffix.lower() == ".eml":
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        chunks = [str(value).encode("utf-8") for value in message.values()]
        for part in message.walk():
            decoded = part.get_payload(decode=True)
            if decoded:
                chunks.append(decoded)
            if part.get_filename():
                chunks.append(part.get_filename().encode("utf-8"))
        payload = b" ".join(chunks)
    else:
        payload = path.read_bytes()
    return {match.decode("ascii") for match in TOKEN.findall(payload)}


class TemplateAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_json("library/catalog.json")

    def test_catalog_has_three_versioned_discoverable_templates(self):
        entries = self.catalog["templates"]
        self.assertEqual(len(entries), 3)
        self.assertEqual({e["artifact_type"] for e in entries}, {"xlsx", "docx", "eml"})
        self.assertEqual({e["template_id"] for e in entries}, {"TMPL-0001", "TMPL-0002", "TMPL-0003"})
        required_facets = {"artifact_type", "authority", "lifecycle", "capabilities"}
        self.assertTrue(required_facets.issubset(self.catalog["discovery_fields"]))
        for entry in entries:
            self.assertRegex(entry["version"], r"^[0-9]+\.[0-9]+\.[0-9]+$")
            self.assertIn(entry["release_status"], {"candidate", "released"})
            self.assertTrue(entry["capabilities"])
            self.assertEqual(set(entry["supported_consumers"]), {"anna", "holodeck-file-generation", "human-artifact-realism"})

    def test_descriptors_and_assets_resolve_without_path_escape(self):
        for entry in self.catalog["templates"]:
            paths = [entry["descriptor"], *entry["native_assets"]]
            for relative in paths:
                pure = PurePosixPath(relative)
                self.assertFalse(pure.is_absolute(), relative)
                self.assertNotIn("..", pure.parts, relative)
                self.assertTrue((ROOT / relative).is_file(), relative)
            descriptor = load_json(entry["descriptor"])
            self.assertEqual(descriptor["template_id"], entry["template_id"])
            self.assertEqual(descriptor["version"], entry["version"])
            self.assertEqual(descriptor["lineage"]["blueprint_id"], entry["blueprint_id"])
            self.assertTrue(descriptor["lineage"]["foundation_ids"])
            for foundation_id in descriptor["lineage"]["foundation_ids"]:
                self.assertTrue((ROOT / f"library/foundations/{foundation_id}.json").is_file())

    def test_native_asset_hashes_match_descriptors(self):
        for entry in self.catalog["templates"]:
            descriptor = load_json(entry["descriptor"])
            self.assertEqual(len(descriptor["native_assets"]), len(entry["native_assets"]))
            for asset in descriptor["native_assets"]:
                path = ROOT / asset["path"]
                self.assertEqual(sha256(path), asset["sha256"], asset["path"])

    def test_hash_gate_detects_single_byte_sabotage(self):
        descriptor = load_json("library/templates/TMPL-0003/1.0.0/template.json")
        asset = descriptor["native_assets"][0]
        original = ROOT / asset["path"]
        with tempfile.TemporaryDirectory() as directory:
            sabotaged = Path(directory) / original.name
            sabotaged.write_bytes(original.read_bytes() + b"\x00")
            self.assertNotEqual(sha256(sabotaged), asset["sha256"])

    def test_every_embedded_build_token_is_declared(self):
        for entry in self.catalog["templates"]:
            descriptor = load_json(entry["descriptor"])
            declared = set(descriptor["slots"])
            observed = set()
            for asset in descriptor["native_assets"]:
                observed.update(tokens_in_asset(ROOT / asset["path"]))
            self.assertEqual(observed, declared, entry["template_id"])

    def test_required_vertical_slice_capabilities(self):
        required = {
            "TMPL-0001": {"native_xlsx", "formula_reconciliation", "exception_log", "control_checks", "renderable"},
            "TMPL-0002": {"native_docx", "issue_analysis", "risk_and_sensitivity", "open_items", "renderable"},
            "TMPL-0003": {"native_eml", "multipart_mime", "quoted_thread", "attachment_correction", "machine_parseable"},
        }
        for entry in self.catalog["templates"]:
            self.assertTrue(required[entry["template_id"]].issubset(entry["capabilities"]))

    def test_xlsx_has_expected_layers_and_formula_controls(self):
        path = ROOT / "library/templates/TMPL-0001/1.0.0/internal-close-reconciliation.xlsx"
        with zipfile.ZipFile(path) as archive:
            workbook_xml = archive.read("xl/workbook.xml")
            formulas = b" ".join(archive.read(name) for name in archive.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml"))
        for name in (b"Instructions", b"Source_Data", b"Account_Map", b"Reconciliation", b"Proposed_Entries", b"Prior_Period", b"Exceptions", b"Checks"):
            self.assertIn(name, workbook_xml)
        self.assertIn(b"SUMIFS", formulas)
        self.assertIn(b"COUNTIF", formulas)
        self.assertIn(b"{{organization_name}}", b" ".join(zipfile.ZipFile(path).read(n) for n in zipfile.ZipFile(path).namelist() if n.endswith(".xml")))

    def test_docx_has_five_renderable_sections_and_no_approval_claim(self):
        path = ROOT / "library/templates/TMPL-0002/1.0.0/internal-controller-memo.docx"
        with zipfile.ZipFile(path) as archive:
            document = archive.read("word/document.xml")
            core = archive.read("docProps/core.xml")
        self.assertGreaterEqual(document.count(b'w:type="page"'), 4)
        self.assertIn(b"NOT APPROVED", document)
        self.assertNotIn(b"signature", core.lower())

    def test_eml_is_multipart_with_two_openable_attachments(self):
        path = ROOT / "library/templates/TMPL-0003/1.0.0/operational-correction-thread.eml"
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        self.assertTrue(message.is_multipart())
        self.assertEqual(message["X-Syn-Studios-Template"], "TMPL-0003@1.0.0")
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 2)
        self.assertEqual({part.get_content_type() for part in attachments}, {"text/csv", "text/plain"})
        for part in attachments:
            self.assertTrue(part.get_payload(decode=True))

    def test_mixed_manifest_references_exact_catalog_versions(self):
        manifest = load_json("examples/manifests/mixed-controller-review-packet.json")
        catalog_pairs = {(item["template_id"], item["version"]) for item in self.catalog["templates"]}
        manifest_pairs = {(item["template_id"], item["version"]) for item in manifest["components"]}
        self.assertEqual(manifest_pairs, catalog_pairs)
        self.assertTrue(manifest["required_additional_components"])


if __name__ == "__main__":
    unittest.main()
