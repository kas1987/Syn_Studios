from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


INPUT = Path(sys.argv[1])
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
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
    "Checks": ("$A$1:$D$14", "portrait"),
}


def qn(local: str) -> str:
    return f"{{{MAIN}}}{local}"


with zipfile.ZipFile(INPUT, "r") as source:
    members = {info.filename: (info, source.read(info.filename)) for info in source.infolist()}

workbook = ET.fromstring(members["xl/workbook.xml"][1])
relationships = ET.fromstring(members["xl/_rels/workbook.xml.rels"][1])
relationship_targets = {
    item.attrib["Id"]: item.attrib["Target"]
    for item in relationships
}

defined_names = workbook.find("m:definedNames", NS)
if defined_names is None:
    defined_names = ET.Element(qn("definedNames"))
    calc_pr = workbook.find("m:calcPr", NS)
    workbook.insert(list(workbook).index(calc_pr) if calc_pr is not None else len(workbook), defined_names)
else:
    for child in list(defined_names):
        if child.attrib.get("name") == "_xlnm.Print_Area":
            defined_names.remove(child)

for index, sheet in enumerate(workbook.find("m:sheets", NS)):
    name = sheet.attrib["name"]
    if name not in PRINT_AREAS:
        raise ValueError(f"missing print profile for {name}")
    relationship_id = sheet.attrib[f"{{{REL}}}id"]
    target = relationship_targets[relationship_id].lstrip("/")
    worksheet_path = target if target.startswith("xl/") else f"xl/{target}"
    worksheet = ET.fromstring(members[worksheet_path][1])

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
    page_setup = ET.Element(qn("pageSetup"), {"paperSize": "1", "orientation": PRINT_AREAS[name][1], "fitToWidth": "1", "fitToHeight": "1"})
    insertion = next((position for position, child in enumerate(worksheet) if child.tag in {qn("headerFooter"), qn("tableParts"), qn("extLst")}), len(worksheet))
    for element in (print_options, margins, page_setup):
        worksheet.insert(insertion, element)
        insertion += 1

    members[worksheet_path] = (members[worksheet_path][0], ET.tostring(worksheet, encoding="utf-8", xml_declaration=True))
    area, _ = PRINT_AREAS[name]
    defined_name = ET.SubElement(defined_names, qn("definedName"), {"name": "_xlnm.Print_Area", "localSheetId": str(index)})
    defined_name.text = f"'{name}'!{area}"

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
