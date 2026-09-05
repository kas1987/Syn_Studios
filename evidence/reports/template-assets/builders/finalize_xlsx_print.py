from __future__ import annotations

import sys
import tempfile
import zipfile
import re
from pathlib import Path
from xml.etree import ElementTree as ET


INPUT = Path(sys.argv[1])
PREPARE_RECALCULATION = "--prepare-recalculation" in sys.argv[2:]
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"m": MAIN, "r": REL}
ET.register_namespace("", MAIN)
ET.register_namespace("r", REL)

PRINT_AREAS = {
    "Close_Control": ("$A$1:$H$16", "landscape"),
    "Source_Data": ("$A$1:$H$29", "landscape"),
    "Account_Map": ("$A$1:$E$29", "portrait"),
    "Reconciliation": ("$A$1:$H$24", "landscape"),
    "Proposed_Entries": ("$A$1:$H$19", "landscape"),
    "Prior_Period": ("$A$1:$E$24", "portrait"),
    "Exceptions": ("$A$1:$H$19", "landscape"),
    "Checks": ("$A$1:$D$16", "portrait"),
}

TABLE_SHEETS = {
    "SourceDataTable": "Source_Data",
    "AccountMapTable": "Account_Map",
    "ReconciliationTable": "Reconciliation",
    "ProposedEntriesTable": "Proposed_Entries",
    "PriorPeriodTable": "Prior_Period",
    "ExceptionsTable": "Exceptions",
}

# Table-backed population and workpaper sheets must keep their columns on one
# page while allowing rows to paginate vertically.  Forcing these sheets to a
# single page makes a legitimate expanded population unreadable.  The compact
# control and checks sheets remain bounded one-page views.
VERTICALLY_PAGINATED_SHEETS = set(TABLE_SHEETS.values())
REPEATED_HEADER_ROWS = "$1:$4"


def qn(local: str) -> str:
    return f"{{{MAIN}}}{local}"


with zipfile.ZipFile(INPUT, "r") as source:
    members = {info.filename: (info, source.read(info.filename)) for info in source.infolist()}

table_ranges = {}
for member_name, (_, payload) in members.items():
    if not member_name.startswith("xl/tables/") or not member_name.endswith(".xml"):
        continue
    table = ET.fromstring(payload)
    table_name = table.attrib.get("name")
    if table_name in TABLE_SHEETS:
        table_ranges[TABLE_SHEETS[table_name]] = table.attrib["ref"]
if set(table_ranges) != set(TABLE_SHEETS.values()):
    missing = sorted(set(TABLE_SHEETS.values()) - set(table_ranges))
    raise ValueError(f"missing bounded tables for: {', '.join(missing)}")


def table_boundary(sheet_name: str) -> tuple[str, int]:
    _, end = table_ranges[sheet_name].split(":")
    match = re.fullmatch(r"([A-Z]+)([0-9]+)", end)
    if match is None:
        raise ValueError(f"unsupported table range for {sheet_name}: {table_ranges[sheet_name]}")
    return match.group(1), int(match.group(2))


capacity_terms = []
for sheet_name in TABLE_SHEETS.values():
    end_column, end_row = table_boundary(sheet_name)
    capacity_terms.append(f'COUNTA(INDIRECT("\'{sheet_name}\'!A{end_row + 1}:{end_column}1048576"))')
CAPACITY_GUARD = "+".join(capacity_terms)

# Source_Data is the executable variable-population carrier. Its rendered
# boundary follows the rebuilt table instead of retaining the reference cap.
source_end_column, source_end_row = table_boundary("Source_Data")
PRINT_AREAS["Source_Data"] = (f"$A$1:${source_end_column}${source_end_row}", "landscape")

# LibreOffice recalculation truthfully updates cached formula values but adds
# application-identifying package properties. They are not part of the model-
# visible template and are removed rather than backdated or falsified.
for member_name in [name for name in members if name.startswith("docProps/")]:
    del members[member_name]

package_relationships = ET.fromstring(members["_rels/.rels"][1])
for relationship in list(package_relationships):
    relationship_type = relationship.attrib.get("Type", "")
    if relationship_type.endswith(("/metadata/core-properties", "/extended-properties", "/thumbnail")):
        package_relationships.remove(relationship)
ET.register_namespace("", PACKAGE_REL)
members["_rels/.rels"] = (
    members["_rels/.rels"][0],
    ET.tostring(package_relationships, encoding="utf-8", xml_declaration=True),
)

content_types = ET.fromstring(members["[Content_Types].xml"][1])
for entry in list(content_types):
    if entry.attrib.get("PartName", "").startswith("/docProps/"):
        content_types.remove(entry)
ET.register_namespace("", CONTENT_TYPES)
members["[Content_Types].xml"] = (
    members["[Content_Types].xml"][0],
    ET.tostring(content_types, encoding="utf-8", xml_declaration=True),
)

# Return subsequent SpreadsheetML serialization to its canonical default
# namespace after rewriting the two package-level OPC documents above.
ET.register_namespace("", MAIN)
workbook = ET.fromstring(members["xl/workbook.xml"][1])
relationships = ET.fromstring(members["xl/_rels/workbook.xml.rels"][1])
relationship_targets = {
    item.attrib["Id"]: item.attrib["Target"]
    for item in relationships
}

