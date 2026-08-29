import hashlib
import json
import re
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET
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


def render_binding_valid(descriptor):
    contract = descriptor["render_contract"]
    if not contract["required"]:
        return contract["evidence_manifest"] is None and contract["expected_page_count"] is None
    manifest = load_json(contract["evidence_manifest"])
    record = manifest["templates"].get(descriptor["template_id"])
    if not isinstance(record, dict) or len(descriptor["native_assets"]) != 1:
        return False
    asset = descriptor["native_assets"][0]
    if record.get("asset_path") != asset["path"] or record.get("asset_sha256") != asset["sha256"]:
        return False
    if record.get("page_count") != contract["expected_page_count"]:
        return False
    if record.get("sheet_names", []) != contract["expected_sheet_names"]:
        return False
    outputs = record.get("rendered_outputs")
    if not isinstance(outputs, list) or len(outputs) != record["page_count"] + 1:
        return False
    return all((ROOT / item["path"]).is_file() and sha256(ROOT / item["path"]) == item["sha256"] for item in outputs)


def table_bindings(path):
    bindings = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("xl/tables/") or not name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.read(name))
            bindings[root.attrib["name"]] = root.attrib["ref"]
    return bindings


def population_binding_valid(descriptor, path):
    observed = table_bindings(path)
    for item in descriptor["population_contract"]["tables"]:
        if observed.get(item["name"]) != item["range"]:
            return False
        first, last = item["range"].split(":")
        first_row = int(re.search(r"[0-9]+$", first).group())
        last_row = int(re.search(r"[0-9]+$", last).group())
        if last_row - first_row != item["maximum_rows"]:
            return False
    return len(observed) == len(descriptor["population_contract"]["tables"])


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
            self.assertEqual(descriptor["blueprint_id"], entry["blueprint_id"])
            self.assertEqual(descriptor["artifact_type"], entry["artifact_type"])
            self.assertEqual(descriptor["authority"], entry["authority"])
            self.assertEqual(descriptor["lifecycle"], entry["lifecycle"])
            self.assertEqual(descriptor["release_status"], entry["release_status"])
            self.assertEqual(descriptor["capabilities"], entry["capabilities"])
            self.assertEqual(descriptor["supported_consumers"], entry["supported_consumers"])
            self.assertEqual([asset["path"] for asset in descriptor["native_assets"]], entry["native_assets"])

    def test_descriptors_reference_blueprints_without_duplicating_lineage(self):
        for entry in self.catalog["templates"]:
            descriptor = load_json(entry["descriptor"])
            self.assertEqual(descriptor["blueprint_id"], entry["blueprint_id"])
            self.assertNotIn("lineage", descriptor)

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

    def test_render_manifest_binds_current_asset_and_every_output_hash(self):
        for entry in self.catalog["templates"]:
            descriptor = load_json(entry["descriptor"])
            self.assertTrue(render_binding_valid(descriptor), entry["template_id"])

    def test_render_evidence_rejects_asset_or_descriptor_drift(self):
        descriptor = load_json("library/templates/TMPL-0002/1.0.0/template.json")
        descriptor["native_assets"][0]["sha256"] = "0" * 64
        self.assertFalse(render_binding_valid(descriptor))

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

    def test_xlsx_population_contract_binds_table_capacity(self):
        descriptor = load_json("library/templates/TMPL-0001/1.0.0/template.json")
        path = ROOT / descriptor["native_assets"][0]["path"]
        self.assertTrue(population_binding_valid(descriptor, path))
        self.assertEqual(descriptor["population_contract"]["capacity_change_policy"], "reject_and_rebuild_with_fresh_formula_and_render_evidence")
        for item in descriptor["population_contract"]["tables"]:
            self.assertGreaterEqual(item["maximum_rows"], item["minimum_rows"])
            self.assertTrue(item["columns"])

    def test_xlsx_population_expansion_without_rebuild_is_rejected(self):
        descriptor = load_json("library/templates/TMPL-0001/1.0.0/template.json")
        original = ROOT / descriptor["native_assets"][0]["path"]
        with tempfile.TemporaryDirectory() as directory:
            expanded = Path(directory) / original.name
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(expanded, "w") as target:
                for info in source.infolist():
                    payload = source.read(info.filename)
                    if info.filename == "xl/tables/table1.xml":
                        payload = payload.replace(b'A4:H29', b'A4:H30')
                    target.writestr(info, payload)
            self.assertFalse(population_binding_valid(descriptor, expanded))

    def test_xlsx_empty_rows_are_not_false_passes_and_readiness_has_no_tokens(self):
        path = ROOT / "library/templates/TMPL-0001/1.0.0/internal-close-reconciliation.xlsx"
        with zipfile.ZipFile(path) as archive:
            worksheets = b" ".join(archive.read(name) for name in archive.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml"))
            checks = ET.fromstring(archive.read("xl/worksheets/sheet8.xml"))
        formulas = b" ".join(re.findall(rb"<[^>]*f[^>]*>(.*?)</[^>]*f>", worksheets))
        self.assertNotIn(b"{{", formulas)
        self.assertIn(b'IF(A5="",""', formulas)
        self.assertIn(b'"NOT READY"', formulas)
        self.assertIn(b'COUNTIF(B5:B11,"NOT READY")=0', formulas)
        self.assertNotIn(b'=0,"PASS",IF(ABS(C9)', formulas)
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        cached = []
        for ref in ["B5", "B6", "B7", "B8", "B9", "B10", "B11", "B14"]:
            value = checks.find(f".//m:c[@r='{ref}']/m:v", ns)
            cached.append(value.text if value is not None else "")
        self.assertNotIn("PASS", cached)
        self.assertEqual(cached[-1], "NOT READY")

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
        self.assertTrue(body.startswith("Team,"))
        self.assertNotIn("{{operations_manager_first_name}}", body)
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
