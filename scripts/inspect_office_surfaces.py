#!/usr/bin/env python3
"""Deterministically inventory integrity-relevant surfaces in Office packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CORE = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
CUSTOM_PROPS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
NS = {"m": MAIN, "w": WORD, "r": REL, "pr": PACKAGE_REL}
FORMULA_ERROR_TOKENS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#NUM!", "#NULL!", "#N/A")
PLACEHOLDER_PATTERN = re.compile(r"\{\{[^{}\r\n]+\}\}|\[\[[^\[\]\r\n]+\]\]|\{%[^%\r\n]+%\}")
PROHIBITED_PATTERNS = {
    "answer_key": re.compile(r"\b(?:hidden\s+)?answer\s+key\b", re.IGNORECASE),
    "ground_truth": re.compile(r"\bground\s+truth\b", re.IGNORECASE),
    "grading_rubric": re.compile(r"\b(?:grading|evaluation)\s+rubric\b", re.IGNORECASE),
    "model_answer": re.compile(r"\bmodel\s+answer\b", re.IGNORECASE),
    "prior_submission": re.compile(r"\bprior\s+submission\b", re.IGNORECASE),
    "system_prompt": re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
}
STALE_CORE_FIELDS = {"creator", "lastModifiedBy", "created", "modified"}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def xml_root(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(archive.read(name))
    except KeyError:
        return None


def attribute(node: ET.Element, name: str) -> str | None:
    return node.attrib.get(f"{{{MAIN}}}{name}") or node.attrib.get(f"{{{WORD}}}{name}") or node.attrib.get(name)


def counter_dict(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def relationship_target(source_part: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target)).lstrip("/")


def relationship_source(rels_name: str) -> str:
    if rels_name == "_rels/.rels":
        return ""
    directory, filename = posixpath.split(rels_name)
    if not directory.endswith("/_rels") or not filename.endswith(".rels"):
        return ""
    return posixpath.join(directory[: -len("/_rels")], filename[: -len(".rels")])


def package_relationships(archive: zipfile.ZipFile) -> list[dict[str, str]]:
    relationships = []
    for name in sorted(item for item in archive.namelist() if item.endswith(".rels")):
        root = xml_root(archive, name)
        if root is None:
            continue
        source = relationship_source(name)
        for node in root.findall("pr:Relationship", NS):
            target = node.attrib.get("Target", "")
            mode = node.attrib.get("TargetMode", "Internal")
            relationships.append(
                {
                    "source": source,
                    "id": node.attrib.get("Id", ""),
                    "type": node.attrib.get("Type", ""),
                    "target": target if mode == "External" else relationship_target(source, target),
                    "target_mode": mode,
                }
            )
    return sorted(relationships, key=lambda item: (item["source"], item["id"], item["target"]))


def package_manifest_hash(archive: zipfile.ZipFile) -> str:
    digest = hashlib.sha256()
    for name in sorted(item for item in archive.namelist() if not item.endswith("/")):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_bytes(archive.read(name)).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def package_text_surfaces(archive: zipfile.ZipFile) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    placeholders: dict[str, set[str]] = defaultdict(set)
    prohibited: list[dict[str, str]] = []
    for name in sorted(archive.namelist()):
        if not name.endswith((".xml", ".rels")):
            continue
        text = archive.read(name).decode("utf-8", errors="replace")
        try:
            parsed_text = " ".join(ET.fromstring(text).itertext())
        except ET.ParseError:
            parsed_text = ""
        scan_text = f"{text} {parsed_text}"
        for token in PLACEHOLDER_PATTERN.findall(text):
            placeholders[token].add(name)
        for code, pattern in PROHIBITED_PATTERNS.items():
            if pattern.search(scan_text):
                prohibited.append({"code": code, "member": name})
    placeholder_records = [
        {"token": token, "members": sorted(members)}
        for token, members in sorted(placeholders.items())
    ]
    return placeholder_records, prohibited


def properties(archive: zipfile.ZipFile) -> dict[str, Any]:
    core: dict[str, str] = {}
    core_root = xml_root(archive, "docProps/core.xml")
    if core_root is not None:
        for node in core_root:
            value = (node.text or "").strip()
            if value:
                core[local_name(node.tag)] = value

    custom = []
    custom_root = xml_root(archive, "docProps/custom.xml")
    if custom_root is not None:
        for node in custom_root.findall(f"{{{CUSTOM_PROPS}}}property"):
            value_node = next(iter(node), None)
            custom.append(
                {
                    "name": node.attrib.get("name", ""),
                    "type": local_name(value_node.tag) if value_node is not None else "",
                    "value": (value_node.text or "") if value_node is not None else "",
                }
            )
    return {"core": dict(sorted(core.items())), "custom": sorted(custom, key=lambda item: item["name"])}


def common_surfaces(archive: zipfile.ZipFile) -> dict[str, Any]:
    names = set(archive.namelist())
    relationships = package_relationships(archive)
    external = [item for item in relationships if item["target_mode"] == "External"]
    custom_xml = []
    for name in sorted(item for item in names if re.fullmatch(r"customXml/item\d+\.xml", item)):
        root = xml_root(archive, name)
        custom_xml.append(
            {
                "part": name,
                "root": local_name(root.tag) if root is not None else "",
                "sha256": sha256_bytes(archive.read(name)),
            }
        )
    embedded = sorted(
        name
        for name in names
        if "/embeddings/" in name or "/activeX/" in name or "/oleObject" in name
    )
    placeholders, prohibited = package_text_surfaces(archive)
    return {
        "member_count": len([name for name in names if not name.endswith("/")]),
        "member_manifest_sha256": package_manifest_hash(archive),
        "relationships_external": external,
        "custom_xml": custom_xml,
        "embedded_objects": embedded,
        "properties": properties(archive),
        "unresolved_tokens": placeholders,
        "prohibited_tokens": prohibited,
    }


def xlsx_style_map(archive: zipfile.ZipFile) -> tuple[dict[str, str], list[dict[str, str]]]:
    styles = xml_root(archive, "xl/styles.xml")
    if styles is None:
        return {}, []
    custom_formats = [
        {"id": node.attrib.get("numFmtId", ""), "code": node.attrib.get("formatCode", "")}
        for node in styles.findall("m:numFmts/m:numFmt", NS)
    ]
    cell_xfs = styles.findall("m:cellXfs/m:xf", NS)
    style_map = {str(index): node.attrib.get("numFmtId", "0") for index, node in enumerate(cell_xfs)}
    return style_map, sorted(custom_formats, key=lambda item: (item["id"], item["code"]))


def inspect_xlsx(archive: zipfile.ZipFile) -> dict[str, Any]:
    workbook = xml_root(archive, "xl/workbook.xml")
    rels = xml_root(archive, "xl/_rels/workbook.xml.rels")
    if workbook is None or rels is None:
        raise ValueError("XLSX is missing workbook structure")
    targets = {
        node.attrib.get("Id", ""): relationship_target("xl/workbook.xml", node.attrib.get("Target", ""))
        for node in rels.findall("pr:Relationship", NS)
        if node.attrib.get("TargetMode") != "External"
    }
    style_map, custom_formats = xlsx_style_map(archive)
    sheets = []
    layout_groups: dict[str, list[str]] = defaultdict(list)
    total_formulas = 0
    formula_errors = []

    for sheet in workbook.findall("m:sheets/m:sheet", NS):
        name = sheet.attrib.get("name", "")
        relationship_id = sheet.attrib.get(f"{{{REL}}}id", "")
        part = targets.get(relationship_id, "")
        worksheet = xml_root(archive, part)
        if worksheet is None:
            raise ValueError(f"XLSX sheet part is missing: {name}")
        hidden_rows = [
            row.attrib.get("r", "")
            for row in worksheet.findall("m:sheetData/m:row", NS)
            if row.attrib.get("hidden") in {"1", "true"}
        ]
        hidden_columns = [
            {"min": col.attrib.get("min", ""), "max": col.attrib.get("max", "")}
            for col in worksheet.findall("m:cols/m:col", NS)
            if col.attrib.get("hidden") in {"1", "true"}
        ]
        widths = [
            f"{col.attrib.get('min', '')}:{col.attrib.get('max', '')}={col.attrib.get('width', '')}"
            for col in worksheet.findall("m:cols/m:col", NS)
        ]
        view = worksheet.find("m:sheetViews/m:sheetView", NS)
        gridlines = view is None or view.attrib.get("showGridLines", "1") not in {"0", "false"}
        cells = worksheet.findall(".//m:c", NS)
        style_distribution = counter_dict([cell.attrib.get("s", "0") for cell in cells])
        number_format_distribution = counter_dict([style_map.get(cell.attrib.get("s", "0"), "0") for cell in cells])
        formulas = worksheet.findall(".//m:f", NS)
        total_formulas += len(formulas)
        sheet_errors = []
        for cell in cells:
            formula = cell.find("m:f", NS)
            value = cell.find("m:v", NS)
            formula_text = (formula.text or "") if formula is not None else ""
            value_text = (value.text or "") if value is not None else ""
            errors = sorted({token for token in FORMULA_ERROR_TOKENS if token in formula_text or token == value_text})
            if cell.attrib.get("t") == "e" and value_text:
                errors = sorted(set(errors) | {value_text})
            if errors:
                record = {"sheet": name, "cell": cell.attrib.get("r", ""), "errors": errors}
                formula_errors.append(record)
                sheet_errors.append(record)
        layout = {
            "gridlines": gridlines,
            "widths": widths,
            "style_distribution": style_distribution,
            "number_format_distribution": number_format_distribution,
        }
        fingerprint = sha256_bytes(json.dumps(layout, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        layout_groups[fingerprint].append(name)
        sheets.append(
            {
                "name": name,
                "visibility": sheet.attrib.get("state", "visible"),
                "part": part,
                "hidden_rows": hidden_rows,
                "hidden_columns": hidden_columns,
                "comments_or_notes": 0,
                "formula_count": len(formulas),
                "formula_errors": sheet_errors,
                "gridlines": gridlines,
                "widths": widths,
                "style_distribution": style_distribution,
                "number_format_distribution": number_format_distribution,
                "layout_fingerprint": fingerprint,
            }
        )

    comments = []
    for part in sorted(name for name in archive.namelist() if re.fullmatch(r"xl/(?:threadedComments/)?comments\d+\.xml", name)):
        root = xml_root(archive, part)
        count = 0 if root is None else len([node for node in root.iter() if local_name(node.tag) in {"comment", "threadedComment"}])
        comments.append({"part": part, "count": count})
    for sheet_record in sheets:
        rels_part = posixpath.join(posixpath.dirname(sheet_record["part"]), "_rels", posixpath.basename(sheet_record["part"]) + ".rels")
        sheet_rels = xml_root(archive, rels_part)
        if sheet_rels is None:
            continue
        comment_parts = {
            relationship_target(sheet_record["part"], node.attrib.get("Target", ""))
            for node in sheet_rels.findall("pr:Relationship", NS)
            if node.attrib.get("Type", "").endswith(("/comments", "/threadedComment"))
        }
        sheet_record["comments_or_notes"] = sum(item["count"] for item in comments if item["part"] in comment_parts)

    defined_names = [
        {
            "name": node.attrib.get("name", ""),
            "local_sheet_id": node.attrib.get("localSheetId"),
            "hidden": node.attrib.get("hidden", "0") in {"1", "true"},
            "value": node.text or "",
        }
        for node in workbook.findall("m:definedNames/m:definedName", NS)
    ]
    connections = sorted(name for name in archive.namelist() if name == "xl/connections.xml" or name.startswith("xl/connections/"))
    external_links = sorted(name for name in archive.namelist() if name.startswith("xl/externalLinks/") and name.endswith(".xml"))
    convergence_groups = sorted(
        (sorted(names) for names in layout_groups.values() if len(names) > 1),
        key=lambda names: (-len(names), names),
    )
    convergence = bool(sheets) and len(sheets) >= 3 and max((len(group) for group in convergence_groups), default=0) / len(sheets) >= 0.75
    return {
        "sheet_visibility": [{"name": item["name"], "state": item["visibility"]} for item in sheets],
        "sheets": sheets,
        "comments_or_notes": comments,
        "defined_names": sorted(defined_names, key=lambda item: (item["name"], item["local_sheet_id"] or "")),
        "external_links": external_links,
        "connections": connections,
        "formula_count": total_formulas,
        "formula_error_count": len(formula_errors),
        "formula_errors": formula_errors,
        "comments_or_notes_count": sum(item["count"] for item in comments),
        "custom_number_formats": custom_formats,
        "layout_convergence_groups": convergence_groups,
        "package_wide_layout_convergence": convergence,
    }


def word_style_distributions(archive: zipfile.ZipFile, document: ET.Element) -> dict[str, dict[str, int]]:
    paragraph_styles = []
    table_styles = []
    numbering = []
    for paragraph in document.findall(".//w:p", NS):
        style = paragraph.find("w:pPr/w:pStyle", NS)
        paragraph_styles.append(attribute(style, "val") if style is not None else "(default)")
        num_id = paragraph.find("w:pPr/w:numPr/w:numId", NS)
        if num_id is not None:
            numbering.append(attribute(num_id, "val") or "")
    for table in document.findall(".//w:tbl", NS):
        style = table.find("w:tblPr/w:tblStyle", NS)
        table_styles.append(attribute(style, "val") if style is not None else "(default)")
    return {
        "paragraph_styles": counter_dict(paragraph_styles),
        "table_styles": counter_dict(table_styles),
        "numbering_ids": counter_dict(numbering),
    }


def inspect_docx(archive: zipfile.ZipFile) -> dict[str, Any]:
    document = xml_root(archive, "word/document.xml")
    if document is None:
        raise ValueError("DOCX is missing word/document.xml")
    names = set(archive.namelist())
    comments = []
    for part in sorted(name for name in names if re.fullmatch(r"word/comments(?:Extended)?\.xml", name)):
        root = xml_root(archive, part)
        count = 0 if root is None else len([node for node in root.iter() if local_name(node.tag) == "comment"])
        comments.append({"part": part, "count": count})
    fields = []
    fields.extend(node.attrib.get(f"{{{WORD}}}instr", "") for node in document.findall(".//w:fldSimple", NS))
    fields.extend((node.text or "") for node in document.findall(".//w:instrText", NS))
    formula_fields = [field.strip() for field in fields if field.strip().startswith("=")]
    visible_text = " ".join(node.text or "" for node in document.findall(".//w:t", NS))
    errors = sorted({token for token in FORMULA_ERROR_TOKENS if token in visible_text or any(token in field for field in fields)})
    table_widths = []
    for table in document.findall(".//w:tbl", NS):
        table_width = table.find("w:tblPr/w:tblW", NS)
        grid_widths = [attribute(node, "w") or "" for node in table.findall("w:tblGrid/w:gridCol", NS)]
        table_widths.append(
            {
                "type": attribute(table_width, "type") if table_width is not None else None,
                "width": attribute(table_width, "w") if table_width is not None else None,
                "grid_columns": grid_widths,
            }
        )
    section_layouts = []
    for section in document.findall(".//w:sectPr", NS):
        size = section.find("w:pgSz", NS)
        margins = section.find("w:pgMar", NS)
        section_layouts.append(
            {
                "page_width": attribute(size, "w") if size is not None else None,
                "page_height": attribute(size, "h") if size is not None else None,
                "orientation": (attribute(size, "orient") if size is not None else None) or "portrait",
                "margins": dict(sorted((local_name(key), value) for key, value in (margins.attrib.items() if margins is not None else []))),
            }
        )
    return {
        "sheet_visibility": None,
        "hidden_rows": None,
        "hidden_columns": None,
        "comments_or_notes": comments,
        "defined_names": None,
        "external_links": [],
        "connections": [],
        "formula_count": len(formula_fields),
        "formula_error_count": len(errors),
        "formula_errors": errors,
        "comments_or_notes_count": sum(item["count"] for item in comments),
        "table_widths": table_widths,
        "gridlines": None,
        "number_format_distribution": None,
        "style_distribution": word_style_distributions(archive, document),
        "section_layouts": section_layouts,
        "layout_convergence_groups": [],
        "package_wide_layout_convergence": False,
    }


def findings_for(common: dict[str, Any], office: dict[str, Any]) -> list[dict[str, str]]:
    findings = []
    for token in common["prohibited_tokens"]:
        findings.append(
            {
                "code": "PROHIBITED_TOKEN",
                "severity": "error",
                "location": token["member"],
                "detail": token["code"],
            }
        )
    external_parts = office.get("external_links", []) + office.get("connections", [])
    if common["relationships_external"] or external_parts:
        findings.append(
            {
                "code": "EXTERNAL_LINK_OR_CONNECTION",
                "severity": "error",
                "location": "package",
                "detail": f"{len(common['relationships_external'])} external relationships; {len(external_parts)} external-link/connection parts",
            }
        )
    stale_core = sorted(set(common["properties"]["core"]) & STALE_CORE_FIELDS)
    custom_properties = common["properties"]["custom"]
    if stale_core or custom_properties:
        findings.append(
            {
                "code": "STALE_OR_CUSTOM_METADATA",
                "severity": "error",
                "location": "docProps",
                "detail": f"core fields={stale_core}; custom properties={len(custom_properties)}",
            }
        )
    if office.get("formula_errors"):
        findings.append(
            {
                "code": "FORMULA_ERROR",
                "severity": "error",
                "location": "formula surfaces",
                "detail": f"{len(office['formula_errors'])} error-bearing formulas or values",
            }
        )
    if office.get("package_wide_layout_convergence"):
        findings.append(
            {
                "code": "LAYOUT_CONVERGENCE",
                "severity": "error",
                "location": "package",
                "detail": "at least 75% of three or more sheets share the same structural layout fingerprint",
            }
        )
    return sorted(findings, key=lambda item: (item["code"], item["location"], item["detail"]))


def inspect_path(path: Path) -> dict[str, Any]:
    path = path.resolve()
    suffix = path.suffix.lower()
    record: dict[str, Any] = {
        "path": path.as_posix(),
        "format": suffix.lstrip("."),
        "bytes": path.stat().st_size,
        "sha256": sha256_path(path),
    }
    if suffix not in {".xlsx", ".docx"}:
        record.update({"verdict": "NOT_APPLICABLE", "findings": [], "surfaces": None})
        return record
    with zipfile.ZipFile(path) as archive:
        common = common_surfaces(archive)
        office = inspect_xlsx(archive) if suffix == ".xlsx" else inspect_docx(archive)
    findings = findings_for(common, office)
    record.update(
        {
            "verdict": "FAIL" if any(item["severity"] == "error" for item in findings) else "PASS",
            "findings": findings,
            "surfaces": {"package": common, "office": office},
        }
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    missing = [str(path) for path in args.paths if not path.is_file()]
    if missing:
        print(json.dumps({"error": "missing input files", "paths": missing}, sort_keys=True), file=sys.stderr)
        return 2
    try:
        records = [inspect_path(path) for path in args.paths]
    except (ET.ParseError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    payload = {
        "schema_version": "1.0.0",
        "verdict": "FAIL" if any(record["verdict"] == "FAIL" for record in records) else "PASS",
        "records": records,
    }
    rendered = json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True)
    print(rendered)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 1 if payload["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