calc_pr = workbook.find("m:calcPr", NS)
if calc_pr is None:
    calc_pr = ET.SubElement(workbook, qn("calcPr"))
calc_pr.set("calcMode", "auto")
calc_pr.set("fullCalcOnLoad", "1")
calc_pr.set("forceFullCalc", "1")

defined_names = workbook.find("m:definedNames", NS)
if defined_names is None:
    defined_names = ET.Element(qn("definedNames"))
    calc_pr = workbook.find("m:calcPr", NS)
    workbook.insert(list(workbook).index(calc_pr) if calc_pr is not None else len(workbook), defined_names)
else:
    for child in list(defined_names):
        if child.attrib.get("name") in {"_xlnm.Print_Area", "_xlnm.Print_Titles"}:
            defined_names.remove(child)

for index, sheet in enumerate(workbook.find("m:sheets", NS)):
    name = sheet.attrib["name"]
    if name not in PRINT_AREAS:
        raise ValueError(f"missing print profile for {name}")
    relationship_id = sheet.attrib[f"{{{REL}}}id"]
    target = relationship_targets[relationship_id].lstrip("/")
    worksheet_path = target if target.startswith("xl/") else f"xl/{target}"
    worksheet = ET.fromstring(members[worksheet_path][1])

    if PREPARE_RECALCULATION:
        for cell in worksheet.findall(".//m:c", NS):
            if cell.find("m:f", NS) is not None:
                cached_value = cell.find("m:v", NS)
                if cached_value is not None:
                    cell.remove(cached_value)

    if name == "Checks":
        capacity_cell = worksheet.find(".//m:c[@r='C13']", NS)
        if capacity_cell is None:
            raise ValueError("missing Checks!C13 capacity guard cell")
        formula = capacity_cell.find("m:f", NS)
        if formula is None:
            formula = ET.Element(qn("f"))
            capacity_cell.insert(0, formula)
        formula.text = CAPACITY_GUARD

    if name != "Close_Control":
        sheet_view = worksheet.find("m:sheetViews/m:sheetView", NS)
        if sheet_view is None:
            raise ValueError(f"missing sheet view for {name}")
        existing_pane = sheet_view.find("m:pane", NS)
        if existing_pane is not None:
            sheet_view.remove(existing_pane)
        sheet_view.insert(0, ET.Element(qn("pane"), {"ySplit": "4", "topLeftCell": "A5", "activePane": "bottomLeft", "state": "frozen"}))

    sheet_pr = worksheet.find("m:sheetPr", NS)
    if sheet_pr is None:
        sheet_pr = ET.Element(qn("sheetPr"))
        worksheet.insert(0, sheet_pr)
    page_setup_pr = sheet_pr.find("m:pageSetUpPr", NS)
    if page_setup_pr is None:
        page_setup_pr = ET.SubElement(sheet_pr, qn("pageSetUpPr"))
    page_setup_pr.set("fitToPage", "1")

    for local in ("printOptions", "pageMargins", "pageSetup"):
        existing = worksheet.find(f"m:{local}", NS)
        if existing is not None:
            worksheet.remove(existing)

    print_options = ET.Element(qn("printOptions"), {"horizontalCentered": "1"})
    margins = ET.Element(qn("pageMargins"), {"left": "0.25", "right": "0.25", "top": "0.5", "bottom": "0.5", "header": "0.2", "footer": "0.2"})
    fit_to_height = "0" if name in VERTICALLY_PAGINATED_SHEETS else "1"
    page_setup = ET.Element(qn("pageSetup"), {"paperSize": "1", "orientation": PRINT_AREAS[name][1], "fitToWidth": "1", "fitToHeight": fit_to_height})
    insertion = next((position for position, child in enumerate(worksheet) if child.tag in {qn("headerFooter"), qn("tableParts"), qn("extLst")}), len(worksheet))
    for element in (print_options, margins, page_setup):
        worksheet.insert(insertion, element)
        insertion += 1

    members[worksheet_path] = (members[worksheet_path][0], ET.tostring(worksheet, encoding="utf-8", xml_declaration=True))
    area, _ = PRINT_AREAS[name]
    defined_name = ET.SubElement(defined_names, qn("definedName"), {"name": "_xlnm.Print_Area", "localSheetId": str(index)})
    defined_name.text = f"'{name}'!{area}"
    if name in VERTICALLY_PAGINATED_SHEETS:
        print_titles = ET.SubElement(defined_names, qn("definedName"), {"name": "_xlnm.Print_Titles", "localSheetId": str(index)})
        print_titles.text = f"'{name}'!{REPEATED_HEADER_ROWS}"

members["xl/workbook.xml"] = (members["xl/workbook.xml"][0], ET.tostring(workbook, encoding="utf-8", xml_declaration=True))

with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx", dir=INPUT.parent) as temporary:
    temporary_path = Path(temporary.name)
try:
    with zipfile.ZipFile(temporary_path, "w") as output:
        for name, (info, payload) in members.items():
            output.writestr(info, payload)
    temporary_path.replace(INPUT)
finally:
    temporary_path.unlink(missing_ok=True)
