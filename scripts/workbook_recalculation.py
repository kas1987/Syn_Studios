"""Portable extraction and verification for workbook recalculation proofs."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
EXPECTED_CONTROL_CELLS = {"B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12", "B13", "B16"}
ALLOWED_CONTROL_STATES = {"PASS", "NOT READY", "NOT APPLICABLE"}
EMPTY_STRING_FORMULA_GUARD = re.compile(
    r'^\s*IF\(\s*(\$?[A-Z]{1,3}\$?\d+)\s*=\s*""\s*,\s*""\s*,',
    re.IGNORECASE,
)


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_text(cell: ET.Element) -> str:
    value = cell.find(f"{{{MAIN}}}v")
    if value is not None:
        return value.text or ""
    return "".join(node.text or "" for node in cell.findall(f"{{{MAIN}}}is//{{{MAIN}}}t"))


def _valid_empty_string_cache(formula: ET.Element, cell_values: dict[str, str]) -> bool:
    match = EMPTY_STRING_FORMULA_GUARD.match(formula.text or "")
    if match is None:
        return False
    guard_cell = match.group(1).replace("$", "").upper()
    return not cell_values.get(guard_cell, "").strip()


def workbook_formula_evidence(path: Path) -> dict[str, Any]:
    """Extract a deterministic formula/control fingerprint from XLSX bytes."""

    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            workbook = ET.fromstring(package.read("xl/workbook.xml"))
            relationships = ET.fromstring(package.read("xl/_rels/workbook.xml.rels"))
            by_id = {item.get("Id"): item for item in relationships.findall(f"{{{PACKAGE_REL}}}Relationship")}
            structure: list[dict[str, Any]] = []
            results: list[dict[str, Any]] = []
            formula_sheets: list[str] = []
            controls: dict[str, str] = {}
            cached_count = error_count = 0
            for sheet in workbook.findall(f"{{{MAIN}}}sheets/{{{MAIN}}}sheet"):
                sheet_name = sheet.get("name") or ""
                relation = by_id.get(sheet.get(f"{{{OFFICE_REL}}}id"))
                target = relation.get("Target") if relation is not None else None
                if not target:
                    raise ValueError(f"worksheet {sheet_name!r} has no relationship target")
                member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                if member not in names:
                    raise ValueError(f"worksheet member is missing: {member}")
                worksheet = ET.fromstring(package.read(member))
                cells = worksheet.findall(f".//{{{MAIN}}}c")
                cell_values = {str(cell.get("r", "")).upper(): _cached_text(cell) for cell in cells}
                sheet_has_formula = False
                for cell in cells:
                    address = str(cell.get("r", "")).upper()
                    cached = _cached_text(cell)
                    if cell.get("t") == "e" or cached.startswith("#"):
                        error_count += 1
                    if sheet_name == "Checks" and address in EXPECTED_CONTROL_CELLS:
                        controls[address] = cached
                    formula = cell.find(f"{{{MAIN}}}f")
                    if formula is None:
                        continue
                    sheet_has_formula = True
                    attributes = {key: formula.attrib[key] for key in sorted(formula.attrib)}
                    formula_record = {
                        "sheet": sheet_name,
                        "cell": address,
                        "formula": formula.text or "",
                        "formula_attributes": attributes,
                    }
                    value = cell.find(f"{{{MAIN}}}v")
                    cache_present = value is not None and (
                        bool((value.text or "").strip())
                        or (cell.get("t") == "str" and _valid_empty_string_cache(formula, cell_values))
                    )
                    cached_count += bool(cache_present)
                    structure.append(formula_record)
                    results.append({
                        **formula_record,
                        "cell_type": cell.get("t") or "n",
                        "cached_value": cached,
                        "cache_present": bool(cache_present),
                    })
                if sheet_has_formula:
                    formula_sheets.append(sheet_name)
    except (KeyError, OSError, zipfile.BadZipFile, ET.ParseError) as error:
        raise ValueError(f"invalid XLSX formula surface: {error}") from error
    return {
        "formula_count": len(structure),
        "cached_formula_count": cached_count,
        "formula_sheets": formula_sheets,
        "error_count": error_count,
        "control_cells": {key: controls[key] for key in sorted(controls)},
        "formula_results": results,
        "formula_structure_sha256": canonical_sha256(structure),
        "formula_results_sha256": canonical_sha256(results),
    }


def recalculation_proof_findings(
    proof: object,
    *,
    release_id: str,
    template_id: str,
    version: str,
    descriptor_sha256: str,
    workbook_path: str,
    workbook_sha256: str,
    current_evidence: dict[str, Any],
) -> list[str]:
    findings: list[str] = []
    if not isinstance(proof, dict):
        return ["machine recalculation proof must be a JSON object"]
    expected_fields = {
        "schema_version", "proof_type", "proof_id", "release_id", "template_id", "version",
        "category", "descriptor_sha256", "source_workbook", "engine", "execution", "formula_evidence", "verdict",
    }
    if set(proof) != expected_fields:
        findings.append("machine recalculation proof fields are incomplete or unexpected")
    expected = {
        "schema_version": "1.0.0",
        "proof_type": "workbook_recalculation_result",
        "proof_id": f"RECALC-{release_id}",
        "release_id": release_id,
        "template_id": template_id,
        "version": version,
        "category": "computational",
        "descriptor_sha256": descriptor_sha256,
        "source_workbook": {"path": workbook_path, "sha256": workbook_sha256},
        "execution": {
            "mode": "headless_cache_stripped_copy",
            "output_format": "xlsx",
            "cache_reset": "remove_all_formula_cached_values",
            "cleared_formula_cache_count": current_evidence.get("formula_count"),
            "prepared_cached_formula_count": 0,
            "source_before_sha256": workbook_sha256,
            "source_after_sha256": workbook_sha256,
            "source_unchanged": True,
        },
        "formula_evidence": current_evidence,
        "verdict": "RECALCULATION_PASS",
    }
    for field, value in expected.items():
        if proof.get(field) != value:
            findings.append(f"machine recalculation proof {field} does not bind the current workbook result")
    engine = proof.get("engine")
    if not isinstance(engine, dict) or set(engine) != {"name", "version"}:
        findings.append("machine recalculation proof engine identity is malformed")
    elif engine.get("name") != "LibreOffice Calc" or not isinstance(engine.get("version"), str) or not engine["version"].startswith("LibreOffice "):
        findings.append("machine recalculation proof engine identity is not LibreOffice Calc")
    formula_count = current_evidence.get("formula_count")
    if not isinstance(formula_count, int) or formula_count < 1:
        findings.append("machine recalculation proof has no formulas")
    if current_evidence.get("cached_formula_count") != formula_count or current_evidence.get("error_count") != 0:
        findings.append("machine recalculation proof does not establish complete error-free formula results")
    controls = current_evidence.get("control_cells")
    if (
        not isinstance(controls, dict)
        or set(controls) != EXPECTED_CONTROL_CELLS
        or not set(controls.values()) <= ALLOWED_CONTROL_STATES
    ):
        findings.append("machine recalculation proof does not establish proof-bound control states")
    return findings
