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
- `library/catalog.json` and `library/releases/` — exact-version discovery and hash-bound release records.
- `library/templates/` — sanitized, fact-free native templates; released entries remain immutable.
- `scripts/inventory_artifacts.py` — read-only structural inventory for DOCX, XLSX, PDF, EML, CSV, and ZIP packages.
- `scripts/validate_library.py` — canonical fail-closed validator for cards, blueprints, templates, evidence, releases, and catalog bindings.
- `scripts/run_template_technical_validation.py` — deterministic category-specific release checks; the assembler cannot manufacture a passing result.
- `scripts/audit_tabular_package.py` — downstream XLSX/CSV conformance audit for populated package artifacts; it reports evidence and never grants acceptance.
- `scripts/inspect_office_surfaces.py` — read-only, hash-bound XLSX/DOCX inventory for hidden content, links, metadata, formulas, styles, widths, and layout convergence.

## Current library state

The local catalog contains three independently reviewed `1.0.0` releases:

- `REL-0001` / `TMPL-0001` — an internal close and reconciliation XLSX with typed native tables, formula checks, CSV import, and rebuildable population capacity;
- `REL-0002` / `TMPL-0002` — a five-page internal controller decision memorandum; and
- `REL-0003` / `TMPL-0003` — a six-message operational correction thread with CSV and text attachments.

The workbook's scale and variation rules are owned by [skill/references/spreadsheet-data-diversity.md](skill/references/spreadsheet-data-diversity.md). The committed workbook is a compact reference capacity: larger XLSX or CSV populations must rebuild table, formula, check, print, and evidence boundaries and pass fresh validation. The regression suite includes a 360-row irregular source population, independent mapping and ledger carriers, mismatch/duplicate/unmapped arms, repeated print headers, and a 17-page expanded render. This is a structural diversity benchmark informed by authorized Sub-002 design targets and reviewed Sub-005 observations; it copies no submission facts, styling, or bytes.

`released` is a repository lifecycle state, not evidence that a GitHub tag, package, pull request, branch-protection rule, ANNA adapter, or local skill installation has been published or applied. Those remain separate external gates.

## Phases

1. **Foundation inventory — complete:** inspect prior work and classify reusable structures without copying source bytes.
2. **Archetype system — complete at the contract layer:** all nine archetypes have one production blueprint plus a positive and anti-pattern fixture; released native breadth remains intentionally smaller.
3. **Sanitized templates — first release complete:** three fact-free native templates have hash-bound provenance and integrity evidence.
4. **Behavior gates — baseline complete:** release-specific Terra and Sol reviews, executable technical results, consumer trajectories, and downstream tabular conformance arms are recorded; package-specific acceptance remains consumer-owned.
5. **Submission adoption — not started here:** versioned patterns may be applied prospectively only under each submission's own controls.

## Validation

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python scripts/inventory_artifacts.py --help
python scripts/validate_library.py
python scripts/run_template_technical_validation.py --actor-id <stable-id> --actor-name "<display name>"
python scripts/audit_tabular_package.py --package-root <package> --policy <package-policy.json>
```

GitHub runs the substantive contract on Linux and Windows. See [CONTRIBUTING.md](CONTRIBUTING.md) for the review flow and [SECURITY.md](SECURITY.md) for private vulnerability reporting.

## Codex skill installation

Install the repository root, not the nested `skill/` directory. The root
`SKILL.md` is a thin entry point to the canonical workflow, while the complete
package retains the catalog, released assets, validators, and integration
contract needed by consume mode. Pin installation to an immutable published
commit:

```powershell
python <skill-installer> --repo kas1987/Syn_Studios --ref <commit-sha> --path . --name synthetic-studio --method download
```

The installer must refuse an existing destination. Installation exposes the
library workflow; it does not grant generation, materialization, downstream
acceptance, or merge authority.

## Local document stack

Binary generation and rendered QA use an isolated local environment plus externally managed applications; no Office, LibreOffice, Poppler, or private Node package is vendored here.

```powershell
.\scripts\bootstrap_document_stack.ps1 -InstallOfficeComFallback
. .\scripts\activate_document_stack.ps1
& $env:SYN_STUDIOS_PYTHON .\scripts\check_document_stack.py --profile all --json
```

The activation script prefers the Codex managed runtime and discovers installed Microsoft Office and LibreOffice fallbacks. See [docs/DOCUMENT_STACK.md](docs/DOCUMENT_STACK.md) for capability boundaries and render validation.
