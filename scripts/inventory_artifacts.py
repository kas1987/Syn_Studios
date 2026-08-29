#!/usr/bin/env python3
"""Read-only structural inventory for synthetic business artifacts and ZIP packages."""

from __future__ import annotations

import argparse
import csv
import email
import hashlib
import io
import json
import re
import sys
import zipfile
from email import policy
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


OFFICE_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


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


def inspect_docx(payload: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        root = xml_root(archive, "word/document.xml")
        if root is None:
            raise ValueError("DOCX is missing word/document.xml")
        paragraphs = root.findall(".//w:p", OFFICE_NS)
        tables = root.findall(".//w:tbl", OFFICE_NS)
        text = "".join(node.text or "" for node in root.findall(".//w:t", OFFICE_NS))
        names = set(archive.namelist())
        return {
            "paragraphs": len(paragraphs),
            "tables": len(tables),
            "text_characters": len(text),
            "headers": len([name for name in names if re.fullmatch(r"word/header\d+\.xml", name)]),
            "footers": len([name for name in names if re.fullmatch(r"word/footer\d+\.xml", name)]),
            "comments": "word/comments.xml" in names,
            "tracked_changes": bool(root.findall(".//w:ins", OFFICE_NS) or root.findall(".//w:del", OFFICE_NS)),
            "embedded_objects": len([name for name in names if name.startswith("word/embeddings/")]),
        }


def inspect_xlsx(payload: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        workbook = xml_root(archive, "xl/workbook.xml")
        if workbook is None:
            raise ValueError("XLSX is missing xl/workbook.xml")
        sheets = workbook.findall(".//m:sheets/m:sheet", OFFICE_NS)
        sheet_names = [sheet.attrib.get("name", "") for sheet in sheets]
        hidden = [sheet.attrib.get("name", "") for sheet in sheets if sheet.attrib.get("state") not in (None, "visible")]
        formulas = 0
        cells = 0
        dimensions: list[str] = []
        filters = 0
        freeze_panes = 0
        print_areas = 0
        for name in sorted(item for item in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", item)):
            root = xml_root(archive, name)
            if root is None:
                continue
            formulas += len(root.findall(".//m:f", OFFICE_NS))
            cells += len(root.findall(".//m:c", OFFICE_NS))
            dimensions.extend(node.attrib.get("ref", "") for node in root.findall(".//m:dimension", OFFICE_NS))
            filters += len(root.findall(".//m:autoFilter", OFFICE_NS))
            freeze_panes += len([node for node in root.findall(".//m:pane", OFFICE_NS) if node.attrib.get("state") == "frozen"])
        defined = workbook.findall(".//m:definedNames/m:definedName", OFFICE_NS)
        print_areas = len([node for node in defined if node.attrib.get("name") == "_xlnm.Print_Area"])
        names = set(archive.namelist())
        return {
            "sheets": len(sheets),
            "sheet_names": sheet_names,
            "hidden_sheets": hidden,
            "cells": cells,
            "formulas": formulas,
            "dimensions": dimensions,
            "filters": filters,
            "freeze_panes": freeze_panes,
            "print_areas": print_areas,
            "comments_parts": len([name for name in names if name.startswith("xl/comments") and name.endswith(".xml")]),
            "external_links": len([name for name in names if name.startswith("xl/externalLinks/") and name.endswith(".xml")]),
            "charts": len([name for name in names if name.startswith("xl/charts/") and name.endswith(".xml")]),
        }


def inspect_pdf(payload: bytes) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"parser": "unavailable", "bytes": len(payload)}
    reader = PdfReader(io.BytesIO(payload))
    characters = 0
    annotations = 0
    for page in reader.pages:
        characters += len(page.extract_text() or "")
        annotations += len(page.get("/Annots") or [])
    return {
        "parser": "pypdf",
        "pages": len(reader.pages),
        "text_characters": characters,
        "annotations": annotations,
        "attachments": len(reader.attachments),
        "encrypted": reader.is_encrypted,
    }


def inspect_eml(payload: bytes) -> dict[str, Any]:
    message = email.message_from_bytes(payload, policy=policy.default)
    attachments = list(message.iter_attachments())
    bodies: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "text" and part.get_content_disposition() != "attachment":
            try:
                bodies.append(part.get_content())
            except (LookupError, UnicodeDecodeError):
                pass
    body = "\n".join(bodies)
    return {
        "subject": str(message.get("Subject", "")),
        "from_present": bool(message.get("From")),
        "to_present": bool(message.get("To")),
        "date_present": bool(message.get("Date")),
        "attachments": len(attachments),
        "body_characters": len(body),
        "quoted_reply_markers": len(re.findall(r"(?mi)^(from:|sent:|to:|subject:|on .+ wrote:|>)", body)),
    }


def inspect_csv(payload: bytes) -> dict[str, Any]:
    text = payload.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    return {
        "rows": len(rows),
        "columns_max": max((len(row) for row in rows), default=0),
        "blank_rows": sum(1 for row in rows if not any(cell.strip() for cell in row)),
    }


def inspect_payload(name: str, payload: bytes) -> dict[str, Any]:
    suffix = Path(name).suffix.lower()
    inspectors = {
        ".docx": inspect_docx,
        ".xlsx": inspect_xlsx,
        ".pdf": inspect_pdf,
        ".eml": inspect_eml,
        ".csv": inspect_csv,
    }
    detail = inspectors[suffix](payload) if suffix in inspectors else {}
    return {"name": name, "type": suffix.lstrip(".") or "unknown", "bytes": len(payload), "sha256": sha256_bytes(payload), "detail": detail}


def iter_targets(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        if path.is_dir():
            for child in sorted(item for item in path.rglob("*") if item.is_file()):
                if child.suffix.lower() in {".docx", ".xlsx", ".pdf", ".eml", ".csv", ".zip"}:
                    yield from iter_targets([child])
            continue
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                package = {
                    "name": str(path),
                    "type": "zip",
                    "bytes": path.stat().st_size,
                    "sha256": sha256_path(path),
                    "detail": {"members": len(members)},
                }
                yield package
                for member in members:
                    if Path(member.filename).suffix.lower() in {".docx", ".xlsx", ".pdf", ".eml", ".csv"}:
                        record = inspect_payload(member.filename, archive.read(member))
                        record["container"] = str(path)
                        yield record
            continue
        yield inspect_payload(str(path), path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--json-out", type=Path, help="Optional machine-readable output path.")
    args = parser.parse_args()
    missing = [str(path) for path in args.paths if not path.exists()]
    if missing:
        print(json.dumps({"error": "missing paths", "paths": missing}), file=sys.stderr)
        return 2
    records = list(iter_targets(args.paths))
    rendered = json.dumps({"schema_version": "1.0.0", "records": records}, indent=2 if args.pretty else None, sort_keys=True)
    print(rendered)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
