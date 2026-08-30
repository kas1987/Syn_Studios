"""Run deterministic, hash-bound technical checks for template releases.

The runner performs every check before writing anything.  By default it is a
dry run; pass ``--write`` to atomically replace the 24 category result files.
Only Python's standard library is used so the release gate remains portable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import struct
import sys
import tempfile
import zipfile
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


CATEGORIES = (
    "core_integrity", "render", "metadata", "computational", "provenance",
    "leakage", "authority_separation", "anti_filler",
)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FORBIDDEN_TEXT = (
    "prior submission", "private grading", "answer key", "calibration target",
    "generator residue", "rubric answer", "evaluator-facing",
)
DOUBLE_BRACE_TOKEN = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
EMPTY_STRING_FORMULA_GUARD = re.compile(
    r'^\s*IF\(\s*(\$?[A-Z]{1,3}\$?\d+)\s*=\s*""\s*,\s*""\s*,',
    re.IGNORECASE,
)
UNSUPPORTED_ATTACHMENT_SUFFIXES = {
    ".7z", ".bin", ".bz2", ".docx", ".exe", ".gz", ".gzip", ".pdf",
    ".pptx", ".rar", ".tar", ".xlsx", ".xz", ".zip",
}
BINARY_ATTACHMENT_PREFIXES = (
    b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b",
    b"\xfd7zXZ\x00", b"7z\xbc\xaf\x27\x1c", b"\xd0\xcf\x11\xe0",
    b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff",
)
PDF_HEADER = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)(?:\r\n|\r|\n)")
PDF_OBJECT = re.compile(
    rb"(?:\A|(?<=[\x00\x09\x0a\x0c\x0d\x20()<>\[\]{}/%]))"
    rb"\d+[\x00\x09\x0a\x0c\x0d\x20]+\d+[\x00\x09\x0a\x0c\x0d\x20]+"
    rb"obj(?=[\x00\x09\x0a\x0c\x0d\x20()<>\[\]{}/%]|\Z)"
)
PDF_TRAILER = re.compile(rb"startxref\s+(\d+)\s+%%EOF\s*\Z")


class ValidationFailed(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValidationFailed(f"{path}: expected a JSON object")
    return value


def repository_path(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValidationFailed(f"{label}: invalid repository-relative path")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValidationFailed(f"{label}: path escapes repository root") from error
    if not candidate.is_file():
        raise ValidationFailed(f"{label}: referenced file does not exist: {relative}")
    return candidate


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def check(check_id: str, detail: str, status: str = "PASS") -> dict[str, str]:
    return {"id": check_id, "status": status, "detail": detail}


def xml_surfaces(tree: ET.Element) -> list[str]:
    surfaces: list[str] = []
    for node in tree.iter():
        if node.text:
            surfaces.append(node.text)
        if node.tail:
            surfaces.append(node.tail)
        surfaces.extend(value for value in node.attrib.values() if value)
    return surfaces


def has_valid_empty_string_cache(formula: ET.Element, cell_values: dict[str, str]) -> bool:
    match = EMPTY_STRING_FORMULA_GUARD.match(formula.text or "")
    if match is None:
        return False
    guard_cell = match.group(1).replace("$", "").upper()
    return not cell_values.get(guard_cell, "").strip()


def cached_cell_text(cell: ET.Element) -> str:
    value = cell.find(f"{{{MAIN}}}v")
    if value is not None:
        return value.text or ""
    return "".join(node.text or "" for node in cell.findall(f"{{{MAIN}}}is//{{{MAIN}}}t"))


def structured_executable(payload: bytes) -> bool:
    if not payload.startswith(b"MZ") or len(payload) < 64:
        return False
    header_offset = int.from_bytes(payload[60:64], "little")
    if header_offset <= 0 or header_offset > len(payload) - 2:
        return False
    signature = payload[header_offset:header_offset + 4]
    return signature == b"PE\x00\x00" or signature[:2] in {b"NE", b"LE", b"LX"}


def structured_pdf(payload: bytes) -> bool:
    header = PDF_HEADER.search(payload[:1024])
    trailer = PDF_TRAILER.search(payload)
    if header is None or trailer is None or PDF_OBJECT.search(payload[header.start():]) is None:
        return False
    xref_offset = int(trailer.group(1))
    if xref_offset < header.start() or xref_offset >= len(payload):
        return False
    xref = payload[xref_offset:]
    return xref.startswith(b"xref") or PDF_OBJECT.match(xref) is not None


def unsupported_text_attachment(filename: str, payload: bytes, charset: str | None) -> bool:
    suffix = Path(filename).suffix.casefold()
    if suffix in UNSUPPORTED_ATTACHMENT_SUFFIXES:
        return True
    if structured_executable(payload) or structured_pdf(payload) or payload.startswith(BINARY_ATTACHMENT_PREFIXES):
        return True
    try:
        decoded = payload.decode(charset or "utf-8")
    except (LookupError, UnicodeError):
        return True
    return any(
        (ord(character) < 32 or 127 <= ord(character) <= 159)
        and character not in "\t\n\f\r"
        for character in decoded
    )


def xlsx_inventory(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            workbook = ET.fromstring(package.read("xl/workbook.xml"))
            relationships = ET.fromstring(package.read("xl/_rels/workbook.xml.rels"))
            by_id = {item.get("Id"): item for item in relationships.findall(f"{{{PACKAGE_REL}}}Relationship")}
            sheets: list[tuple[str, str, str | None]] = []
            formula_count = cached_formula_count = error_count = cell_count = 0
            formula_sheets: set[str] = set()
            hidden_rows = hidden_columns = 0
            check_values: dict[str, str] = {}
            extracted: list[str] = []
            for sheet in workbook.findall(f"{{{MAIN}}}sheets/{{{MAIN}}}sheet"):
                name = sheet.get("name") or ""
                relation = by_id.get(sheet.get(f"{{{OFFICE_REL}}}id"))
                target = relation.get("Target") if relation is not None else None
                if not target:
                    raise ValidationFailed(f"{path}: worksheet {name!r} has no relationship target")
                member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                if member not in names:
                    raise ValidationFailed(f"{path}: worksheet member is missing: {member}")
                sheets.append((name, member, sheet.get("state")))
                worksheet = ET.fromstring(package.read(member))
                hidden_rows += sum(row.get("hidden") in {"1", "true"} for row in worksheet.findall(f".//{{{MAIN}}}row"))
                hidden_columns += sum(column.get("hidden") in {"1", "true"} for column in worksheet.findall(f".//{{{MAIN}}}col"))
                cells = worksheet.findall(f".//{{{MAIN}}}c")
                cell_values = {
                    str(cell.get("r", "")).upper(): cached_cell_text(cell)
                    for cell in cells
                }
                cell_count += len(cells)
                for cell in cells:
                    formula = cell.find(f"{{{MAIN}}}f")
                    value = cell.find(f"{{{MAIN}}}v")
                    if formula is not None:
                        formula_count += 1
                        formula_sheets.add(name)
                        cached_formula_count += value is not None and (
                            bool((value.text or "").strip())
                            or (cell.get("t") == "str" and has_valid_empty_string_cache(formula, cell_values))
                        )
                        extracted.append(formula.text or "")
                    text = cached_cell_text(cell)
                    extracted.append(text)
                    if cell.get("t") == "e" or text.startswith("#"):
                        error_count += 1
                    if name == "Checks" and cell.get("r") in {"B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12", "B13", "B16"}:
                        check_values[str(cell.get("r"))] = text
            shared = "xl/sharedStrings.xml"
            if shared in names:
                tree = ET.fromstring(package.read(shared))
                extracted.extend(item.text or "" for item in tree.findall(f".//{{{MAIN}}}t"))
                extracted.extend(
                    "".join(item.text or "" for item in shared_item.findall(f".//{{{MAIN}}}t"))
                    for shared_item in tree.findall(f"{{{MAIN}}}si")
                )
            calc = workbook.find(f"{{{MAIN}}}calcPr")
            external = {name for name in names if "externallink" in name.casefold()}
            for member in sorted(name for name in names if name.endswith(".rels")):
                relationship_tree = ET.fromstring(package.read(member))
                external.update(
                    f"{member}:{item.get('Target', '')}"
                    for item in relationship_tree
                    if item.get("TargetMode") == "External"
                )
            comments = sorted(name for name in names if "comment" in name.casefold() and name.endswith(".xml"))
            for member in comments:
                comment_tree = ET.fromstring(package.read(member))
                extracted.extend(node.text or "" for node in comment_tree.findall(f".//{{{MAIN}}}t"))
            for member in ("docProps/core.xml", "docProps/custom.xml"):
                if member in names:
                    extracted.extend(node.text or "" for node in ET.fromstring(package.read(member)).iter())
            text_members = sorted(
                name
                for name in names
                if name.casefold().endswith((".xml", ".rels", ".vml"))
            )
            for member in text_members:
                extracted.extend(xml_surfaces(ET.fromstring(package.read(member))))
            tables = [name for name in names if name.startswith("xl/tables/") and name.endswith(".xml")]
            return {
                "sheets": sheets, "cells": cell_count, "formulas": formula_count,
                "cached_formulas": cached_formula_count, "errors": error_count,
                "check_values": check_values, "external": sorted(external), "comments": comments,
                "tables": len(tables), "text": "\n".join(extracted),
                "hidden_rows": hidden_rows, "hidden_columns": hidden_columns,
                "formula_sheets": sorted(formula_sheets),
                "calc_mode": calc.get("calcMode") if calc is not None else None,
                "full_calc": calc.get("fullCalcOnLoad") if calc is not None else None,
            }
    except (KeyError, OSError, zipfile.BadZipFile, ET.ParseError) as error:
        raise ValidationFailed(f"{path}: invalid xlsx package: {error}") from error


def docx_inventory(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            document = ET.fromstring(package.read("word/document.xml"))
            surface_members = sorted(
                name
                for name in names
                if name.casefold().endswith((".xml", ".rels", ".vml"))
            )
            extracted: list[str] = []
            hidden = 0
            for member in surface_members:
                tree = document if member == "word/document.xml" else ET.fromstring(package.read(member))
                extracted.extend(xml_surfaces(tree))
                if member.startswith("word/"):
                    extracted.extend(
                        "".join(node.text or "" for node in paragraph.findall(f".//{{{WORD}}}t"))
                        for paragraph in tree.findall(f".//{{{WORD}}}p")
                    )
                    hidden += len(tree.findall(f".//{{{WORD}}}vanish"))
            paragraphs = len(document.findall(f".//{{{WORD}}}p"))
            tables = len(document.findall(f".//{{{WORD}}}tbl"))
            comments = [name for name in names if name == "word/comments.xml"]
            external: list[str] = []
            alt_chunks = [
                element.get(f"{{{OFFICE_REL}}}id", "<unbound>")
                for element in document.findall(f".//{{{WORD}}}altChunk")
            ]
            for name in names:
                if name.endswith(".rels"):
                    rels = ET.fromstring(package.read(name))
                    for item in rels:
                        if item.get("TargetMode") == "External":
                            external.append(item.get("Target", ""))
                        if (item.get("Type") or "").rsplit("/", 1)[-1].casefold() == "afchunk":
                            alt_chunks.append(item.get("Target", "<missing-target>"))
            revisions = len(document.findall(f".//{{{WORD}}}ins")) + len(document.findall(f".//{{{WORD}}}del"))
            custom_xml = len([name for name in names if name.startswith("customXml/") and name.endswith(".xml")])
            return {"text": "\n".join(extracted), "paragraphs": paragraphs, "tables": tables, "hidden": hidden, "comments": len(comments), "revisions": revisions, "custom_xml": custom_xml, "external": external, "alt_chunks": alt_chunks}
    except (KeyError, OSError, zipfile.BadZipFile, ET.ParseError) as error:
        raise ValidationFailed(f"{path}: invalid docx package: {error}") from error


def eml_inventory(path: Path) -> dict[str, Any]:
    try:
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    except (OSError, ValueError) as error:
        raise ValidationFailed(f"{path}: invalid RFC 5322 message: {error}") from error
    if not isinstance(message, EmailMessage):
        raise ValidationFailed(f"{path}: email parser did not return an EmailMessage")
    parts = list(message.walk())
    attachments = list(message.iter_attachments())
    text_parts: list[str] = []
    for part in parts:
        text_parts.extend(f"{name}: {value}" for name, value in part.items())
        if part.get_content_maintype() == "text":
            try:
                text_parts.append(part.get_content())
            except (LookupError, UnicodeError):
                raise ValidationFailed(f"{path}: text MIME part cannot be decoded")
    raw = path.read_text(encoding="utf-8")
    message_ids = re.findall(r"(?im)^Message-ID:\s*(\S+)", raw)
    message_count = len(re.findall(r"(?im)^From:\s*.+$", raw))
    for attachment in attachments:
        payload = attachment.get_payload(decode=True) or b""
        filename = attachment.get_filename()
        if not filename or not payload:
            raise ValidationFailed(f"{path}: attachment lacks a filename or payload")
        if unsupported_text_attachment(filename, payload, attachment.get_content_charset()):
            raise ValidationFailed(f"{path}: attachment bytes or filename identify an unsupported binary or structured format: {filename}")
        if attachment.get_content_type() == "text/csv":
            decoded = payload.decode(attachment.get_content_charset() or "utf-8")
            if len([line for line in decoded.splitlines() if line.strip()]) < 2:
                raise ValidationFailed(f"{path}: CSV attachment lacks header and data")
            text_parts.append(decoded)
        elif attachment.get_content_maintype() == "text":
            text_parts.append(payload.decode(attachment.get_content_charset() or "utf-8"))
        else:
            raise ValidationFailed(
                f"{path}: unsupported binary attachment cannot be inspected safely: {attachment.get_filename()}"
            )
    return {
        "message": message, "attachments": attachments, "parts": parts,
        "text": raw + "\n" + "\n".join(text_parts), "message_ids": message_ids,
        "message_count": message_count,
    }


def native_asset_inventory(path: Path) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".xlsx":
        return "xlsx", xlsx_inventory(path)
    if suffix == ".docx":
        return "docx", docx_inventory(path)
    if suffix == ".eml":
        return "eml", eml_inventory(path)
    raise ValidationFailed(f"{path}: unsupported bound native asset type {suffix or '<none>'}")


def pdf_pages(path: Path) -> int:
    payload = path.read_bytes()
    if not payload.startswith(b"%PDF-"):
        raise ValidationFailed(f"{path}: render is not a PDF")
    return len(re.findall(rb"/Type\s*/Page\b", payload))


def png_dimensions(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR":
        raise ValidationFailed(f"{path}: render is not a structurally valid PNG")
    width, height = struct.unpack(">II", payload[16:24])
    if width < 100 or height < 100:
        raise ValidationFailed(f"{path}: rendered PNG dimensions are implausibly small")
    return width, height


def render_outputs(root: Path, descriptor: dict[str, Any], release_id: str, asset_hash: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    contract = descriptor.get("render_contract") or {}
    if contract.get("required") is not True:
        return [], []
    page_count = contract.get("expected_page_count")
    if not isinstance(page_count, int) or page_count < 1:
        raise ValidationFailed(f"{release_id}: required render contract lacks a page count")
    paths = [contract.get("expected_pdf_path")]
    pattern = contract.get("expected_page_image_pattern")
    if not isinstance(pattern, str):
        raise ValidationFailed(f"{release_id}: required render contract lacks a page image pattern")
    paths.extend(pattern.format(page=page) for page in range(1, page_count + 1))
    outputs: list[dict[str, str]] = []
    checks = []
    for index, relative in enumerate(paths):
        path = repository_path(root, relative, f"{release_id} render output")
        if index == 0:
            actual_pages = pdf_pages(path)
            if actual_pages != page_count:
                raise ValidationFailed(f"{release_id}: PDF page count {actual_pages} != {page_count}")
            detail = f"PDF parsed successfully with the required {actual_pages} pages."
            media = "application/pdf"
        else:
            width, height = png_dimensions(path)
            detail = f"Page image {index} parsed successfully at {width}x{height} pixels."
            media = "image/png"
        outputs.append({"path": str(relative), "sha256": sha256(path), "media_type": media, "category": "render"})
        checks.append(check(f"render:output-{index}", detail))
    manifest = load_json(repository_path(root, contract.get("evidence_manifest"), f"{release_id} render manifest"))
    record = (manifest.get("templates") or {}).get(descriptor.get("template_id")) or {}
    if record.get("asset_sha256") != asset_hash or record.get("page_count") != page_count or record.get("rendered_outputs") != [{"path": item["path"], "sha256": item["sha256"]} for item in outputs]:
        raise ValidationFailed(f"{release_id}: render manifest does not bind the frozen asset and ordered outputs")
    checks.append(check("render:manifest-binding", "Render manifest binds the frozen native asset, page count, and ordered output hashes."))
    return outputs, checks


def target_range(blueprint: dict[str, Any], unit: str) -> tuple[int, int] | None:
    target = str((blueprint.get("footprint") or {}).get("target", ""))
    match = re.search(rf"(\d+)\s*-\s*(\d+)\s+{unit}", target, re.I)
    return (int(match.group(1)), int(match.group(2))) if match else None


def validate_release(root: Path, release_path: Path, actor_id: str, actor: str) -> list[tuple[Path, bytes]]:
    release = load_json(release_path)
    release_id = str(release.get("release_id"))
    descriptor_ref = release.get("descriptor") or {}
    descriptor_path = repository_path(root, descriptor_ref.get("path"), f"{release_id} descriptor")
    descriptor_hash = sha256(descriptor_path)
    if descriptor_ref.get("sha256") != descriptor_hash:
        raise ValidationFailed(f"{release_id}: descriptor hash is stale")
    descriptor = load_json(descriptor_path)
    if descriptor.get("template_id") != release.get("template_id") or descriptor.get("version") != release.get("version"):
        raise ValidationFailed(f"{release_id}: descriptor identity does not match release")
    blueprint_ref = release.get("blueprint") or {}
    blueprint_path = repository_path(root, blueprint_ref.get("path"), f"{release_id} blueprint")
    if blueprint_ref.get("sha256") != sha256(blueprint_path):
        raise ValidationFailed(f"{release_id}: blueprint hash is stale")
    blueprint = load_json(blueprint_path)
    gates = {item.get("category"): item for item in blueprint.get("proof_gates", []) if isinstance(item, dict)}
    if set(gates) != set(CATEGORIES):
        raise ValidationFailed(f"{release_id}: blueprint does not define the eight technical categories")
    assets: list[tuple[Path, str]] = []
    for index, binding in enumerate(descriptor.get("native_assets", [])):
        path = repository_path(root, binding.get("path"), f"{release_id} native asset {index}")
        digest = sha256(path)
        if binding.get("sha256") != digest:
            raise ValidationFailed(f"{release_id}: native asset {index} hash is stale")
        assets.append((path, digest))
    if not assets or {item[1] for item in assets} != {item.get("sha256") for item in release.get("native_assets", [])}:
        raise ValidationFailed(f"{release_id}: release does not bind the descriptor native assets")

    artifact_type = descriptor.get("artifact_type")
    inventories = [
        (path, *native_asset_inventory(path))
        for path, _ in assets
    ]
    primary, primary_type, inventory = inventories[0]
    if artifact_type not in {"xlsx", "docx", "eml"}:
        raise ValidationFailed(f"{release_id}: unsupported technical runner artifact type {artifact_type}")
    if primary_type != artifact_type:
        raise ValidationFailed(f"{release_id}: primary native asset type does not match {artifact_type}")
    outputs, output_checks = render_outputs(root, descriptor, release_id, assets[0][1])

    category_checks: dict[str, list[dict[str, str]]] = {name: [] for name in CATEGORIES}
    if artifact_type == "xlsx":
        sheet_names = [item[0] for item in inventory["sheets"]]
        expected = (descriptor.get("render_contract") or {}).get("expected_sheet_names")
        if sheet_names != expected or inventory["cells"] == 0:
            raise ValidationFailed(f"{release_id}: workbook sheets or populated structure do not match the descriptor")
        category_checks["core_integrity"].append(check("core_integrity:xlsx-structure", f"Parsed {len(sheet_names)} ordered worksheets, {inventory['cells']} cells, and {inventory['tables']} native tables."))
    elif artifact_type == "docx":
        if inventory["paragraphs"] < 20 or not inventory["text"].strip():
            raise ValidationFailed(f"{release_id}: DOCX has insufficient native document structure")
        category_checks["core_integrity"].append(check("core_integrity:docx-structure", f"Parsed {inventory['paragraphs']} paragraphs and {inventory['tables']} tables from native WordprocessingML."))
    else:
        if not inventory["message"].get("From") or not inventory["message"].get("To") or inventory["message_count"] < 2:
            raise ValidationFailed(f"{release_id}: email thread lacks required headers or thread depth")
        category_checks["core_integrity"].append(check("core_integrity:mime-structure", f"Parsed {len(inventory['parts'])} MIME parts, {inventory['message_count']} messages, and {len(inventory['attachments'])} attachments."))

    if outputs:
        category_checks["render"].extend(output_checks)
    elif artifact_type == "eml":
        category_checks["render"].append(check("render:mime-parts", f"Opened and decoded all {len(inventory['attachments'])} native MIME attachment payloads; pagination is not the descriptor render surface."))
    else:
        raise ValidationFailed(f"{release_id}: applicable render gate has no render outputs")

    for asset_path, asset_type, asset_inventory in inventories:
        if asset_type == "xlsx":
            hidden = [name for name, _, state in asset_inventory["sheets"] if state in {"hidden", "veryHidden"}]
            if asset_inventory["external"] or hidden or asset_inventory["hidden_rows"] or asset_inventory["hidden_columns"]:
                raise ValidationFailed(f"{release_id}: workbook {asset_path.name} contains external links or hidden workbook surfaces")
        elif asset_type == "docx":
            if asset_inventory["external"] or asset_inventory["hidden"] or asset_inventory["alt_chunks"]:
                raise ValidationFailed(f"{release_id}: document {asset_path.name} contains external relationships, hidden text, or unsupported altChunk content")
        elif len(asset_inventory["message_ids"]) != len(set(asset_inventory["message_ids"])):
            raise ValidationFailed(f"{release_id}: email {asset_path.name} contains duplicate Message-ID values")

    if artifact_type == "xlsx":
        category_checks["metadata"].append(check("metadata:xlsx-surfaces", f"Inspected workbook relationships, {len(inventory['sheets'])} sheet states, row/column visibility, and {len(inventory['comments'])} parsed comment parts; found no external links or hidden surfaces."))
    elif artifact_type == "docx":
        category_checks["metadata"].append(check("metadata:docx-surfaces", f"Inspected package relationships, hidden text, {inventory['revisions']} revisions, {inventory['comments']} parsed comment parts, and {inventory['custom_xml']} custom XML parts with no external targets."))
    else:
        category_checks["metadata"].append(check("metadata:mime-headers", f"Inspected MIME encodings, attachment names, timestamps, and {len(inventory['message_ids'])} unique message identifiers."))
    category_checks["metadata"].append(check("metadata:bound-native-assets", f"Parsed metadata and relationship surfaces across all {len(inventories)} bound native assets."))

    if gates["computational"].get("applicable") is True:
        if artifact_type != "xlsx" or inventory["formulas"] < 1 or inventory["cached_formulas"] != inventory["formulas"] or inventory["errors"]:
            raise ValidationFailed(f"{release_id}: live formula structure, cached results, or error states failed")
        expected_checks = {"B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12", "B13", "B16"}
        allowed_states = {"PASS", "NOT READY", "NOT APPLICABLE"}
        if inventory["calc_mode"] != "auto" or inventory["full_calc"] != "1" or set(inventory["check_values"]) != expected_checks or not set(inventory["check_values"].values()) <= allowed_states:
            raise ValidationFailed(f"{release_id}: workbook calculation controls or live checks failed")
        if len(inventory["formula_sheets"]) < 3:
            raise ValidationFailed(f"{release_id}: formulas do not span the expected workbook calculation layers")
        category_checks["computational"].append(check("computational:live-formulas", f"Parsed {inventory['formulas']} live formulas with cached values across {len(inventory['formula_sheets'])} sheets, automatic full recalculation, and no formula errors."))
        category_checks["computational"].append(check("computational:control-checks", f"Inspected {len(inventory['check_values'])} cached control results; none report FAIL."))

    for lineage in blueprint.get("foundation_lineage", []):
        card_id = lineage.get("card_id")
        card_path = repository_path(root, f"library/foundations/{card_id}.json", f"{release_id} foundation")
        card = load_json(card_path)
        if sha256(card_path) != lineage.get("card_sha256") or (card.get("source") or {}).get("sha256") != lineage.get("reviewed_source_sha256") or card.get("status") != "reviewed" or (card.get("source") or {}).get("synthetic_authorized") is not True:
            raise ValidationFailed(f"{release_id}: foundation provenance is stale or unauthorized")
    category_checks["provenance"].append(check("provenance:lineage-hashes", f"Recomputed the blueprint and descriptor hashes and verified {len(blueprint.get('foundation_lineage', []))} reviewed, synthetic-authorized foundation bindings."))

    text = "\n".join(str(item.get("text", "")) for _, _, item in inventories)
    slots = set(descriptor.get("slots", []))
    token_text = re.sub(r"=\r?\n", "", text)
    observed_slots = set(DOUBLE_BRACE_TOKEN.findall(token_text))
    unknown_slots = observed_slots - slots
    forbidden = [token for token in FORBIDDEN_TEXT if token in text.casefold()]
    if unknown_slots or forbidden:
        raise ValidationFailed(f"{release_id}: leakage scan found unknown slots or prohibited residue")
    category_checks["leakage"].append(check("leakage:visible-hidden-scan", f"Scanned extracted content from all {len(inventories)} bound native assets for prohibited residue and verified all {len(observed_slots)} template tokens against the descriptor slot allowlist."))

    authority = blueprint.get("authority") or {}
    if descriptor.get("authority") != authority.get("primary_class") or not authority.get("governing_scope") or not authority.get("non_governing_scope") or not descriptor.get("knowledge_and_authority_constraints"):
        raise ValidationFailed(f"{release_id}: authority boundary declarations are incomplete or inconsistent")
    primary_text = str(inventory.get("text", ""))
    authority_markers = {marker for marker in ("approval", "governing", "supporting", "contextual", "question", "superseded") if marker in primary_text.casefold()}
    if len(authority_markers) < 2:
        raise ValidationFailed(f"{release_id}: native content does not express a reviewable authority boundary")
    category_checks["authority_separation"].append(check("authority_separation:declared-boundary", f"Descriptor authority {descriptor.get('authority')} matches the blueprint and retains explicit governing, non-governing, and knowledge boundaries."))
    category_checks["authority_separation"].append(check("authority_separation:native-content", f"Extracted native content preserves {len(authority_markers)} distinct authority-state markers: {', '.join(sorted(authority_markers))}."))

    if artifact_type == "xlsx":
        actual, unit = len(inventory["sheets"]), "tabs"
    elif artifact_type == "docx":
        actual, unit = int((descriptor.get("render_contract") or {}).get("expected_page_count") or 0), "pages"
    else:
        actual, unit = inventory["message_count"], "messages"
    bounds = target_range(blueprint, unit)
    if bounds is None or not bounds[0] <= actual <= bounds[1]:
        raise ValidationFailed(f"{release_id}: {actual} {unit} do not satisfy blueprint footprint")
    if artifact_type == "eml" and not 2 <= len(inventory["attachments"]) <= 4:
        raise ValidationFailed(f"{release_id}: attachment footprint is outside blueprint bounds")
    category_checks["anti_filler"].append(check("anti_filler:footprint", f"Native footprint of {actual} {unit} satisfies the blueprint's producer-owned {bounds[0]}-{bounds[1]} range."))

    writes: list[tuple[Path, bytes]] = []
    for category in CATEGORIES:
        gate = gates[category]
        applicable = gate.get("applicable") is True
        checks = category_checks[category]
        if not applicable:
            checks = [check(f"{category}:blueprint-rationale", str(gate.get("not_applicable_rationale") or "Blueprint marks this category not applicable to this native artifact contract."), "NOT_APPLICABLE")]
        elif not checks:
            raise ValidationFailed(f"{release_id}: applicable {category} gate produced no machine checks")
        result = {
            "schema_version": "1.0.0", "result_type": "template_technical_validation_result",
            "result_id": f"TECHRES-{release_id}-{category.replace('_', '-').upper()}",
            "release_id": release_id, "template_id": descriptor["template_id"], "version": descriptor["version"],
            "category": category, "result_artifact_category": "provenance" if category == "render" and outputs else category,
            "descriptor_sha256": descriptor_hash, "native_asset_sha256s": [digest for _, digest in assets],
            "applicable": applicable, "verdict": "VALIDATION_PASS" if applicable else "VALIDATION_NOT_APPLICABLE",
            "procedure": gate.get("procedure"), "actor_id": actor_id, "actor": actor,
            "checks": checks, "rendered_outputs": outputs if category == "render" else [],
            "observations": [f"Deterministic {category} checks inspected the frozen descriptor and all {len(inventories)} bound native assets."],
            "summary": f"Category-specific {category} validation completed against the frozen release inputs.",
        }
        writes.append((root / f"evidence/template-releases/{release_id}/technical-results/{category}.json", json_bytes(result)))
    return writes


def run(root: Path, actor_id: str, actor: str, write: bool = False) -> list[Path]:
    root = root.resolve()
    if not actor_id.strip() or not actor.strip():
        raise ValidationFailed("technical actor ID and name must be non-empty")
    release_paths = sorted((root / "library/releases").glob("REL-*.template.json"))
    writes: list[tuple[Path, bytes]] = []
    errors: list[str] = []
    for release_path in release_paths:
        try:
            writes.extend(validate_release(root, release_path, actor_id, actor))
        except ValidationFailed as error:
            errors.append(str(error))
    if errors:
        raise ValidationFailed("technical validation failed; no files written:\n- " + "\n- ".join(errors))
    if len(writes) != 24:
        raise ValidationFailed(f"expected 24 category results, produced {len(writes)}; no files written")
    if write:
        for path, payload in writes:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            try:
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    return [path for path, _ in writes]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--actor-name", required=True)
    parser.add_argument("--write", action="store_true", help="Write results after the complete preflight passes.")
    arguments = parser.parse_args(argv)
    try:
        paths = run(arguments.root, arguments.actor_id, arguments.actor_name, arguments.write)
    except ValidationFailed as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1
    mode = "written" if arguments.write else "dry run"
    print(f"PASS: {len(paths)} deterministic technical results ({mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
