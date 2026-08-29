#!/usr/bin/env python3
"""Discover or select exact released Syn Studios template versions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
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
REQUIRED_EVIDENCE = {
    "core_integrity",
    "render",
    "metadata",
    "computational",
    "provenance",
    "leakage",
    "authority_separation",
    "anti_filler",
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
    if not isinstance(value, str) or not value:
        return False
    windows_path = PureWindowsPath(value)
    posix_path = PurePosixPath(value.replace("\\", "/"))
    if windows_path.drive or windows_path.root or posix_path.is_absolute() or ":" in value:
        return False
    return ".." not in posix_path.parts and posix_path.as_posix() not in {".", ""}


def resolve_repo_path(root: Path, value: Any, *, require_file: bool = True) -> Path:
    """Resolve a catalog-controlled path and prove containment on Windows or POSIX."""
    if not _safe_repo_path(value):
        raise CatalogQueryError("path must be safe and repository-relative")
    root = root.resolve()
    resolved = (root / Path(str(value).replace("\\", "/"))).resolve()
    try:
        contained = os.path.commonpath((str(root), str(resolved))) == str(root)
    except ValueError:
        contained = False
    if not contained:
        raise CatalogQueryError("resolved path escapes repository root")
    if require_file and not resolved.is_file():
        raise CatalogQueryError(f"referenced file does not exist: {value}")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(parent: Path, child: Path) -> bool:
    try:
        return os.path.commonpath((str(parent.resolve()), str(child.resolve()))) == str(parent.resolve())
    except ValueError:
        return False


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogQueryError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogQueryError(f"{label} must be a JSON object")
    return value


def _entry_context(root: Path, entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    descriptor = _load_object(resolve_repo_path(root, entry["descriptor"]), "template descriptor")
    blueprint_matches = sorted((root / "examples" / "blueprints").glob(f"{entry['blueprint_id']}.*.json"))
    if len(blueprint_matches) != 1:
        raise CatalogQueryError("catalog blueprint_id must resolve to exactly one blueprint")
    blueprint = _load_object(blueprint_matches[0], "artifact blueprint")
    return descriptor, blueprint


def _compatible_context(
    descriptor: dict[str, Any],
    blueprint: dict[str, Any],
    *,
    producer_role: str | None,
    medium: str | None,
    required_allowed_knowledge: Iterable[str],
    prohibited_knowledge: Iterable[str],
) -> bool:
    producer = descriptor.get("producer", {})
    if producer_role is not None and (
        not isinstance(producer, dict) or str(producer.get("role", "")).casefold() != producer_role.casefold()
    ):
        return False
    if medium is not None and str(blueprint.get("medium", "")).casefold() != medium.casefold():
        return False
    if not _contains_all(descriptor, "knowledge_and_authority_constraints", required_allowed_knowledge):
        return False
    if not _contains_all(descriptor, "prohibited_content", prohibited_knowledge):
        return False
    return True


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
    producer_role: str | None = None,
    medium: str | None = None,
    consumers: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    required_allowed_knowledge: Iterable[str] = (),
    prohibited_knowledge: Iterable[str] = (),
    repository_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return released entries matching every supplied constraint."""
    required_allowed_knowledge = tuple(required_allowed_knowledge)
    prohibited_knowledge = tuple(prohibited_knowledge)
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
        contextual_filters = producer_role is not None or medium is not None or bool(required_allowed_knowledge) or bool(prohibited_knowledge)
        if contextual_filters:
            if repository_root is None:
                raise CatalogQueryError("repository_root is required for producer, medium, or knowledge constraints")
            descriptor, blueprint = _entry_context(repository_root.resolve(), entry)
            if not _compatible_context(
                descriptor,
                blueprint,
                producer_role=producer_role,
                medium=medium,
                required_allowed_knowledge=required_allowed_knowledge,
                prohibited_knowledge=prohibited_knowledge,
            ):
                continue
        matches.append(entry)
    return sorted(matches, key=lambda item: (item.get("template_id", ""), item.get("version", "")))


