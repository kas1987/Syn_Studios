import csv
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from scripts.audit_tabular_package import SHEET_NS, audit


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


def rewrite_workbook(path: Path, replacements: dict[str, str]):
    with zipfile.ZipFile(path) as package:
        members = {item.filename: package.read(item.filename) for item in package.infolist()}
    members.update({name: payload.encode("utf-8") for name, payload in replacements.items()})
    with zipfile.ZipFile(path, "w") as package:
        for name, payload in members.items():
            package.writestr(name, payload)


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

    def test_split_inline_string_token_is_reconstructed_before_scanning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = self.build_package(root)
            worksheet = (
                f'<worksheet xmlns="{SHEET_NS}"><sheetData><row r="1">'
                '<c r="A1" t="n"><f>1+1</f><v>2</v></c>'
                '<c r="B1" t="inlineStr"><is>'
                '<r><t>{</t></r><r><t>{organization_name}</t></r><r><t>}</t></r>'
                '</is></c></row></sheetData></worksheet>'
            )
            rewrite_workbook(
                root / policy["workbook"]["path"],
                {"xl/worksheets/sheet1.xml": worksheet},
            )

            result = audit(root, policy)

            self.assertIn(
                "working.xlsx: unresolved build token in xl/worksheets/sheet1.xml",
                result["findings"],
            )

    def test_split_shared_string_token_is_reconstructed_before_scanning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = self.build_package(root)
            worksheet = (
                f'<worksheet xmlns="{SHEET_NS}"><sheetData><row r="1">'
                '<c r="A1" t="n"><f>1+1</f><v>2</v></c>'
                '<c r="B1" t="s"><v>0</v></c>'
                '</row></sheetData></worksheet>'
            )
            shared_strings = (
                f'<sst xmlns="{SHEET_NS}" count="1" uniqueCount="1"><si>'
                '<r><t>{{organization</t></r><r><t>_name}</t></r><r><t>}</t></r>'
                '</si></sst>'
            )
            rewrite_workbook(
                root / policy["workbook"]["path"],
                {
                    "xl/worksheets/sheet1.xml": worksheet,
                    "xl/sharedStrings.xml": shared_strings,
                },
            )

            result = audit(root, policy)

            self.assertIn(
                "working.xlsx: unresolved build token in xl/sharedStrings.xml",
                result["findings"],
            )

    def test_malformed_relationship_xml_is_an_explicit_finding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = self.build_package(root)
            rewrite_workbook(
                root / policy["workbook"]["path"],
                {
                    "xl/_rels/workbook.xml.rels": (
                        '<Relationships xmlns="http://schemas.openxmlformats.org/'
                        'package/2006/relationships"><Relationship'
                    )
                },
            )

            result = audit(root, policy)

            self.assertTrue(
                any(
                    "malformed relationship XML in xl/_rels/workbook.xml.rels" in finding
                    for finding in result["findings"]
                ),
                result,
            )

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

    def test_reconciliation_cannot_pass_when_operand_columns_are_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = self.build_package(root)
            policy["reconciliations"][0]["left_column"] = "Missing left"
            policy["reconciliations"][0]["right_column"] = "Missing right"

            result = audit(root, policy)

            self.assertIn(
                "reconciliation source-to-map references a missing operand column",
                result["findings"],
            )

    def test_reconciliation_cannot_pass_with_blank_operand_populations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = self.build_package(root)
            for filename in ("source.csv", "mapping.csv"):
                path = root / filename
                with path.open("r", encoding="utf-8", newline="") as source:
                    rows = list(csv.DictReader(source))
                    headers = list(rows[0])
                for row in rows:
                    row["Account"] = ""
                with path.open("w", encoding="utf-8", newline="") as target:
                    writer = csv.DictWriter(target, fieldnames=headers)
                    writer.writeheader()
                    writer.writerows(rows)
            for carrier in policy["csv_carriers"]:
                carrier.pop("id_column", None)
                carrier.pop("minimum_unique", None)

            result = audit(root, policy)

            self.assertIn(
                "reconciliation source-to-map has an empty operand population",
                result["findings"],
            )

    def test_missing_configured_csv_control_columns_are_findings(self):
        for control in ("id_column", "minimum_unique"):
            with self.subTest(control=control), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                policy = self.build_package(root)
                carrier = policy["csv_carriers"][0]
                if control == "id_column":
                    carrier[control] = "Typo Row ID"
                else:
                    carrier[control] = {"Typo Diversity Column": 2}

                result = audit(root, policy)

                self.assertTrue(
                    any(f"configured {control}" in finding and "missing" in finding for finding in result["findings"]),
                    result,
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

    def test_true_lexical_hidden_workbook_row_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = self.build_package(root)
            workbook = root / policy["workbook"]["path"]
            with zipfile.ZipFile(workbook) as package:
                members = {item.filename: package.read(item.filename) for item in package.infolist()}
            worksheet = ElementTree.fromstring(members["xl/worksheets/sheet1.xml"])
            worksheet.find(f".//{{{SHEET_NS}}}row").set("hidden", "true")
            members["xl/worksheets/sheet1.xml"] = ElementTree.tostring(
                worksheet, encoding="utf-8", xml_declaration=True
            )
            with zipfile.ZipFile(workbook, "w") as package:
                for name, payload in members.items():
                    package.writestr(name, payload)

            result = audit(root, policy)

            self.assertTrue(any("hidden rows" in finding for finding in result["findings"]), result)


if __name__ == "__main__":
    unittest.main()
