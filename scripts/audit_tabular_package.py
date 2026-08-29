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
from pathlib import Path
from xml.etree import ElementTree


SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
TOKEN = re.compile(rb"\{\{[^{}]+\}\}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    if identifier in headers:
        values = [row.get(identifier, "").strip() for row in rows]
        if any(not value for value in values) or len(values) != len(set(values)):
            findings.append(f"{path.name}: {identifier} must be populated and unique")
    for column, threshold in rule.get("minimum_unique", {}).items():
        if column not in headers:
            continue
        observed = {row.get(column, "").strip() for row in rows if row.get(column, "").strip()}
        if len(observed) < int(threshold):
            findings.append(f"{path.name}: {column} has {len(observed)} unique values; requires {threshold}")
    if rows and len({tuple(row.get(header, "") for header in headers) for row in rows}) != len(rows):
        findings.append(f"{path.name}: contains exact duplicate rows")
    return rows


def audit_workbook(path: Path, rule: dict, findings: list[str]) -> dict[str, object]:
    inventory = {"sha256": sha256(path), "formula_count": 0, "hidden_surfaces": [], "external_links": [], "embedded_objects": []}
    try:
        with zipfile.ZipFile(path) as package:
            names = package.namelist()
            for name in names:
                payload = package.read(name)
                if TOKEN.search(payload):
                    findings.append(f"{path.name}: unresolved build token in {name}")
                if name.startswith(("customXml/", "xl/comments", "xl/threadedComments", "xl/embeddings/")):
                    inventory["embedded_objects"].append(name)
            workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
            for sheet in workbook.findall(f"{{{SHEET_NS}}}sheets/{{{SHEET_NS}}}sheet"):
                if sheet.get("state", "visible") != "visible":
                    inventory["hidden_surfaces"].append(f"sheet:{sheet.get('name')}")
            for name in names:
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    root = ElementTree.fromstring(package.read(name))
                    inventory["formula_count"] += len(root.findall(f".//{{{SHEET_NS}}}f"))
                    for row in root.findall(f".//{{{SHEET_NS}}}row[@hidden='1']"):
                        inventory["hidden_surfaces"].append(f"{name}:row:{row.get('r')}")
                    for column in root.findall(f".//{{{SHEET_NS}}}col[@hidden='1']"):
                        inventory["hidden_surfaces"].append(f"{name}:column:{column.get('min')}-{column.get('max')}")
                    if root.findall(f".//{{{SHEET_NS}}}c[@t='e']"):
                        findings.append(f"{path.name}: formula error cells present in {name}")
                if name.endswith(".rels"):
                    try:
                        relationships = ElementTree.fromstring(package.read(name))
                    except ElementTree.ParseError:
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
    provenance = package_root / str(policy.get("provenance_reference", ""))
    if not provenance.is_file():
        findings.append("package provenance reference is missing")
    workbook_rule = policy.get("workbook")
    workbook_inventory = None
    if isinstance(workbook_rule, dict):
        workbook = package_root / str(workbook_rule.get("path", ""))
        workbook_inventory = audit_workbook(workbook, workbook_rule, findings) if workbook.is_file() else None
        if workbook_inventory is None:
            findings.append("declared workbook is missing")
    carriers: dict[str, list[dict[str, str]]] = {}
    carrier_metrics: dict[str, dict[str, object]] = {}
    for rule in policy.get("csv_carriers", []):
        relative = str(rule.get("path", ""))
        path = package_root / relative
        if not path.is_file():
            findings.append(f"{relative}: declared CSV carrier is missing")
            continue
        rows = load_csv(path, rule, findings)
        carriers[relative] = rows
        carrier_metrics[relative] = {"sha256": sha256(path), "rows": len(rows)}
    for rule in policy.get("reconciliations", []):
        left_rows = carriers.get(str(rule.get("left_path")), [])
        right_rows = carriers.get(str(rule.get("right_path")), [])
        left = {row.get(str(rule.get("left_column")), "").strip() for row in left_rows}
        right = {row.get(str(rule.get("right_column")), "").strip() for row in right_rows}
        left.discard("")
        right.discard("")
        relation = rule.get("relationship", "equal")
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
