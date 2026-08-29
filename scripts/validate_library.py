#!/usr/bin/env python3
"""Validate library records, lineage, fixtures, releases, and the consumer catalog."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import posixpath
import re
import struct
import sys
import unicodedata
import zipfile
import zlib
from email import policy
from email.parser import BytesParser
from io import StringIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("FAIL: jsonschema is required to validate the library", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_NAMES = (
    "foundation-card",
    "artifact-blueprint",
    "template-descriptor",
    "release-evidence",
    "template-release",
    "artifact-catalog",
)
LAYER_ORDER = {name: index for index, name in enumerate(("core", "operational_depth", "adjacent_context", "working_residue", "handling_history"))}
PROOF_CATEGORIES = {"core_integrity", "render", "metadata", "computational", "provenance", "leakage", "authority_separation", "anti_filler"}
EVIDENCE_ROOT = Path("evidence/template-releases")
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
WORDPROCESSING_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MEDIA_SUFFIXES = {
    "text/plain": {".txt"},
    "text/csv": {".csv"},
    "application/json": {".json"},
    "application/x-ndjson": {".jsonl", ".ndjson"},
    "image/png": {".png"},
    "application/pdf": {".pdf"},
}


def as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def load_json(path: Path, root: Path, findings: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        findings.append(f"{display_path(path, root)}:<root>: cannot read JSON: {error}")
        return None
    if not isinstance(value, dict):
        findings.append(f"{display_path(path, root)}:<root>: record must be a JSON object")
        return None
    return value


def schema_findings(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    findings: list[str] = []
    for error in sorted(Draft202012Validator(schema).iter_errors(data), key=lambda item: [str(part) for part in item.path]):
        location = ".".join(str(part) for part in error.path) or "<root>"
        findings.append(f"{label}:{location}: {error.message}")
    return findings


def validate_descriptor_data(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    findings = schema_findings(data, schema, label)
    tables = [item for item in as_list(as_dict(data.get("population_contract")).get("tables")) if isinstance(item, dict)]
    table_names = [item.get("name") for item in tables if isinstance(item.get("name"), str)]
    if len(table_names) != len(set(table_names)):
        findings.append(f"{label}:population_contract.tables: table names must be unique")
    for index, table in enumerate(tables):
        minimum_rows, maximum_rows = table.get("minimum_rows"), table.get("maximum_rows")
        if isinstance(minimum_rows, int) and isinstance(maximum_rows, int) and minimum_rows > maximum_rows:
            findings.append(f"{label}:population_contract.tables.{index}: minimum_rows must not exceed maximum_rows")
    table_columns = {item.get("name"): set(as_dict(item.get("columns"))) for item in tables if isinstance(item.get("name"), str)}
    carrier_kinds: list[str] = []
    for index, carrier in enumerate(as_list(as_dict(data.get("population_contract")).get("source_carriers"))):
        if not isinstance(carrier, dict):
            continue
        kind, target = carrier.get("kind"), carrier.get("target_table")
        if isinstance(kind, str):
            carrier_kinds.append(kind)
        target_known = isinstance(target, str) and target in table_columns
        if not target_known:
            findings.append(f"{label}:population_contract.source_carriers.{index}.target_table: unknown population table")
        if kind == "csv_import" and target_known:
            required_columns = {value for value in as_list(carrier.get("required_target_columns")) if isinstance(value, str)}
            if required_columns != table_columns[target]:
                findings.append(f"{label}:population_contract.source_carriers.{index}.required_target_columns: must exactly match target table columns")
    if len(carrier_kinds) != len(set(carrier_kinds)):
        findings.append(f"{label}:population_contract.source_carriers: carrier kinds must be unique")
    return findings


def column_number(value: str) -> int | None:
    match = re.fullmatch(r"([A-Z]+)[1-9][0-9]*", value.replace("$", ""))
    if match is None:
        return None
    result = 0
    for character in match.group(1):
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def range_shape(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    first, last = (part.replace("$", "") for part in value.upper().split(":", 1))
    first_column, last_column = column_number(first), column_number(last)
    first_row_match, last_row_match = re.search(r"[0-9]+$", first), re.search(r"[0-9]+$", last)
    if first_column is None or last_column is None or first_row_match is None or last_row_match is None:
        return None
    return last_column - first_column + 1, int(last_row_match.group()) - int(first_row_match.group())


def inspect_xlsx_contract(path: Path, descriptor: dict[str, Any], label: str, findings: list[str]) -> None:
    """Bind declared sheets, tables, ranges, columns, and capacities to native OOXML."""
    try:
        with zipfile.ZipFile(path) as package:
            workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(package.read("xl/_rels/workbook.xml.rels"))
            by_id = {item.get("Id"): item for item in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship") if item.get("Id")}
            sheets: list[str] = []
            sheet_members: dict[str, str] = {}
            for sheet in workbook.findall(f"{{{SPREADSHEET_NS}}}sheets/{{{SPREADSHEET_NS}}}sheet"):
                name, relationship_id = sheet.get("name"), sheet.get(f"{{{OFFICE_REL_NS}}}id")
                relationship = by_id.get(relationship_id)
                target = relationship.get("Target") if relationship is not None else None
                if not name or not target:
                    continue
                sheets.append(name)
                sheet_members[name] = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))

            expected_sheets = as_list(as_dict(descriptor.get("render_contract")).get("expected_sheet_names"))
            if expected_sheets != sheets:
                findings.append(f"{label}:render_contract.expected_sheet_names: must exactly match native workbook sheet order")

            native_tables: dict[str, tuple[str, str, list[str]]] = {}
            for sheet_name, sheet_member in sheet_members.items():
                relationship_member = posixpath.join(posixpath.dirname(sheet_member), "_rels", posixpath.basename(sheet_member) + ".rels")
                if relationship_member not in package.namelist():
                    continue
                sheet_relationships = ElementTree.fromstring(package.read(relationship_member))
                for relationship in sheet_relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
                    if not str(relationship.get("Type", "")).endswith("/table") or not relationship.get("Target"):
                        continue
                    target = str(relationship.get("Target"))
                    table_member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(sheet_member), target))
                    table = ElementTree.fromstring(package.read(table_member))
                    name, reference = table.get("name") or table.get("displayName"), table.get("ref")
                    columns = [item.get("name", "") for item in table.findall(f"{{{SPREADSHEET_NS}}}tableColumns/{{{SPREADSHEET_NS}}}tableColumn")]
                    if not name or not reference:
                        continue
                    if name in native_tables:
                        findings.append(f"{label}:population_contract.tables: duplicate native table identity {name}")
                    native_tables[name] = (sheet_name, reference.replace("$", ""), columns)

            declared_tables = [item for item in as_list(as_dict(descriptor.get("population_contract")).get("tables")) if isinstance(item, dict)]
            for index, declared in enumerate(declared_tables):
                prefix = f"{label}:population_contract.tables.{index}"
                name = declared.get("name")
                native = native_tables.get(name) if isinstance(name, str) else None
                if native is None:
                    findings.append(f"{prefix}.name: does not resolve to a native workbook table")
                    continue
                native_sheet, native_range, native_columns = native
                declared_range = str(declared.get("range", "")).replace("$", "")
                if declared.get("sheet") != native_sheet:
                    findings.append(f"{prefix}.sheet: does not match native table worksheet")
                if declared_range != native_range:
                    findings.append(f"{prefix}.range: does not match native table range")
                shape = range_shape(declared_range)
                declared_columns = list(as_dict(declared.get("columns")))
                if shape is not None:
                    width, capacity = shape
                    if width != len(declared_columns) or width != len(native_columns):
                        findings.append(f"{prefix}.columns: count does not match native table width")
                    if declared.get("maximum_rows") != capacity:
                        findings.append(f"{prefix}.maximum_rows: must equal native table data capacity")
                if declared_columns != native_columns:
                    findings.append(f"{prefix}.columns: names and order must exactly match native table columns")
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        findings.append(f"{label}: cannot validate workbook contract: {error}")


def pdf_page_count(payload: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page\b", payload))


def validate_blueprint_data(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    findings = schema_findings(data, schema, label)
    layer_values = [item.get("layer") for item in as_list(data.get("complexity_layers")) if isinstance(item, dict)]
    string_layers = [value for value in layer_values if isinstance(value, str)]
    if layer_values:
        if layer_values[0] != "core":
            findings.append(f"{label}:complexity_layers: first layer must be core")
        if len(string_layers) != len(set(string_layers)):
            findings.append(f"{label}:complexity_layers: layer names must be unique")
        known = [LAYER_ORDER[layer] for layer in string_layers if layer in LAYER_ORDER]
        if known != sorted(known):
            findings.append(f"{label}:complexity_layers: layers must follow core-to-handling order")
    gates = [item for item in as_list(data.get("proof_gates")) if isinstance(item, dict)]
    categories = [item.get("category") for item in gates]
    string_categories = [value for value in categories if isinstance(value, str)]
    if set(string_categories) != PROOF_CATEGORIES or len(string_categories) != len(PROOF_CATEGORIES):
        findings.append(f"{label}:proof_gates: must contain each required category exactly once")
    by_category = {item.get("category"): item for item in gates if isinstance(item.get("category"), str)}
    for category in PROOF_CATEGORIES - {"computational"}:
        if by_category.get(category, {}).get("applicable") is not True:
            findings.append(f"{label}:proof_gates.{category}: category must be applicable")
    if data.get("artifact_type") in {"xlsx", "mixed_package"} and by_category.get("computational", {}).get("applicable") is not True:
        findings.append(f"{label}:proof_gates.computational: category must be applicable for computational artifact types")
    return findings


def resolve_bound_path(root: Path, relative: object, label: str, findings: list[str], required_root: Path | None = None) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute():
        findings.append(f"{label}: path must be repository-relative")
        return None
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        findings.append(f"{label}: path escapes repository root")
        return None
    if required_root is not None:
        allowed = (root / required_root).resolve()
        if resolved != allowed and allowed not in resolved.parents:
            findings.append(f"{label}: path must be under {required_root.as_posix()}")
            return None
    if not resolved.is_file():
        findings.append(f"{label}: referenced file does not exist: {relative}")
        return None
    return resolved


def check_bound_file(root: Path, record: dict[str, Any], path_key: str, hash_key: str, label: str, findings: list[str], required_root: Path | None = None) -> Path | None:
    path = resolve_bound_path(root, record.get(path_key), f"{label}.{path_key}", findings, required_root)
    if path is not None and record.get(hash_key) != sha256_file(path):
        findings.append(f"{label}.{hash_key}: hash does not match {record.get(path_key)}")
    return path


def bound_pairs(value: object) -> list[tuple[str, str]]:
    pairs = []
    for item in as_list(value):
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
            pairs.append((item["path"], item["sha256"]))
    return pairs


def validate_pdf_shape(payload: bytes, label: str, findings: list[str]) -> None:
    if len(payload) < 128 or not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-1024:]:
        findings.append(f"{label}: is not a structurally valid PDF")
        return
    media_box = re.search(rb"/MediaBox\s*\[\s*0(?:\.0+)?\s+0(?:\.0+)?\s+([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)\s*\]", payload)
    if media_box is None or float(media_box.group(1)) < 16 or float(media_box.group(2)) < 16 or b"stream" not in payload or re.search(rb"/Type\s*/Page\b", payload) is None:
        findings.append(f"{label}: PDF lacks meaningful page dimensions or content")


def validate_png_shape(payload: bytes, label: str, findings: list[str]) -> None:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        findings.append(f"{label}: declared PNG proof has an invalid signature")
        return
    offset = 8
    chunks: list[tuple[bytes, bytes]] = []
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(payload):
            findings.append(f"{label}: declared PNG proof has a truncated chunk")
            return
        chunk_type = payload[offset + 4:offset + 8]
        chunk_data = payload[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", payload[offset + 8 + length:end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            findings.append(f"{label}: declared PNG proof has an invalid chunk checksum")
            return
        chunks.append((chunk_type, chunk_data))
        offset = end
        if chunk_type == b"IEND":
            break
    if offset != len(payload) or not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13 or chunks[-1] != (b"IEND", b"") or not any(kind == b"IDAT" and data for kind, data in chunks):
        findings.append(f"{label}: declared PNG proof is missing required image chunks")
        return
    width, height = struct.unpack(">II", chunks[0][1][:8])
    if width < 16 or height < 16:
        findings.append(f"{label}: PNG proof dimensions are not meaningful")


def validate_native_asset_shape(path: Path, artifact_type: object, label: str, findings: list[str]) -> None:
    """Reject extension-only impostors before release evidence is considered."""
    expected_suffixes = {"xlsx": ".xlsx", "docx": ".docx", "pdf": ".pdf", "eml": ".eml", "csv": ".csv"}
    if artifact_type == "mixed_package":
        artifact_type = {suffix: kind for kind, suffix in expected_suffixes.items()}.get(path.suffix.casefold())
        if artifact_type is None:
            findings.append(f"{label}: mixed-package asset has an unsupported native suffix")
            return
    expected_suffix = expected_suffixes.get(artifact_type)
    if expected_suffix is not None and path.suffix.casefold() != expected_suffix:
        findings.append(f"{label}: {artifact_type} asset must use the {expected_suffix} suffix")
    if artifact_type in {"xlsx", "docx"}:
        required_member = "xl/workbook.xml" if artifact_type == "xlsx" else "word/document.xml"
        try:
            with zipfile.ZipFile(path) as package:
                names = set(package.namelist())
                if "[Content_Types].xml" not in names or required_member not in names:
                    findings.append(f"{label}: is missing required {artifact_type} package members")
                    return
                content_types = ElementTree.fromstring(package.read("[Content_Types].xml"))
                document = ElementTree.fromstring(package.read(required_member))
                if content_types.tag != f"{{{CONTENT_TYPES_NS}}}Types":
                    findings.append(f"{label}: has an invalid OOXML content-types root")
                expected_part = "/xl/workbook.xml" if artifact_type == "xlsx" else "/word/document.xml"
                expected_main_type = "spreadsheetml.sheet.main+xml" if artifact_type == "xlsx" else "wordprocessingml.document.main+xml"
                overrides = content_types.findall(f"{{{CONTENT_TYPES_NS}}}Override")
                defaults = content_types.findall(f"{{{CONTENT_TYPES_NS}}}Default")
                main_declared = any(item.get("PartName") == expected_part and str(item.get("ContentType", "")).endswith(expected_main_type) for item in overrides)
                main_declared = main_declared or any(item.get("Extension") == "xml" and str(item.get("ContentType", "")).endswith(expected_main_type) for item in defaults)
                if not main_declared:
                    findings.append(f"{label}: OOXML content types do not declare the main document part")
                if artifact_type == "docx":
                    if document.tag != f"{{{WORDPROCESSING_NS}}}document" or document.find(f"{{{WORDPROCESSING_NS}}}body") is None:
                        findings.append(f"{label}: has an invalid WordprocessingML document root or body")
                else:
                    relationship_member = "xl/_rels/workbook.xml.rels"
                    if document.tag != f"{{{SPREADSHEET_NS}}}workbook":
                        findings.append(f"{label}: has an invalid SpreadsheetML workbook root")
                    sheets = document.findall(f"{{{SPREADSHEET_NS}}}sheets/{{{SPREADSHEET_NS}}}sheet")
                    if not sheets or relationship_member not in names:
                        findings.append(f"{label}: workbook must bind at least one worksheet relationship")
                        return
                    relationships = ElementTree.fromstring(package.read(relationship_member))
                    if relationships.tag != f"{{{PACKAGE_REL_NS}}}Relationships":
                        findings.append(f"{label}: has an invalid workbook relationships root")
                        return
                    by_id = {item.get("Id"): item for item in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship") if item.get("Id")}
                    for sheet in sheets:
                        relationship_id = sheet.get(f"{{{OFFICE_REL_NS}}}id")
                        relationship = by_id.get(relationship_id)
                        target = relationship.get("Target") if relationship is not None else None
                        relation_type = relationship.get("Type") if relationship is not None else None
                        if not sheet.get("name") or relationship is None or not target or not relation_type or not relation_type.endswith("/worksheet"):
                            findings.append(f"{label}: workbook sheet is missing a valid worksheet relationship")
                            continue
                        target_member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                        if target_member not in names:
                            findings.append(f"{label}: workbook worksheet relationship target is missing")
                            continue
                        worksheet = ElementTree.fromstring(package.read(target_member))
                        if worksheet.tag != f"{{{SPREADSHEET_NS}}}worksheet":
                            findings.append(f"{label}: worksheet relationship target has an invalid root")
        except (OSError, zipfile.BadZipFile):
            findings.append(f"{label}: is not a valid {artifact_type} OOXML package")
        except (ElementTree.ParseError, KeyError) as error:
            findings.append(f"{label}: contains malformed {artifact_type} OOXML: {error}")
    elif artifact_type == "pdf":
        try:
            validate_pdf_shape(path.read_bytes(), label, findings)
        except OSError as error:
            findings.append(f"{label}: cannot inspect native asset: {error}")
    elif artifact_type == "csv":
        try:
            rows = list(csv.reader(StringIO(path.read_text(encoding="utf-8"))))
        except (OSError, UnicodeError, csv.Error) as error:
            findings.append(f"{label}: is not valid UTF-8 CSV: {error}")
            return
        if len(rows) < 2 or len(rows[0]) < 2 or any(len(row) != len(rows[0]) for row in rows):
            findings.append(f"{label}: CSV must contain a consistent header and data row")
    elif artifact_type == "eml":
        try:
            message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        except (OSError, ValueError) as error:
            findings.append(f"{label}: is not a parseable EML message: {error}")
            return
        required_headers = ("From", "To", "Date", "Subject")
        body = message.get_body(preferencelist=("plain", "html")) if message.is_multipart() else message
        content = body.get_content() if body is not None else ""
        if message.defects or any(not message.get(header) for header in required_headers) or not str(content).strip():
            findings.append(f"{label}: EML must have valid From, To, Date, Subject, MIME structure, and body")


def validate_proof_artifact(
    path: Path,
    artifact: dict[str, Any],
    release: dict[str, Any],
    asset_hashes: set[str],
    label: str,
    findings: list[str],
) -> None:
    media_type = artifact.get("media_type")
    suffix = path.suffix.casefold()
    allowed_suffixes = MEDIA_SUFFIXES.get(media_type)
    if allowed_suffixes is None:
        findings.append(f"{label}.media_type: unsupported proof media type {media_type}")
        return
    if suffix not in allowed_suffixes:
        findings.append(f"{label}.media_type: does not match file suffix {suffix or '<none>'}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        findings.append(f"{label}.path: cannot inspect proof output: {error}")
        return
    if len(payload) < 64:
        findings.append(f"{label}.path: proof output is too small to be meaningful")
        return

    searchable = ""
    if media_type in {"text/plain", "text/csv", "application/json", "application/x-ndjson"}:
        try:
            searchable = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            findings.append(f"{label}.path: declared text proof is not UTF-8: {error}")
            return
        if media_type == "application/json":
            try:
                parsed = json.loads(searchable)
            except json.JSONDecodeError as error:
                findings.append(f"{label}.path: declared JSON proof is malformed: {error}")
                return
            if not isinstance(parsed, dict):
                findings.append(f"{label}.path: JSON proof must be an object")
        elif media_type == "application/x-ndjson":
            try:
                rows = [json.loads(line) for line in searchable.splitlines() if line.strip()]
            except json.JSONDecodeError as error:
                findings.append(f"{label}.path: declared NDJSON proof is malformed: {error}")
                return
            if not rows or any(not isinstance(row, dict) for row in rows):
                findings.append(f"{label}.path: NDJSON proof must contain objects")
        elif media_type == "text/csv":
            rows = list(csv.reader(StringIO(searchable)))
            if len(rows) < 2 or len(rows[0]) < 2:
                findings.append(f"{label}.path: CSV proof must contain a header and data row")
        if not searchable.strip():
            findings.append(f"{label}.path: text proof must not be blank")
            return
        required_tokens = (release.get("release_id"), release.get("template_id"), artifact.get("category"))
        for token in required_tokens:
            if isinstance(token, str) and token not in searchable:
                findings.append(f"{label}.path: text proof does not bind {token}")
        if asset_hashes and not any(hash_value in searchable for hash_value in asset_hashes):
            findings.append(f"{label}.path: text proof does not bind a native asset hash")
    elif media_type == "image/png":
        validate_png_shape(payload, f"{label}.path", findings)
    elif media_type == "application/pdf":
        validate_pdf_shape(payload, f"{label}.path", findings)


def expected_render_paths(descriptor: dict[str, Any]) -> list[str]:
    contract = as_dict(descriptor.get("render_contract"))
    page_count, pdf_path, image_pattern = contract.get("expected_page_count"), contract.get("expected_pdf_path"), contract.get("expected_page_image_pattern")
    if contract.get("required") is not True or not isinstance(page_count, int) or not isinstance(pdf_path, str) or not isinstance(image_pattern, str):
        return []
    try:
        return [pdf_path, *(image_pattern.format(page=page) for page in range(1, page_count + 1))]
    except (IndexError, KeyError, ValueError):
        return []


def validate_render_outputs(root: Path, descriptor: dict[str, Any], outputs: object, label: str, findings: list[str]) -> None:
    expected = expected_render_paths(descriptor)
    records = [item for item in as_list(outputs) if isinstance(item, dict)]
    paths = [item.get("path") for item in records]
    hashes = [item.get("sha256") for item in records]
    if paths != expected:
        findings.append(f"{label}: render outputs must exactly match ordered descriptor PDF and page-image paths")
        return
    if len(paths) != len(set(paths)) or len(hashes) != len(set(hashes)):
        findings.append(f"{label}: render output paths and hashes must be unique")
    page_count = as_dict(descriptor.get("render_contract")).get("expected_page_count")
    for index, record in enumerate(records):
        media_type = "application/pdf" if index == 0 else "image/png"
        artifact = {"path": record.get("path"), "sha256": record.get("sha256"), "media_type": media_type, "category": "render"}
        path = check_bound_file(root, artifact, "path", "sha256", f"{label}.{index}", findings)
        if path is None:
            continue
        validate_proof_artifact(path, artifact, {}, set(), f"{label}.{index}", findings)
        if index == 0:
            try:
                observed_pages = pdf_page_count(path.read_bytes())
            except OSError:
                continue
            if observed_pages != page_count:
                findings.append(f"{label}.0: PDF page count {observed_pages} does not match render contract {page_count}")


def validate_descriptor_render_manifest(root: Path, descriptor: dict[str, Any], label: str, findings: list[str], bound_files: set[Path] | None = None) -> None:
    contract = as_dict(descriptor.get("render_contract"))
    if contract.get("required") is not True:
        return
    manifest_path = resolve_bound_path(root, contract.get("evidence_manifest"), f"{label}:render_contract.evidence_manifest", findings, Path("evidence"))
    if manifest_path is None:
        return
    if bound_files is not None:
        bound_files.add(manifest_path)
    manifest = load_json(manifest_path, root, findings)
    if manifest is None:
        return
    template_id = descriptor.get("template_id")
    record = as_dict(as_dict(manifest.get("templates")).get(template_id)) if isinstance(template_id, str) else {}
    assets = bound_pairs(descriptor.get("native_assets"))
    if len(assets) != 1:
        findings.append(f"{label}:render_contract: rendered descriptors must bind exactly one native asset")
        return
    asset_path, asset_hash = assets[0]
    comparisons = {
        "asset_path": asset_path,
        "asset_sha256": asset_hash,
        "page_count": contract.get("expected_page_count"),
        "sheet_names": contract.get("expected_sheet_names"),
    }
    for field, expected in comparisons.items():
        if record.get(field) != expected:
            findings.append(f"{label}:render_contract.evidence_manifest.{field}: does not match descriptor")
    validate_render_outputs(root, descriptor, record.get("rendered_outputs"), f"{label}:render_contract.evidence_manifest.rendered_outputs", findings)


def validate_release_render_evidence(root: Path, descriptor: dict[str, Any], record: dict[str, Any], label: str, findings: list[str]) -> None:
    if as_dict(descriptor.get("render_contract")).get("required") is not True:
        return
    render_artifacts = [item for item in as_list(record.get("artifacts")) if isinstance(item, dict) and item.get("category") == "render"]
    expected = expected_render_paths(descriptor)
    observed = [item.get("path") for item in render_artifacts]
    if observed != expected:
        findings.append(f"{label}: render evidence must contain the exact ordered descriptor PDF and page images; text-only proof is not sufficient")
        return
    for index, artifact in enumerate(render_artifacts):
        expected_media_type = "application/pdf" if index == 0 else "image/png"
        if artifact.get("media_type") != expected_media_type:
            findings.append(f"{label}:artifacts.{index}.media_type: expected {expected_media_type}")
    validate_render_outputs(root, descriptor, render_artifacts, f"{label}:artifacts", findings)


def apply_fixture(base: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for mutation in as_list(fixture.get("mutations")):
        if not isinstance(mutation, dict) or not isinstance(mutation.get("path"), str):
            raise ValueError("fixture mutations must be objects with a path")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in mutation["path"].strip("/").split("/") if part]
        if not parts:
            raise ValueError("fixture mutation path must not be empty")
        parent: Any = result
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = parts[-1]
        if mutation.get("op") == "remove":
            parent.pop(int(key)) if isinstance(parent, list) else parent.pop(key)
        elif mutation.get("op") == "replace" and "value" in mutation:
            if isinstance(parent, list):
                parent[int(key)] = mutation["value"]
            else:
                parent[key] = mutation["value"]
        else:
            raise ValueError(f"unsupported fixture mutation: {mutation.get('op')}")
    return result


def load_typed_evidence(
    root: Path,
    reference: dict[str, Any],
    schema: dict[str, Any],
    release: dict[str, Any],
    descriptor_hash: object,
    asset_hashes: set[str],
    expected_type: str,
    label: str,
    findings: list[str],
    evidence_ids: dict[str, Path],
    bound_evidence_files: set[Path],
    category: str | None = None,
    actor_names: dict[str, str] | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    release_id = release.get("release_id")
    release_evidence_root = EVIDENCE_ROOT / release_id if isinstance(release_id, str) else EVIDENCE_ROOT
    path = check_bound_file(root, reference, "record_path", "record_sha256", label, findings, release_evidence_root)
    if path is None:
        return None, {}
    expected_parent = (root / release_evidence_root).resolve()
    if path.parent != expected_parent or path.suffix != ".json":
        findings.append(f"{label}.record_path: evidence records must be direct lowercase .json children of the release evidence directory")
    bound_evidence_files.add(path)
    record = load_json(path, root, findings)
    if record is None:
        return path, {}
    record_label = display_path(path, root)
    findings.extend(schema_findings(record, schema, record_label))
    actor_id, actor = record.get("actor_id"), record.get("actor")
    if actor_names is not None and isinstance(actor_id, str) and isinstance(actor, str):
        stable_id = unicodedata.normalize("NFKC", actor_id).strip().casefold()
        stable_name = unicodedata.normalize("NFKC", actor).strip().casefold()
        previous_name = actor_names.get(stable_id)
        if previous_name is not None and previous_name != stable_name:
            findings.append(f"{label}:actor: actor_id must map to one stable actor name")
        actor_names[stable_id] = stable_name
    record_id = record.get("record_id")
    if isinstance(record_id, str):
        prior = evidence_ids.get(record_id)
        if prior is not None and prior != path:
            findings.append(f"{label}:record_id: duplicate evidence identity {record_id}")
        else:
            evidence_ids[record_id] = path
    expected = {
        "release_id": release.get("release_id"),
        "template_id": release.get("template_id"),
        "version": release.get("version"),
        "descriptor_sha256": descriptor_hash,
    }
    for field, value in expected.items():
        if record.get(field) != value:
            findings.append(f"{label}:{field}: typed evidence is not bound to the release")
    record_hashes = {value for value in as_list(record.get("native_asset_sha256s")) if isinstance(value, str)}
    if record_hashes != asset_hashes:
        findings.append(f"{label}:native_asset_sha256s: typed evidence is not bound to every native asset")
    if record.get("record_type") != expected_type:
        findings.append(f"{label}:record_type: expected {expected_type}")
    if category is not None:
        categories = {value for value in as_list(record.get("categories")) if isinstance(value, str)}
        if category not in categories:
            findings.append(f"{label}:categories: technical record does not declare {category}")
    artifact_categories: set[str] = set()
    for index, artifact in enumerate(as_list(record.get("artifacts"))):
        if not isinstance(artifact, dict):
            continue
        artifact_path = check_bound_file(root, artifact, "path", "sha256", f"{label}:artifacts.{index}", findings, release_evidence_root)
        if artifact_path is not None:
            bound_evidence_files.add(artifact_path)
            if artifact_path == path:
                findings.append(f"{label}:artifacts.{index}.path: evidence record cannot cite itself as proof output")
            validate_proof_artifact(artifact_path, artifact, release, asset_hashes, f"{label}:artifacts.{index}", findings)
        artifact_category = artifact.get("category")
        if isinstance(artifact_category, str):
            artifact_categories.add(artifact_category)
    if category is not None and category not in artifact_categories:
        findings.append(f"{label}:artifacts: no proof output is bound to category {category}")
    return path, record


def validate_repository(root: Path = ROOT) -> tuple[list[str], int]:
    root = root.resolve()
    findings: list[str] = []
    schemas: dict[str, dict[str, Any]] = {}
    for name in SCHEMA_NAMES:
        data = load_json(root / f"schemas/{name}.schema.json", root, findings)
        if data is not None:
            schemas[name] = data
    if len(schemas) != len(SCHEMA_NAMES):
        return findings, 0

    cards: dict[str, tuple[Path, dict[str, Any]]] = {}
    blueprints: dict[str, tuple[Path, dict[str, Any]]] = {}
    releases: dict[str, tuple[Path, dict[str, Any]]] = {}
    release_template_keys: set[tuple[str, str]] = set()
    release_descriptors: dict[Path, dict[str, Any]] = {}
    release_version_files: dict[Path, set[Path]] = {}
    evidence_ids: dict[str, Path] = {}
    actor_names: dict[str, str] = {}
    bound_evidence_files: set[Path] = set()
    count = 0

    foundation_root = root / "library/foundations"
    foundation_paths: list[Path] = []
    if foundation_root.is_dir():
        for path in sorted((item for item in foundation_root.rglob("*") if item.is_file() and item.suffix.casefold() == ".json"), key=str):
            if path.parent != foundation_root or re.fullmatch(r"FOUND-[0-9]{4}\.json", path.name) is None:
                findings.append(f"{display_path(path, root)}:<root>: unexpected foundation JSON filename or location")
                continue
            foundation_paths.append(path)
    for path in foundation_paths:
        data = load_json(path, root, findings)
        if data is None:
            continue
        count += 1
        label = display_path(path, root)
        findings.extend(schema_findings(data, schemas["foundation-card"], label))
        card_id = data.get("card_id")
        if isinstance(card_id, str):
            if path.stem != card_id:
                findings.append(f"{label}:card_id: must match filename")
            if card_id in cards:
                findings.append(f"{label}:card_id: duplicate {card_id}")
            else:
                cards[card_id] = (path, data)

    blueprint_root = root / "examples/blueprints"
    blueprint_paths: list[Path] = []
    fixture_paths: list[Path] = []
    if blueprint_root.is_dir():
        fixture_root = blueprint_root / "fixtures"
        for path in sorted((item for item in blueprint_root.rglob("*") if item.is_file() and item.suffix.casefold() == ".json"), key=str):
            if path.parent == blueprint_root and re.fullmatch(r"BP-[0-9]{4}\.[a-z0-9-]+\.json", path.name):
                blueprint_paths.append(path)
            elif path.parent == fixture_root and re.fullmatch(r"[a-z0-9-]+\.(positive|anti)\.json", path.name):
                fixture_paths.append(path)
            else:
                findings.append(f"{display_path(path, root)}:<root>: unexpected blueprint or fixture JSON filename or location")
    for path in blueprint_paths:
        data = load_json(path, root, findings)
        if data is None:
            continue
        count += 1
        label = display_path(path, root)
        findings.extend(validate_blueprint_data(data, schemas["artifact-blueprint"], label))
        blueprint_id = data.get("blueprint_id")
        if isinstance(blueprint_id, str):
            if not path.name.startswith(f"{blueprint_id}."):
                findings.append(f"{label}:blueprint_id: must match filename prefix")
            if blueprint_id in blueprints:
                findings.append(f"{label}:blueprint_id: duplicate {blueprint_id}")
            else:
                blueprints[blueprint_id] = (path, data)

    for path, data in blueprints.values():
        label = display_path(path, root)
        seen_cards: set[str] = set()
        for index, lineage in enumerate(as_list(data.get("foundation_lineage"))):
            if not isinstance(lineage, dict):
                continue
            prefix = f"{label}:foundation_lineage.{index}"
            card_id = lineage.get("card_id")
            if not isinstance(card_id, str):
                continue
            if card_id in seen_cards:
                findings.append(f"{prefix}.card_id: duplicate lineage card")
            seen_cards.add(card_id)
            card_entry = cards.get(card_id)
            if card_entry is None:
                findings.append(f"{prefix}.card_id: unknown foundation card {card_id}")
                continue
            card_path, card = card_entry
            if lineage.get("card_sha256") != sha256_file(card_path):
                findings.append(f"{prefix}.card_sha256: does not match current foundation card bytes")
            if lineage.get("reviewed_source_sha256") != as_dict(card.get("source")).get("sha256"):
                findings.append(f"{prefix}.reviewed_source_sha256: does not match foundation source hash")
            status, mode = card.get("status"), lineage.get("use_mode")
            if mode == "reviewed_pattern" and status != "reviewed":
                findings.append(f"{prefix}.use_mode: reviewed_pattern requires a reviewed foundation card")
            if mode == "rejected_pattern_only" and status != "rejected":
                findings.append(f"{prefix}.use_mode: rejected_pattern_only requires a rejected foundation card")
            if status == "candidate":
                findings.append(f"{prefix}.card_id: candidate foundation cards cannot feed blueprints")
            allowed_patterns = {value for value in as_list(as_dict(card.get("reuse")).get("patterns")) if isinstance(value, str)}
            for pattern in as_list(lineage.get("patterns_used")):
                if isinstance(pattern, str) and pattern not in allowed_patterns:
                    findings.append(f"{prefix}.patterns_used: pattern is not named by the foundation card")

    archetype_schema = as_dict(as_dict(schemas["artifact-blueprint"].get("properties")).get("archetype"))
    required_archetypes = {value for value in as_list(archetype_schema.get("enum")) if isinstance(value, str)}
    archetype_blueprints: dict[str, list[str]] = {}
    for blueprint_id, (_, blueprint) in blueprints.items():
        archetype = blueprint.get("archetype")
        if isinstance(archetype, str):
            archetype_blueprints.setdefault(archetype, []).append(blueprint_id)
    for archetype in sorted(required_archetypes):
        identifiers = archetype_blueprints.get(archetype, [])
        if len(identifiers) != 1:
            findings.append(f"examples/blueprints:{archetype}: requires exactly one production blueprint; found {len(identifiers)}")

    release_root = root / "library/releases"
    release_paths: list[Path] = []
    if release_root.is_dir():
        for path in sorted((item for item in release_root.rglob("*") if item.is_file() and item.suffix.casefold() == ".json"), key=str):
            if path.parent != release_root or re.fullmatch(r"REL-[0-9]{4}\.[a-z0-9-]+\.json", path.name) is None:
                findings.append(f"{display_path(path, root)}:<root>: unexpected release JSON filename or location")
                continue
            release_paths.append(path)
    for path in release_paths:
        data = load_json(path, root, findings)
        if data is None:
            continue
        count += 1
        label = display_path(path, root)
        findings.extend(schema_findings(data, schemas["template-release"], label))
        release_id = data.get("release_id")
        if isinstance(release_id, str):
            if not path.name.startswith(f"{release_id}."):
                findings.append(f"{label}:release_id: must match filename prefix")
            if release_id in releases:
                findings.append(f"{label}:release_id: duplicate {release_id}")
            else:
                releases[release_id] = (path, data)
            template_id, version = data.get("template_id"), data.get("version")
            if isinstance(template_id, str) and isinstance(version, str):
                template_key = (template_id, version)
                if template_key in release_template_keys:
                    findings.append(f"{label}:template_id/version: duplicate released template identity")
                release_template_keys.add(template_key)

    for path, data in releases.values():
        label = display_path(path, root)
        template_id, version = data.get("template_id"), data.get("version")
        version_root = None
        if isinstance(template_id, str) and isinstance(version, str):
            version_root = (root / "library/templates" / template_id / version).resolve()
            release_version_files.setdefault(version_root, set())
        descriptor_ref = as_dict(data.get("descriptor"))
        descriptor_path = check_bound_file(root, descriptor_ref, "path", "sha256", f"{label}:descriptor", findings, Path("library/templates"))
        if descriptor_path is not None and version_root is not None:
            if descriptor_path.parent != version_root:
                findings.append(f"{label}:descriptor.path: must be directly under its template version directory")
            if descriptor_path.name != "template.json":
                findings.append(f"{label}:descriptor.path: descriptor filename must be template.json")
            release_version_files[version_root].add(descriptor_path)
        descriptor_hash = descriptor_ref.get("sha256")
        descriptor = load_json(descriptor_path, root, findings) if descriptor_path is not None else None
        if descriptor is not None:
            findings.extend(validate_descriptor_data(descriptor, schemas["template-descriptor"], display_path(descriptor_path, root)))
            release_descriptors[path.resolve()] = descriptor
        render_contract_hash = canonical_json_sha256(descriptor.get("render_contract")) if descriptor is not None else None

        asset_pairs = bound_pairs(data.get("native_assets"))
        if len(asset_pairs) != len(set(asset_pairs)) or len({path_value for path_value, _ in asset_pairs}) != len(asset_pairs):
            findings.append(f"{label}:native_assets: duplicate asset path or binding")
        for index, asset in enumerate(as_list(data.get("native_assets"))):
            if isinstance(asset, dict):
                asset_path = check_bound_file(root, asset, "path", "sha256", f"{label}:native_assets.{index}", findings, Path("library/templates"))
                if asset_path is not None and version_root is not None:
                    if asset_path != version_root and version_root not in asset_path.parents:
                        findings.append(f"{label}:native_assets.{index}.path: must be under its template version directory")
                    release_version_files[version_root].add(asset_path)
                    validate_native_asset_shape(asset_path, descriptor.get("artifact_type") if descriptor else None, f"{label}:native_assets.{index}", findings)
                    if descriptor and (descriptor.get("artifact_type") == "xlsx" or (descriptor.get("artifact_type") == "mixed_package" and asset_path.suffix.casefold() == ".xlsx")):
                        inspect_xlsx_contract(asset_path, descriptor, display_path(descriptor_path, root) if descriptor_path else label, findings)
        asset_hashes = {hash_value for _, hash_value in asset_pairs}
        if descriptor is not None:
            validate_descriptor_render_manifest(root, descriptor, display_path(descriptor_path, root) if descriptor_path else label, findings, bound_evidence_files)

        blueprint_ref = as_dict(data.get("blueprint"))
        blueprint_path = check_bound_file(root, blueprint_ref, "path", "sha256", f"{label}:blueprint", findings, Path("examples/blueprints"))
        blueprint_id = blueprint_ref.get("blueprint_id")
        blueprint_entry = blueprints.get(blueprint_id) if isinstance(blueprint_id, str) else None
        if isinstance(blueprint_id, str) and blueprint_entry is None:
            findings.append(f"{label}:blueprint.blueprint_id: unknown blueprint")
        elif blueprint_entry is not None and blueprint_path is not None and blueprint_path != blueprint_entry[0].resolve():
            findings.append(f"{label}:blueprint.path: does not identify the indexed blueprint")

        if descriptor is not None:
            comparisons = {
                "template_id": data.get("template_id"),
                "version": data.get("version"),
                "artifact_type": blueprint_entry[1].get("artifact_type") if blueprint_entry else None,
            }
            for field, expected in comparisons.items():
                if descriptor.get(field) != expected:
                    findings.append(f"{label}:descriptor.{field}: does not match release")
            if descriptor.get("blueprint_id") != blueprint_id:
                findings.append(f"{label}:descriptor.blueprint_id: does not match release")
            if descriptor.get("release_status") != data.get("status"):
                findings.append(f"{label}:descriptor.release_status: does not match release")
            if set(bound_pairs(descriptor.get("native_assets"))) != set(asset_pairs):
                findings.append(f"{label}:descriptor.native_assets: must exactly match release native assets")

        if blueprint_entry is not None:
            lineage_ids = {item.get("card_id") for item in as_list(blueprint_entry[1].get("foundation_lineage")) if isinstance(item, dict) and isinstance(item.get("card_id"), str)}
            supplied_ids = {item for item in as_list(as_dict(data.get("sanitization")).get("foundation_card_ids")) if isinstance(item, str)}
            if supplied_ids != lineage_ids:
                findings.append(f"{label}:sanitization.foundation_card_ids: must exactly match blueprint lineage")

        typed: dict[str, tuple[Path | None, dict[str, Any]]] = {}
        typed["build"] = load_typed_evidence(root, as_dict(data.get("build")), schemas["release-evidence"], data, descriptor_hash, asset_hashes, "build_attestation", f"{label}:build", findings, evidence_ids, bound_evidence_files, actor_names=actor_names)
        typed["sanitization"] = load_typed_evidence(root, as_dict(as_dict(data.get("sanitization")).get("evidence")), schemas["release-evidence"], data, descriptor_hash, asset_hashes, "sanitization", f"{label}:sanitization.evidence", findings, evidence_ids, bound_evidence_files, actor_names=actor_names)
        reviews = as_dict(data.get("reviews"))
        typed["terra"] = load_typed_evidence(root, as_dict(reviews.get("terra")), schemas["release-evidence"], data, descriptor_hash, asset_hashes, "terra_review", f"{label}:reviews.terra", findings, evidence_ids, bound_evidence_files, actor_names=actor_names)
        typed["sol"] = load_typed_evidence(root, as_dict(reviews.get("sol")), schemas["release-evidence"], data, descriptor_hash, asset_hashes, "sol_review", f"{label}:reviews.sol", findings, evidence_ids, bound_evidence_files, actor_names=actor_names)
        typed["conductor"] = load_typed_evidence(root, as_dict(data.get("conductor_approval")), schemas["release-evidence"], data, descriptor_hash, asset_hashes, "conductor_approval", f"{label}:conductor_approval", findings, evidence_ids, bound_evidence_files, actor_names=actor_names)
        independent_lanes = ("build", "sanitization", "terra", "sol", "conductor")
        review_paths = [typed[name][0] for name in independent_lanes if typed[name][0] is not None]
        if len(review_paths) != len(set(review_paths)):
            findings.append(f"{label}:reviews: build, sanitization, Terra, Sol, and conductor record paths must be distinct")
        actor_ids = [typed[name][1].get("actor_id") for name in independent_lanes if isinstance(typed[name][1].get("actor_id"), str)]
        normalized_actor_ids = [unicodedata.normalize("NFKC", actor_id).strip().casefold() for actor_id in actor_ids]
        if len(actor_ids) != len(independent_lanes) or len(normalized_actor_ids) != len(set(normalized_actor_ids)):
            findings.append(f"{label}:reviews: build, sanitization, Terra, Sol, and conductor identities must be independent")
        blueprint_gates = {gate.get("category"): gate for gate in as_list(blueprint_entry[1].get("proof_gates")) if blueprint_entry and isinstance(gate, dict) and isinstance(gate.get("category"), str)} if blueprint_entry else {}
        for category in PROOF_CATEGORIES:
            reference = as_dict(as_dict(data.get("evidence")).get(category))
            _, record = load_typed_evidence(root, reference, schemas["release-evidence"], data, descriptor_hash, asset_hashes, "technical_validation", f"{label}:evidence.{category}", findings, evidence_ids, bound_evidence_files, category, actor_names)
            expected_verdict = "VALIDATION_PASS" if as_dict(blueprint_gates.get(category)).get("applicable") is True else "VALIDATION_NOT_APPLICABLE"
            if record and record.get("verdict") != expected_verdict:
                findings.append(f"{label}:evidence.{category}.verdict: must align with blueprint applicability")
            expected_procedure = as_dict(blueprint_gates.get(category)).get("procedure")
            if record and as_dict(record.get("procedures")).get(category) != expected_procedure:
                findings.append(f"{label}:evidence.{category}.procedures: must exactly match the blueprint proof gate")
            if category == "render" and record and record.get("render_contract_sha256") != render_contract_hash:
                findings.append(f"{label}:evidence.render.render_contract_sha256: must exactly bind the descriptor render contract")
            if category == "render" and record and descriptor is not None:
                validate_release_render_evidence(root, descriptor, record, f"{label}:evidence.render", findings)

    fixture_pairs: dict[str, list[tuple[str, str]]] = {}
    for path in fixture_paths:
        fixture = load_json(path, root, findings)
        if fixture is None:
            continue
        count += 1
        label = display_path(path, root)
        required = {"fixture_version", "archetype", "expected", "base_blueprint", "mutations"}
        if set(fixture) != required or fixture.get("fixture_version") != "1.0.0" or fixture.get("expected") not in {"pass", "fail"}:
            findings.append(f"{label}:<root>: invalid fixture descriptor")
            continue
        base_path = resolve_bound_path(root, fixture.get("base_blueprint"), f"{label}:base_blueprint", findings, Path("examples/blueprints"))
        if base_path is None:
            continue
        indexed = next(((blueprint_id, data) for blueprint_id, (path_value, data) in blueprints.items() if path_value.resolve() == base_path), None)
        if indexed is None:
            findings.append(f"{label}:base_blueprint: must identify an indexed production blueprint")
            continue
        blueprint_id, base = indexed
        if fixture.get("archetype") != base.get("archetype"):
            findings.append(f"{label}:archetype: must match the base blueprint archetype")
        fixture_pairs.setdefault(blueprint_id, []).append((str(fixture.get("expected")), label))
        try:
            candidate = apply_fixture(base, fixture)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            findings.append(f"{label}:mutations: cannot apply fixture: {error}")
            continue
        candidate_findings = validate_blueprint_data(candidate, schemas["artifact-blueprint"], label)
        if fixture["expected"] == "pass" and candidate_findings:
            findings.append(f"{label}:expected: positive fixture failed: {'; '.join(candidate_findings)}")
        if fixture["expected"] == "fail" and not candidate_findings:
            findings.append(f"{label}:expected: anti-pattern fixture unexpectedly passed")
    for blueprint_id in sorted(blueprints):
        outcomes = [outcome for outcome, _ in fixture_pairs.get(blueprint_id, [])]
        if sorted(outcomes) != ["fail", "pass"]:
            findings.append(f"examples/blueprints/fixtures:{blueprint_id}: requires exactly one pass and one fail fixture")

    catalog_path = root / "library/catalog.json"
    catalog_version_files: dict[Path, set[Path]] = {}
    release_catalog_counts: dict[Path, int] = {path.resolve(): 0 for path, _ in releases.values()}
    if catalog_path.is_file():
        count += 1
        catalog = load_json(catalog_path, root, findings)
        if catalog is not None:
            label = display_path(catalog_path, root)
            findings.extend(schema_findings(catalog, schemas["artifact-catalog"], label))
            seen_keys: set[tuple[str, str]] = set()
            release_by_path = {path.resolve(): data for path, data in releases.values()}
            for index, entry in enumerate(as_list(catalog.get("templates"))):
                if not isinstance(entry, dict):
                    continue
                prefix = f"{label}:templates.{index}"
                template_id, version = entry.get("template_id"), entry.get("version")
                version_root = None
                if isinstance(template_id, str) and isinstance(version, str):
                    key = (template_id, version)
                    if key in seen_keys:
                        findings.append(f"{prefix}: duplicate template_id/version")
                    seen_keys.add(key)
                    version_root = (root / "library/templates" / template_id / version).resolve()
                    catalog_version_files.setdefault(version_root, set())

                descriptor_path = resolve_bound_path(root, entry.get("descriptor"), f"{prefix}.descriptor", findings, Path("library/templates"))
                descriptor_data = load_json(descriptor_path, root, findings) if descriptor_path is not None else None
                if descriptor_path is not None and version_root is not None:
                    if descriptor_path.parent != version_root:
                        findings.append(f"{prefix}.descriptor: must be directly under its template version directory")
                    if descriptor_path.name != "template.json":
                        findings.append(f"{prefix}.descriptor: descriptor filename must be template.json")
                    catalog_version_files[version_root].add(descriptor_path)
                if descriptor_data is not None:
                    findings.extend(validate_descriptor_data(descriptor_data, schemas["template-descriptor"], display_path(descriptor_path, root)))

                catalog_asset_paths: set[str] = set()
                for asset_index, asset_value in enumerate(as_list(entry.get("native_assets"))):
                    asset_path = resolve_bound_path(root, asset_value, f"{prefix}.native_assets.{asset_index}", findings, Path("library/templates"))
                    if isinstance(asset_value, str):
                        catalog_asset_paths.add(asset_value)
                    if asset_path is not None and version_root is not None:
                        if asset_path != version_root and version_root not in asset_path.parents:
                            findings.append(f"{prefix}.native_assets.{asset_index}: must be under its template version directory")
                        catalog_version_files[version_root].add(asset_path)

                descriptor_asset_pairs = bound_pairs(as_dict(descriptor_data).get("native_assets"))
                descriptor_asset_paths = {path_value for path_value, _ in descriptor_asset_pairs}
                if descriptor_data is not None and catalog_asset_paths != descriptor_asset_paths:
                    findings.append(f"{prefix}.native_assets: must exactly match descriptor native assets")
                for asset_index, (asset_value, asset_hash) in enumerate(descriptor_asset_pairs):
                    asset_path = resolve_bound_path(root, asset_value, f"{prefix}.descriptor.native_assets.{asset_index}", findings, Path("library/templates"))
                    if asset_path is not None:
                        if sha256_file(asset_path) != asset_hash:
                            findings.append(f"{prefix}.descriptor.native_assets.{asset_index}.sha256: hash does not match {asset_value}")
                        validate_native_asset_shape(asset_path, as_dict(descriptor_data).get("artifact_type"), f"{prefix}.descriptor.native_assets.{asset_index}", findings)
                        if as_dict(descriptor_data).get("artifact_type") == "xlsx" or (as_dict(descriptor_data).get("artifact_type") == "mixed_package" and asset_path.suffix.casefold() == ".xlsx"):
                            inspect_xlsx_contract(asset_path, as_dict(descriptor_data), display_path(descriptor_path, root) if descriptor_path else prefix, findings)
                if descriptor_data is not None:
                    validate_descriptor_render_manifest(root, descriptor_data, display_path(descriptor_path, root) if descriptor_path else prefix, findings, bound_evidence_files)

                blueprint_id = as_dict(descriptor_data).get("blueprint_id")
                bound_blueprint = blueprints.get(blueprint_id) if isinstance(blueprint_id, str) else None
                comparisons = {
                    "template_id": as_dict(descriptor_data).get("template_id"), "version": as_dict(descriptor_data).get("version"),
                    "name": as_dict(descriptor_data).get("name"), "artifact_type": as_dict(descriptor_data).get("artifact_type"),
                    "blueprint_id": blueprint_id, "authority": as_dict(descriptor_data).get("authority"),
                    "lifecycle": as_dict(descriptor_data).get("lifecycle"), "release_status": as_dict(descriptor_data).get("release_status"),
                }
                for field, expected in comparisons.items():
                    if entry.get(field) != expected:
                        findings.append(f"{prefix}.{field}: does not match descriptor")
                for field in ("supported_consumers", "capabilities"):
                    if set(as_list(entry.get(field))) != set(as_list(as_dict(descriptor_data).get(field))):
                        findings.append(f"{prefix}.{field}: does not match descriptor")
                if bound_blueprint is None and isinstance(blueprint_id, str):
                    findings.append(f"{prefix}.blueprint_id: unknown blueprint")
                elif bound_blueprint is not None:
                    if entry.get("artifact_type") != bound_blueprint[1].get("artifact_type"):
                        findings.append(f"{prefix}.artifact_type: does not match blueprint")
                    if entry.get("authority") != as_dict(bound_blueprint[1].get("authority")).get("primary_class"):
                        findings.append(f"{prefix}.authority: does not match blueprint")

                if entry.get("release_status") in {"released", "deprecated", "withdrawn"}:
                    release_ref = as_dict(entry.get("release_record"))
                    release_path = check_bound_file(root, release_ref, "path", "sha256", f"{prefix}.release_record", findings, Path("library/releases"))
                    release = release_by_path.get(release_path) if release_path is not None else None
                    if release is None:
                        findings.append(f"{prefix}.release_record: does not identify an indexed release")
                    else:
                        release_catalog_counts[release_path] += 1
                        release_comparisons = {"template_id": release.get("template_id"), "version": release.get("version"), "blueprint_id": as_dict(release.get("blueprint")).get("blueprint_id"), "release_status": release.get("status")}
                        for field, expected in release_comparisons.items():
                            if entry.get(field) != expected:
                                findings.append(f"{prefix}.{field}: does not match release")
                        if entry.get("descriptor") != as_dict(release.get("descriptor")).get("path"):
                            findings.append(f"{prefix}.descriptor: does not match release")
                        if catalog_asset_paths != {path_value for path_value, _ in bound_pairs(release.get("native_assets"))}:
                            findings.append(f"{prefix}.native_assets: do not match release")

    for release_path, release in releases.values():
        uses = release_catalog_counts.get(release_path.resolve(), 0)
        if uses != 1:
            findings.append(f"{display_path(release_path, root)}: release must appear exactly once in library/catalog.json; found {uses}")

    evidence_root = root / EVIDENCE_ROOT
    if evidence_root.is_dir():
        for path in sorted((item.resolve() for item in evidence_root.rglob("*") if item.is_file() and item.suffix.casefold() == ".json"), key=str):
            if path not in bound_evidence_files:
                findings.append(f"{display_path(path, root)}:<root>: evidence JSON is not bound by a release record")

    templates_root = root / "library/templates"
    expected_template_files: set[Path] = set()
    for files in list(release_version_files.values()) + list(catalog_version_files.values()):
        expected_template_files.update(files)
    if templates_root.is_dir():
        for actual in sorted((path.resolve() for path in templates_root.rglob("*") if path.is_file()), key=str):
            if actual in expected_template_files:
                continue
            if actual.name == ".gitkeep" and not actual.read_bytes().strip():
                continue
            findings.append(f"{display_path(actual, root)}: file is not bound by a catalog descriptor/native asset or release")

    return findings, count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root to validate")
    args = parser.parse_args(argv)
    findings, count = validate_repository(args.root)
    if findings:
        print("FAIL")
        print("\n".join(findings))
        return 1
    print(f"PASS: {count} library records and fixtures validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
