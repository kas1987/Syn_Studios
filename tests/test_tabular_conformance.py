import csv
import json
import shutil
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
    def build_package(self, root: Path, *, mismatch=False, token=False, formula=True, hidden=False, malformed_lifecycle=False):
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
        exceptions = [
            {"Exception ID": "X-01", "Status": "Open", "Resolution Reference": "bad" if malformed_lifecycle else ""},
            {"Exception ID": "X-02", "Status": "Resolved", "Resolution Reference": "NOTE-17"},
            {"Exception ID": "X-03", "Status": "Retained", "Resolution Reference": "POLICY-4"},
            {"Exception ID": "X-04", "Status": "Superseded", "Resolution Reference": "X-02"},
        ]
        with (root / "exceptions.csv").open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=list(exceptions[0]))
            writer.writeheader(); writer.writerows(exceptions)
        return {
            "provenance_reference": "manifest.md",
            "workbook": {"path": "working.xlsx", "require_formulas": True},
            "csv_carriers": [
                {"path": "source.csv", "minimum_rows": 3, "required_columns": ["Row ID", "Entity", "Account"], "id_column": "Row ID", "minimum_unique": {"Entity": 2, "Account": 3}},
                {"path": "mapping.csv", "minimum_rows": 3, "required_columns": ["Account"], "id_column": "Account"},
                {"path": "exceptions.csv", "minimum_rows": 4, "required_columns": ["Exception ID", "Status", "Resolution Reference"], "id_column": "Exception ID", "lifecycle": {"status_column": "Status", "resolution_column": "Resolution Reference", "allowed_statuses": ["Open", "Resolved", "Retained", "Superseded"], "required_statuses": ["Open", "Resolved", "Retained", "Superseded"], "requires_resolution": ["Resolved", "Retained", "Superseded"], "forbids_resolution": ["Open"]}},
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

    def test_malformed_exception_lifecycle_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = audit(root, self.build_package(root, malformed_lifecycle=True))
            self.assertTrue(any("Open cannot carry a resolution reference" in finding for finding in result["findings"]), result)

    def test_parent_traversal_is_rejected_for_every_package_path_kind(self):
        for path_kind, policy_value, source_name in [
            ("provenance", "../outside-manifest.md", "manifest.md"),
            ("workbook", "../outside-working.xlsx", "working.xlsx"),
            ("CSV carrier", "../outside-source.csv", "source.csv"),
        ]:
            with self.subTest(path_kind=path_kind), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                root = base / "package"
                root.mkdir()
                policy = self.build_package(root)
                outside = base / Path(policy_value).name
                shutil.copyfile(root / source_name, outside)
                if path_kind == "provenance":
                    policy["provenance_reference"] = policy_value
                elif path_kind == "workbook":
                    policy["workbook"]["path"] = policy_value
                else:
                    policy["csv_carriers"][0]["path"] = policy_value
                    policy["reconciliations"][0]["left_path"] = policy_value

                result = audit(root, policy)

                self.assertTrue(
                    any("path must remain within package root" in finding for finding in result["findings"]),
                    result,
                )

    def test_symlink_escape_is_rejected_for_every_package_path_kind(self):
        for path_kind, link_name, source_name in [
            ("provenance", "linked-manifest.md", "manifest.md"),
            ("workbook", "linked-working.xlsx", "working.xlsx"),
            ("CSV carrier", "linked-source.csv", "source.csv"),
        ]:
            with self.subTest(path_kind=path_kind), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                root = base / "package"
                root.mkdir()
                policy = self.build_package(root)
                outside = base / f"outside-{source_name}"
                shutil.copyfile(root / source_name, outside)
                try:
                    (root / link_name).symlink_to(outside)
                except OSError as error:
                    self.skipTest(f"file symlinks are unavailable: {error}")
                if path_kind == "provenance":
                    policy["provenance_reference"] = link_name
                elif path_kind == "workbook":
                    policy["workbook"]["path"] = link_name
                else:
                    policy["csv_carriers"][0]["path"] = link_name
                    policy["reconciliations"][0]["left_path"] = link_name

                result = audit(root, policy)

                self.assertTrue(
                    any("path must remain within package root" in finding for finding in result["findings"]),
                    result,
                )

    def test_unknown_reconciliation_relationship_is_a_finding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = self.build_package(root)
            policy["reconciliations"][0]["relationship"] = "equals"

            result = audit(root, policy)

            self.assertIn(
                "reconciliation source-to-map has unsupported relationship: equals",
                result["findings"],
            )

    def test_reconciliation_cannot_pass_when_both_carrier_paths_are_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = self.build_package(root)
            policy["reconciliations"][0]["left_path"] = "missing-left.csv"
            policy["reconciliations"][0]["right_path"] = "missing-right.csv"

            result = audit(root, policy)

            self.assertIn(
                "reconciliation source-to-map references an unavailable CSV carrier",
                result["findings"],
            )

    def test_ntfs_alternate_data_stream_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = self.build_package(root)
            policy["workbook"]["path"] = "carrier.bin:working.xlsx"

            result = audit(root, policy)

            self.assertTrue(
                any("declared workbook: path must remain within package root" in item for item in result["findings"]),
                result,
            )


if __name__ == "__main__":
    unittest.main()
