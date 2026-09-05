# Experimental craft lanes

Optional operator surfaces that sit **beside** synthetic packages. They are not a complexity layer in `SYNTHETIC_DESIGN.md`, not a delivery phase, and not part of a submission ZIP.

Craft here costs extra time. It may never ship with Sub-* work. Phase 5 remains controlling: the submission’s own manifest, calibration, package, prompt, rubric, and iteration rules stay authoritative. Frozen packets are not restyled to match this note.

## Three objects

| Object | Owner | What it is | Hard fence |
| --- | --- | --- | --- |
| **In-world artifact** | This design system plus the emit path (native DOCX, XLSX, PDF, EML, ZIP) | Workpapers, memos, exports, executed files whose grammar is `SYNTHETIC_DESIGN.md` | Must look like a real producer’s output. Editorial SVG, product-UI polish, and identical “designed” quirks across producers are generator tells. |
| **Operator surface** | Optional frontend skill (for example [Impeccable](https://github.com/pbakaus/impeccable)) | A dock or browser for packets: inventory, lifecycle, authority class, empty states | Lives only in an operator app tree. Never rewrite packet bytes. |
| **Ex-world schematic** | Optional figure skill (for example [Diagram Design](https://github.com/cathrynlavery/diagram-design)) | A diagram of *how a package is composed* for humans (producers, layers A–E, evidence mix) | Lives only in operator docs. Not an exhibit unless the in-world file is a native figure a real employee would make. |

Path names are local convention, not a required layout:

- submission / emit output stays native business files;
- operator UI stays out of the package;
- operator schematics stay out of the package.

If the same path would be touched by two lanes, serialize. Do not merge craft floors onto one HTML or one PDF.

## Why the floors conflict

`SYNTHETIC_DESIGN.md` fails thin polished workbooks, evaluator answer-sheet memos, and theatrical mess. Impeccable optimizes product UI (contrast, depth, type, live iteration). Diagram Design optimizes editorial figures (orthogonal connectors, density budget, no shadows). Those rules improve operator tools. They damage in-world realism if they leak into the packet.

An in-world flowchart belongs in Word or Visio with ordinary awkwardness. A Diagram Design redraw of the same information is an operator study copy, labeled as such.

## Parallel agent contract

Write this down before splitting work:

1. Blueprint id (producer, medium, footprint, prohibited, proof gates).
2. Authority class per file.
3. Which tree is in-world vs operator UI vs operator schematic.
4. One line: do not restyle a frozen submission to adopt a newer library look.

Then:

- **Lane S:** `skill/SKILL.md` design / audit / extract only. Inventory and render gates. Emit native bytes.
- **Lane H:** frontend craft on the dock only (`npx impeccable detect` on the app, not on the PDF).
- **Lane D:** figures about the packet for operators only.

Shared tokens (palette, type) may pass from a shipped operator UI into a schematic profile. They must not restyle authoritative records.

## Relation to existing gates

- Finish gate: `SYNTHETIC_DESIGN.md` §10.
- Package variation: `skill/references/package-composition.md`.
- Terra / Sol review: `docs/REVIEW_LANES.md` apply to artifacts and templates, not to operator chrome.
- Adoption: `docs/PHASES.md` Phase 5.

This file does not authorize new templates, copied submission bytes, or a change to an active sub.
