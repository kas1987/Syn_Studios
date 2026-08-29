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
import tempfile
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
    "kind",
    "template_id",
    "version",
    "name",
    "artifact_type",
    "authority",
    "lifecycle",
    "blueprint_id",
    "descriptor",
    "native_assets",
    "supported_consumers",
    "capabilities",
    "release_status",
    "release_record",
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
    if entry["kind"] != "artifact_template":
        raise CatalogQueryError("released entry has invalid kind")
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
    release_record = entry["release_record"]
    if (
        not isinstance(release_record, dict)
        or set(release_record) != {"path", "sha256"}
        or not _safe_repo_path(release_record.get("path"))
        or not re.fullmatch(r"[a-f0-9]{64}", str(release_record.get("sha256", "")))
    ):
        raise CatalogQueryError("released entry release_record must be a bound repository-relative path")


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


def _bound_file(root: Path, reference: Any, label: str, *, under: Path | None = None) -> Path:
    if not isinstance(reference, dict) or not {"path", "sha256"}.issubset(reference):
        raise CatalogQueryError(f"{label} must be a bound path and hash")
    path = resolve_repo_path(root, reference.get("path"))
    if under is not None and not _is_within((root / under).resolve(), path):
        raise CatalogQueryError(f"{label} path is outside its owned directory")
    if reference.get("sha256") != _sha256(path):
        raise CatalogQueryError(f"{label} hash does not match file bytes")
    return path


