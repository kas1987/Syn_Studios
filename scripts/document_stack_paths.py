"""Portable executable resolution for the optional document stack."""

from __future__ import annotations

import os
from pathlib import Path


def poppler_executable(directory: Path, stem: str) -> Path:
    """Return an existing platform form, or the native expected path."""

    names = (f"{stem}.exe", stem) if os.name == "nt" else (stem, f"{stem}.exe")
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return directory / names[0]
