import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.audit_tabular_package import audit


def workbook(path: Path, *, token=False, formula=True, hidden=False):
    sheet_data = '<row r="1"{}><c r="A1"{}><f>1+1</f><v>2</v></c></row>'.format(
        ' hidden="1"' if hidden else "", ' t="n"' if formula else ' t="inlineStr"'
    )
    if token:
        sheet_data = '<row r="1"><c r="A1" t="inlineStr"><is><t>{{organization_name}}</t></is></c></row>'
    elif not formula:
        sheet_data = '<row r="1"><c r="A1" t="inlineStr"><is><t>complete</t></is></c></row>'
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="Data" sheetId="1"/></sheets></workbook>')
        package.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{sheet_data}</sheetData></worksheet>')


class TabularConformanceTests(unittest.TestCase):
    def build_package(self, root: Path, *, mismatch=False, token=False, formula=True, hidden=False):
        (root / "manifest.md").write_text("approved synthetic package\n", encoding="utf-8")
        workbook(root / "working.xlsx", token=token, formula=formula, hidden=hidden)
        rows = [
            {"Row ID": "R-001", "Entity": "E-01", "Account": "A-10"},
            {"Row ID": "R-002", "Entity": "E-02", "Account": "A-20"},
            {"Row ID": "R-003", "Entity": "E-01", "Account": "A-30"},
        ]
        with (root / "source.csv").open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        mappings = [{"Account": "A-10"}, {"Account": "A-20"}, {"Account": "A-99" if mismatch else "A-30"}]
        with (root / "mapping.csv").open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=["Account"])
            writer.writeheader(); writer.writerows(mappings)
        return {
            "provenance_reference": "manifest.md",
            "workbook": {"path": "working.xlsx", "require_formulas": True},
            "csv_carriers": [
                {"path": "source.csv", "minimum_rows": 3, "required_columns": ["Row ID", "Entity", "Account"], "id_column": "Row ID", "minimum_unique": {"Entity": 2, "Account": 3}},
                {"path": "mapping.csv", "minimum_rows": 3, "required_columns": ["Account"], "id_column": "Account"},
            ],
            "reconciliations": [{"id": "source-to-map", "left_path": "source.csv", "left_column": "Account", "right_path": "mapping.csv", "right_column": "Account", "relationship": "equal"}],
        }

    def test_complete_multi_carrier_package_passes_without_claiming_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = audit(root, self.build_package(root))
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["scope"], "downstream_conformance_only_not_acceptance")

    def test_cross_file_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = audit(root, self.build_package(root, mismatch=True))
            self.assertIn("reconciliation source-to-map failed: equal", result["findings"])

    def test_tokens_formula_free_and_hidden_surfaces_fail(self):
        for options, fragment in [
            ({"token": True}, "unresolved build token"),
            ({"formula": False}, "live formulas are required"),
            ({"hidden": True}, "hidden rows, columns, or sheets"),
        ]:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = audit(root, self.build_package(root, **options))
                self.assertTrue(any(fragment in finding for finding in result["findings"]), result)


if __name__ == "__main__":
    unittest.main()
