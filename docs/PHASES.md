# Delivery phases

## Current checkpoint — 2026-08-29

- Phase 1 is complete.
- Phase 2 has three implemented vertical slices (close workbook, controller memo, and operational email), but the full archetype list and positive/anti-pattern fixture coverage remain open.
- Phase 3 has three hash-bound `1.0.0` releases: `REL-0001`, `REL-0002`, and `REL-0003`.
- Phase 4 release-specific Terra and Sol lanes passed for those exact descriptor and native-asset hashes; broader behavior validation is still open.
- Phase 5 has not been performed by this repository closeout. Sub-006 remains frozen.

Repository release does not imply public publication or downstream installation. Before any release claim is handed off, the integrated commit must pass the canonical validator and full test suite from a fresh clean checkout so checkout-time transformations are included in the proof.

## Phase 1 — foundation inventory

- Establish the design contract and repository boundaries.
- Inventory representative artifacts from prior submissions without modifying them.
- Create reviewed foundation cards for strong patterns and cautionary examples.
- Gate: repository tests pass and every card has a source hash, reuse boundary, and proof gate.

## Phase 2 — archetype library

- Define fuller internal and external archetypes for workbooks, memos, emails, agreements, policies, exports, notes, and mixed packets.
- Add footprint tests and package-variation checks.
- Gate: each archetype includes at least one positive and one anti-pattern fixture.

## Phase 3 — sanitized templates

- Convert selected reviewed foundations into fact-free templates.
- Remove submission facts, names, answers, hidden metadata, links, comments, and execution residue.
- Gate: structural, computational, provenance, and rendered QA pass independently.

## Phase 4 — behavior validation

- Run realistic design, audit, and extract scenarios across the current agent lineup.
- Terra lane: ordinary workflow usability and instruction routing.
- Sol lane: adversarial integrity, leakage, false-authority, and over-complexity review.
- Gate: trace-based behavior meets the scenario invariant; prose claims alone do not count.

## Phase 5 — adoption

- Apply versioned design patterns to Sub-005 and Sub-007.
- Keep Sub-006 frozen unless a submission-blocking defect requires a scoped repair.
- Gate: the submission's own manifest, calibration, package, prompt, rubric, and iteration controls remain authoritative.
