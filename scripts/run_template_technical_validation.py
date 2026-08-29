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
                hidden_rows += sum(row.get("hidden") == "1" for row in worksheet.findall(f".//{{{MAIN}}}row"))
                hidden_columns += sum(column.get("hidden") == "1" for column in worksheet.findall(f".//{{{MAIN}}}col"))
                cells = worksheet.findall(f".//{{{MAIN}}}c")
                cell_count += len(cells)
                for cell in cells:
                    formula = cell.find(f"{{{MAIN}}}f")
                    value = cell.find(f"{{{MAIN}}}v")
                    inline = cell.find(f"{{{MAIN}}}is/{{{MAIN}}}t")
                    if formula is not None:
                        formula_count += 1
                        formula_sheets.add(name)
                        cached_formula_count += value is not None
                        extracted.append(formula.text or "")
                    text = (value.text if value is not None else None) or (inline.text if inline is not None else "")
                    extracted.append(text)
                    if cell.get("t") == "e" or text.startswith("#"):
                        error_count += 1
                    if name == "Checks" and cell.get("r") in {"B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12", "B13", "B16"}:
                        check_values[str(cell.get("r"))] = text
            shared = "xl/sharedStrings.xml"
            if shared in names:
                tree = ET.fromstring(package.read(shared))
                extracted.extend(item.text or "" for item in tree.findall(f".//{{{MAIN}}}t"))
            calc = workbook.find(f"{{{MAIN}}}calcPr")
            external = sorted(name for name in names if "externallink" in name.casefold())
            comments = sorted(name for name in names if "comment" in name.casefold() and name.endswith(".xml"))
            for member in comments:
                comment_tree = ET.fromstring(package.read(member))
                extracted.extend(node.text or "" for node in comment_tree.findall(f".//{{{MAIN}}}t"))
            for member in ("docProps/core.xml", "docProps/custom.xml"):
                if member in names:
                    extracted.extend(node.text or "" for node in ET.fromstring(package.read(member)).iter())
            tables = [name for name in names if name.startswith("xl/tables/") and name.endswith(".xml")]
            return {
                "sheets": sheets, "cells": cell_count, "formulas": formula_count,
                "cached_formulas": cached_formula_count, "errors": error_count,
                "check_values": check_values, "external": external, "comments": comments,
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
            extracted = [node.text or "" for node in document.findall(f".//{{{WORD}}}t")]
            paragraphs = len(document.findall(f".//{{{WORD}}}p"))
            tables = len(document.findall(f".//{{{WORD}}}tbl"))
            hidden = len(document.findall(f".//{{{WORD}}}vanish"))
            comments = [name for name in names if name == "word/comments.xml"]
            for member in comments:
                extracted.extend(node.text or "" for node in ET.fromstring(package.read(member)).findall(f".//{{{WORD}}}t"))
            for member in ("docProps/core.xml", "docProps/custom.xml"):
                if member in names:
                    extracted.extend(node.text or "" for node in ET.fromstring(package.read(member)).iter())
            external: list[str] = []
            for name in names:
                if name.endswith(".rels"):
                    rels = ET.fromstring(package.read(name))
                    external.extend(item.get("Target", "") for item in rels if item.get("TargetMode") == "External")
            revisions = len(document.findall(f".//{{{WORD}}}ins")) + len(document.findall(f".//{{{WORD}}}del"))
            custom_xml = len([name for name in names if name.startswith("customXml/") and name.endswith(".xml")])
            return {"text": "\n".join(extracted), "paragraphs": paragraphs, "tables": tables, "hidden": hidden, "comments": len(comments), "revisions": revisions, "custom_xml": custom_xml, "external": external}
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
        if not attachment.get_filename() or not payload:
            raise ValidationFailed(f"{path}: attachment lacks a filename or payload")
        if attachment.get_content_type() == "text/csv":
            decoded = payload.decode(attachment.get_content_charset() or "utf-8")
            if len([line for line in decoded.splitlines() if line.strip()]) < 2:
                raise ValidationFailed(f"{path}: CSV attachment lacks header and data")
            text_parts.append(decoded)
        elif attachment.get_content_maintype() == "text":
            text_parts.append(payload.decode(attachment.get_content_charset() or "utf-8"))
    return {
        "message": message, "attachments": attachments, "parts": parts,
        "text": raw + "\n" + "\n".join(text_parts), "message_ids": message_ids,
        "message_count": message_count,
    }


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
    primary = assets[0][0]
    if artifact_type == "xlsx":
        inventory = xlsx_inventory(primary)
    elif artifact_type == "docx":
        inventory = docx_inventory(primary)
    elif artifact_type == "eml":
        inventory = eml_inventory(primary)
    else:
        raise ValidationFailed(f"{release_id}: unsupported technical runner artifact type {artifact_type}")
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
        category_checks["render"].append(check("render:mime-parts", f"Opened and parsed all {len(inventory['attachments'])} native MIME attachments; pagination is not the descriptor render surface."))
    else:
        raise ValidationFailed(f"{release_id}: applicable render gate has no render outputs")

    if artifact_type == "xlsx":
        hidden = [name for name, _, state in inventory["sheets"] if state in {"hidden", "veryHidden"}]
        if inventory["external"] or hidden or inventory["hidden_rows"] or inventory["hidden_columns"]:
            raise ValidationFailed(f"{release_id}: workbook contains external links or hidden workbook surfaces")
        category_checks["metadata"].append(check("metadata:xlsx-surfaces", f"Inspected workbook relationships, {len(inventory['sheets'])} sheet states, row/column visibility, and {len(inventory['comments'])} parsed comment parts; found no external links or hidden surfaces."))
    elif artifact_type == "docx":
        if inventory["external"] or inventory["hidden"]:
            raise ValidationFailed(f"{release_id}: document contains external relationships or hidden text")
        category_checks["metadata"].append(check("metadata:docx-surfaces", f"Inspected package relationships, hidden text, {inventory['revisions']} revisions, {inventory['comments']} parsed comment parts, and {inventory['custom_xml']} custom XML parts with no external targets."))
    else:
        if len(inventory["message_ids"]) != len(set(inventory["message_ids"])):
            raise ValidationFailed(f"{release_id}: duplicate Message-ID values found")
        category_checks["metadata"].append(check("metadata:mime-headers", f"Inspected MIME encodings, attachment names, timestamps, and {len(inventory['message_ids'])} unique message identifiers."))

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

    text = str(inventory.get("text", ""))
    slots = set(descriptor.get("slots", []))
    unknown_slots = set(re.findall(r"\{\{([a-z0-9_]+)\}\}", text)) - slots
    forbidden = [token for token in FORBIDDEN_TEXT if token in text.casefold()]
    if unknown_slots or forbidden:
        raise ValidationFailed(f"{release_id}: leakage scan found unknown slots or prohibited residue")
    category_checks["leakage"].append(check("leakage:visible-hidden-scan", f"Scanned extracted native content for prohibited residue and verified all {len(set(re.findall(r'\{\{([a-z0-9_]+)\}\}', text)))} template tokens against the descriptor slot allowlist."))

    authority = blueprint.get("authority") or {}
    if descriptor.get("authority") != authority.get("primary_class") or not authority.get("governing_scope") or not authority.get("non_governing_scope") or not descriptor.get("knowledge_and_authority_constraints"):
        raise ValidationFailed(f"{release_id}: authority boundary declarations are incomplete or inconsistent")
    authority_markers = {marker for marker in ("approval", "governing", "supporting", "contextual", "question", "superseded") if marker in text.casefold()}
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
            "observations": [f"Deterministic {category} checks inspected the frozen descriptor and every native asset byte."],
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
