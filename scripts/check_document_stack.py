#!/usr/bin/env python3
"""Read-only, profile-aware capability check for the Syn Studios stack."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tomllib
from typing import Any

from document_stack_paths import poppler_executable


IMPORT_NAMES = {
    "python-docx": "docx",
    "XlsxWriter": "xlsxwriter",
    "Pillow": "PIL",
    "python-pptx": "pptx",
    "pywin32": "win32com.client",
}


def package_status(distribution: str, accepted: str) -> dict[str, Any]:
    module = IMPORT_NAMES.get(distribution, distribution)
    try:
        importlib.import_module(module)
        actual = importlib.metadata.version(distribution)
    except Exception as exc:
        return {"status": "MISSING", "accepted": accepted, "error": str(exc)}
    status = "PASS" if actual == accepted else "INCOMPATIBLE"
    return {"status": status, "accepted": accepted, "actual": actual}


def executable_status(path: str, *args: str, accepted: str) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {"status": "CANNOT_CHECK", "path": path or None, "accepted": accepted}
    try:
        completed = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "INCOMPATIBLE", "path": path, "accepted": accepted, "error": str(exc)}
    output = (completed.stdout or completed.stderr).strip().splitlines()
    version = output[0] if output else ""
    return {
        "status": "PASS" if completed.returncode == 0 and version.startswith(accepted) else "INCOMPATIBLE",
        "path": path,
        "returncode": completed.returncode,
        "version": version,
        "accepted": accepted,
    }


def artifact_tool_status(node_path: str, accepted: str) -> dict[str, Any]:
    if not node_path or not Path(node_path).is_file():
        return {"status": "CANNOT_CHECK", "path": node_path or None, "accepted": accepted}
    probe = Path(__file__).with_name("check_artifact_tool.mjs")
    try:
        completed = subprocess.run(
            [node_path, str(probe)], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "INCOMPATIBLE", "path": node_path, "accepted": accepted, "error": str(exc)}
    raw_output = (completed.stdout or completed.stderr).strip()
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        return {
            "status": "INCOMPATIBLE",
            "path": node_path,
            "accepted": accepted,
            "error": str(exc),
            "output": raw_output,
        }
    payload["accepted"] = accepted
    payload["path"] = node_path
    payload["status"] = "PASS" if completed.returncode == 0 and payload.get("spreadsheetFile") and payload.get("version") == accepted else "INCOMPATIBLE"
    return payload


def office_status(path: str, executable_name: str, accepted: str) -> dict[str, Any]:
    candidate = Path(path) if path else None
    if not candidate or not candidate.is_file():
        return {"status": "CANNOT_CHECK", "path": path or None, "accepted": accepted}
    if candidate.name.casefold() != executable_name.casefold():
        return {"status": "INCOMPATIBLE", "path": path, "accepted": accepted, "error": "unexpected executable name"}
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        return {"status": "CANNOT_CHECK", "path": path, "accepted": accepted, "error": "PowerShell unavailable for version check"}
    process_environment = dict(os.environ)
    process_environment["SYN_STUDIOS_OFFICE_VERSION_PATH"] = str(candidate)
    completed = subprocess.run(
        [powershell, "-NoProfile", "-Command", "(Get-Item -LiteralPath $env:SYN_STUDIOS_OFFICE_VERSION_PATH).VersionInfo.ProductVersion"],
        capture_output=True, text=True, timeout=30, check=False, env=process_environment,
    )
    actual = completed.stdout.strip()
    return {
        "status": "PASS" if completed.returncode == 0 and actual == accepted else "INCOMPATIBLE",
        "path": path,
        "accepted": accepted,
        "version": actual,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("core", "analysis", "render", "generation", "dev", "office", "all"), default="core")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((root / "toolchain.toml").read_text(encoding="utf-8"))
    profile = manifest["profiles"][args.profile]
    package_versions = manifest["python"]["packages"]
    accepted = manifest["executables"]["accepted"]
    packages = {name: package_status(name, package_versions[name]) for name in profile["python_packages"]}

    env = os.environ
    poppler = Path(env.get("SYN_STUDIOS_POPPLER_BIN", ""))
    def check_executable(name: str) -> dict[str, Any]:
        if name == "node":
            return executable_status(env.get("SYN_STUDIOS_NODE", ""), "--version", accepted=accepted["node"])
        if name == "pnpm":
            return executable_status(env.get("SYN_STUDIOS_PNPM", ""), "--version", accepted=accepted["pnpm"])
        if name == "git":
            return executable_status(env.get("SYN_STUDIOS_GIT", ""), "--version", accepted=accepted["git"])
        if name == "pdfinfo":
            return executable_status(str(poppler_executable(poppler, "pdfinfo")), "-v", accepted=accepted["pdfinfo"])
        if name == "pdftoppm":
            return executable_status(str(poppler_executable(poppler, "pdftoppm")), "-v", accepted=accepted["pdftoppm"])
        if name == "libreoffice":
            return executable_status(env.get("SYN_STUDIOS_SOFFICE", ""), "--headless", "--version", accepted=accepted["libreoffice"])
        if name == "artifact_tool":
            return artifact_tool_status(env.get("SYN_STUDIOS_NODE", ""), accepted["artifact_tool"])
        office_names = {"excel": "EXCEL.EXE", "word": "WINWORD.EXE", "powerpoint": "POWERPNT.EXE"}
        if name in office_names:
            path = env.get(f"SYN_STUDIOS_{name.upper()}", "")
            return office_status(path, office_names[name], accepted["microsoft_office"])
        raise KeyError(f"Unknown executable capability: {name}")

    executables = {name: check_executable(name) for name in profile["executables"]}

    minimum = tuple(int(part) for part in manifest["python"]["minimum"].split("."))
    python_ok = sys.version_info[:2] >= minimum
    required = list(packages.values()) + list(executables.values())
    statuses = {item["status"] for item in required}
    if not python_ok or "INCOMPATIBLE" in statuses:
        overall_status = "INCOMPATIBLE"
    elif statuses <= {"PASS"}:
        overall_status = "PASS"
    else:
        overall_status = "CANNOT_CHECK"
    result = {
        "profile": args.profile,
        "status": overall_status,
        "python": {
            "status": "PASS" if python_ok else "INCOMPATIBLE",
            "path": sys.executable,
            "version": sys.version.split()[0],
            "minimum": manifest["python"]["minimum"],
        },
        "packages": packages,
        "executables": executables,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["status"])
        for group in ("packages", "executables"):
            for name, item in result[group].items():
                print(f"  {group}:{name}: {item['status']}")
    return {"PASS": 0, "INCOMPATIBLE": 1, "CANNOT_CHECK": 2}[overall_status]


if __name__ == "__main__":
    raise SystemExit(main())
