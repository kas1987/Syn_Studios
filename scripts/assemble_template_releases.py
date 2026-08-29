#!/usr/bin/env python3
"""Assemble hash-bound template releases after independent Terra and Sol review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


PROOF_CATEGORIES = (
    "core_integrity",
    "render",
    "metadata",
    "computational",
    "provenance",
    "leakage",
    "authority_separation",
    "anti_filler",
)
REVIEW_CONTRACTS = {
    "terra": ("terra_review", "USABILITY_PASS"),
    "sol": ("sol_review", "INTEGRITY_PASS"),
}
MEDIA_SUFFIXES = {
    "text/plain": {".txt"},
    "text/csv": {".csv"},
    "application/json": {".json"},
    "application/x-ndjson": {".jsonl", ".ndjson"},
    "image/png": {".png"},
    "application/pdf": {".pdf"},
}


class AssemblyRefused(RuntimeError):
    """Raised before any release output is written."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AssemblyRefused(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise AssemblyRefused(f"{path} must contain a JSON object")
    return value


def normalized_actor(value: object) -> str:
    if not isinstance(value, str) or len(value.strip()) < 3:
        raise AssemblyRefused("actor IDs and display names must contain at least three characters")
    return unicodedata.normalize("NFKC", value).strip().casefold()


def repository_path(root: Path, value: object, label: str, required_root: Path | None = None) -> Path:
    if not isinstance(value, str) or not value:
        raise AssemblyRefused(f"{label} must be a non-empty repository-relative path")
    candidate = Path(value)
    if candidate.is_absolute():
        raise AssemblyRefused(f"{label} must be repository-relative")
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise AssemblyRefused(f"{label} escapes the repository")
    if required_root is not None:
        allowed = (root / required_root).resolve()
        if resolved != allowed and allowed not in resolved.parents:
            raise AssemblyRefused(f"{label} must be under {required_root.as_posix()}")
    if not resolved.is_file():
        raise AssemblyRefused(f"{label} does not exist: {value}")
    return resolved


