#!/usr/bin/env python3
"""Generate a hash-bound workbook proof from a real LibreOffice recalculation."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from xml.etree import ElementTree as ET

try:
    from .workbook_recalculation import (
        ALLOWED_CONTROL_STATES,
        EXPECTED_CONTROL_CELLS,
        MAIN,
        file_sha256,
        workbook_formula_evidence,
    )
except ImportError:  # Direct execution
    from workbook_recalculation import (
        ALLOWED_CONTROL_STATES,
        EXPECTED_CONTROL_CELLS,
        MAIN,
        file_sha256,
        workbook_formula_evidence,
    )


class ProofGenerationFailed(RuntimeError):
    pass


def load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProofGenerationFailed(f"{path}: expected a JSON object")
    return value


def repository_path(
    root: Path,
    relative: object,
    label: str,
    *,
    must_exist: bool = True,
) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ProofGenerationFailed(f"{label}: invalid repository-relative path")
    posix_path = PurePosixPath(relative)
    windows_path = PureWindowsPath(relative)
    if posix_path.is_absolute() or windows_path.drive or windows_path.root or ".." in posix_path.parts:
        raise ProofGenerationFailed(f"{label}: invalid repository-relative path")
    root = root.resolve()
    candidate = (root / Path(*posix_path.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ProofGenerationFailed(f"{label}: path escapes repository root") from error
    if must_exist and not candidate.is_file():
        raise ProofGenerationFailed(f"{label}: referenced file does not exist: {relative}")
    return candidate


def find_soffice(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        Path(os.environ["SYN_STUDIOS_SOFFICE"]) if os.environ.get("SYN_STUDIOS_SOFFICE") else None,
        Path(found) if (found := shutil.which("soffice.com")) else None,
        Path(found) if (found := shutil.which("soffice")) else None,
        Path(r"C:\Program Files\LibreOffice\program\soffice.com"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    raise ProofGenerationFailed("LibreOffice soffice executable was not found")


def strip_formula_caches(source: Path, target: Path) -> int:
    with zipfile.ZipFile(source) as package:
        members = [(item, package.read(item.filename)) for item in package.infolist()]
    removed = 0
    rewritten: list[tuple[zipfile.ZipInfo, bytes]] = []
    for item, payload in members:
        if item.filename.startswith("xl/worksheets/") and item.filename.endswith(".xml"):
            tree = ET.fromstring(payload)
            for cell in tree.findall(f".//{{{MAIN}}}c"):
                if cell.find(f"{{{MAIN}}}f") is None:
                    continue
                value = cell.find(f"{{{MAIN}}}v")
                if value is not None:
                    cell.remove(value)
                    removed += 1
            payload = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
        rewritten.append((item, payload))
    with zipfile.ZipFile(target, "w") as package:
        for item, payload in rewritten:
            package.writestr(item, payload)
    return removed


def generate(root: Path, release_id: str, soffice: Path) -> tuple[Path, bytes]:
    root = root.resolve()
    release_path = repository_path(
        root,
        f"library/releases/{release_id}.template.json",
        "release record",
    )
    release = load_object(release_path)
    descriptor_reference = release.get("descriptor") or {}
    descriptor_path = repository_path(
        root,
        descriptor_reference.get("path"),
        "release descriptor",
    )
    descriptor = load_object(descriptor_path)
    descriptor_sha256 = file_sha256(descriptor_path)
    if descriptor_reference.get("sha256") != descriptor_sha256:
        raise ProofGenerationFailed("release descriptor hash is stale")
    assets = descriptor.get("native_assets")
    if descriptor.get("artifact_type") != "xlsx" or not isinstance(assets, list) or len(assets) != 1:
        raise ProofGenerationFailed("recalculation proof requires exactly one XLSX native asset")
    binding = assets[0]
    workbook_path = repository_path(
        root,
        binding.get("path"),
        "descriptor workbook",
    )
    source_hash = file_sha256(workbook_path)
    if binding.get("sha256") != source_hash:
        raise ProofGenerationFailed("descriptor workbook hash is stale")
    source_evidence = workbook_formula_evidence(workbook_path)

    version_process = subprocess.run(
        [str(soffice), "--headless", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    version = (version_process.stdout or version_process.stderr).strip().splitlines()
    if version_process.returncode != 0 or not version or not version[0].startswith("LibreOffice "):
        raise ProofGenerationFailed("LibreOffice version probe failed")

    with tempfile.TemporaryDirectory(prefix="syn-studios-recalc-") as temporary:
        workspace = Path(temporary)
        input_dir = workspace / "input"
        output_dir = workspace / "output"
        profile_dir = workspace / "profile"
        input_dir.mkdir()
        output_dir.mkdir()
        profile_dir.mkdir()
        exact_copy = workspace / "exact-source-copy.xlsx"
        shutil.copy2(workbook_path, exact_copy)
        recalculation_input = input_dir / workbook_path.name
        stripped_count = strip_formula_caches(exact_copy, recalculation_input)
        before_hash = file_sha256(workbook_path)
        if file_sha256(exact_copy) != source_hash:
            raise ProofGenerationFailed("temporary exact source copy does not match the frozen workbook")
        stripped_evidence = workbook_formula_evidence(recalculation_input)
        if (
            stripped_count != source_evidence["formula_count"]
            or stripped_evidence["formula_count"] != source_evidence["formula_count"]
            or stripped_evidence["cached_formula_count"] != 0
            or stripped_evidence["formula_structure_sha256"] != source_evidence["formula_structure_sha256"]
        ):
            raise ProofGenerationFailed("formula-cache stripping did not preserve the exact formula structure")
        process = subprocess.run(
            [
                str(soffice),
                "--headless",
                f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                "--convert-to",
                'xlsx:Calc MS Excel 2007 XML',
                "--outdir",
                str(output_dir),
                str(recalculation_input),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        recalculated = output_dir / workbook_path.name
        if process.returncode != 0 or not recalculated.is_file():
            detail = (process.stderr or process.stdout).strip()
            raise ProofGenerationFailed(f"LibreOffice recalculation failed: {detail}")
        if file_sha256(workbook_path) != before_hash or before_hash != source_hash:
            raise ProofGenerationFailed("source workbook changed during recalculation")
        recalculated_evidence = workbook_formula_evidence(recalculated)

    if (
        recalculated_evidence["formula_count"] < 1
        or recalculated_evidence["formula_structure_sha256"] != source_evidence["formula_structure_sha256"]
        or recalculated_evidence["cached_formula_count"] != recalculated_evidence["formula_count"]
        or recalculated_evidence["error_count"] != 0
        or set(recalculated_evidence["control_cells"]) != EXPECTED_CONTROL_CELLS
        or not set(recalculated_evidence["control_cells"].values()) <= ALLOWED_CONTROL_STATES
    ):
        raise ProofGenerationFailed("recalculated workbook does not satisfy formula/cache/control gates")
    if recalculated_evidence["formula_results_sha256"] != source_evidence["formula_results_sha256"]:
        raise ProofGenerationFailed(
            "recalculated formula results differ from the frozen source; rebuild or replace the frozen workbook with engine-recalculated bytes before release"
        )

    proof = {
        "schema_version": "1.0.0",
        "proof_type": "workbook_recalculation_result",
        "proof_id": f"RECALC-{release_id}",
        "release_id": release_id,
        "template_id": str(release.get("template_id")),
        "version": str(release.get("version")),
        "category": "computational",
        "descriptor_sha256": descriptor_sha256,
        "source_workbook": {"path": str(binding["path"]), "sha256": source_hash},
        "engine": {"name": "LibreOffice Calc", "version": version[0]},
        "execution": {
            "mode": "headless_cache_stripped_copy",
            "output_format": "xlsx",
            "cache_reset": "remove_all_formula_cached_values",
            "cleared_formula_cache_count": stripped_count,
            "prepared_cached_formula_count": stripped_evidence["cached_formula_count"],
            "source_before_sha256": source_hash,
            "source_after_sha256": file_sha256(workbook_path),
            "source_unchanged": True,
        },
        "formula_evidence": recalculated_evidence,
        "verdict": "RECALCULATION_PASS",
    }
    output = repository_path(
        root,
        f"evidence/template-releases/{release_id}/machine-proofs/workbook-recalculation.json",
        "machine proof output",
        must_exist=False,
    )
    payload = (json.dumps(proof, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return output, payload


def publish_proof_once(output: Path, payload: bytes) -> str:
    """Create a proof atomically, or accept an existing byte-identical proof."""

    def existing_disposition() -> str | None:
        try:
            existing = output.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ProofGenerationFailed(f"cannot inspect existing machine proof: {error}") from error
        if existing != payload:
            raise ProofGenerationFailed(
                "existing machine proof is immutable and differs from the generated bytes"
            )
        return "unchanged"

    disposition = existing_disposition()
    if disposition is not None:
        return disposition

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=output.name + ".",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, output)
        except FileExistsError:
            disposition = existing_disposition()
            if disposition is None:
                raise ProofGenerationFailed(
                    "machine proof target changed during atomic publication"
                )
            return disposition
    finally:
        temporary.unlink(missing_ok=True)
    return "written"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--release-id", default="REL-0001")
    parser.add_argument("--soffice", type=Path)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        output, payload = generate(arguments.root, arguments.release_id, find_soffice(arguments.soffice))
        disposition = publish_proof_once(output, payload) if arguments.write else "dry run"
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile, ET.ParseError, ProofGenerationFailed) as error:
        print(f"REFUSED: {error}")
        return 1
    print(f"PASS: {output.relative_to(arguments.root.resolve()).as_posix()} ({disposition})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
