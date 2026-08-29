#!/usr/bin/env python3
"""Discover or select exact released Syn Studios template versions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "library" / "catalog.json"
FLOATING_VERSIONS = {"latest", "*", "current", "stable"}
AUTHORITY_CLASSES = {
    "authoritative",
    "supporting",
    "contextual",
    "superseded",
    "question-only",
    "incidental",
    "mixed",
}
RELEASED_REQUIRED_FIELDS = {
    "template_id",
    "version",
    "artifact_type",
    "authority",
    "lifecycle",
    "blueprint_id",
    "descriptor",
    "native_assets",
    "supported_consumers",
    "capabilities",
    "release_status",
}


class CatalogQueryError(ValueError):
    """A deterministic catalog input or query failure."""


def load_catalog(path: Path) -> dict[str, Any]:
    """Load the minimum catalog contract required by the consumer seam."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogQueryError(f"catalog not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogQueryError(f"catalog is not valid JSON: {exc}") from exc

    if data.get("schema_version") != "1.0.0":
        raise CatalogQueryError("unsupported catalog schema_version")
    if data.get("catalog_id") != "syn-studios-artifact-library":
        raise CatalogQueryError("unexpected catalog_id")
    if not isinstance(data.get("templates"), list):
        raise CatalogQueryError("catalog templates must be an array")
    released_keys = set()
    for entry in data["templates"]:
        if not isinstance(entry, dict):
            raise CatalogQueryError("catalog entries must be objects")
        if not _released(entry):
            continue
        _validate_released_entry(entry)
        key = (entry["template_id"], entry["version"])
        if key in released_keys:
            raise CatalogQueryError("catalog contains duplicate released template_id/version entries")
        released_keys.add(key)
    return data


def _released(entry: dict[str, Any]) -> bool:
    return entry.get("release_status") == "released"


def _safe_repo_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and path.as_posix() not in {".", ""}


def _validate_released_entry(entry: dict[str, Any]) -> None:
    missing = sorted(RELEASED_REQUIRED_FIELDS - entry.keys())
    if missing:
        raise CatalogQueryError(f"released entry missing fields: {', '.join(missing)}")
    if not re.fullmatch(r"TMPL-[0-9]{4}", entry["template_id"]):
        raise CatalogQueryError("released entry has invalid template_id")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", entry["version"]):
        raise CatalogQueryError("released entry version must be exact semantic version")
    if not re.fullmatch(r"BP-[0-9]{4}", entry["blueprint_id"]):
        raise CatalogQueryError("released entry has invalid blueprint_id")
    if entry["authority"] not in AUTHORITY_CLASSES:
        raise CatalogQueryError("released entry has unsupported authority class")
    if not _safe_repo_path(entry["descriptor"]):
        raise CatalogQueryError("released entry descriptor must be a safe repository-relative path")
    if not isinstance(entry["native_assets"], list) or not entry["native_assets"]:
        raise CatalogQueryError("released entry native_assets must be a non-empty array")
    if not all(_safe_repo_path(value) for value in entry["native_assets"]):
        raise CatalogQueryError("released entry native_assets must use safe repository-relative paths")
    for field in ("supported_consumers", "capabilities"):
        if not isinstance(entry[field], list) or not all(isinstance(value, str) and value for value in entry[field]):
            raise CatalogQueryError(f"released entry {field} must be an array of non-empty strings")


def _contains_all(entry: dict[str, Any], field: str, requested: Iterable[str]) -> bool:
    actual = entry.get(field, [])
    return isinstance(actual, list) and set(requested).issubset(actual)


def discover(
    catalog: dict[str, Any],
    *,
    artifact_type: str | None = None,
    blueprint_id: str | None = None,
    authority: str | None = None,
    lifecycle: str | None = None,
    consumers: Iterable[str] = (),
    capabilities: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return released entries matching every supplied constraint."""
    scalar_filters = {
        "artifact_type": artifact_type,
        "blueprint_id": blueprint_id,
        "authority": authority,
        "lifecycle": lifecycle,
    }
    matches = []
    for entry in catalog["templates"]:
        if not isinstance(entry, dict) or not _released(entry):
            continue
        if any(value is not None and entry.get(field) != value for field, value in scalar_filters.items()):
            continue
        if not _contains_all(entry, "supported_consumers", consumers):
            continue
        if not _contains_all(entry, "capabilities", capabilities):
            continue
        matches.append(entry)
    return sorted(matches, key=lambda item: (item.get("template_id", ""), item.get("version", "")))


def select_exact(
    catalog: dict[str, Any],
    *,
    template_id: str,
    version: str,
    consumers: Iterable[str] = (),
    capabilities: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Select one released version; never resolve a floating version label."""
    normalized = version.strip().lower()
    if not normalized or normalized in FLOATING_VERSIONS:
        raise CatalogQueryError("version must be an exact value; floating versions are forbidden")

    matches = [
        entry
        for entry in discover(catalog, consumers=consumers, capabilities=capabilities)
        if entry.get("template_id") == template_id and entry.get("version") == version
    ]
    return matches[0] if matches else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    discover_parser = subparsers.add_parser("discover", help="list compatible released templates")
    discover_parser.add_argument("--artifact-type")
    discover_parser.add_argument("--blueprint-id")
    discover_parser.add_argument("--authority")
    discover_parser.add_argument("--lifecycle")
    discover_parser.add_argument("--consumer", action="append", default=[])
    discover_parser.add_argument("--capability", action="append", default=[])

    select_parser = subparsers.add_parser("select", help="select one exact released version")
    select_parser.add_argument("--template-id", required=True)
    select_parser.add_argument("--version", required=True)
    select_parser.add_argument("--consumer", action="append", default=[])
    select_parser.add_argument("--capability", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
        if args.operation == "discover":
            matches = discover(
                catalog,
                artifact_type=args.artifact_type,
                blueprint_id=args.blueprint_id,
                authority=args.authority,
                lifecycle=args.lifecycle,
                consumers=args.consumer,
                capabilities=args.capability,
            )
            result = {"status": "ok", "operation": "discover", "count": len(matches), "templates": matches}
        else:
            match = select_exact(
                catalog,
                template_id=args.template_id,
                version=args.version,
                consumers=args.consumer,
                capabilities=args.capability,
            )
            result = {
                "status": "ok" if match else "no_match",
                "operation": "select",
                "template": match,
            }
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "ok" else 3
    except CatalogQueryError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