def _evidence_file(
    root: Path,
    reference: Any,
    *,
    release: dict[str, Any],
    descriptor_hash: str,
    asset_hashes: set[str],
    expected_type: str,
    expected_verdict: str,
    label: str,
    category: str | None = None,
    procedure: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(reference, dict) or set(reference) != {"record_path", "record_sha256"}:
        raise CatalogQueryError(f"{label} must be a bound evidence reference")
    path = resolve_repo_path(root, reference.get("record_path"))
    evidence_root = root / "evidence" / "template-releases" / str(release.get("release_id", ""))
    if not _is_within(evidence_root, path):
        raise CatalogQueryError(f"{label} is outside the release evidence directory")
    if reference.get("record_sha256") != _sha256(path):
        raise CatalogQueryError(f"{label} record hash mismatch")
    record = _load_object(path, label)
    expected = {
        "schema_version": "1.0.0",
        "release_id": release.get("release_id"),
        "template_id": release.get("template_id"),
        "version": release.get("version"),
        "descriptor_sha256": descriptor_hash,
        "record_type": expected_type,
        "verdict": expected_verdict,
    }
    for field, value in expected.items():
        if record.get(field) != value:
            raise CatalogQueryError(f"{label} {field} is not bound to the release")
    record_asset_hashes = record.get("native_asset_sha256s")
    if not isinstance(record_asset_hashes, list) or not all(isinstance(value, str) for value in record_asset_hashes) or set(record_asset_hashes) != asset_hashes:
        raise CatalogQueryError(f"{label} is not bound to every native asset")
    actor_id = record.get("actor_id")
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise CatalogQueryError(f"{label} requires an actor_id")
    observations = record.get("observations")
    if not isinstance(observations, list) or not observations or not all(isinstance(item, str) and item.strip() for item in observations):
        raise CatalogQueryError(f"{label} requires concrete observations")
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise CatalogQueryError(f"{label} requires proof artifacts")
    artifact_categories = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise CatalogQueryError(f"{label} artifact {index} must be an object")
        artifact_path = _bound_file(root, artifact, f"{label} artifact {index}", under=Path("evidence/template-releases") / release["release_id"])
        if artifact_path == path or artifact_path.stat().st_size == 0:
            raise CatalogQueryError(f"{label} proof artifact must be nonempty and distinct from its record")
        artifact_categories.add(artifact.get("category"))
    if category is not None:
        categories = record.get("categories")
        if not isinstance(categories, list) or category not in categories or category not in artifact_categories:
            raise CatalogQueryError(f"{label} does not cover {category}")
        if not isinstance(record.get("procedures"), dict) or record["procedures"].get(category) != procedure:
            raise CatalogQueryError(f"{label} procedure does not match the blueprint")
    return path, record


def validate_release(root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Validate one catalog release through the schema-owned bound records."""
    root = root.resolve()
    descriptor_path = resolve_repo_path(root, entry["descriptor"])
    descriptor = _load_object(descriptor_path, "template descriptor")
    descriptor_hash = _sha256(descriptor_path)
    descriptor_fields = ("template_id", "version", "release_status", "artifact_type", "authority", "lifecycle", "supported_consumers", "capabilities")
    for field in descriptor_fields:
        if descriptor.get(field) != entry.get(field):
            raise CatalogQueryError(f"descriptor {field} does not match catalog")
    descriptor_lineage = descriptor.get("lineage")
    if not isinstance(descriptor_lineage, dict) or descriptor_lineage.get("blueprint_id") != entry.get("blueprint_id"):
        raise CatalogQueryError("descriptor blueprint lineage does not match catalog")

    release_path = _bound_file(root, entry["release_record"], "catalog release_record", under=Path("library/releases"))
    release = _load_object(release_path, "release record")
    release_expected = {
        "schema_version": "2.0.0",
        "template_id": entry["template_id"],
        "version": entry["version"],
        "status": "released",
    }
    for field, value in release_expected.items():
        if release.get(field) != value:
            raise CatalogQueryError(f"release {field} does not match catalog")
    if not re.fullmatch(r"REL-[0-9]{4}", str(release.get("release_id", ""))):
        raise CatalogQueryError("release has invalid release_id")
    if not release_path.name.startswith(f"{release['release_id']}."):
        raise CatalogQueryError("release_id does not match release filename")

    release_descriptor = release.get("descriptor")
    if not isinstance(release_descriptor, dict) or release_descriptor.get("path") != entry["descriptor"] or release_descriptor.get("sha256") != descriptor_hash:
        raise CatalogQueryError("release descriptor binding does not match catalog and descriptor bytes")

    descriptor_assets = descriptor.get("native_assets")
    release_assets = release.get("native_assets")
    if not isinstance(descriptor_assets, list) or not isinstance(release_assets, list) or not release_assets:
        raise CatalogQueryError("descriptor and release native_assets must be nonempty arrays")
    if not all(
        isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))
        for item in descriptor_assets + release_assets
    ):
        raise CatalogQueryError("descriptor and release native_assets must contain bound paths")
    descriptor_pairs = {(item.get("path"), item.get("sha256")) for item in descriptor_assets if isinstance(item, dict)}
    release_pairs = {(item.get("path"), item.get("sha256")) for item in release_assets if isinstance(item, dict)}
    if len(descriptor_pairs) != len(descriptor_assets) or len(release_pairs) != len(release_assets) or descriptor_pairs != release_pairs:
        raise CatalogQueryError("release and descriptor native asset bindings must match exactly")
    if [item.get("path") for item in descriptor_assets] != entry.get("native_assets"):
        raise CatalogQueryError("descriptor native assets do not match catalog")
    asset_hashes: dict[str, str] = {}
    for relative, expected_hash in descriptor_pairs:
        path = resolve_repo_path(root, relative)
        if not _is_within(root / "library" / "templates" / entry["template_id"] / entry["version"], path):
            raise CatalogQueryError("native asset is outside its template version directory")
        digest = _sha256(path)
        if expected_hash != digest:
            raise CatalogQueryError("native asset hash does not match file bytes")
        asset_hashes[relative] = digest

    blueprint_ref = release.get("blueprint")
    if not isinstance(blueprint_ref, dict) or blueprint_ref.get("blueprint_id") != entry["blueprint_id"]:
        raise CatalogQueryError("release blueprint identity does not match catalog")
    blueprint_path = _bound_file(root, {"path": blueprint_ref.get("path"), "sha256": blueprint_ref.get("sha256")}, "release blueprint", under=Path("examples/blueprints"))
    blueprint = _load_object(blueprint_path, "artifact blueprint")
    if blueprint.get("blueprint_id") != entry["blueprint_id"] or not blueprint_path.name.startswith(f"{entry['blueprint_id']}."):
        raise CatalogQueryError("release blueprint does not identify the indexed blueprint")
    blueprint_authority = blueprint.get("authority", {})
    comparisons = {
        "artifact_type": entry["artifact_type"],
        "lifecycle": entry["lifecycle"],
    }
    for field, value in comparisons.items():
        if blueprint.get(field) != value:
            raise CatalogQueryError(f"blueprint {field} does not match catalog")
    if not isinstance(blueprint_authority, dict) or blueprint_authority.get("primary_class") != entry["authority"]:
        raise CatalogQueryError("blueprint authority does not match catalog")
    proof_gates = blueprint.get("proof_gates")
    if not isinstance(proof_gates, list):
        raise CatalogQueryError("blueprint proof_gates must be an array")
    gates = {gate.get("category"): gate for gate in proof_gates if isinstance(gate, dict)}
    if set(gates) != REQUIRED_EVIDENCE:
        raise CatalogQueryError("blueprint must declare every release evidence category")

    sanitization = release.get("sanitization")
    descriptor_foundation_values = descriptor_lineage.get("foundation_ids", [])
    sanitization_values = sanitization.get("foundation_card_ids", []) if isinstance(sanitization, dict) else []
    if not isinstance(descriptor_foundation_values, list) or not isinstance(sanitization_values, list):
        raise CatalogQueryError("release sanitization lineage must use foundation arrays")
    descriptor_foundations = set(descriptor_foundation_values)
    blueprint_lineage = blueprint.get("foundation_lineage")
    if not isinstance(blueprint_lineage, list):
        raise CatalogQueryError("blueprint foundation_lineage must be an array")
    blueprint_foundations = {item.get("card_id") for item in blueprint_lineage if isinstance(item, dict)}
    if not isinstance(sanitization, dict) or set(sanitization_values) != descriptor_foundations or descriptor_foundations != blueprint_foundations:
        raise CatalogQueryError("release sanitization lineage does not match descriptor and blueprint")

    common = {
        "root": root,
        "release": release,
        "descriptor_hash": descriptor_hash,
        "asset_hashes": set(asset_hashes.values()),
    }
    _, sanitization_record = _evidence_file(**common, reference=sanitization.get("evidence"), expected_type="sanitization", expected_verdict="SANITIZATION_PASS", label="release sanitization")
    reviews = release.get("reviews")
    if not isinstance(reviews, dict):
        raise CatalogQueryError("release reviews must be an object")
    terra_path, terra = _evidence_file(**common, reference=reviews.get("terra"), expected_type="terra_review", expected_verdict="USABILITY_PASS", label="Terra review")
    sol_path, sol = _evidence_file(**common, reference=reviews.get("sol"), expected_type="sol_review", expected_verdict="INTEGRITY_PASS", label="Sol review")
    conductor_path, conductor = _evidence_file(**common, reference=release.get("conductor_approval"), expected_type="conductor_approval", expected_verdict="APPROVED", label="conductor approval")
    if len({terra_path, sol_path, conductor_path}) != 3:
        raise CatalogQueryError("Terra, Sol, and conductor records must be distinct")
    actor_ids = [str(record.get("actor_id", "")).strip().casefold() for record in (terra, sol, conductor)]
    if len(set(actor_ids)) != 3:
        raise CatalogQueryError("Terra, Sol, and conductor identities must be independent")

    evidence = release.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != REQUIRED_EVIDENCE:
        raise CatalogQueryError("release evidence must contain every required check family")
    for category, reference in evidence.items():
        gate = gates[category]
        verdict = "VALIDATION_PASS" if gate.get("applicable") is True else "VALIDATION_NOT_APPLICABLE"
        _evidence_file(**common, reference=reference, expected_type="technical_validation", expected_verdict=verdict, label=f"release evidence {category}", category=category, procedure=gate.get("procedure"))

    return {
        "status": "pass",
        "operation": "validate",
        "template_id": entry["template_id"],
        "version": entry["version"],
        "release_id": release["release_id"],
        "release_record": release_path.relative_to(root).as_posix(),
        "validated_native_assets": asset_hashes,
        "descriptor_sha256": descriptor_hash,
        "sanitization_record_id": sanitization_record.get("record_id"),
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
        sources = []
        for relative in entry["native_assets"]:
            source = resolve_repo_path(root, relative)
            if _sha256(source) != validation["validated_native_assets"][relative]:
                raise CatalogQueryError("native asset changed after release validation")
            sources.append(source)
        target_names = [source.name for source in sources]
        if len(target_names) != len(set(target_names)):
            raise CatalogQueryError("native asset names collide at output location")
        if output.exists() or output.is_symlink():
            raise CatalogQueryError("instantiate requires a new output location and will not overwrite")
        if not output.parent.is_dir():
            raise CatalogQueryError("instantiate output parent must already exist")
        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.syn-studios-", dir=output.parent))
        committed = False
        try:
            staged = []
            for relative, source in zip(entry["native_assets"], sources):
                target = staging / source.name
                shutil.copy2(source, target)
                target_hash = _sha256(target)
                if target_hash != validation["validated_native_assets"][relative]:
                    raise CatalogQueryError("materialized copy hash does not match source bytes")
                staged.append((target.name, target_hash))
            staging.replace(output)
            committed = True
            copies = [
                {"path": str(output / name), "sha256": target_hash}
                for name, target_hash in staged
            ]
        except OSError as exc:
            raise CatalogQueryError(f"materialization failed before commit: {exc}") from exc
        finally:
            if not committed and staging.exists():
                shutil.rmtree(staging)
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
