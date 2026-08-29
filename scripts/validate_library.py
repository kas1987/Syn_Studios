#!/usr/bin/env python3
"""Validate library records, lineage, fixtures, releases, and the consumer catalog."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

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
    category: str | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    path = check_bound_file(root, reference, "record_path", "record_sha256", label, findings, EVIDENCE_ROOT)
    if path is None:
        return None, {}
    record = load_json(path, root, findings)
    if record is None:
        return path, {}
    record_label = display_path(path, root)
    findings.extend(schema_findings(record, schema, record_label))
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
    count = 0

    for path in sorted((root / "library/foundations").glob("FOUND-*.json")):
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

    for path in sorted((root / "examples/blueprints").glob("BP-*.json")):
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

    for path in sorted((root / "library/releases").glob("REL-*.json")):
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
        descriptor_ref = as_dict(data.get("descriptor"))
        descriptor_path = check_bound_file(root, descriptor_ref, "path", "sha256", f"{label}:descriptor", findings, Path("library/templates"))
        descriptor_hash = descriptor_ref.get("sha256")
        descriptor = load_json(descriptor_path, root, findings) if descriptor_path is not None else None
        if descriptor is not None:
            findings.extend(schema_findings(descriptor, schemas["template-descriptor"], display_path(descriptor_path, root)))
            release_descriptors[path.resolve()] = descriptor

        asset_pairs = bound_pairs(data.get("native_assets"))
        if len(asset_pairs) != len(set(asset_pairs)) or len({path_value for path_value, _ in asset_pairs}) != len(asset_pairs):
            findings.append(f"{label}:native_assets: duplicate asset path or binding")
        for index, asset in enumerate(as_list(data.get("native_assets"))):
            if isinstance(asset, dict):
                check_bound_file(root, asset, "path", "sha256", f"{label}:native_assets.{index}", findings, Path("library/templates"))
        asset_hashes = {hash_value for _, hash_value in asset_pairs}

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
                "blueprint_id": blueprint_id,
                "blueprint_sha256": blueprint_ref.get("sha256"),
            }
            for field, expected in comparisons.items():
                if descriptor.get(field) != expected:
                    findings.append(f"{label}:descriptor.{field}: does not match release")
            if set(bound_pairs(descriptor.get("native_assets"))) != set(asset_pairs):
                findings.append(f"{label}:descriptor.native_assets: must exactly match release native assets")

        if blueprint_entry is not None:
            lineage_ids = {item.get("card_id") for item in as_list(blueprint_entry[1].get("foundation_lineage")) if isinstance(item, dict) and isinstance(item.get("card_id"), str)}
            supplied_ids = {item for item in as_list(as_dict(data.get("sanitization")).get("foundation_card_ids")) if isinstance(item, str)}
            if supplied_ids != lineage_ids:
                findings.append(f"{label}:sanitization.foundation_card_ids: must exactly match blueprint lineage")

        typed: dict[str, tuple[Path | None, dict[str, Any]]] = {}
        typed["sanitization"] = load_typed_evidence(root, as_dict(as_dict(data.get("sanitization")).get("evidence")), schemas["release-evidence"], data, descriptor_hash, asset_hashes, "sanitization", f"{label}:sanitization.evidence", findings)
        reviews = as_dict(data.get("reviews"))
        typed["terra"] = load_typed_evidence(root, as_dict(reviews.get("terra")), schemas["release-evidence"], data, descriptor_hash, asset_hashes, "terra_review", f"{label}:reviews.terra", findings)
        typed["sol"] = load_typed_evidence(root, as_dict(reviews.get("sol")), schemas["release-evidence"], data, descriptor_hash, asset_hashes, "sol_review", f"{label}:reviews.sol", findings)
        typed["conductor"] = load_typed_evidence(root, as_dict(data.get("conductor_approval")), schemas["release-evidence"], data, descriptor_hash, asset_hashes, "conductor_approval", f"{label}:conductor_approval", findings)
        review_paths = [typed[name][0] for name in ("terra", "sol", "conductor") if typed[name][0] is not None]
        if len(review_paths) != len(set(review_paths)):
            findings.append(f"{label}:reviews: Terra, Sol, and conductor record paths must be distinct")
        actors = [typed[name][1].get("actor") for name in ("terra", "sol", "conductor") if isinstance(typed[name][1].get("actor"), str)]
        if len(actors) != len(set(actors)):
            findings.append(f"{label}:reviews: Terra, Sol, and conductor identities must be independent")

        blueprint_gates = {gate.get("category"): gate for gate in as_list(blueprint_entry[1].get("proof_gates")) if blueprint_entry and isinstance(gate, dict) and isinstance(gate.get("category"), str)} if blueprint_entry else {}
        for category in PROOF_CATEGORIES:
            reference = as_dict(as_dict(data.get("evidence")).get(category))
            _, record = load_typed_evidence(root, reference, schemas["release-evidence"], data, descriptor_hash, asset_hashes, "technical_validation", f"{label}:evidence.{category}", findings, category)
            expected_verdict = "VALIDATION_PASS" if as_dict(blueprint_gates.get(category)).get("applicable") is True else "VALIDATION_NOT_APPLICABLE"
            if record and record.get("verdict") != expected_verdict:
                findings.append(f"{label}:evidence.{category}.verdict: must align with blueprint applicability")
            expected_procedure = as_dict(blueprint_gates.get(category)).get("procedure")
            if record and as_dict(record.get("procedures")).get(category) != expected_procedure:
                findings.append(f"{label}:evidence.{category}.procedures: must exactly match the blueprint proof gate")

    fixture_pairs: dict[str, list[tuple[str, str]]] = {}
    for path in sorted((root / "examples/blueprints/fixtures").glob("*.json")):
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
                if isinstance(template_id, str) and isinstance(version, str):
                    key = (template_id, version)
                    if key in seen_keys:
                        findings.append(f"{prefix}: duplicate template_id/version")
                    seen_keys.add(key)
                if entry.get("release_status") != "released":
                    continue
                release_ref = as_dict(entry.get("release_record"))
                release_path = check_bound_file(root, release_ref, "path", "sha256", f"{prefix}.release_record", findings, Path("library/releases"))
                release = release_by_path.get(release_path) if release_path is not None else None
                if release is None:
                    findings.append(f"{prefix}.release_record: does not identify an indexed release")
                    continue
                comparisons = {
                    "template_id": release.get("template_id"),
                    "version": release.get("version"),
                    "blueprint_id": as_dict(release.get("blueprint")).get("blueprint_id"),
                }
                descriptor_data = release_descriptors.get(release_path.resolve(), {})
                comparisons["artifact_type"] = as_dict(descriptor_data).get("artifact_type")
                bound_blueprint = blueprints.get(comparisons["blueprint_id"]) if isinstance(comparisons["blueprint_id"], str) else None
                if bound_blueprint is not None:
                    comparisons["authority"] = as_dict(bound_blueprint[1].get("authority")).get("primary_class")
                    comparisons["lifecycle"] = bound_blueprint[1].get("lifecycle")
                for field, expected in comparisons.items():
                    if entry.get(field) != expected:
                        findings.append(f"{prefix}.{field}: does not match release")
                if release.get("status") != "released":
                    findings.append(f"{prefix}.release_status: bound release is not released")
                if as_dict(entry.get("descriptor")) != as_dict(release.get("descriptor")):
                    findings.append(f"{prefix}.descriptor: does not match release")
                if set(bound_pairs(entry.get("native_assets"))) != set(bound_pairs(release.get("native_assets"))):
                    findings.append(f"{prefix}.native_assets: do not match release")
                if descriptor_data is not None:
                    for field in ("supported_consumers", "capabilities"):
                        entry_values = {value for value in as_list(entry.get(field)) if isinstance(value, str)}
                        descriptor_values = {value for value in as_list(descriptor_data.get(field)) if isinstance(value, str)}
                        if entry_values != descriptor_values:
                            findings.append(f"{prefix}.{field}: does not match descriptor")

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
