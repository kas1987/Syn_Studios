#!/usr/bin/env python3
"""Validate library records, lineage, fixtures, and hash-bound releases."""

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
LAYER_ORDER = {name: index for index, name in enumerate(("core", "operational_depth", "adjacent_context", "working_residue", "handling_history"))}
PROOF_CATEGORIES = {"core_integrity", "render", "metadata", "computational", "provenance", "leakage", "authority_separation", "anti_filler"}


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
    layers = [item.get("layer") for item in as_list(data.get("complexity_layers")) if isinstance(item, dict)]
    if layers:
        if layers[0] != "core":
            findings.append(f"{label}:complexity_layers: first layer must be core")
        if len(layers) != len(set(layers)):
            findings.append(f"{label}:complexity_layers: layer names must be unique")
        known = [LAYER_ORDER[layer] for layer in layers if layer in LAYER_ORDER]
        if known != sorted(known):
            findings.append(f"{label}:complexity_layers: layers must follow core-to-handling order")
    gates = [item for item in as_list(data.get("proof_gates")) if isinstance(item, dict)]
    categories = [item.get("category") for item in gates]
    if set(categories) != PROOF_CATEGORIES or len(categories) != len(PROOF_CATEGORIES):
        findings.append(f"{label}:proof_gates: must contain each required category exactly once")
    by_category = {item.get("category"): item for item in gates}
    for category in PROOF_CATEGORIES - {"computational"}:
        if by_category.get(category, {}).get("applicable") is not True:
            findings.append(f"{label}:proof_gates.{category}: category must be applicable")
    if data.get("artifact_type") in {"xlsx", "mixed_package"} and by_category.get("computational", {}).get("applicable") is not True:
        findings.append(f"{label}:proof_gates.computational: category must be applicable for computational artifact types")
    return findings


def resolve_bound_path(root: Path, relative: object, label: str, findings: list[str]) -> Path | None:
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
    if not resolved.is_file():
        findings.append(f"{label}: referenced file does not exist: {relative}")
        return None
    return resolved


def check_bound_file(root: Path, record: dict[str, Any], path_key: str, hash_key: str, label: str, findings: list[str]) -> Path | None:
    path = resolve_bound_path(root, record.get(path_key), f"{label}.{path_key}", findings)
    if path is not None and record.get(hash_key) != sha256_file(path):
        findings.append(f"{label}.{hash_key}: hash does not match {record.get(path_key)}")
    return path


