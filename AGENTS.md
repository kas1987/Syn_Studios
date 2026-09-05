# Repository Guidelines

## Purpose

Syn Studios is the source-first design system and pattern library for authorized synthetic business artifacts. It preserves reusable structure, realism methods, validators, and sanitized templates without turning prior submissions into an uncontrolled copy library.

## Source of truth

- `SYNTHETIC_DESIGN.md` owns the durable artifact-design contract.
- `skill/SKILL.md` owns agent routing and operating behavior.
- `skill/references/` owns detailed archetype and package-composition guidance.
- `schemas/` owns machine-readable card contracts.
- `library/foundations/` owns reviewed foundation cards; it does not contain raw submission artifacts.
- `scripts/` owns deterministic inspection and validation.
- `docs/DOCUMENT_STACK.md` owns the local document-toolchain contract.
- `tests/` proves repository invariants.
- `docs/EXPERIMENTAL_CRAFT_LANES.md` records optional operator UI and schematic lanes. It does not change the artifact contract or submission freeze rules.

Do not duplicate a rule across these surfaces. Link to the owning source.

## Integrity boundaries

- Work only with authorized synthetic or fictional materials.
- Never copy a prior artifact into `library/templates/` until it is sanitized, de-factualized, provenance-reviewed, and independently validated.
- Preserve submission files in place. The library records locators, hashes, structural observations, and reusable patterns—not private answers or world facts.
- Complexity must arise from a plausible producer, purpose, source system, lifecycle, and handling history. Do not add arbitrary filler, hidden answers, fabricated authority, broken formulas, or false governing evidence.
- Treat executed/external records as restrained; concentrate working residue in internal workbooks, drafts, notes, and correspondence where the lifecycle supports it.
- Do not modify an active submission while studying it for library patterns.

## Development workflow

1. Read `SYNTHETIC_DESIGN.md` and the relevant skill reference.
2. Inventory the candidate artifact without changing it.
3. Write or update a foundation card.
4. Sanitize into a template only in a separately reviewed change.
5. Run `python -m unittest discover -s tests -v`.

For document generation or rendered QA, activate and validate the optional local stack described in `docs/DOCUMENT_STACK.md` before touching artifact bytes.

Prefer standard-library tooling. Keep scripts deterministic and make optional format parsers fail transparently.
