#!/usr/bin/env python3
"""Validate all tracked foundation cards and artifact blueprints."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("FAIL: jsonschema is required to validate the library", file=sys.stderr)
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parents[1]


def validate_group(schema_path: Path, paths: list[Path]) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    findings: list[str] = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for error in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in error.path) or "<root>"
            findings.append(f"{path.relative_to(ROOT)}:{location}: {error.message}")
    return findings


def main() -> int:
    groups = [
        (ROOT / "schemas/foundation-card.schema.json", sorted((ROOT / "library/foundations").glob("FOUND-*.json"))),
        (ROOT / "schemas/artifact-blueprint.schema.json", sorted((ROOT / "examples/blueprints").glob("BP-*.json"))),
    ]
    findings: list[str] = []
    for schema, paths in groups:
        findings.extend(validate_group(schema, paths))
    if findings:
        print("FAIL")
        print("\n".join(findings))
        return 1
    print(f"PASS: {sum(len(paths) for _, paths in groups)} library records validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

