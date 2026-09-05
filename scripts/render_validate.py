#!/usr/bin/env python3
"""Convert and render an authorized synthetic office file without mutating it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from document_stack_paths import poppler_executable


OFFICE_SUFFIXES = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"{label} not found: {path}")
    return path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode != 0:
        raise SystemExit(
            f"Command failed ({completed.returncode}): {command[0]}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    source = require_file(args.input, "input")
    output_dir = Path(args.output_dir).expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"Output directory must be empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    before = sha256(source)

    poppler = Path(os.environ.get("SYN_STUDIOS_POPPLER_BIN", ""))
    pdfinfo = require_file(str(poppler_executable(poppler, "pdfinfo")), "pdfinfo")
    pdftoppm = require_file(str(poppler_executable(poppler, "pdftoppm")), "pdftoppm")

    try:
        with tempfile.TemporaryDirectory(prefix="syn-studios-render-") as temp_name:
            temp_dir = Path(temp_name)
            if source.suffix.lower() == ".pdf":
                pdf = source
            elif source.suffix.lower() in OFFICE_SUFFIXES:
                soffice = require_file(os.environ.get("SYN_STUDIOS_SOFFICE", ""), "LibreOffice")
                profile = (temp_dir / "lo-profile").as_uri()
                run([
                    str(soffice), f"-env:UserInstallation={profile}", "--headless",
                    "--convert-to", "pdf", "--outdir", str(temp_dir), str(source),
                ])
                converted = temp_dir / f"{source.stem}.pdf"
                if not converted.is_file():
                    raise SystemExit(f"LibreOffice did not create expected PDF: {converted}")
                pdf = output_dir / converted.name
                shutil.copy2(converted, pdf)
            else:
                raise SystemExit(f"Unsupported input suffix: {source.suffix}")

            info = run([str(pdfinfo), str(pdf)])
            prefix = output_dir / f"{source.stem}-page"
            run([str(pdftoppm), "-png", str(pdf), str(prefix)])
    finally:
        after = sha256(source)
        if before != after:
            raise SystemExit("Source hash changed during render validation")
    pages = sorted(output_dir.glob(f"{source.stem}-page-*.png"))
    if not pages:
        raise SystemExit("Poppler produced no page images")
    page_match = re.search(r"^Pages:\s+(\d+)\s*$", info.stdout, re.MULTILINE)
    if not page_match:
        raise SystemExit("pdfinfo did not report a page count")
    expected_pages = int(page_match.group(1))
    if len(pages) != expected_pages:
        raise SystemExit(f"Rendered page count mismatch: expected {expected_pages}, found {len(pages)}")
    result = {
        "input": str(source),
        "source_sha256": before,
        "source_unchanged": True,
        "pdf": str(pdf),
        "rendered_pages": [str(page) for page in pages],
        "page_count": expected_pages,
        "pdfinfo": info.stdout.strip(),
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