def apply_fixture(base: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for mutation in fixture.get("mutations", []):
        parts = [part.replace("~1", "/").replace("~0", "~") for part in mutation["path"].strip("/").split("/") if part]
        parent: Any = result
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = parts[-1]
        if mutation["op"] == "remove":
            parent.pop(int(key)) if isinstance(parent, list) else parent.pop(key)
        elif mutation["op"] == "replace":
            if isinstance(parent, list):
                parent[int(key)] = mutation["value"]
            else:
                parent[key] = mutation["value"]
        else:
            raise ValueError(f"unsupported fixture mutation: {mutation['op']}")
    return result


def validate_repository(root: Path = ROOT) -> tuple[list[str], int]:
    root = root.resolve()
    findings: list[str] = []
    schemas: dict[str, dict[str, Any]] = {}
    for name in ("foundation-card", "artifact-blueprint", "template-release"):
        data = load_json(root / f"schemas/{name}.schema.json", root, findings)
        if data is not None:
            schemas[name] = data
    if len(schemas) != 3:
        return findings, 0

    cards: dict[str, tuple[Path, dict[str, Any]]] = {}
    blueprints: dict[str, tuple[Path, dict[str, Any]]] = {}
    releases: dict[str, tuple[Path, dict[str, Any]]] = {}
    count = 0

    for path in sorted((root / "library/foundations").glob("FOUND-*.json")):
        data = load_json(path, root, findings)
        if data is None:
            continue
        count += 1
        label = display_path(path, root)
        findings.extend(schema_findings(data, schemas["foundation-card"], label))
        card_id = data.get("card_id")
        if path.stem != card_id:
            findings.append(f"{label}:card_id: must match filename")
        if card_id in cards:
            findings.append(f"{label}:card_id: duplicate {card_id}")
        elif isinstance(card_id, str):
            cards[card_id] = (path, data)

    for path in sorted((root / "examples/blueprints").glob("BP-*.json")):
        data = load_json(path, root, findings)
        if data is None:
            continue
        count += 1
        label = display_path(path, root)
        findings.extend(validate_blueprint_data(data, schemas["artifact-blueprint"], label))
        blueprint_id = data.get("blueprint_id")
        if not path.name.startswith(f"{blueprint_id}."):
            findings.append(f"{label}:blueprint_id: must match filename prefix")
        if blueprint_id in blueprints:
            findings.append(f"{label}:blueprint_id: duplicate {blueprint_id}")
        elif isinstance(blueprint_id, str):
            blueprints[blueprint_id] = (path, data)

    for _, (path, data) in blueprints.items():
        label = display_path(path, root)
        seen_cards: set[str] = set()
        for index, lineage in enumerate(as_list(data.get("foundation_lineage"))):
            if not isinstance(lineage, dict):
                continue
            prefix = f"{label}:foundation_lineage.{index}"
            card_id = lineage.get("card_id")
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
            allowed_patterns = set(as_list(as_dict(card.get("reuse")).get("patterns")))
            for pattern in as_list(lineage.get("patterns_used")):
                if pattern not in allowed_patterns:
                    findings.append(f"{prefix}.patterns_used: pattern is not named by the foundation card")

    for path in sorted((root / "library/releases").glob("REL-*.json")):
        data = load_json(path, root, findings)
        if data is None:
            continue
        count += 1
        label = display_path(path, root)
        findings.extend(schema_findings(data, schemas["template-release"], label))
        release_id = data.get("release_id")
        if not path.name.startswith(f"{release_id}."):
            findings.append(f"{label}:release_id: must match filename prefix")
        if release_id in releases:
            findings.append(f"{label}:release_id: duplicate {release_id}")
        elif isinstance(release_id, str):
            releases[release_id] = (path, data)

    for _, (path, data) in releases.items():
        label = display_path(path, root)
        template = as_dict(data.get("template"))
        template_path = check_bound_file(root, template, "path", "sha256", f"{label}:template", findings)
        template_hash = template.get("sha256")
        templates_root = (root / "library/templates").resolve()
        if template_path is not None and templates_root not in template_path.parents:
            findings.append(f"{label}:template.path: template must be under library/templates")
        blueprint_ref = as_dict(data.get("blueprint"))
        blueprint_path = check_bound_file(root, blueprint_ref, "path", "sha256", f"{label}:blueprint", findings)
        blueprint_entry = blueprints.get(blueprint_ref.get("blueprint_id"))
        if blueprint_entry is None:
            findings.append(f"{label}:blueprint.blueprint_id: unknown blueprint")
        elif blueprint_path is not None and blueprint_path != blueprint_entry[0].resolve():
            findings.append(f"{label}:blueprint.path: does not identify the indexed blueprint")
        if blueprint_entry is not None:
            blueprint = blueprint_entry[1]
            if template.get("artifact_type") != blueprint.get("artifact_type"):
                findings.append(f"{label}:template.artifact_type: must match blueprint artifact type")
            lineage_ids = {item.get("card_id") for item in as_list(blueprint.get("foundation_lineage")) if isinstance(item, dict)}
            supplied_ids = {
                item for item in as_list(as_dict(data.get("sanitization")).get("foundation_card_ids"))
                if isinstance(item, str)
            }
            if supplied_ids != lineage_ids:
                findings.append(f"{label}:sanitization.foundation_card_ids: must exactly match blueprint lineage")
        reviews = as_dict(data.get("reviews"))
        evidence = as_dict(data.get("evidence"))
        terra, sol = as_dict(reviews.get("terra")), as_dict(reviews.get("sol"))
        conductor = as_dict(data.get("conductor_approval"))
        sanitization = as_dict(data.get("sanitization"))
        bound_objects = [sanitization, terra, sol, conductor]
        bound_objects.extend(evidence.values())
        for bound in bound_objects:
            if isinstance(bound, dict) and bound.get("template_sha256") != template_hash:
                findings.append(f"{label}: same-hash binding failed for release evidence")
        reviewers = [terra.get("reviewer"), sol.get("reviewer"), conductor.get("approver")]
        if len(reviewers) != len(set(reviewers)):
            findings.append(f"{label}:reviews: Terra, Sol, and conductor identities must be independent")
        for name, record in (("sanitization", sanitization), ("reviews.terra", terra), ("reviews.sol", sol), ("conductor_approval", conductor)):
            if isinstance(record, dict):
                check_bound_file(root, record, "record_path", "record_sha256", f"{label}:{name}", findings)
        for category, record in evidence.items():
            if isinstance(record, dict) and record.get("status") == "pass":
                check_bound_file(root, record, "record_path", "record_sha256", f"{label}:evidence.{category}", findings)
        if blueprint_entry is not None:
            blueprint_gates = {
                gate.get("category"): gate
                for gate in as_list(blueprint_entry[1].get("proof_gates"))
                if isinstance(gate, dict)
            }
            for category in PROOF_CATEGORIES:
                gate = as_dict(blueprint_gates.get(category))
                proof = as_dict(evidence.get(category))
                if proof.get("blueprint_procedure") != gate.get("procedure"):
                    findings.append(f"{label}:evidence.{category}.blueprint_procedure: must exactly match blueprint proof gate")
                expected_status = "pass" if gate.get("applicable") is True else "not_applicable"
                if proof.get("status") != expected_status:
                    findings.append(f"{label}:evidence.{category}.status: must align with blueprint applicability")

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
        base_path = resolve_bound_path(root, fixture.get("base_blueprint"), f"{label}:base_blueprint", findings)
        if base_path is None:
            continue
        base = load_json(base_path, root, findings)
        try:
            candidate = apply_fixture(base or {}, fixture)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            findings.append(f"{label}:mutations: cannot apply fixture: {error}")
            continue
        candidate_findings = validate_blueprint_data(candidate, schemas["artifact-blueprint"], label)
        if fixture["expected"] == "pass" and candidate_findings:
            findings.append(f"{label}:expected: positive fixture failed: {'; '.join(candidate_findings)}")
        if fixture["expected"] == "fail" and not candidate_findings:
            findings.append(f"{label}:expected: anti-pattern fixture unexpectedly passed")

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
