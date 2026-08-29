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
FORBIDDEN_NATIVE_RESIDUE = (
    b"syn studios",
    b"syn-studios",
    b"template",
    b"build-time",
    b"double-brace",
    b"instantiation gate",
    b"generator residue",
    b"template.invalid",
    b"2013-12-23",
)


def load_json(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_visible_payload(path):
    if path.suffix.lower() == ".xlsx":
        with zipfile.ZipFile(path) as archive:
            return b" ".join(
                archive.read(name)
                for name in archive.namelist()
                if name.startswith("xl/worksheets/")
                or name in {"xl/sharedStrings.xml", "xl/workbook.xml", "docProps/core.xml", "docProps/app.xml"}
            )
    if path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as archive:
            return b" ".join(
                archive.read(name)
                for name in archive.namelist()
                if name == "word/document.xml"
                or name.startswith("word/header")
                or name.startswith("word/footer")
                or name.startswith("word/comments")
                or name in {"docProps/core.xml", "docProps/app.xml"}
            )
    if path.suffix.lower() == ".eml":
        raw = path.read_bytes()
        message = BytesParser(policy=policy.default).parsebytes(raw)
        chunks = [raw, *[str(value).encode("utf-8") for value in message.values()]]
        for part in message.walk():
            decoded = part.get_payload(decode=True)
            if decoded:
                chunks.append(decoded)
            if part.get_filename():
                chunks.append(part.get_filename().encode("utf-8"))
        return b" ".join(chunks)
    return path.read_bytes()


def tokens_in_asset(path):
    return {match.decode("ascii") for match in TOKEN.findall(model_visible_payload(path))}


def release_binding_valid(entry):
    if entry.get("release_status") != "released":
        return True
    relative = entry.get("release_record")
    if not isinstance(relative, str):
        return False
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        return False
    path = ROOT / relative
    if not path.is_file():
        return False
    release = json.loads(path.read_text(encoding="utf-8"))
    descriptor = load_json(entry["descriptor"])
    assets = {asset["path"]: asset for asset in descriptor["native_assets"]}
    template = release.get("template", {})
    blueprint = release.get("blueprint", {})
    return all(
        (
            release.get("status") == "released",
            release.get("version") == entry["version"],
            blueprint.get("blueprint_id") == entry["blueprint_id"],
            template.get("artifact_type") == entry["artifact_type"],
            template.get("path") in entry["native_assets"],
            template.get("path") in assets,
            assets.get(template.get("path"), {}).get("sha256") == template.get("sha256"),
        )
    )


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
            self.assertTrue(release_binding_valid(entry), entry["template_id"])

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
            self.assertEqual(descriptor["artifact_type"], entry["artifact_type"])
            self.assertEqual(descriptor["authority"], entry["authority"])
            self.assertEqual(descriptor["lifecycle"], entry["lifecycle"])
            self.assertEqual(descriptor["release_status"], entry["release_status"])
            self.assertEqual(descriptor["capabilities"], entry["capabilities"])
            self.assertEqual(descriptor["supported_consumers"], entry["supported_consumers"])
            self.assertEqual([asset["path"] for asset in descriptor["native_assets"]], entry["native_assets"])
            self.assertTrue(descriptor["lineage"]["foundation_ids"])
            for foundation_id in descriptor["lineage"]["foundation_ids"]:
                self.assertTrue((ROOT / f"library/foundations/{foundation_id}.json").is_file())

    def test_descriptor_lineage_matches_repaired_blueprint_lineage(self):
        expected = {
            "TMPL-0001": ("BP-0001", ["FOUND-0001"]),
            "TMPL-0002": ("BP-0002", ["FOUND-0005"]),
            "TMPL-0003": ("BP-0003", ["FOUND-0008"]),
        }
        for entry in self.catalog["templates"]:
            descriptor = load_json(entry["descriptor"])
            blueprint_id, foundation_ids = expected[entry["template_id"]]
            self.assertEqual(descriptor["lineage"]["blueprint_id"], blueprint_id)
            self.assertEqual(descriptor["lineage"]["foundation_ids"], foundation_ids)
            self.assertIn("#foundation_lineage", descriptor["lineage"]["lineage_source"])

    def test_catalog_sabotage_cannot_self_promote_candidate(self):
        entry = dict(self.catalog["templates"][0])
        entry["release_status"] = "released"
        self.assertFalse(release_binding_valid(entry))

    def test_catalog_authority_sabotage_breaks_descriptor_binding(self):
        entry = dict(self.catalog["templates"][0])
        entry["authority"] = "authoritative"
        descriptor = load_json(entry["descriptor"])
        self.assertNotEqual(entry["authority"], descriptor["authority"])

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

    def test_native_assets_contain_no_library_or_build_residue(self):
        for entry in self.catalog["templates"]:
            for relative in entry["native_assets"]:
                payload = model_visible_payload(ROOT / relative).lower()
                for residue in FORBIDDEN_NATIVE_RESIDUE:
                    self.assertNotIn(residue, payload, f"{relative}: {residue!r}")

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
        for name in (b"Close_Control", b"Source_Data", b"Account_Map", b"Reconciliation", b"Proposed_Entries", b"Prior_Period", b"Exceptions", b"Checks"):
            self.assertIn(name, workbook_xml)
        self.assertEqual(workbook_xml.count(b"_xlnm.Print_Area"), 8)
        self.assertEqual(formulas.count(b"fitToWidth=\"1\""), 8)
        self.assertGreaterEqual(formulas.count(b"<pane"), 7)
        self.assertGreaterEqual(formulas.count(b"tablePart"), 6)
        self.assertIn(b"SUMIFS", formulas)
        self.assertIn(b"COUNTIF", formulas)
        with zipfile.ZipFile(path) as archive:
            all_xml = b" ".join(archive.read(name) for name in archive.namelist() if name.endswith(".xml"))
        self.assertIn(b"{{organization_name}}", all_xml)

    def test_docx_has_five_renderable_sections_and_no_approval_claim(self):
        path = ROOT / "library/templates/TMPL-0002/1.0.0/internal-controller-memo.docx"
        with zipfile.ZipFile(path) as archive:
            document = archive.read("word/document.xml")
            core = archive.read("docProps/core.xml")
            app = archive.read("docProps/app.xml")
        self.assertGreaterEqual(document.count(b'w:type="page"'), 4)
        self.assertIn(b"NOT APPROVED", document)
        self.assertNotIn(b"dcterms:created", core)
        self.assertNotIn(b"dc:creator", core)
        self.assertNotIn(b"Application", app)

    def test_eml_is_multipart_with_two_openable_attachments(self):
        path = ROOT / "library/templates/TMPL-0003/1.0.0/operational-correction-thread.eml"
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        self.assertTrue(message.is_multipart())
        self.assertFalse(any(name.lower().startswith("x-syn") for name in message.keys()))
        self.assertNotIn("syn", message.get_boundary().lower())
        self.assertNotIn("template", message.get_boundary().lower())
        body = message.get_body(preferencelist=("plain",)).get_content()
        self.assertEqual(body.count("-----Original Message-----") + 1, 6)
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
