---
name: synthetic-studio
description: Design, audit, extract, or consume reusable patterns and released templates for authorized synthetic business documents and mixed-file packages when substantive depth, internal/external realism, or release-safe reuse matters. Do not use to alter real records or to add arbitrary clutter to a frozen submission.
---

# Synthetic Studio

Build business-complete artifacts whose complexity comes from their producer and workflow, not from evaluator-facing padding.

## Setup

1. Read the repository `SYNTHETIC_DESIGN.md`.
2. Confirm the source is authorized synthetic or fictional material.
3. Select one mode: `design`, `audit`, `extract`, or `consume`.
4. Read only the reference that owns the current mode.

## Modes

### Design

Use for a new artifact or package. Read [references/artifact-archetypes.md](references/artifact-archetypes.md), [references/spreadsheet-data-diversity.md](references/spreadsheet-data-diversity.md) for XLSX, Excel, CSV, or other row-carrier work, and [references/package-composition.md](references/package-composition.md) for mixed packages. Define producer, purpose, medium, lifecycle, authority, substantive footprint, and handling history before generation.

### Audit

Use for an existing artifact or package. Inventory native structure, render every material page or sheet, separate integrity blockers from realism defects, and compare the result with `SYNTHETIC_DESIGN.md`.

### Extract

Use when prior work may become a reusable pattern. Create a foundation card under `library/foundations/`; do not copy source bytes. Identify the reusable structure, prohibited facts, known defects, sanitization plan, and proof gate. Template creation is a later reviewed action.

### Consume

Use when ANNA, Holodeck, world exploration, or a realism workflow needs a reusable resource. Read [the consumer integration contract](../docs/INTEGRATIONS.md) and its [machine-readable profile](../integrations/consumer-profile.v1.json), then perform only the requested operation:

- `discover`: query the catalog using package requirements through `python integrations/query_catalog.py discover`;
- `select`: use `python integrations/query_catalog.py select` with an exact version to return a compatible released template or an explicit no-match result;
- `instantiate`: after the consumer's manifest, authorization, release-evidence, output-containment, and toolchain gates pass, return a package-local binding plan; materialize an unchanged copy only when explicitly requested;
- `validate`: use `python integrations/query_catalog.py validate` to check catalog, descriptor, native bytes, blueprint, and release-evidence consistency without claiming downstream acceptance.

Foundation cards and blueprints are design inputs, not releasable template bytes. World facts, manifests, prompts, provenance cards, and template bindings remain in the consumer package.

## Hard boundaries

- Preserve locked facts, formulas, provenance, readability, and authority.
- Do not fabricate signatures, approvals, real identities, timestamps, or handling history.
- Do not use hidden answers, false governing evidence, corruption, illegibility, or broken formulas as complexity.
- Do not change a frozen submission merely to adopt a newer library pattern.
- Treat executed and external records as restrained; concentrate working residue where real internal workflows support it.
- Do not treat discovery, selection, ANNA routing, or validation as write authorization or acceptance.

## Completion

Return the exact design, card, selection, binding, or evidence created; checks run; integrity and realism results; confidence limits; and any downstream template work that remains unapproved.
