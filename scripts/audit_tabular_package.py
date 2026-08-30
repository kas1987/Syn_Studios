#!/usr/bin/env python3
"""Audit populated synthetic workbook/CSV carriers without claiming acceptance."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from xml.etree import ElementTree


SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
TOKEN = re.compile(rb"\{\{[^{}]+\}\}")
RECONCILIATION_RELATIONSHIPS = ("equal", "left_subset_of_right")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def has_reconstructed_token(root: ElementTree.Element, container: str) -> bool:
    """Scan one logical rich-text string at a time across its XML runs."""
    for item in root.findall(f".//{{{SHEET_NS}}}{container}"):
        visible_text = "".join(
            node.text or "" for node in item.findall(f".//{{{SHEET_NS}}}t")
        )
        if TOKEN.search(visible_text.encode("utf-8")):
            return True
    return False


def resolve_package_path(package_root: Path, value: object, label: str, findings: list[str]) -> Path | None:
    """Resolve one policy path without allowing it to leave the package."""
    raw = str(value)
    windows_path = PureWindowsPath(raw)
    posix_path = PurePosixPath(raw.replace("\\", "/"))
    relative = Path(raw)
    if (
        not raw
        or windows_path.drive
        or windows_path.root
        or posix_path.is_absolute()
        or ":" in raw
        or ".." in posix_path.parts
    ):
        findings.append(f"{label}: path must remain within package root")
        return None
    try:
        root = package_root.resolve()
        resolved = (root / relative).resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        findings.append(f"{label}: path must remain within package root")
        return None
    return resolved


def load_csv(path: Path, rule: dict, findings: list[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        headers = reader.fieldnames or []
        rows = list(reader)
    if not headers or len(headers) != len(set(headers)) or any(not value for value in headers):
        findings.append(f"{path.name}: headers must be nonempty and unique")
        return rows
    required = rule.get("required_columns", [])
    missing = [name for name in required if name not in headers]
    if missing:
        findings.append(f"{path.name}: missing required columns {missing}")
    minimum = int(rule.get("minimum_rows", 1))
    if len(rows) < minimum:
        findings.append(f"{path.name}: {len(rows)} rows is below minimum {minimum}")
    identifier = rule.get("id_column")
    if identifier and identifier not in headers:
        findings.append(f"{path.name}: configured id_column is missing: {identifier}")
    elif identifier in headers:
        values = [row.get(identifier, "").strip() for row in rows]
        if any(not value for value in values) or len(values) != len(set(values)):
            findings.append(f"{path.name}: {identifier} must be populated and unique")
    for column, threshold in rule.get("minimum_unique", {}).items():
        if column not in headers:
            findings.append(f"{path.name}: configured minimum_unique column is missing: {column}")
            continue
        observed = {row.get(column, "").strip() for row in rows if row.get(column, "").strip()}
        if len(observed) < int(threshold):
            findings.append(f"{path.name}: {column} has {len(observed)} unique values; requires {threshold}")
    lifecycle = rule.get("lifecycle")
    if isinstance(lifecycle, dict):
        status_column = str(lifecycle.get("status_column", ""))
        resolution_column = str(lifecycle.get("resolution_column", ""))
        allowed = set(lifecycle.get("allowed_statuses", []))
        observed_statuses = {row.get(status_column, "").strip() for row in rows}
        invalid = sorted(observed_statuses - allowed) if allowed else []
        if invalid:
            findings.append(f"{path.name}: invalid lifecycle statuses {invalid}")
        required = set(lifecycle.get("required_statuses", []))
        if not required <= observed_statuses:
            findings.append(f"{path.name}: missing required lifecycle statuses {sorted(required - observed_statuses)}")
        for row_number, row in enumerate(rows, start=2):
            status = row.get(status_column, "").strip()
            resolution = row.get(resolution_column, "").strip()
            if status in set(lifecycle.get("requires_resolution", [])) and not resolution:
                findings.append(f"{path.name}: row {row_number} {status} requires a resolution reference")
            if status in set(lifecycle.get("forbids_resolution", [])) and resolution:
                findings.append(f"{path.name}: row {row_number} {status} cannot carry a resolution reference")
    if rows and len({tuple(row.get(header, "") for header in headers) for row in rows}) != len(rows):
        findings.append(f"{path.name}: contains exact duplicate rows")
    return rows


def audit_workbook(path: Path, rule: dict, findings: list[str]) -> dict[str, object]:
    inventory = {"sha256": sha256(path), "formula_count": 0, "hidden_surfaces": [], "external_links": [], "embedded_objects": []}
    try:
        with zipfile.ZipFile(path) as package:
            names = package.namelist()
            token_members: set[str] = set()
            for name in names:
                payload = package.read(name)
                if TOKEN.search(payload):
                    findings.append(f"{path.name}: unresolved build token in {name}")
                    token_members.add(name)
                if name.startswith(("customXml/", "xl/comments", "xl/threadedComments", "xl/embeddings/")):
                    inventory["embedded_objects"].append(name)
            workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
            for sheet in workbook.findall(f"{{{SHEET_NS}}}sheets/{{{SHEET_NS}}}sheet"):
                if sheet.get("state", "visible") != "visible":
                    inventory["hidden_surfaces"].append(f"sheet:{sheet.get('name')}")
            for name in names:
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    root = ElementTree.fromstring(package.read(name))
                    if name not in token_members and has_reconstructed_token(root, "is"):
                        findings.append(f"{path.name}: unresolved build token in {name}")
                    inventory["formula_count"] += len(root.findall(f".//{{{SHEET_NS}}}f"))
                    for row in root.findall(f".//{{{SHEET_NS}}}row"):
                        if row.get("hidden") in {"1", "true"}:
                            inventory["hidden_surfaces"].append(f"{name}:row:{row.get('r')}")
                    for column in root.findall(f".//{{{SHEET_NS}}}col"):
                        if column.get("hidden") in {"1", "true"}:
                            inventory["hidden_surfaces"].append(f"{name}:column:{column.get('min')}-{column.get('max')}")
                    if root.findall(f".//{{{SHEET_NS}}}c[@t='e']"):
                        findings.append(f"{path.name}: formula error cells present in {name}")
                if name == "xl/sharedStrings.xml":
                    root = ElementTree.fromstring(package.read(name))
                    if name not in token_members and has_reconstructed_token(root, "si"):
                        findings.append(f"{path.name}: unresolved build token in {name}")
                if name.endswith(".rels"):
                    try:
                        relationships = ElementTree.fromstring(package.read(name))
                    except ElementTree.ParseError as error:
                        findings.append(
                            f"{path.name}: malformed relationship XML in {name}: {error}"
                        )
                        continue
                    for relationship in relationships.findall(f"{{{REL_NS}}}Relationship"):
                        if relationship.get("TargetMode") == "External":
                            inventory["external_links"].append(relationship.get("Target"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        findings.append(f"{path.name}: cannot inspect workbook: {error}")
        return inventory
    if rule.get("require_formulas") and not inventory["formula_count"]:
        findings.append(f"{path.name}: live formulas are required")
    if inventory["external_links"]:
        findings.append(f"{path.name}: external workbook links are prohibited")
    if inventory["hidden_surfaces"] and not rule.get("allow_hidden_surfaces", False):
        findings.append(f"{path.name}: hidden rows, columns, or sheets require explicit authorization")
    if inventory["embedded_objects"] and not rule.get("allow_embedded_objects", False):
        findings.append(f"{path.name}: comments, custom XML, or embedded objects require explicit authorization")
    return inventory


def audit(package_root: Path, policy: dict) -> dict[str, object]:
    findings: list[str] = []
    provenance = resolve_package_path(
        package_root,
        policy.get("provenance_reference", ""),
        "package provenance reference",
        findings,
    )
    if provenance is not None and not provenance.is_file():
        findings.append("package provenance reference is missing")
    workbook_rule = policy.get("workbook")
    workbook_inventory = None
    if isinstance(workbook_rule, dict):
        workbook = resolve_package_path(
            package_root,
            workbook_rule.get("path", ""),
            "declared workbook",
            findings,
        )
        workbook_inventory = (
            audit_workbook(workbook, workbook_rule, findings)
            if workbook is not None and workbook.is_file()
            else None
        )
        if workbook is not None and workbook_inventory is None:
            findings.append("declared workbook is missing")
    carriers: dict[str, list[dict[str, str]]] = {}
    carrier_metrics: dict[str, dict[str, object]] = {}
    for rule in policy.get("csv_carriers", []):
        relative = str(rule.get("path", ""))
        path = resolve_package_path(
            package_root,
            relative,
            f"{relative}: declared CSV carrier",
            findings,
        )
        if path is None:
            continue
        if not path.is_file():
            findings.append(f"{relative}: declared CSV carrier is missing")
            continue
        rows = load_csv(path, rule, findings)
        carriers[relative] = rows
        carrier_metrics[relative] = {"sha256": sha256(path), "rows": len(rows)}
    for rule in policy.get("reconciliations", []):
        left_path = str(rule.get("left_path"))
        right_path = str(rule.get("right_path"))
        if left_path not in carriers or right_path not in carriers:
            findings.append(
                f"reconciliation {rule.get('id', 'unnamed')} references an unavailable CSV carrier"
            )
            continue
        left_rows = carriers[left_path]
        right_rows = carriers[right_path]
        left_column = rule.get("left_column")
        right_column = rule.get("right_column")
        if (
            not isinstance(left_column, str)
            or not isinstance(right_column, str)
            or not left_rows
            or not right_rows
            or left_column not in left_rows[0]
            or right_column not in right_rows[0]
        ):
            findings.append(
                f"reconciliation {rule.get('id', 'unnamed')} references a missing operand column"
            )
            continue
        left = {row.get(left_column, "").strip() for row in left_rows}
        right = {row.get(right_column, "").strip() for row in right_rows}
        left.discard("")
        right.discard("")
        if not left or not right:
            findings.append(
                f"reconciliation {rule.get('id', 'unnamed')} has an empty operand population"
            )
            continue
        relation = rule.get("relationship")
        if relation not in RECONCILIATION_RELATIONSHIPS:
            findings.append(
                f"reconciliation {rule.get('id', 'unnamed')} has unsupported relationship: {relation}"
            )
            continue
        passed = left == right if relation == "equal" else left <= right
        if not passed:
            findings.append(f"reconciliation {rule.get('id', 'unnamed')} failed: {relation}")
    return {
        "schema_version": "1.0.0",
        "status": "pass" if not findings else "fail",
        "scope": "downstream_conformance_only_not_acceptance",
        "findings": findings,
        "workbook_inventory": workbook_inventory,
        "csv_carriers": carrier_metrics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args(argv)
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    result = audit(args.package_root.resolve(), policy)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
