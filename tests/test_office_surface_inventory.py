from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.inspect_office_surfaces import MAIN, inspect_path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/inspect_office_surfaces.py"
XLSX = ROOT / "library/templates/TMPL-0001/1.0.0/internal-close-reconciliation.xlsx"
DOCX = ROOT / "library/templates/TMPL-0002/1.0.0/internal-controller-memo.docx"
EML = ROOT / "library/templates/TMPL-0003/1.0.0/operational-correction-thread.eml"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def rewrite_package(source: Path, target: Path, replacements: dict[str, bytes]) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as rewritten:
        for info in original.infolist():
            rewritten.writestr(info, replacements.get(info.filename, original.read(info.filename)))
        existing = set(original.namelist())
        for name, payload in replacements.items():
            if name not in existing:
                rewritten.writestr(name, payload)


def finding_codes(record: dict) -> set[str]:
    return {item["code"] for item in record["findings"]}


class OfficeSurfaceInventoryTests(unittest.TestCase):
    def test_current_three_assets_are_hash_bound_and_office_assets_pass(self):
        xlsx = inspect_path(XLSX)
        docx = inspect_path(DOCX)
        eml = inspect_path(EML)

        self.assertEqual(xlsx["verdict"], "PASS")
        self.assertEqual(docx["verdict"], "PASS")
        self.assertEqual(eml["verdict"], "NOT_APPLICABLE")
        for path, record in ((XLSX, xlsx), (DOCX, docx), (EML, eml)):
            self.assertEqual(record["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertRegex(record["sha256"], r"^[0-9a-f]{64}$")

        workbook = xlsx["surfaces"]["office"]
        package = xlsx["surfaces"]["package"]
        self.assertEqual(len(workbook["sheet_visibility"]), 8)
        self.assertTrue(all(item["state"] == "visible" for item in workbook["sheet_visibility"]))
        self.assertTrue(all(not item["hidden_rows"] and not item["hidden_columns"] for item in workbook["sheets"]))
        self.assertGreater(workbook["formula_count"], 0)
        self.assertFalse(workbook["formula_errors"])
        self.assertTrue(workbook["defined_names"])
        self.assertFalse(workbook["external_links"])
        self.assertFalse(workbook["connections"])
        self.assertFalse(package["relationships_external"])
        self.assertFalse(package["embedded_objects"])
        self.assertTrue(package["unresolved_tokens"])
        self.assertFalse(package["prohibited_tokens"])
        self.assertRegex(package["member_manifest_sha256"], r"^[0-9a-f]{64}$")
        for sheet in workbook["sheets"]:
            self.assertIsInstance(sheet["gridlines"], bool)
            self.assertTrue(sheet["widths"])
            self.assertTrue(sheet["style_distribution"])
            self.assertTrue(sheet["number_format_distribution"])

        document = docx["surfaces"]["office"]
        doc_package = docx["surfaces"]["package"]
        self.assertTrue(document["table_widths"])
        self.assertTrue(document["style_distribution"]["paragraph_styles"])
        self.assertEqual(document["section_layouts"][0]["orientation"], "portrait")
        self.assertEqual(doc_package["custom_xml"][0]["root"], "Sources")
        self.assertFalse(doc_package["properties"]["core"])
        self.assertFalse(doc_package["properties"]["custom"])

    def test_inventory_is_deterministic_and_cli_gates_complete_asset_set(self):
        self.assertEqual(inspect_path(XLSX), inspect_path(XLSX))
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(XLSX), str(DOCX), str(EML)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "PASS")
        self.assertEqual([item["format"] for item in payload["records"]], ["xlsx", "docx", "eml"])

    def test_hidden_answer_note_is_detected_with_hidden_structure(self):
        with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(XLSX) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            row = sheet.find(f".//{{{MAIN}}}row")
            self.assertIsNotNone(row)
            row.set("hidden", "1")
            column = sheet.find(f".//{{{MAIN}}}col")
            self.assertIsNotNone(column)
            column.set("hidden", "1")
            comments = (
                f'<comments xmlns="{MAIN}"><authors><author>Reviewer</author></authors>'
                '<commentList><comment ref="A1" authorId="0"><text>'
                "<r><t>HIDDEN ANSWER</t></r><r><t>KEY: use account 4000</t></r>"
                "</text></comment></commentList></comments>"
            ).encode("utf-8")
            comment_relationship = (
                f'<Relationships xmlns="{PACKAGE_REL}"><Relationship Id="rIdCommentSabotage" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" '
                'Target="../comments1.xml"/></Relationships>'
            ).encode("utf-8")
            sabotaged = Path(directory) / "hidden-answer.xlsx"
            rewrite_package(
                XLSX,
                sabotaged,
                {
                    "xl/worksheets/sheet1.xml": ET.tostring(sheet, encoding="utf-8", xml_declaration=True),
                    "xl/comments1.xml": comments,
                    "xl/worksheets/_rels/sheet1.xml.rels": comment_relationship,
                },
            )
            record = inspect_path(sabotaged)

        self.assertEqual(record["verdict"], "FAIL")
        self.assertIn("PROHIBITED_TOKEN", finding_codes(record))
        self.assertEqual(record["surfaces"]["office"]["comments_or_notes"][0]["count"], 1)
        self.assertEqual(record["surfaces"]["office"]["sheets"][0]["comments_or_notes"], 1)
        self.assertTrue(record["surfaces"]["office"]["sheets"][0]["hidden_rows"])
        self.assertTrue(record["surfaces"]["office"]["sheets"][0]["hidden_columns"])

    def test_external_relationship_link_and_connection_are_detected(self):
        with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(XLSX) as archive:
            relationships = ET.fromstring(archive.read("_rels/.rels"))
            ET.SubElement(
                relationships,
                f"{{{PACKAGE_REL}}}Relationship",
                {
                    "Id": "rIdExternalSabotage",
                    "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink",
                    "Target": "https://example.invalid/source.xlsx",
                    "TargetMode": "External",
                },
            )
            sabotaged = Path(directory) / "external-link.xlsx"
            rewrite_package(
                XLSX,
                sabotaged,
                {
                    "_rels/.rels": ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
                    "xl/externalLinks/externalLink1.xml": f'<externalLink xmlns="{MAIN}"/>'.encode("utf-8"),
                    "xl/connections.xml": f'<connections xmlns="{MAIN}"/>'.encode("utf-8"),
                },
            )
            record = inspect_path(sabotaged)

        self.assertEqual(record["verdict"], "FAIL")
        self.assertIn("EXTERNAL_LINK_OR_CONNECTION", finding_codes(record))
        self.assertEqual(len(record["surfaces"]["package"]["relationships_external"]), 1)
        self.assertTrue(record["surfaces"]["office"]["external_links"])
        self.assertTrue(record["surfaces"]["office"]["connections"])

    def test_stale_core_and_custom_metadata_are_detected(self):
        core = (
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/">'
            '<dc:creator>Prior Author</dc:creator><cp:lastModifiedBy>Generator</cp:lastModifiedBy>'
            '<dcterms:created>2024-01-01T00:00:00Z</dcterms:created></cp:coreProperties>'
        ).encode("utf-8")
        custom = (
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="SubmissionId">'
            '<vt:lpwstr>SUB-SECRET</vt:lpwstr></property></Properties>'
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            sabotaged = Path(directory) / "stale-metadata.docx"
            rewrite_package(DOCX, sabotaged, {"docProps/core.xml": core, "docProps/custom.xml": custom})
            record = inspect_path(sabotaged)

        self.assertEqual(record["verdict"], "FAIL")
        self.assertIn("STALE_OR_CUSTOM_METADATA", finding_codes(record))
        metadata = record["surfaces"]["package"]["properties"]
        self.assertEqual(metadata["core"]["creator"], "Prior Author")
        self.assertEqual(metadata["custom"][0]["name"], "SubmissionId")

    def test_formula_error_is_detected(self):
        with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(XLSX) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            sheet_data = sheet.find(f"{{{MAIN}}}sheetData")
            row = ET.SubElement(sheet_data, f"{{{MAIN}}}row", {"r": "999"})
            cell = ET.SubElement(row, f"{{{MAIN}}}c", {"r": "A999", "t": "e"})
            ET.SubElement(cell, f"{{{MAIN}}}f").text = "#REF!"
            ET.SubElement(cell, f"{{{MAIN}}}v").text = "#REF!"
            sabotaged = Path(directory) / "formula-error.xlsx"
            rewrite_package(
                XLSX,
                sabotaged,
                {"xl/worksheets/sheet1.xml": ET.tostring(sheet, encoding="utf-8", xml_declaration=True)},
            )
            record = inspect_path(sabotaged)

        self.assertEqual(record["verdict"], "FAIL")
        self.assertIn("FORMULA_ERROR", finding_codes(record))
        self.assertEqual(record["surfaces"]["office"]["formula_errors"][0]["cell"], "A999")

    def test_package_wide_layout_convergence_is_detected(self):
        with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(XLSX) as archive:
            repeated_sheet = archive.read("xl/worksheets/sheet1.xml")
            replacements = {f"xl/worksheets/sheet{index}.xml": repeated_sheet for index in range(2, 9)}
            sabotaged = Path(directory) / "converged-layout.xlsx"
            rewrite_package(XLSX, sabotaged, replacements)
            record = inspect_path(sabotaged)

        office = record["surfaces"]["office"]
        self.assertEqual(record["verdict"], "FAIL")
        self.assertIn("LAYOUT_CONVERGENCE", finding_codes(record))
        self.assertTrue(office["package_wide_layout_convergence"])
        self.assertEqual(len(office["layout_convergence_groups"][0]), 8)


if __name__ == "__main__":
    unittest.main()
