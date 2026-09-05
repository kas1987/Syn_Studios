"""Build, recalculate, render, and hash-bind a CSV-driven close workbook."""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
BUILDERS = Path(__file__).resolve().parent


def required_environment_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is unset; activate docs/DOCUMENT_STACK.md")
    path = Path(value).resolve()
    if not path.exists():
        raise SystemExit(f"{name} does not exist: {path}")
    return path


def run(command: list[str], timeout: int = 120) -> None:
    completed = subprocess.run(command, check=False, timeout=timeout)
    if completed.returncode:
        raise SystemExit(f"command failed ({completed.returncode}): {command[0]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-csv", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args()

    source_csv = args.source_csv.resolve()
    output = args.output.resolve()
    evidence_dir = args.evidence_dir.resolve()
    if not source_csv.is_file():
        raise SystemExit(f"source CSV not found: {source_csv}")
    if output.exists():
        raise SystemExit(f"output already exists: {output}")
    if evidence_dir.exists() and any(evidence_dir.iterdir()):
        raise SystemExit(f"evidence directory must be empty: {evidence_dir}")

    node = required_environment_path("SYN_STUDIOS_NODE")
    soffice = required_environment_path("SYN_STUDIOS_SOFFICE")
    required_environment_path("SYN_STUDIOS_POPPLER_BIN")
    if not os.environ.get("SYN_STUDIOS_NODE_MODULES"):
        raise SystemExit("SYN_STUDIOS_NODE_MODULES is unset; activate docs/DOCUMENT_STACK.md")

    with source_csv.open("r", encoding="utf-8", newline="") as handle:
        row_count = sum(1 for _ in csv.reader(handle)) - 1
    if row_count < 1:
        raise SystemExit("source CSV must contain at least one data row")

    output.parent.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    render_dir = evidence_dir / "render"
    with tempfile.TemporaryDirectory(prefix="syn-studios-population-") as temporary:
        temp = Path(temporary)
        raw = temp / output.name
        recalculated_dir = temp / "recalculated"
        recalculated_dir.mkdir()
        run([str(node), str(BUILDERS / "build_close_template.mjs"), str(raw), "--source-csv", str(source_csv)])
        run([sys.executable, str(BUILDERS / "finalize_xlsx_print.py"), str(raw), "--prepare-recalculation"])
        run([
            str(soffice),
            "--headless",
            f"-env:UserInstallation={(temp / 'lo-profile').as_uri()}",
            "--convert-to",
            "xlsx",
            "--outdir",
            str(recalculated_dir),
            str(raw),
        ])
        recalculated = recalculated_dir / output.name
        if not recalculated.is_file():
            raise SystemExit(f"LibreOffice did not produce: {recalculated}")
        run([sys.executable, str(BUILDERS / "finalize_xlsx_print.py"), str(recalculated)])
        shutil.copy2(recalculated, output)

    run([sys.executable, str(ROOT / "scripts/render_validate.py"), "--input", str(output), "--output-dir", str(render_dir)])
    run([
        sys.executable,
        str(BUILDERS / "verify_population_rebuild.py"),
        "--input",
        str(output),
        "--expected-source-rows",
        str(row_count),
        "--render-dir",
        str(render_dir),
        "--output",
        str(evidence_dir / "population-evidence.json"),
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
