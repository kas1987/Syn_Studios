from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/populations"
SOURCE_FIXTURE = FIXTURES / "source-irregular-360.csv"
MAPPING_FIXTURE = FIXTURES / "account-map-representative.csv"
LEDGER_FIXTURE = FIXTURES / "ledger-representative.csv"
RELEASED_WORKBOOK = ROOT / "library/templates/TMPL-0001/1.0.0/internal-close-reconciliation.xlsx"
FINALIZER = ROOT / "evidence/reports/template-assets/builders/finalize_xlsx_print.py"
PIPELINE = ROOT / "evidence/reports/template-assets/builders/build_close_population.py"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"m": MAIN}
TABLE_SHEETS = {
    "Source_Data",
    "Account_Map",
    "Reconciliation",
    "Proposed_Entries",
    "Prior_Period",
    "Exceptions",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def reconcile_carriers(
    source_rows: list[dict[str, str]],
    mapping_rows: list[dict[str, str]],
    ledger_rows: list[dict[str, str]],
) -> dict[str, Decimal]:
    row_ids = [row["Source Row ID"] for row in source_rows]
    if len(row_ids) != len(set(row_ids)):
        raise ValueError("duplicate source row ID")

    mapping_codes = [row["Account Code"] for row in mapping_rows]
    if len(mapping_codes) != len(set(mapping_codes)):
        raise ValueError("duplicate mapping account")
    mapping = {row["Account Code"]: row for row in mapping_rows}

    ledger_codes = [row["Account Code"] for row in ledger_rows]
    if len(ledger_codes) != len(set(ledger_codes)):
        raise ValueError("duplicate ledger account")
    ledger = {row["Account Code"]: Decimal(row["Ledger Balance"]) for row in ledger_rows}

    source_net: dict[str, Decimal] = defaultdict(Decimal)
    for row in source_rows:
        account = row["Account Code"]
        if account not in mapping:
            raise ValueError(f"source account is unmapped: {account}")
        source_net[account] += Decimal(row["Debit"]) - Decimal(row["Credit"])

    if set(source_net) != set(ledger):
        raise ValueError("ledger account population does not match used source accounts")
    mismatches = {
        account: source_net[account] - ledger[account]
        for account in source_net
        if source_net[account] != ledger[account]
    }
    if mismatches:
        raise ValueError(f"ledger balance mismatch: {mismatches}")
    return dict(source_net)


def workbook_parts(path: Path) -> tuple[dict[str, ET.Element], list[ET.Element], ET.Element]:
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = list(workbook.find("m:sheets", NS))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        worksheets = {}
        for sheet in sheets:
            relationship_id = sheet.attrib[f"{{{REL}}}id"]
            target = targets[relationship_id].lstrip("/")
            part = target if target.startswith("xl/") else f"xl/{target}"
            worksheets[sheet.attrib["name"]] = ET.fromstring(archive.read(part))
    return worksheets, sheets, workbook


class SpreadsheetDiversityTests(unittest.TestCase):
    def test_representative_source_fixture_is_large_irregular_and_balanced(self):
        rows = read_csv(SOURCE_FIXTURE)
        self.assertEqual(len(rows), 360)
        self.assertEqual(len({row["Source Row ID"] for row in rows}), 360)
        self.assertGreaterEqual(len({row["Entity Code"] for row in rows}), 4)
        self.assertGreaterEqual(len({row["Account Code"] for row in rows}), 10)
        self.assertGreaterEqual(len({row["Transaction Date"] for row in rows}), 20)
        self.assertGreaterEqual(len({row["Status Code"] for row in rows}), 4)
        self.assertGreaterEqual(len({row["Description"] for row in rows}), 100)

        debits = [Decimal(row["Debit"]) for row in rows]
        credits = [Decimal(row["Credit"]) for row in rows]
        populated_amounts = [amount for amount in debits + credits if amount]
        self.assertTrue(any(debits))
        self.assertTrue(any(credits))
        self.assertEqual(sum(debits), sum(credits))
        self.assertGreaterEqual(len(set(populated_amounts)), 150)
        self.assertGreaterEqual(len({int(amount * 100) % 100 for amount in populated_amounts}), 50)

        # Reject a mechanically repeated population even if its total happens to balance.
        for field in ("Entity Code", "Account Code", "Transaction Date", "Status Code", "Description"):
            most_common = Counter(row[field] for row in rows).most_common(1)[0][1]
            self.assertLess(most_common, len(rows) * 0.80, field)

    def test_multi_carrier_reconciliation_passes_with_unused_inactive_mapping(self):
        source = read_csv(SOURCE_FIXTURE)
        mapping = read_csv(MAPPING_FIXTURE)
        ledger = read_csv(LEDGER_FIXTURE)
        used = reconcile_carriers(source, mapping, ledger)
        self.assertEqual(len(used), 12)
        self.assertIn("7999", {row["Account Code"] for row in mapping})
        self.assertNotIn("7999", used)

    def test_multi_carrier_reconciliation_rejects_mismatch_unmapped_and_duplicates(self):
        source = read_csv(SOURCE_FIXTURE)
        mapping = read_csv(MAPPING_FIXTURE)
        ledger = read_csv(LEDGER_FIXTURE)

        mismatched_ledger = [dict(row) for row in ledger]
        mismatched_ledger[0]["Ledger Balance"] = str(Decimal(mismatched_ledger[0]["Ledger Balance"]) + Decimal("0.01"))
        with self.assertRaisesRegex(ValueError, "ledger balance mismatch"):
            reconcile_carriers(source, mapping, mismatched_ledger)

        missing_mapping = [row for row in mapping if row["Account Code"] != source[0]["Account Code"]]
        with self.assertRaisesRegex(ValueError, "unmapped"):
            reconcile_carriers(source, missing_mapping, ledger)

        duplicate_source = [dict(row) for row in source]
        duplicate_source[1]["Source Row ID"] = duplicate_source[0]["Source Row ID"]
        with self.assertRaisesRegex(ValueError, "duplicate source row ID"):
            reconcile_carriers(duplicate_source, mapping, ledger)

        with self.assertRaisesRegex(ValueError, "duplicate mapping account"):
            reconcile_carriers(source, mapping + [dict(mapping[0])], ledger)
        with self.assertRaisesRegex(ValueError, "duplicate ledger account"):
            reconcile_carriers(source, mapping, ledger + [dict(ledger[0])])

    def test_print_finalizer_paginates_population_sheets_vertically(self):
        with tempfile.TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "finalized.xlsx"
            shutil.copy2(RELEASED_WORKBOOK, workbook_path)
            subprocess.run([sys.executable, str(FINALIZER), str(workbook_path)], check=True, timeout=60)
            worksheets, sheets, workbook = workbook_parts(workbook_path)

        for name, worksheet in worksheets.items():
            page_setup = worksheet.find("m:pageSetup", NS)
            self.assertIsNotNone(page_setup, name)
            self.assertEqual(page_setup.attrib.get("fitToWidth"), "1", name)
            expected_height = "0" if name in TABLE_SHEETS else "1"
            self.assertEqual(page_setup.attrib.get("fitToHeight"), expected_height, name)

        titles = {
            int(item.attrib["localSheetId"]): item.text
            for item in workbook.findall("m:definedNames/m:definedName", NS)
            if item.attrib.get("name") == "_xlnm.Print_Titles"
        }
        self.assertEqual(len(titles), len(TABLE_SHEETS))
        for index, sheet in enumerate(sheets):
            name = sheet.attrib["name"]
            if name in TABLE_SHEETS:
                self.assertEqual(titles[index], f"'{name}'!$1:$4")
            else:
                self.assertNotIn(index, titles)

    def test_360_row_population_rebuild_expands_formulas_and_render_pages(self):
        node = os.environ.get("SYN_STUDIOS_NODE") or shutil.which("node")
        if not node or not os.environ.get("SYN_STUDIOS_NODE_MODULES"):
            raise unittest.SkipTest("activated document generation stack is unavailable")
        if not os.environ.get("SYN_STUDIOS_SOFFICE") or not os.environ.get("SYN_STUDIOS_POPPLER_BIN"):
            raise unittest.SkipTest("activated render stack is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook_path = root / "expanded-360.xlsx"
            evidence_dir = root / "evidence"
            subprocess.run(
                [
                    sys.executable,
                    str(PIPELINE),
                    "--source-csv",
                    str(SOURCE_FIXTURE),
                    "--output",
                    str(workbook_path),
                    "--evidence-dir",
                    str(evidence_dir),
                ],
                check=True,
                timeout=300,
            )
            evidence = json.loads((evidence_dir / "population-evidence.json").read_text(encoding="utf-8"))
            worksheets, _, _ = workbook_parts(workbook_path)

        self.assertEqual(evidence["source_table_ref"], "A4:H364")
        self.assertEqual(evidence["source_print_area"], "'Source_Data'!$A$1:$H$364")
        self.assertEqual(evidence["formula_boundary"], 364)
        self.assertEqual(evidence["workbook_readiness"], "NOT READY")
        self.assertEqual(evidence["rebuild_verdict"], "PASS")
        source_setup = worksheets["Source_Data"].find("m:pageSetup", NS)
        self.assertEqual(source_setup.attrib.get("fitToWidth"), "1")
        self.assertEqual(source_setup.attrib.get("fitToHeight"), "0")

        rendered = evidence["rendered_outputs"]
        page_images = [item for item in rendered if Path(item["path"]).suffix.lower() == ".png"]
        self.assertGreater(len(page_images), 9, "360 rows must render at least two pages beyond the 8-page reference")
        self.assertGreater(len(rendered), 10)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in rendered))


if __name__ == "__main__":
    unittest.main()