def evidence_binding(root: Path, path: Path) -> dict[str, str]:
    return {"record_path": path.relative_to(root).as_posix(), "record_sha256": sha256_file(path)}


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def proof_bytes(release_id: str, template_id: str, category: str, asset_hashes: list[str], statement: str) -> bytes:
    lines = [
        f"release_id={release_id}",
        f"template_id={template_id}",
        f"category={category}",
        *(f"native_asset_sha256={value}" for value in asset_hashes),
        f"observation={statement}",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def validate_review_artifact(root: Path, release_root: Path, release_id: str, template_id: str, asset_hashes: set[str], artifact: object, label: str) -> None:
    if not isinstance(artifact, dict):
        raise AssemblyRefused(f"{label} must be an object")
    media_type, category = artifact.get("media_type"), artifact.get("category")
    if media_type not in MEDIA_SUFFIXES or category not in PROOF_CATEGORIES:
        raise AssemblyRefused(f"{label} uses an unsupported media type or category")
    path = repository_path(root, artifact.get("path"), f"{label}.path", release_root.relative_to(root))
    if path.suffix.casefold() not in MEDIA_SUFFIXES[media_type]:
        raise AssemblyRefused(f"{label} media type does not match its suffix")
    if artifact.get("sha256") != sha256_file(path):
        raise AssemblyRefused(f"{label} hash does not match its proof artifact")
    payload = path.read_bytes()
    if len(payload) < 64:
        raise AssemblyRefused(f"{label} proof artifact is not meaningful")
    if media_type in {"text/plain", "text/csv", "application/json", "application/x-ndjson"}:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AssemblyRefused(f"{label} is not UTF-8: {error}") from error
        for token in (release_id, template_id, category):
            if token not in text:
                raise AssemblyRefused(f"{label} does not bind {token}")
        if not any(value in text for value in asset_hashes):
            raise AssemblyRefused(f"{label} does not bind a native asset hash")


def validate_review(root: Path, release_id: str, template_id: str, version: str, descriptor_hash: str, asset_hashes: list[str], lane: str) -> tuple[dict[str, str], str, str, str]:
    release_root = root / "evidence/template-releases" / release_id
    path = release_root / f"{lane}.json"
    if not path.is_file():
        raise AssemblyRefused(f"missing required {lane} review: {path.relative_to(root).as_posix()}")
    record = load_object(path)
    expected_type, expected_verdict = REVIEW_CONTRACTS[lane]
    expected = {
        "schema_version": "1.0.0",
        "record_type": expected_type,
        "verdict": expected_verdict,
        "release_id": release_id,
        "template_id": template_id,
        "version": version,
        "descriptor_sha256": descriptor_hash,
    }
    for field, value in expected.items():
        if record.get(field) != value:
            raise AssemblyRefused(f"{lane} review {field} does not match the frozen release")
    if not isinstance(record.get("record_id"), str) or re.fullmatch(r"EVID-[A-Z0-9-]{4,64}", record["record_id"]) is None:
        raise AssemblyRefused(f"{lane} review has an invalid record_id")
    review_hashes = record.get("native_asset_sha256s")
    if not isinstance(review_hashes, list) or not all(isinstance(value, str) for value in review_hashes) or len(review_hashes) != len(set(review_hashes)) or set(review_hashes) != set(asset_hashes):
        raise AssemblyRefused(f"{lane} review is not bound to every native asset")
    actor_id, actor = record.get("actor_id"), record.get("actor")
    normalized_actor(actor_id)
    normalized_actor(actor)
    observations, artifacts, summary = record.get("observations"), record.get("artifacts"), record.get("summary")
    if not isinstance(observations, list) or not observations or not all(isinstance(item, str) and len(item) >= 8 for item in observations):
        raise AssemblyRefused(f"{lane} review observations are missing or not meaningful")
    if not isinstance(summary, str) or len(summary) < 12 or not isinstance(artifacts, list) or not artifacts:
        raise AssemblyRefused(f"{lane} review summary or proof artifacts are missing")
    for index, artifact in enumerate(artifacts):
        validate_review_artifact(root, release_root, release_id, template_id, set(asset_hashes), artifact, f"{lane}.artifacts.{index}")
    return evidence_binding(root, path), str(actor_id), str(actor), str(record["record_id"])


def find_blueprint(root: Path, blueprint_id: object) -> tuple[Path, dict[str, Any]]:
    if not isinstance(blueprint_id, str):
        raise AssemblyRefused("descriptor blueprint_id is invalid")
    matches = sorted((root / "examples/blueprints").glob(f"{blueprint_id}.*.json"))
    if len(matches) != 1:
        raise AssemblyRefused(f"{blueprint_id} must resolve to exactly one blueprint")
    blueprint = load_object(matches[0])
    if blueprint.get("blueprint_id") != blueprint_id:
        raise AssemblyRefused(f"{blueprint_id} does not match its blueprint record")
    return matches[0], blueprint


def render_artifacts(root: Path, descriptor: dict[str, Any], release_id: str) -> list[dict[str, str]]:
    contract = descriptor.get("render_contract")
    if not isinstance(contract, dict) or contract.get("required") is not True:
        return []
    page_count, pdf_path, pattern = contract.get("expected_page_count"), contract.get("expected_pdf_path"), contract.get("expected_page_image_pattern")
    if not isinstance(page_count, int) or not isinstance(pdf_path, str) or not isinstance(pattern, str):
        raise AssemblyRefused("required render contract is incomplete")
    try:
        expected = [pdf_path, *(pattern.format(page=page) for page in range(1, page_count + 1))]
    except (IndexError, KeyError, ValueError) as error:
        raise AssemblyRefused(f"render page-image pattern is invalid: {error}") from error
    result = []
    hashes = set()
    for index, relative in enumerate(expected):
        path = repository_path(root, relative, f"render output {index}", Path("evidence/template-releases") / release_id)
        digest = sha256_file(path)
        if digest in hashes:
            raise AssemblyRefused("render outputs must have unique hashes")
        hashes.add(digest)
        result.append({"path": relative, "sha256": digest, "media_type": "application/pdf" if index == 0 else "image/png", "category": "render"})
    return result


def validate_technical_result(
    root: Path,
    release_id: str,
    template_id: str,
    version: str,
    descriptor_hash: str,
    asset_hashes: list[str],
    category: str,
    gate: dict[str, Any],
    technical_id: str,
    technical_name: str,
    expected_rendered_outputs: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, str]]:
    release_root = root / "evidence/template-releases" / release_id
    path = release_root / "technical-results" / f"{category}.json"
    if not path.is_file():
        raise AssemblyRefused(f"missing technical result: {path.relative_to(root).as_posix()}")
    result = load_object(path)
    required_fields = {
        "schema_version", "result_type", "result_id", "release_id", "template_id", "version",
        "category", "result_artifact_category", "descriptor_sha256", "native_asset_sha256s",
        "applicable", "verdict", "procedure", "actor_id", "actor", "checks",
        "rendered_outputs", "observations", "summary",
    }
    if set(result) != required_fields:
        raise AssemblyRefused(f"{release_id} {category} technical result fields are incomplete or unexpected")
    applicable = gate.get("applicable") is True
    expected = {
        "schema_version": "1.0.0",
        "result_type": "template_technical_validation_result",
        "release_id": release_id,
        "template_id": template_id,
        "version": version,
        "category": category,
        "descriptor_sha256": descriptor_hash,
        "applicable": applicable,
        "verdict": "VALIDATION_PASS" if applicable else "VALIDATION_NOT_APPLICABLE",
        "procedure": gate.get("procedure"),
    }
    for field, value in expected.items():
        if result.get(field) != value:
            raise AssemblyRefused(f"{release_id} {category} technical result {field} does not match the frozen release")
    expected_result_id = f"TECHRES-{release_id}-{category.replace('_', '-').upper()}"
    if result.get("result_id") != expected_result_id:
        raise AssemblyRefused(f"{release_id} {category} technical result result_id does not match")
    result_hashes = result.get("native_asset_sha256s")
    if not isinstance(result_hashes, list) or not all(isinstance(value, str) for value in result_hashes) or len(result_hashes) != len(set(result_hashes)) or set(result_hashes) != set(asset_hashes):
        raise AssemblyRefused(f"{release_id} {category} technical result is not bound to every native asset")
    if normalized_actor(result.get("actor_id")) != normalized_actor(technical_id) or normalized_actor(result.get("actor")) != normalized_actor(technical_name):
        raise AssemblyRefused(f"{release_id} {category} technical result actor does not match the supplied technical validator")
    checks = result.get("checks")
    expected_status = "PASS" if applicable else "NOT_APPLICABLE"
    if not isinstance(checks, list) or not checks:
        raise AssemblyRefused(f"{release_id} {category} technical result has no structured checks")
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or set(check) != {"id", "status", "detail"}:
            raise AssemblyRefused(f"{release_id} {category} technical result check {index} is malformed")
        check_id, status, detail = check.get("id"), check.get("status"), check.get("detail")
        if not isinstance(check_id, str) or not check_id.startswith(f"{category}:"):
            raise AssemblyRefused(f"{release_id} {category} technical result check {index} is not category-specific")
        if status != expected_status or not isinstance(detail, str) or len(detail.strip()) < 12:
            raise AssemblyRefused(f"{release_id} {category} technical result check {index} is not substantive or has the wrong status")
    observations, summary = result.get("observations"), result.get("summary")
    if not isinstance(observations, list) or not observations or not all(isinstance(item, str) and len(item.strip()) >= 12 for item in observations):
        raise AssemblyRefused(f"{release_id} {category} technical result observations are missing or not meaningful")
    if not isinstance(summary, str) or len(summary.strip()) < 12:
        raise AssemblyRefused(f"{release_id} {category} technical result summary is missing or not meaningful")
    if result.get("rendered_outputs") != expected_rendered_outputs:
        raise AssemblyRefused(f"{release_id} {category} technical result rendered_outputs do not match the frozen release")
    expected_artifact_category = "provenance" if category == "render" and expected_rendered_outputs else category
    if result.get("result_artifact_category") != expected_artifact_category:
        raise AssemblyRefused(f"{release_id} {category} technical result artifact category does not match")
    artifact = {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "media_type": "application/json",
        "category": expected_artifact_category,
    }
    validate_review_artifact(root, release_root, release_id, template_id, set(asset_hashes), artifact, f"{release_id}.{category}.technical_result")
    return result, artifact


