# Syn Studios

[![Validate](https://github.com/kas1987/Syn_Studios/actions/workflows/validate.yml/badge.svg)](https://github.com/kas1987/Syn_Studios/actions/workflows/validate.yml)
[![Security](https://github.com/kas1987/Syn_Studios/actions/workflows/security.yml/badge.svg)](https://github.com/kas1987/Syn_Studios/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Syn Studios is a reusable design system for synthetic business-document packages. It applies the same source-first idea that a frontend design system applies to interface work: one durable design contract, focused archetype references, reviewed patterns, deterministic inspection tools, fixtures, and release gates.

The repository is not a warehouse of old submissions. Prior artifacts enter first as foundation cards describing what is reusable, what failed, and what must be sanitized before any template is created.

## Core surfaces

- `SYNTHETIC_DESIGN.md` — the equivalent of a frontend `DESIGN.md`: artifact grammar, complexity model, lifecycle states, integrity rules, and package composition.
- `skill/SKILL.md` — the agent-facing workflow for designing, auditing, or extracting artifact patterns.
- `library/foundations/` — reviewed cards pointing to strong prior foundations and known anti-patterns.
- `library/templates/` — future sanitized, fact-free templates only.
- `scripts/inventory_artifacts.py` — read-only structural inventory for DOCX, XLSX, PDF, EML, CSV, and ZIP packages.
- `schemas/foundation-card.schema.json` — minimum evidence contract for foundation cards.

## Phases

1. **Foundation inventory:** inspect prior work and classify reusable structures without copying source bytes.
2. **Archetype system:** define internal/external file profiles and substantive-footprint ranges.
3. **Sanitized templates:** create fact-free templates with provenance and integrity tests.
4. **Behavior gates:** test agent use of the design contract across representative requests and models.
5. **Submission adoption:** apply versioned patterns to new work, beginning with Sub-005 and Sub-007.

## Validation

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python scripts/inventory_artifacts.py --help
python scripts/validate_library.py
```

GitHub runs the substantive contract on Linux and Windows. See [CONTRIBUTING.md](CONTRIBUTING.md) for the review flow and [SECURITY.md](SECURITY.md) for private vulnerability reporting.