def select_exact(
    catalog: dict[str, Any],
    *,
    template_id: str,
    version: str,
    authority: str | None = None,
    producer_role: str | None = None,
    medium: str | None = None,
    consumers: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    required_allowed_knowledge: Iterable[str] = (),
    prohibited_knowledge: Iterable[str] = (),
    repository_root: Path | None = None,
) -> dict[str, Any] | None:
    """Select one released version; never resolve a floating version label."""
    normalized = version.strip().lower()
    if not normalized or normalized in FLOATING_VERSIONS:
        raise CatalogQueryError("version must be an exact value; floating versions are forbidden")

    matches = [
        entry
        for entry in discover(
            catalog,
            authority=authority,
            producer_role=producer_role,
            medium=medium,
            consumers=consumers,
            capabilities=capabilities,
            required_allowed_knowledge=required_allowed_knowledge,
            prohibited_knowledge=prohibited_knowledge,
            repository_root=repository_root,
        )
        if entry.get("template_id") == template_id and entry.get("version") == version
    ]
    return matches[0] if matches else None


def validate_release(root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Validate catalog, descriptor, native bytes, blueprint, and release evidence."""
    root = root.resolve()
    descriptor, blueprint = _entry_context(root, entry)
    for field in ("template_id", "version"):
        if descriptor.get(field) != entry.get(field):
            raise CatalogQueryError(f"descriptor {field} does not match catalog")
    if descriptor.get("lineage", {}).get("blueprint_id") != entry.get("blueprint_id"):
        raise CatalogQueryError("descriptor blueprint lineage does not match catalog")
    for field in ("release_status", "artifact_type", "authority", "lifecycle", "supported_consumers", "capabilities"):
        if descriptor.get(field) != entry.get(field):
            raise CatalogQueryError(f"descriptor {field} does not match catalog")
    if blueprint.get("blueprint_id") != entry.get("blueprint_id"):
        raise CatalogQueryError("blueprint identity does not match catalog")
    proof_gates = blueprint.get("proof_gates")
    if not isinstance(proof_gates, list):
        raise CatalogQueryError("blueprint proof_gates must be an array")
    gates_by_category = {
        gate.get("category"): gate for gate in proof_gates if isinstance(gate, dict) and gate.get("category") in REQUIRED_EVIDENCE
    }
    if set(gates_by_category) != REQUIRED_EVIDENCE:
        raise CatalogQueryError("blueprint must declare every release evidence category")
    descriptor_assets = descriptor.get("native_assets")
    if not isinstance(descriptor_assets, list):
        raise CatalogQueryError("descriptor native_assets must be an array")
    described_paths = [item.get("path") for item in descriptor_assets if isinstance(item, dict)]
    if described_paths != entry.get("native_assets"):
        raise CatalogQueryError("descriptor native assets do not match catalog")
    asset_hashes: dict[str, str] = {}
    for item in descriptor_assets:
        path = resolve_repo_path(root, item.get("path"))
        digest = _sha256(path)
        if item.get("sha256") != digest:
            raise CatalogQueryError("descriptor native asset hash does not match file bytes")
        asset_hashes[item["path"]] = digest

    release_matches = []
    for release_path in sorted((root / "library" / "releases").glob("REL-*.json")):
        release = _load_object(release_path, "release record")
        if (
            release.get("status") == "released"
            and release.get("version") == entry.get("version")
            and release.get("blueprint", {}).get("blueprint_id") == entry.get("blueprint_id")
            and release.get("template", {}).get("path") in asset_hashes
        ):
            release_matches.append((release_path, release))
    if len(release_matches) != 1:
        raise CatalogQueryError("catalog entry must resolve to exactly one released evidence record")
    release_path, release = release_matches[0]
    template = release.get("template", {})
    if template.get("sha256") != asset_hashes.get(template.get("path")):
        raise CatalogQueryError("release template hash does not match native file bytes")
    if template.get("artifact_type") != entry.get("artifact_type"):
        raise CatalogQueryError("release artifact_type does not match catalog")

    blueprint_ref = release.get("blueprint", {})
    blueprint_path = resolve_repo_path(root, blueprint_ref.get("path"))
    if blueprint_path.name not in {path.name for path in (root / "examples" / "blueprints").glob(f"{entry['blueprint_id']}.*.json")}:
        raise CatalogQueryError("release blueprint path does not match catalog blueprint_id")
    if blueprint_ref.get("sha256") != _sha256(blueprint_path):
        raise CatalogQueryError("release blueprint hash does not match file bytes")

    evidence = release.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != REQUIRED_EVIDENCE:
        raise CatalogQueryError("release evidence must contain every required check family")
    template_hash = template["sha256"]
    for category, record in evidence.items():
        if not isinstance(record, dict) or record.get("template_sha256") != template_hash:
            raise CatalogQueryError(f"release evidence {category} is not bound to template hash")
        gate = gates_by_category[category]
        expected_status = "pass" if gate.get("applicable") is True else "not_applicable"
        if record.get("status") != expected_status:
            raise CatalogQueryError(f"release evidence {category} status conflicts with blueprint applicability")
        if record.get("blueprint_procedure") != gate.get("procedure"):
            raise CatalogQueryError(f"release evidence {category} procedure does not match blueprint")
        if record.get("status") == "pass":
            record_path = resolve_repo_path(root, record.get("record_path"))
            if record.get("record_sha256") != _sha256(record_path):
                raise CatalogQueryError(f"release evidence {category} record hash mismatch")
        elif record.get("status") != "not_applicable":
            raise CatalogQueryError(f"release evidence {category} has invalid status")
        elif not isinstance(record.get("rationale"), str) or not record["rationale"].strip():
            raise CatalogQueryError(f"release evidence {category} needs a not-applicable rationale")
    bound_records = {
        "sanitization": release.get("sanitization"),
        "terra_review": release.get("reviews", {}).get("terra"),
        "sol_review": release.get("reviews", {}).get("sol"),
        "conductor_approval": release.get("conductor_approval"),
    }
    for label, record in bound_records.items():
        if not isinstance(record, dict) or record.get("template_sha256") != template_hash:
            raise CatalogQueryError(f"release {label} is not bound to template hash")
        record_path = resolve_repo_path(root, record.get("record_path"))
        if record.get("record_sha256") != _sha256(record_path):
            raise CatalogQueryError(f"release {label} record hash mismatch")
    identities = [
        bound_records["terra_review"].get("reviewer"),
        bound_records["sol_review"].get("reviewer"),
        bound_records["conductor_approval"].get("approver"),
    ]
    if len(set(identities)) != 3:
        raise CatalogQueryError("release reviewer and approver identities must be independent")
    if bound_records["terra_review"].get("verdict") != "pass" or bound_records["sol_review"].get("verdict") != "pass":
        raise CatalogQueryError("release requires passing Terra and Sol reviews")
    if bound_records["conductor_approval"].get("decision") != "approved":
        raise CatalogQueryError("release requires conductor approval")
    descriptor_foundations = descriptor.get("lineage", {}).get("foundation_ids")
    if not isinstance(descriptor_foundations, list) or set(descriptor_foundations) != set(bound_records["sanitization"].get("foundation_card_ids", [])):
        raise CatalogQueryError("release sanitization lineage does not match template descriptor")
    return {
        "status": "pass",
        "operation": "validate",
        "template_id": entry["template_id"],
        "version": entry["version"],
        "release_id": release.get("release_id"),
        "release_record": release_path.relative_to(root).as_posix(),
        "validated_native_assets": asset_hashes,
    }


def instantiate(
    *,
    root: Path,
    catalog: dict[str, Any],
    template_id: str,
    version: str,
    package_root: Path,
    output_location: Path,
    manifest_approved: bool,
    write_authorized: bool,
    source_authorized: bool,
    world_fact_keys: Iterable[str] = (),
    provenance_reference: str,
    materialize: bool = False,
) -> dict[str, Any]:
    """Return or materialize a package-local binding after all authority gates pass."""
    if not (manifest_approved and write_authorized and source_authorized):
        raise CatalogQueryError("instantiate requires manifest, write, and source authorization")
    entry = select_exact(catalog, template_id=template_id, version=version)
    if entry is None:
        raise CatalogQueryError("exact released template selection not found")
    validation = validate_release(root, entry)

    package_root = package_root.resolve()
    output = output_location if output_location.is_absolute() else package_root / output_location
    output = output.resolve()
    if not _is_within(package_root, output):
        raise CatalogQueryError("output location escapes package root")
    library_root = (root.resolve() / "library").resolve()
    if _is_within(library_root, output):
        raise CatalogQueryError("instantiate output cannot be inside the Syn Studios library")

    copies = []
    if materialize:
        output.mkdir(parents=True, exist_ok=True)
        target_names = [Path(relative).name for relative in entry["native_assets"]]
        if len(target_names) != len(set(target_names)):
            raise CatalogQueryError("native asset names collide at output location")
        for relative in entry["native_assets"]:
            source = resolve_repo_path(root, relative)
            target = output / source.name
            if target.exists() or target.is_symlink():
                raise CatalogQueryError("instantiate will not overwrite an existing output")
            shutil.copy2(source, target)
            copies.append({"path": str(target), "sha256": _sha256(target)})
    return {
        "status": "ready",
        "operation": "instantiate",
        "mode": "materialized_copy" if materialize else "plan",
        "binding": {
            "template_id": template_id,
            "version": version,
            "blueprint_id": entry["blueprint_id"],
            "release_id": validation["release_id"],
            "provenance_reference": provenance_reference,
            "world_fact_keys": sorted(set(world_fact_keys)),
            "output_location": str(output),
        },
        "materialized_assets": copies,
        "template_bytes_mutated": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--catalog", type=Path)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    discover_parser = subparsers.add_parser("discover", help="list compatible released templates")
    discover_parser.add_argument("--artifact-type")
    discover_parser.add_argument("--blueprint-id")
    discover_parser.add_argument("--authority")
    discover_parser.add_argument("--lifecycle")
    discover_parser.add_argument("--producer-role")
    discover_parser.add_argument("--medium")
    discover_parser.add_argument("--consumer", action="append", default=[])
    discover_parser.add_argument("--capability", action="append", default=[])
    discover_parser.add_argument("--required-knowledge", action="append", default=[])
    discover_parser.add_argument("--prohibited-knowledge", action="append", default=[])

    select_parser = subparsers.add_parser("select", help="select one exact released version")
    select_parser.add_argument("--template-id", required=True)
    select_parser.add_argument("--version", required=True)
    select_parser.add_argument("--authority")
    select_parser.add_argument("--producer-role")
    select_parser.add_argument("--medium")
    select_parser.add_argument("--consumer", action="append", default=[])
    select_parser.add_argument("--capability", action="append", default=[])
    select_parser.add_argument("--required-knowledge", action="append", default=[])
    select_parser.add_argument("--prohibited-knowledge", action="append", default=[])

    instantiate_parser = subparsers.add_parser("instantiate", help="plan or copy an authorized package-local binding")
    instantiate_parser.add_argument("--template-id", required=True)
    instantiate_parser.add_argument("--version", required=True)
    instantiate_parser.add_argument("--package-root", type=Path, required=True)
    instantiate_parser.add_argument("--output-location", type=Path, required=True)
    instantiate_parser.add_argument("--provenance-reference", required=True)
    instantiate_parser.add_argument("--world-fact-key", action="append", default=[])
    instantiate_parser.add_argument("--manifest-approved", action="store_true")
    instantiate_parser.add_argument("--write-authorized", action="store_true")
    instantiate_parser.add_argument("--source-authorized", action="store_true")
    instantiate_parser.add_argument("--materialize", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="validate an exact release and its bound files")
    validate_parser.add_argument("--template-id", required=True)
    validate_parser.add_argument("--version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.root.resolve()
        catalog_path = args.catalog or (root / "library" / "catalog.json")
        catalog = load_catalog(catalog_path)
        if args.operation == "discover":
            matches = discover(
                catalog,
                artifact_type=args.artifact_type,
                blueprint_id=args.blueprint_id,
                authority=args.authority,
                lifecycle=args.lifecycle,
                producer_role=args.producer_role,
                medium=args.medium,
                consumers=args.consumer,
                capabilities=args.capability,
                required_allowed_knowledge=args.required_knowledge,
                prohibited_knowledge=args.prohibited_knowledge,
                repository_root=root,
            )
            result = {"status": "ok", "operation": "discover", "count": len(matches), "templates": matches}
        elif args.operation == "select":
            match = select_exact(
                catalog,
                template_id=args.template_id,
                version=args.version,
                authority=args.authority,
                producer_role=args.producer_role,
                medium=args.medium,
                consumers=args.consumer,
                capabilities=args.capability,
                required_allowed_knowledge=args.required_knowledge,
                prohibited_knowledge=args.prohibited_knowledge,
                repository_root=root,
            )
            result = {
                "status": "ok" if match else "no_match",
                "operation": "select",
                "template": match,
            }
        elif args.operation == "instantiate":
            result = instantiate(
                root=root,
                catalog=catalog,
                template_id=args.template_id,
                version=args.version,
                package_root=args.package_root,
                output_location=args.output_location,
                manifest_approved=args.manifest_approved,
                write_authorized=args.write_authorized,
                source_authorized=args.source_authorized,
                world_fact_keys=args.world_fact_key,
                provenance_reference=args.provenance_reference,
                materialize=args.materialize,
            )
        else:
            entry = select_exact(catalog, template_id=args.template_id, version=args.version)
            if entry is None:
                result = {"status": "no_match", "operation": "validate", "template": None}
            else:
                result = validate_release(root, entry)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] in {"ok", "ready", "pass"} else 3
    except CatalogQueryError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