def evidence_record(record_id: str, record_type: str, verdict: str, release_id: str, template_id: str, version: str, descriptor_hash: str, asset_hashes: list[str], actor_id: str, actor: str, observations: list[str], artifacts: list[dict[str, str]], summary: str, *, categories: list[str] | None = None, procedures: dict[str, str] | None = None, render_contract_hash: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "1.0.0", "record_id": record_id, "record_type": record_type,
        "release_id": release_id, "template_id": template_id, "version": version,
        "descriptor_sha256": descriptor_hash, "native_asset_sha256s": asset_hashes,
        "verdict": verdict, "actor_id": actor_id, "actor": actor,
        "observations": observations, "artifacts": artifacts, "summary": summary,
    }
    if categories is not None:
        record["categories"] = categories
    if procedures is not None:
        record["procedures"] = procedures
    if render_contract_hash is not None:
        record["render_contract_sha256"] = render_contract_hash
    return record


def assemble(root: Path, *, approved: bool, builder_id: str, builder_name: str, sanitizer_id: str, sanitizer_name: str, technical_id: str, technical_name: str, conductor_id: str, conductor_name: str) -> list[Path]:
    root = root.resolve()
    if not approved:
        raise AssemblyRefused("explicit conductor approval flag is required")
    supplied_actors = {
        "builder": (builder_id, builder_name), "sanitizer": (sanitizer_id, sanitizer_name),
        "technical": (technical_id, technical_name), "conductor": (conductor_id, conductor_name),
    }
    normalized_ids = {lane: normalized_actor(values[0]) for lane, values in supplied_actors.items()}
    actor_registry = {normalized_ids[lane]: normalized_actor(values[1]) for lane, values in supplied_actors.items()}
    if len(set(normalized_ids.values())) != len(normalized_ids):
        raise AssemblyRefused("builder, sanitizer, technical validator, and conductor actor IDs must be independent")

    catalog_path = root / "library/catalog.json"
    catalog = load_object(catalog_path)
    entries = catalog.get("templates")
    if not isinstance(entries, list) or [item.get("template_id") for item in entries if isinstance(item, dict)] != ["TMPL-0001", "TMPL-0002", "TMPL-0003"]:
        raise AssemblyRefused("catalog must contain exactly ordered TMPL-0001 through TMPL-0003")

    plans: list[dict[str, Any]] = []
    seen_review_ids: set[str] = set()
    review_roles: dict[str, str] = {}
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise AssemblyRefused("catalog entries must be objects")
        release_id = f"REL-{index:04d}"
        template_id, version = entry.get("template_id"), entry.get("version")
        descriptor_path = repository_path(root, entry.get("descriptor"), f"{release_id} descriptor", Path("library/templates"))
        descriptor = load_object(descriptor_path)
        if descriptor.get("template_id") != template_id or descriptor.get("version") != version or descriptor.get("release_status") != "released":
            raise AssemblyRefused(f"{release_id} descriptor identity/status is not frozen for release")
        descriptor_hash = sha256_file(descriptor_path)
        descriptor_comparisons = {
            "template_id": descriptor.get("template_id"), "version": descriptor.get("version"),
            "name": descriptor.get("name"), "artifact_type": descriptor.get("artifact_type"),
            "blueprint_id": descriptor.get("blueprint_id"), "authority": descriptor.get("authority"),
            "lifecycle": descriptor.get("lifecycle"), "supported_consumers": descriptor.get("supported_consumers"),
            "capabilities": descriptor.get("capabilities"),
        }
        for field, expected in descriptor_comparisons.items():
            if entry.get(field) != expected:
                raise AssemblyRefused(f"{release_id} catalog {field} does not match the frozen descriptor")
        descriptor_assets = descriptor.get("native_assets")
        if not isinstance(descriptor_assets, list) or not descriptor_assets:
            raise AssemblyRefused(f"{release_id} descriptor has no native assets")
        assets: list[dict[str, str]] = []
        for asset_index, asset in enumerate(descriptor_assets):
            if not isinstance(asset, dict):
                raise AssemblyRefused(f"{release_id} native asset {asset_index} is invalid")
            path = repository_path(root, asset.get("path"), f"{release_id} native asset {asset_index}", Path("library/templates"))
            actual = sha256_file(path)
            if asset.get("sha256") != actual:
                raise AssemblyRefused(f"{release_id} native asset hash drift")
            assets.append({"path": str(asset["path"]), "sha256": actual})
        if entry.get("native_assets") != [item["path"] for item in assets]:
            raise AssemblyRefused(f"{release_id} catalog native assets do not match descriptor")
        asset_hashes = [item["sha256"] for item in assets]
        blueprint_path, blueprint = find_blueprint(root, descriptor.get("blueprint_id"))
        gates = blueprint.get("proof_gates")
        gate_categories = [item.get("category") for item in gates if isinstance(item, dict)] if isinstance(gates, list) else []
        if not all(isinstance(value, str) for value in gate_categories) or len(gate_categories) != len(PROOF_CATEGORIES) or set(gate_categories) != set(PROOF_CATEGORIES):
            raise AssemblyRefused(f"{release_id} blueprint proof gates are incomplete")
        gate_map = {str(item["category"]): item for item in gates if isinstance(item, dict)}
        lineage = blueprint.get("foundation_lineage")
        foundation_ids = [item.get("card_id") for item in lineage] if isinstance(lineage, list) and all(isinstance(item, dict) for item in lineage) else []
        if not foundation_ids or not all(isinstance(value, str) for value in foundation_ids) or len(foundation_ids) != len(set(foundation_ids)):
            raise AssemblyRefused(f"{release_id} blueprint lineage is incomplete")
        review_refs: dict[str, dict[str, str]] = {}
        review_actor_ids: dict[str, str] = {}
        for lane in REVIEW_CONTRACTS:
            reference, actor_id, actor_name, record_id = validate_review(root, release_id, str(template_id), str(version), descriptor_hash, asset_hashes, lane)
            stable_actor = normalized_actor(actor_id)
            if stable_actor in normalized_ids.values():
                raise AssemblyRefused(f"{release_id} {lane} actor aliases a build, sanitizer, technical, conductor, or peer-review identity")
            stable_name = normalized_actor(actor_name)
            previous_name = actor_registry.get(stable_actor)
            if previous_name is not None and previous_name != stable_name:
                raise AssemblyRefused(f"{release_id} {lane} actor_id changes display identity")
            actor_registry[stable_actor] = stable_name
            previous_role = review_roles.get(stable_actor)
            if previous_role is not None and previous_role != lane:
                raise AssemblyRefused(f"{release_id} reviewer actor_id swaps Terra/Sol lanes")
            review_roles[stable_actor] = lane
            review_actor_ids[lane] = stable_actor
            if record_id in seen_review_ids:
                raise AssemblyRefused(f"duplicate Terra/Sol evidence record ID: {record_id}")
            seen_review_ids.add(record_id)
            review_refs[lane] = reference
        if review_actor_ids["terra"] == review_actor_ids["sol"]:
            raise AssemblyRefused(f"{release_id} Terra and Sol actors must be independent")
        planned_render = render_artifacts(root, descriptor, release_id)
        technical_results = {
            category: validate_technical_result(
                root, release_id, str(template_id), str(version), descriptor_hash, asset_hashes,
                category, gate_map[category], technical_id, technical_name,
                planned_render if category == "render" else [],
            )
            for category in PROOF_CATEGORIES
        }
        plans.append({
            "release_id": release_id, "template_id": str(template_id), "version": str(version), "entry": entry,
            "descriptor_path": descriptor_path, "descriptor": descriptor, "descriptor_hash": descriptor_hash,
            "assets": assets, "asset_hashes": asset_hashes, "blueprint_path": blueprint_path, "blueprint": blueprint,
            "gate_map": gate_map, "foundation_ids": foundation_ids, "reviews": review_refs,
            "render_artifacts": planned_render, "technical_results": technical_results,
        })

    writes: dict[Path, bytes] = {}
    release_records: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for plan in plans:
        release_id, template_id, version = plan["release_id"], plan["template_id"], plan["version"]
        release_root = root / "evidence/template-releases" / release_id

        def add_text_proof(name: str, category: str, statement: str) -> dict[str, str]:
            path = release_root / "proofs" / f"{name}.txt"
            payload = proof_bytes(release_id, template_id, category, plan["asset_hashes"], statement)
            writes[path] = payload
            return {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(payload).hexdigest(), "media_type": "text/plain", "category": category}

        generated: dict[str, tuple[Path, dict[str, Any]]] = {}
        generated["build"] = (release_root / "build.json", evidence_record(
            f"EVID-{release_id}-BUILD", "build_attestation", "BUILD_COMPLETE", release_id, template_id, version,
            plan["descriptor_hash"], plan["asset_hashes"], builder_id, builder_name,
            ["Frozen descriptor and native asset hashes were recomputed before release assembly."],
            [add_text_proof("build", "core_integrity", "Frozen descriptor, native assets, and blueprint bindings were recomputed by the release assembler.")],
            "Deterministic build attestation for the frozen template bytes.",
        ))
        generated["sanitization"] = (release_root / "sanitization.json", evidence_record(
            f"EVID-{release_id}-SANITIZATION", "sanitization", "SANITIZATION_PASS", release_id, template_id, version,
            plan["descriptor_hash"], plan["asset_hashes"], sanitizer_id, sanitizer_name,
            ["Sanitization evidence binds the released descriptor and every native asset hash."],
            [add_text_proof("sanitization", "leakage", "Sanitization approval binds the frozen artifact set and its prohibited-content review.")],
            "Hash-bound sanitization approval for this exact template version.",
        ))
        for category in PROOF_CATEGORIES:
            gate = plan["gate_map"][category]
            technical_result, result_artifact = plan["technical_results"][category]
            artifacts = [result_artifact]
            if category == "render" and plan["render_artifacts"]:
                artifacts.extend(plan["render_artifacts"])
            record = evidence_record(
                f"EVID-{release_id}-TECH-{category.replace('_', '-').upper()}", "technical_validation",
                str(technical_result["verdict"]), release_id, template_id, version,
                plan["descriptor_hash"], plan["asset_hashes"], str(technical_result["actor_id"]), str(technical_result["actor"]),
                list(technical_result["observations"]), artifacts, str(technical_result["summary"]),
                categories=[category], procedures={category: str(gate.get("procedure"))},
                render_contract_hash=canonical_json_sha256(plan["descriptor"].get("render_contract")) if category == "render" else None,
            )
            path = release_root / f"technical-{category}.json"
            generated[f"technical:{category}"] = (path, record)
        generated["conductor"] = (release_root / "conductor.json", evidence_record(
            f"EVID-{release_id}-CONDUCTOR", "conductor_approval", "APPROVED", release_id, template_id, version,
            plan["descriptor_hash"], plan["asset_hashes"], conductor_id, conductor_name,
            ["Conductor approval was explicitly supplied after Terra, Sol, and technical evidence were bound."],
            [add_text_proof("conductor", "provenance", "Explicit conductor approval covers this release ID, template ID, and frozen native asset set.")],
            "Explicit conductor approval for publication of this frozen template version.",
        ))
        references: dict[str, dict[str, str]] = {}
        for key, (path, record) in generated.items():
            payload = json_bytes(record)
            writes[path] = payload
            references[key] = {"record_path": path.relative_to(root).as_posix(), "record_sha256": hashlib.sha256(payload).hexdigest()}
        release = {
            "schema_version": "3.0.0", "release_id": release_id, "template_id": template_id, "version": version, "status": "released",
            "descriptor": {"path": plan["descriptor_path"].relative_to(root).as_posix(), "sha256": plan["descriptor_hash"]},
            "native_assets": plan["assets"],
            "blueprint": {"blueprint_id": plan["blueprint"]["blueprint_id"], "path": plan["blueprint_path"].relative_to(root).as_posix(), "sha256": sha256_file(plan["blueprint_path"])},
            "build": references["build"],
            "sanitization": {"evidence": references["sanitization"], "foundation_card_ids": plan["foundation_ids"]},
            "reviews": plan["reviews"], "conductor_approval": references["conductor"],
            "evidence": {category: references[f"technical:{category}"] for category in PROOF_CATEGORIES},
        }
        release_path = root / "library/releases" / f"{release_id}.template.json"
        release_records.append((release_path, release, plan["entry"]))

    for release_path, release, entry in release_records:
        payload = json_bytes(release)
        writes[release_path] = payload
        entry["release_status"] = "released"
        entry["release_record"] = {"path": release_path.relative_to(root).as_posix(), "sha256": hashlib.sha256(payload).hexdigest()}
    writes[catalog_path] = json_bytes(catalog)

    written = []
    for path in sorted(writes, key=lambda item: item.as_posix()):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(writes[path])
        os.replace(temporary, path)
        written.append(path)
    return written


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    result.add_argument("--approve", action="store_true", help="Explicitly authorize conductor approval records and catalog promotion.")
    for role in ("builder", "sanitizer", "technical", "conductor"):
        result.add_argument(f"--{role}-actor-id", required=True)
        result.add_argument(f"--{role}-actor-name", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        written = assemble(
            arguments.root, approved=arguments.approve,
            builder_id=arguments.builder_actor_id, builder_name=arguments.builder_actor_name,
            sanitizer_id=arguments.sanitizer_actor_id, sanitizer_name=arguments.sanitizer_actor_name,
            technical_id=arguments.technical_actor_id, technical_name=arguments.technical_actor_name,
            conductor_id=arguments.conductor_actor_id, conductor_name=arguments.conductor_actor_name,
        )
    except AssemblyRefused as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1
    print(f"ASSEMBLED: {len(written)} deterministic release files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
