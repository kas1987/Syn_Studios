#!/usr/bin/env python3
"""Discover, recommend, or resolve exact released Syn Studios versions."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.immutable_publication import locked_directory


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
SUBMISSION_PROFILE_SCHEMA = Path("schemas/submission-profile.schema.json")
SUBMISSION_PROFILE_ROOT = Path("library/submissions")


class CatalogQueryError(ValueError):
    """A deterministic catalog input or query failure."""


def _canonical_validate_repository(root: Path) -> None:
    """Fail closed unless the repository's canonical control-plane validator passes."""
    root = root.resolve()
    validator_path = root / "scripts" / "validate_library.py"
    if not validator_path.is_file():
        raise CatalogQueryError("canonical library validator is unavailable")
    try:
        spec = importlib.util.spec_from_file_location("syn_studios_canonical_validator", validator_path)
        if spec is None or spec.loader is None:
            raise ImportError("validator module could not be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        validate_repository = getattr(module, "validate_repository")
        findings, _ = validate_repository(root)
    except SystemExit as exc:
        raise CatalogQueryError(f"canonical library validator terminated with exit code {exc.code}") from exc
    except Exception as exc:
        raise CatalogQueryError(f"canonical library validation could not run: {exc}") from exc
    if findings:
        raise CatalogQueryError(f"canonical library validation failed: {findings[0]}")


def _canonical_consumer_ids(root: Path) -> set[str]:
    profile_path = root.resolve() / "integrations" / "consumer-profile.v1.json"
    profile = _load_object(profile_path, "consumer profile")
    interface = profile.get("interface")
    operations = interface.get("operations") if isinstance(interface, dict) else None
    resolver_modes = interface.get("resolver_modes") if isinstance(interface, dict) else None
    operation_contracts = profile.get("operations")
    if (
        profile.get("schema_version") != "1.0.0"
        or profile.get("profile_id") != "syn-studios-consumer"
        or profile.get("status") != "stable"
        or not isinstance(interface, dict)
        or not isinstance(operations, list)
        or not operations
        or any(not isinstance(value, str) or not value for value in operations)
        or len(set(operations)) != len(operations)
        or resolver_modes != operations
        or interface.get("resolver") != "integrations/query_catalog.py"
        or not isinstance(operation_contracts, dict)
        or set(operation_contracts) != set(operations)
    ):
        raise CatalogQueryError("consumer profile interface does not match the canonical resolver contract")
    consumers = profile.get("consumers")
    if not isinstance(consumers, list):
        raise CatalogQueryError("consumer profile consumers must be an array")
    identifiers = {
        item.get("consumer_id")
        for item in consumers
        if isinstance(item, dict) and isinstance(item.get("consumer_id"), str)
    }
    if len(identifiers) != len(consumers) or not identifiers:
        raise CatalogQueryError("consumer profile must declare unique canonical consumer IDs")
    for item in consumers:
        modes = item.get("modes")
        if (
            not isinstance(modes, list)
            or not modes
            or any(not isinstance(value, str) or not value for value in modes)
            or len(set(modes)) != len(modes)
        ):
            raise CatalogQueryError("consumer profile modes must be nonempty unique strings")
    return identifiers


def _require_consumer_id(root: Path, consumer_id: str, operation: str) -> None:
    if not isinstance(consumer_id, str) or consumer_id not in _canonical_consumer_ids(root):
        raise CatalogQueryError("consumer_id must exactly match a canonical consumer profile ID")
    profile = _load_object(root.resolve() / "integrations" / "consumer-profile.v1.json", "consumer profile")
    if operation not in profile["interface"]["operations"]:
        raise CatalogQueryError(f"consumer profile does not declare resolver operation: {operation}")
    consumer = next(item for item in profile["consumers"] if item["consumer_id"] == consumer_id)
    if operation not in consumer["modes"]:
        raise CatalogQueryError(f"consumer profile does not allow {consumer_id} mode: {operation}")


def _validate_catalog_consumer_ids(root: Path, catalog: dict[str, Any]) -> None:
    canonical_ids = _canonical_consumer_ids(root)
    for entry in catalog.get("templates", []):
        if not isinstance(entry, dict):
            continue
        supported = entry.get("supported_consumers", [])
        if not isinstance(supported, list) or any(value not in canonical_ids for value in supported):
            raise CatalogQueryError("catalog supported_consumers must use canonical consumer profile IDs")


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
    selectable_keys = set()
    for entry in data["templates"]:
        if not isinstance(entry, dict):
            raise CatalogQueryError("catalog entries must be objects")
        if not _selectable(entry):
            continue
        _validate_selectable_entry(entry)
        key = (entry["template_id"], entry["version"])
        if key in selectable_keys:
            raise CatalogQueryError("catalog contains duplicate selectable template_id/version entries")
        selectable_keys.add(key)
    return data


def _selectable(entry: dict[str, Any]) -> bool:
    return entry.get("release_status") in {"released", "deprecated"}


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


def _is_path_indirection(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def _atomic_rename_no_replace(
    source: Path,
    target: Path,
    directory_descriptor: int | None = None,
) -> None:
    """Atomically publish a staged directory only when the target is absent."""
    if os.name == "nt":
        if directory_descriptor is not None:
            raise OSError(errno.EINVAL, "Windows publication uses a locked parent path")
        os.rename(source, target)
        return
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        source_descriptor = directory_descriptor if directory_descriptor is not None else -100
        target_descriptor = directory_descriptor if directory_descriptor is not None else -100
        source_name = source.name if directory_descriptor is not None else source
        target_name = target.name if directory_descriptor is not None else target
        if renameat2(
            source_descriptor,
            os.fsencode(source_name),
            target_descriptor,
            os.fsencode(target_name),
            1,
        ) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
        return
    raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")


def _path_exists_in_directory(
    parent: Path,
    name: str,
    directory_descriptor: int | None,
) -> bool:
    if directory_descriptor is None:
        candidate = parent / name
        return candidate.exists() or candidate.is_symlink()
    try:
        os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _create_staging_directory(
    parent: Path,
    output_name: str,
    directory_descriptor: int | None,
) -> Path:
    if directory_descriptor is None:
        return Path(tempfile.mkdtemp(prefix=f".{output_name}.syn-studios-", dir=parent))
    descriptor_root = Path(f"/proc/self/fd/{directory_descriptor}")
    if not descriptor_root.is_dir():
        raise OSError(errno.ENOTSUP, "identity-bound directory staging requires /proc/self/fd")
    for _ in range(100):
        name = f".{output_name}.syn-studios-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=directory_descriptor)
        except FileExistsError:
            continue
        return descriptor_root / name
    raise FileExistsError("could not allocate a unique materialization staging directory")


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogQueryError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogQueryError(f"{label} must be a JSON object")
    return value


def _load_json_value(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogQueryError(f"cannot read {label}: {exc}") from exc


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


def _validate_selectable_entry(entry: dict[str, Any]) -> None:
    missing = sorted(RELEASED_REQUIRED_FIELDS - entry.keys())
    if missing:
        raise CatalogQueryError(f"selectable entry missing fields: {', '.join(missing)}")
    if entry["kind"] != "artifact_template":
        raise CatalogQueryError("selectable entry has invalid kind")
    if not re.fullmatch(r"TMPL-[0-9]{4}", entry["template_id"]):
        raise CatalogQueryError("selectable entry has invalid template_id")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", entry["version"]):
        raise CatalogQueryError("selectable entry version must be exact semantic version")
    if not re.fullmatch(r"BP-[0-9]{4}", entry["blueprint_id"]):
        raise CatalogQueryError("selectable entry has invalid blueprint_id")
    if entry["authority"] not in AUTHORITY_CLASSES:
        raise CatalogQueryError("selectable entry has unsupported authority class")
    if not _safe_repo_path(entry["descriptor"]):
        raise CatalogQueryError("selectable entry descriptor must be a safe repository-relative path")
    if not isinstance(entry["native_assets"], list) or not entry["native_assets"]:
        raise CatalogQueryError("selectable entry native_assets must be a non-empty array")
    if not all(_safe_repo_path(value) for value in entry["native_assets"]):
        raise CatalogQueryError("selectable entry native_assets must use safe repository-relative paths")
    for field in ("supported_consumers", "capabilities"):
        if not isinstance(entry[field], list) or not all(isinstance(value, str) and value for value in entry[field]):
            raise CatalogQueryError(f"selectable entry {field} must be an array of non-empty strings")
    release_record = entry["release_record"]
    if (
        not isinstance(release_record, dict)
        or set(release_record) != {"path", "sha256"}
        or not _safe_repo_path(release_record.get("path"))
        or not re.fullmatch(r"[a-f0-9]{64}", str(release_record.get("sha256", "")))
    ):
        raise CatalogQueryError("selectable entry release_record must be a bound repository-relative path")


def _contains_all(entry: dict[str, Any], field: str, requested: Iterable[str]) -> bool:
    actual = entry.get(field, [])
    return isinstance(actual, list) and set(requested).issubset(actual)


def _schema_validate(root: Path, value: Any, definition: str, label: str) -> None:
    schema = _load_object(root / SUBMISSION_PROFILE_SCHEMA, "submission profile schema")
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict) or definition not in definitions:
        raise CatalogQueryError(f"submission profile schema does not define {definition}")
    validation_schema = {
        "$schema": schema.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "$ref": f"#/$defs/{definition}",
        "$defs": definitions,
    }
    errors = sorted(
        Draft202012Validator(validation_schema).iter_errors(value),
        key=lambda error: [str(part) for part in error.path],
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.path) or "<root>"
        raise CatalogQueryError(f"{label}.{location}: {error.message}")


def _fingerprint_payload(root: Path, profiled_template: dict[str, Any]) -> dict[str, Any]:
    invariants = _pattern_invariants(root, profiled_template)
    return {
        "diversity_dimensions": {
            key: value
            for key, value in sorted(profiled_template.get("diversity_dimensions", {}).items())
        },
        "semantic_pattern_ids": sorted({item["semantic_pattern_id"] for item in invariants}),
    }


def diversity_fingerprint(
    profiled_template: dict[str, Any],
    repository_root: Path = ROOT,
) -> str:
    """Derive the structural diversity identity without template or submission identity."""
    payload = json.dumps(
        _fingerprint_payload(repository_root.resolve(), profiled_template),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_submission_profiles(root: Path) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    profile_root = root / SUBMISSION_PROFILE_ROOT
    if not profile_root.is_dir():
        return profiles
    if _is_path_indirection(profile_root):
        raise CatalogQueryError("submission profile root cannot be a symbolic link or junction")
    profile_root_resolved = profile_root.resolve(strict=True)
    for path in sorted(profile_root.glob("SUB-*/profile.json"), key=lambda item: item.as_posix()):
        if _is_path_indirection(path.parent) or _is_path_indirection(path):
            raise CatalogQueryError("submission profile path cannot contain a symbolic link or junction")
        resolved = path.resolve(strict=True)
        if not _is_within(profile_root_resolved, resolved):
            raise CatalogQueryError("resolved submission profile path escapes the profile root")
        profile = _load_object(resolved, "submission profile")
        profiles.append(profile)
    return profiles


def _profiled_templates(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for profile in _load_submission_profiles(root):
        if profile.get("status") != "reviewed":
            continue
        for template in profile.get("templates", []):
            if not isinstance(template, dict):
                continue
            key = (template.get("template_id"), template.get("version"))
            if not all(isinstance(value, str) for value in key) or key in indexed:
                raise CatalogQueryError("submission profiles must declare unique exact template identities")
            if template.get("diversity_fingerprint") != diversity_fingerprint(template, root):
                raise CatalogQueryError("submission profile diversity_fingerprint is not derived from its stable dimensions and Pattern Invariants")
            indexed[key] = {
                "submission_profile_id": profile.get("submission_profile_id"),
                "submission_id": profile.get("submission_id"),
                "profile_status": profile.get("status"),
                "reuse_policy": profile.get("reuse_policy"),
                "template": template,
            }
    return indexed


def _pattern_invariants(root: Path, template: dict[str, Any]) -> list[dict[str, str]]:
    card_bindings = {
        item["card_id"]: item
        for item in template["foundation_cards"]
        if isinstance(item, dict) and isinstance(item.get("card_id"), str)
    }
    resolved: list[dict[str, str]] = []
    for reference in template["pattern_invariants"]:
        card_id = reference["foundation_card_id"]
        binding = card_bindings.get(card_id)
        if binding is None:
            raise CatalogQueryError("Pattern Invariant references an unbound foundation card")
        card = _load_object(resolve_repo_path(root, binding["path"]), "foundation card")
        index = int(reference["json_pointer"].rsplit("/", 1)[1])
        patterns = card.get("reuse", {}).get("patterns", [])
        if index >= len(patterns) or not isinstance(patterns[index], str):
            raise CatalogQueryError("Pattern Invariant JSON pointer does not resolve to a foundation pattern")
        resolved.append({**reference, "statement": patterns[index]})
    return resolved


def _profile_matches(
    root: Path,
    entry: dict[str, Any],
    profiled: dict[str, Any],
    compatibility: dict[str, Any],
    requested_facets: dict[str, Any],
) -> bool:
    scalar_filters = {
        "artifact_type": compatibility.get("artifact_type"),
        "blueprint_id": compatibility.get("blueprint_id"),
        "authority": compatibility.get("authority"),
        "lifecycle": compatibility.get("lifecycle"),
    }
    if any(value is not None and entry.get(field) != value for field, value in scalar_filters.items()):
        return False
    if not _contains_all(entry, "capabilities", compatibility.get("capabilities", [])):
        return False
    contextual_filters = any(
        compatibility.get(field)
        for field in ("producer_role", "medium", "required_allowed_knowledge", "prohibited_knowledge")
    )
    if contextual_filters:
        descriptor, blueprint = _entry_context(root, entry)
        if not _compatible_context(
            descriptor,
            blueprint,
            producer_role=compatibility.get("producer_role"),
            medium=compatibility.get("medium"),
            required_allowed_knowledge=compatibility.get("required_allowed_knowledge", []),
            prohibited_knowledge=compatibility.get("prohibited_knowledge", []),
        ):
            return False
    candidate_facets = profiled["template"]["facets"]
    return all(value in candidate_facets.get(name, []) for name, value in requested_facets.items())


def _target_key(value: dict[str, Any] | None) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    target = value.get("target")
    if not isinstance(target, dict):
        return None
    return target.get("template_id"), target.get("version")


def recommend(
    requirements: dict[str, Any],
    recent_usage: list[dict[str, str]],
    *,
    repository_root: Path = ROOT,
) -> dict[str, Any]:
    """Rank exact profiled releases using package-local reuse evidence only."""
    root = repository_root.resolve()
    _canonical_validate_repository(root)
    _schema_validate(root, requirements, "recommendation_request", "requirements")
    _schema_validate(root, recent_usage, "recent_usage", "recent_usage")
    consumer_id = requirements["consumer_id"]
    _require_consumer_id(root, consumer_id, "recommend")

    compatibility = requirements["compatibility"]
    requested_facets = requirements["facets"]
    if (
        compatibility.get("authority") is not None
        and requested_facets.get("authority_class") is not None
        and compatibility["authority"] != requested_facets["authority_class"]
    ):
        raise CatalogQueryError("compatibility.authority conflicts with facets.authority_class")
    if (
        compatibility.get("artifact_type") is not None
        and requested_facets.get("medium") is not None
        and compatibility["artifact_type"] != requested_facets["medium"]
    ):
        raise CatalogQueryError("compatibility.artifact_type conflicts with facets.medium")

    catalog = load_catalog(root / "library/catalog.json")
    _validate_catalog_consumer_ids(root, catalog)
    catalog_by_key = {
        (entry["template_id"], entry["version"]): entry
        for entry in catalog["templates"]
        if isinstance(entry, dict) and _selectable(entry)
    }
    profiles = _profiled_templates(root)
    for index, identity in enumerate(recent_usage):
        key = (identity["template_id"], identity["version"])
        if key not in catalog_by_key:
            raise CatalogQueryError(f"recent_usage.{index}: unknown selectable template identity")

    plan = requirements.get("material_transformation_plan")
    plan_key = _target_key(plan)
    if plan_key is not None and plan_key not in profiles:
        raise CatalogQueryError("material_transformation_plan target is not recommendation-profiled")

    compatible: list[tuple[tuple[str, str], dict[str, Any], dict[str, Any]]] = []
    for key, profiled in profiles.items():
        entry = catalog_by_key.get(key)
        if entry is None or not _contains_all(entry, "supported_consumers", (consumer_id,)):
            continue
        if _profile_matches(root, entry, profiled, compatibility, requested_facets):
            compatible.append((key, profiled, entry))

    window = recent_usage[-requirements["recent_window"] :]
    window_keys = [(item["template_id"], item["version"]) for item in window]
    unprofiled_history_count = sum(key not in profiles for key in window_keys)
    last_key = window_keys[-1] if window_keys else None
    material_kinds = set()
    if isinstance(plan, dict):
        policy = profiles[plan_key]["reuse_policy"]
        material_kinds = set(plan["change_kinds"]).intersection(policy["material_change_kinds"])

    recommendations: list[dict[str, Any]] = []
    for key, profiled, entry in compatible:
        template = profiled["template"]
        exact_count = window_keys.count(key)
        fingerprint = template["diversity_fingerprint"]
        fingerprint_count = sum(
            history_key in profiles
            and profiles[history_key]["template"]["diversity_fingerprint"] == fingerprint
            for history_key in window_keys
        )
        lineage_count = sum(
            history_key in profiles
            and profiles[history_key]["submission_profile_id"] == profiled["submission_profile_id"]
            for history_key in window_keys
        )
        if exact_count and (plan_key != key or not material_kinds):
            continue
        consecutive = key == last_key
        if consecutive:
            continue

        reason_codes = ["reviewed_profile", "profile_compatible"]
        if exact_count:
            reason_codes.append("exact_reuse_material_transformation_planned")
        else:
            reason_codes.append("exact_not_recent")
        if lineage_count:
            reason_codes.append("submission_lineage_repeated")
        if fingerprint_count:
            reason_codes.append("diversity_fingerprint_repeated")
        recommendation = {
                "template_id": key[0],
                "version": key[1],
                "release_status": entry["release_status"],
                "submission_profile_id": profiled["submission_profile_id"],
                "submission_id": profiled["submission_id"],
                "profile_status": profiled["profile_status"],
                "foundation_card_ids": [item["card_id"] for item in template["foundation_cards"]],
                "blueprint_id": template["blueprint"]["blueprint_id"],
                "release_id": template["release"]["release_id"],
                "diversity_fingerprint": fingerprint,
                "diversity_dimensions": template["diversity_dimensions"],
                "exact_reuse_count": exact_count,
                "lineage_reuse_count": lineage_count,
                "fingerprint_reuse_count": fingerprint_count,
                "reason_codes": reason_codes,
                "confidence_limits": template["confidence_limits"],
                "pattern_invariants": _pattern_invariants(root, template),
                "transformation_obligations": template["transformation_obligations"],
                "next_operation": "select",
            }
        if exact_count:
            recommendation["material_transformation"] = {
                "status": "planned_not_validated",
                "plan": {
                    "target": dict(plan["target"]),
                    "change_kinds": sorted(material_kinds),
                },
                "must_bind_to": "package_local_template_binding",
                "completion_owner": "consumer_candidate_artifact_validation",
            }
        recommendations.append(recommendation)

    recommendations.sort(
        key=lambda item: (
            item["release_status"] == "deprecated",
            item["exact_reuse_count"] > 0,
            item["lineage_reuse_count"],
            item["fingerprint_reuse_count"],
            item["submission_profile_id"],
            item["template_id"],
            item["version"],
        )
    )
    for rank, item in enumerate(recommendations, start=1):
        item["rank"] = rank
    if recommendations:
        return {
            "status": "ok",
            "operation": "recommend",
            "request_schema_version": requirements["schema_version"],
            "recent_window": requirements["recent_window"],
            "history_count_considered": len(window),
            "unprofiled_history_count_considered": unprofiled_history_count,
            "count": len(recommendations),
            "recommendations": recommendations,
        }
    return {
        "status": "no_match",
        "operation": "recommend",
        "request_schema_version": requirements["schema_version"],
        "recent_window": requirements["recent_window"],
        "history_count_considered": len(window),
        "unprofiled_history_count_considered": unprofiled_history_count,
        "reason_codes": [
            "reuse_policy_blocked_all_candidates" if compatible else "no_compatible_profiled_template"
        ],
        "count": 0,
        "recommendations": [],
        "next_operation": None,
    }


def discover(
    catalog: dict[str, Any],
    *,
    consumer_id: str,
    artifact_type: str | None = None,
    blueprint_id: str | None = None,
    authority: str | None = None,
    lifecycle: str | None = None,
    producer_role: str | None = None,
    medium: str | None = None,
    capabilities: Iterable[str] = (),
    required_allowed_knowledge: Iterable[str] = (),
    prohibited_knowledge: Iterable[str] = (),
    repository_root: Path,
) -> list[dict[str, Any]]:
    """Return released or deprecated entries matching every supplied constraint."""
    repository_root = repository_root.resolve()
    _canonical_validate_repository(repository_root)
    canonical_catalog = load_catalog(repository_root / "library" / "catalog.json")
    if catalog != canonical_catalog:
        raise CatalogQueryError("catalog must exactly match the canonical repository catalog")
    _require_consumer_id(repository_root, consumer_id, "discover")
    _validate_catalog_consumer_ids(repository_root, catalog)
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
        if not isinstance(entry, dict) or not _selectable(entry):
            continue
        if any(value is not None and entry.get(field) != value for field, value in scalar_filters.items()):
            continue
        if not _contains_all(entry, "supported_consumers", (consumer_id,)):
            continue
        if not _contains_all(entry, "capabilities", capabilities):
            continue
        contextual_filters = producer_role is not None or medium is not None or bool(required_allowed_knowledge) or bool(prohibited_knowledge)
        if contextual_filters:
            descriptor, blueprint = _entry_context(repository_root, entry)
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
    consumer_id: str,
    authority: str | None = None,
    producer_role: str | None = None,
    medium: str | None = None,
    capabilities: Iterable[str] = (),
    required_allowed_knowledge: Iterable[str] = (),
    prohibited_knowledge: Iterable[str] = (),
    repository_root: Path,
) -> dict[str, Any] | None:
    """Select one released or deprecated version; never resolve a floating label."""
    normalized = version.strip().lower()
    if not normalized or normalized in FLOATING_VERSIONS:
        raise CatalogQueryError("version must be an exact value; floating versions are forbidden")
    _require_consumer_id(repository_root.resolve(), consumer_id, "select")

    matches = [
        entry
        for entry in discover(
            catalog,
            consumer_id=consumer_id,
            authority=authority,
            producer_role=producer_role,
            medium=medium,
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


def validate_release(root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Return one release after the canonical repository validator accepts it."""
    root = root.resolve()
    _canonical_validate_repository(root)
    canonical_catalog = load_catalog(root / "library" / "catalog.json")
    if entry not in canonical_catalog["templates"]:
        raise CatalogQueryError("release entry is not the exact canonical catalog record")
    release_path = _bound_file(root, entry["release_record"], "catalog release_record", under=Path("library/releases"))
    release = _load_object(release_path, "release record")
    descriptor_path = _bound_file(root, release["descriptor"], "release descriptor", under=Path("library/templates"))
    descriptor_hash = _sha256(descriptor_path)
    asset_hashes: dict[str, str] = {}
    for binding in release["native_assets"]:
        path = _bound_file(root, binding, "release native asset", under=Path("library/templates"))
        asset_hashes[binding["path"]] = _sha256(path)

    return {
        "status": "pass",
        "operation": "validate",
        "template_id": entry["template_id"],
        "version": entry["version"],
        "release_id": release["release_id"],
        "release_record": release_path.relative_to(root).as_posix(),
        "validated_native_assets": asset_hashes,
        "descriptor_sha256": descriptor_hash,
    }


def instantiate(
    *,
    root: Path,
    catalog: dict[str, Any],
    template_id: str,
    version: str,
    consumer_id: str,
    package_root: Path,
    output_location: Path,
    manifest_approved: bool,
    write_authorized: bool,
    source_authorized: bool,
    authority: str | None = None,
    producer_role: str | None = None,
    medium: str | None = None,
    capabilities: Iterable[str] = (),
    required_allowed_knowledge: Iterable[str] = (),
    prohibited_knowledge: Iterable[str] = (),
    world_fact_keys: Iterable[str] = (),
    provenance_reference: str,
    materialize: bool = False,
) -> dict[str, Any]:
    """Return or materialize a package-local binding after all authority gates pass."""
    _require_consumer_id(root.resolve(), consumer_id, "instantiate")
    if not (manifest_approved and write_authorized and source_authorized):
        raise CatalogQueryError("instantiate requires manifest, write, and source authorization")
    if not isinstance(provenance_reference, str) or not provenance_reference.strip():
        raise CatalogQueryError("instantiate requires a nonempty provenance_reference")
    capabilities = tuple(capabilities)
    required_allowed_knowledge = tuple(required_allowed_knowledge)
    prohibited_knowledge = tuple(prohibited_knowledge)
    world_fact_keys = tuple(world_fact_keys)
    entry = select_exact(
        catalog,
        template_id=template_id,
        version=version,
        consumer_id=consumer_id,
        authority=authority,
        producer_role=producer_role,
        medium=medium,
        capabilities=capabilities,
        required_allowed_knowledge=required_allowed_knowledge,
        prohibited_knowledge=prohibited_knowledge,
        repository_root=root,
    )
    if entry is None:
        raise CatalogQueryError("exact selectable template version not found")
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
        if not output.parent.is_dir():
            raise CatalogQueryError("instantiate output parent must already exist")
        try:
            with locked_directory(output.parent) as directory_descriptor:
                if _path_exists_in_directory(output.parent, output.name, directory_descriptor):
                    raise CatalogQueryError("instantiate requires a new output location and will not overwrite")
                staging = _create_staging_directory(
                    output.parent,
                    output.name,
                    directory_descriptor,
                )
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
                    _atomic_rename_no_replace(staging, output, directory_descriptor)
                    committed = True
                    copies = [
                        {"path": str(output / name), "sha256": target_hash}
                        for name, target_hash in staged
                    ]
                finally:
                    if not committed and staging.exists():
                        shutil.rmtree(staging)
        except OSError as exc:
            raise CatalogQueryError(f"materialization failed before commit: {exc}") from exc
    return {
        "status": "ready",
        "operation": "instantiate",
        "mode": "materialized_copy" if materialize else "plan",
        "binding": {
            "template_id": template_id,
            "version": version,
            "consumer_id": consumer_id,
            "blueprint_id": entry["blueprint_id"],
            "release_id": validation["release_id"],
            "provenance_reference": provenance_reference,
            "selection_constraints": {
                "authority": authority,
                "producer_role": producer_role,
                "medium": medium,
                "capabilities": sorted(set(capabilities)),
                "required_allowed_knowledge": sorted(set(required_allowed_knowledge)),
                "prohibited_knowledge": sorted(set(prohibited_knowledge)),
            },
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

    discover_parser = subparsers.add_parser("discover", help="list compatible released or deprecated templates")
    discover_parser.add_argument("--consumer-id", required=True)
    discover_parser.add_argument("--artifact-type")
    discover_parser.add_argument("--blueprint-id")
    discover_parser.add_argument("--authority")
    discover_parser.add_argument("--lifecycle")
    discover_parser.add_argument("--producer-role")
    discover_parser.add_argument("--medium")
    discover_parser.add_argument("--capability", action="append", default=[])
    discover_parser.add_argument("--required-knowledge", action="append", default=[])
    discover_parser.add_argument("--prohibited-knowledge", action="append", default=[])

    select_parser = subparsers.add_parser("select", help="select one exact released or deprecated version")
    select_parser.add_argument("--consumer-id", required=True)
    select_parser.add_argument("--template-id", required=True)
    select_parser.add_argument("--version", required=True)
    select_parser.add_argument("--authority")
    select_parser.add_argument("--producer-role")
    select_parser.add_argument("--medium")
    select_parser.add_argument("--capability", action="append", default=[])
    select_parser.add_argument("--required-knowledge", action="append", default=[])
    select_parser.add_argument("--prohibited-knowledge", action="append", default=[])

    instantiate_parser = subparsers.add_parser("instantiate", help="plan or copy an authorized package-local binding")
    instantiate_parser.add_argument("--consumer-id", required=True)
    instantiate_parser.add_argument("--template-id", required=True)
    instantiate_parser.add_argument("--version", required=True)
    instantiate_parser.add_argument("--package-root", type=Path, required=True)
    instantiate_parser.add_argument("--output-location", type=Path, required=True)
    instantiate_parser.add_argument("--provenance-reference", required=True)
    instantiate_parser.add_argument("--authority")
    instantiate_parser.add_argument("--producer-role")
    instantiate_parser.add_argument("--medium")
    instantiate_parser.add_argument("--capability", action="append", default=[])
    instantiate_parser.add_argument("--required-knowledge", action="append", default=[])
    instantiate_parser.add_argument("--prohibited-knowledge", action="append", default=[])
    instantiate_parser.add_argument("--world-fact-key", action="append", default=[])
    instantiate_parser.add_argument("--manifest-approved", action="store_true")
    instantiate_parser.add_argument("--write-authorized", action="store_true")
    instantiate_parser.add_argument("--source-authorized", action="store_true")
    instantiate_parser.add_argument("--materialize", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="validate an exact release and its bound files")
    validate_parser.add_argument("--consumer-id", required=True)
    validate_parser.add_argument("--template-id", required=True)
    validate_parser.add_argument("--version", required=True)

    recommend_parser = subparsers.add_parser("recommend", help="rank profiled exact versions using package-local history")
    recommend_parser.add_argument("--requirements", type=Path, required=True)
    recommend_parser.add_argument("--recent-usage", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.root.resolve()
        canonical_catalog = (root / "library" / "catalog.json").resolve()
        catalog_path = args.catalog.resolve() if args.catalog else canonical_catalog
        if catalog_path != canonical_catalog:
            raise CatalogQueryError("catalog override must identify the canonical repository catalog")
        catalog = load_catalog(catalog_path)
        if args.operation == "recommend":
            requirements = _load_json_value(args.requirements, "recommendation requirements")
            recent_usage = _load_json_value(args.recent_usage, "recent usage")
            result = recommend(requirements, recent_usage, repository_root=root)
        elif args.operation == "discover":
            matches = discover(
                catalog,
                consumer_id=args.consumer_id,
                artifact_type=args.artifact_type,
                blueprint_id=args.blueprint_id,
                authority=args.authority,
                lifecycle=args.lifecycle,
                producer_role=args.producer_role,
                medium=args.medium,
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
                consumer_id=args.consumer_id,
                authority=args.authority,
                producer_role=args.producer_role,
                medium=args.medium,
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
                consumer_id=args.consumer_id,
                package_root=args.package_root,
                output_location=args.output_location,
                manifest_approved=args.manifest_approved,
                write_authorized=args.write_authorized,
                source_authorized=args.source_authorized,
                world_fact_keys=args.world_fact_key,
                provenance_reference=args.provenance_reference,
                authority=args.authority,
                producer_role=args.producer_role,
                medium=args.medium,
                capabilities=args.capability,
                required_allowed_knowledge=args.required_knowledge,
                prohibited_knowledge=args.prohibited_knowledge,
                materialize=args.materialize,
            )
        else:
            _require_consumer_id(root, args.consumer_id, "validate")
            entry = select_exact(
                catalog,
                template_id=args.template_id,
                version=args.version,
                consumer_id=args.consumer_id,
                repository_root=root,
            )
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
