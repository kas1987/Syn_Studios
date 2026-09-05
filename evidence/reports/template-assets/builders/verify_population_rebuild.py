"""Verify a rebuilt XLSX population and bind its fresh render evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"m": MAIN}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--expected-source-rows", required=True, type=int)
    parser.add_argument("--render-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    workbook_path = args.input.resolve()
    expected_end = args.expected_source_rows + 4
    expected_table_ref = f"A4:H{expected_end}"
    expected_print_area = f"'Source_Data'!$A$1:$H${expected_end}"

    with zipfile.ZipFile(workbook_path) as archive:
        table = next(
            ET.fromstring(archive.read(name))
            for name in archive.namelist()
            if name.startswith("xl/tables/")
            and name.endswith(".xml")
            and ET.fromstring(archive.read(name)).attrib.get("name") == "SourceDataTable"
        )
        if table.attrib["ref"] != expected_table_ref:
            raise SystemExit(f"SourceDataTable is {table.attrib['ref']}, expected {expected_table_ref}")

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = list(workbook.find("m:sheets", NS))
        source_index = next(index for index, sheet in enumerate(sheets) if sheet.attrib["name"] == "Source_Data")
        print_areas = {
            int(item.attrib["localSheetId"]): item.text
            for item in workbook.findall("m:definedNames/m:definedName", NS)
            if item.attrib.get("name") == "_xlnm.Print_Area"
        }
        if print_areas.get(source_index) != expected_print_area:
            raise SystemExit(f"Source_Data print area is {print_areas.get(source_index)!r}, expected {expected_print_area!r}")

        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        source_id = sheets[source_index].attrib[f"{{{REL}}}id"]
        target = targets[source_id].lstrip("/")
        source_sheet_path = target if target.startswith("xl/") else f"xl/{target}"
        source_sheet = ET.fromstring(archive.read(source_sheet_path))
        populated_ids = 0
        for row_number in range(5, expected_end + 1):
            cell = source_sheet.find(f".//m:c[@r='A{row_number}']", NS)
            if cell is not None and (cell.find("m:v", NS) is not None or cell.find("m:is", NS) is not None):
                populated_ids += 1
        if populated_ids != args.expected_source_rows:
            raise SystemExit(f"Source_Data contains {populated_ids} populated row IDs, expected {args.expected_source_rows}")

        checks_index = next(index for index, sheet in enumerate(sheets) if sheet.attrib["name"] == "Checks")
        checks_id = sheets[checks_index].attrib[f"{{{REL}}}id"]
        checks_target = targets[checks_id].lstrip("/")
        checks_path = checks_target if checks_target.startswith("xl/") else f"xl/{checks_target}"
        checks_sheet = ET.fromstring(archive.read(checks_path))
        control_results = {}
        for reference in ("B5", "B6", "B7", "B8", "B13", "B16"):
            value = checks_sheet.find(f".//m:c[@r='{reference}']/m:v", NS)
            control_results[reference] = value.text if value is not None else None
        expected_results = {"B5": "PASS", "B6": "PASS", "B7": "PASS", "B8": "NOT READY", "B13": "PASS", "B16": "NOT READY"}
        if control_results != expected_results:
            raise SystemExit(f"rebuilt workbook control results are {control_results}, expected {expected_results}")

        formulas = " ".join(
            formula.text or ""
            for name in archive.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
            for formula in ET.fromstring(archive.read(name)).findall(".//m:f", NS)
        )
        for required in (f"$F$5:$F${expected_end}", f"$G$5:$G${expected_end}", f"$C$5:$C${expected_end}"):
            if required not in formulas:
                raise SystemExit(f"rebuilt formulas do not reach source boundary {required}")
        if f"Source_Data'!A{expected_end + 1}:H1048576" not in formulas:
            raise SystemExit("out-of-capacity guard does not begin after rebuilt table")

    render_files = sorted(path for path in args.render_dir.resolve().iterdir() if path.suffix.lower() in {".pdf", ".png"})
    if not render_files or not any(path.suffix.lower() == ".pdf" for path in render_files) or not any(path.suffix.lower() == ".png" for path in render_files):
        raise SystemExit("render directory must contain a PDF and page images")
    outputs = [{"path": str(path), "sha256": sha256(path)} for path in render_files]
    if len({item["sha256"] for item in outputs}) != len(outputs):
        raise SystemExit("render evidence contains duplicate output hashes")

    evidence = {
        "schema_version": "1.0.0",
        "input": str(workbook_path),
        "input_sha256": sha256(workbook_path),
        "source_rows": args.expected_source_rows,
        "source_table_ref": expected_table_ref,
        "source_print_area": expected_print_area,
        "formula_boundary": expected_end,
        "control_results": control_results,
        "rendered_outputs": outputs,
        "workbook_readiness": control_results["B16"],
        "rebuild_verdict": "PASS",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
