---
name: Syn Studios Synthetic Artifact System
version: 0.1.0
status: foundation
description: Source-first design contract for complete, realistic, integrity-preserving synthetic business artifacts and packages.
---

# Synthetic Design System

## 1. North star: business-complete, not artificially busy

A strong synthetic artifact looks like the natural output of a real role inside a real workflow. It contains the ordinary volume, repeated detail, attachments, side work, and handling residue that the producer would create even if no evaluation existed.

Length is evidence only when the source owns the added material. Messiness is evidence only when a person, purpose, medium, and moment explain it. A longer file with filler is weaker than a short file with the correct footprint.

## 2. The artifact grammar

Every artifact is designed from seven explicit layers:

1. **Producer:** role, department, organization, vocabulary, and degree of authority.
2. **Purpose:** the job the file performs independent of the task prompt.
3. **System or medium:** ERP export, analyst workbook, native memo, email chain, executed PDF, scan, handwritten note, or assembled packet.
4. **Lifecycle:** draft, working, reviewed, approved, executed, transmitted, archived, or reused.
5. **Authority:** authoritative, supporting, contextual, superseded, question-only, or incidental.
6. **Substantive footprint:** expected pages, sheets, records, repeated fields, attachments, boilerplate, blank work areas, and side calculations.
7. **Handling history:** the smallest credible sequence of creation, review, correction, transmission, and storage.

No realism treatment may expand the approved information boundary.

## 3. Complexity layers

Complexity is assembled in order. Later layers never compensate for missing earlier layers.

### Layer A — complete core record

The governing facts, definitions, dates, amounts, identifiers, formulas, signatures or execution status, and required supporting detail are present and internally consistent.

### Layer B — ordinary operational depth

Add routine detail the producer naturally maintains: transaction rows, account mappings, repeated clauses, schedules, status fields, attachment lists, system metadata, exception fields, or supporting pages.

### Layer C — adjacent workflow context

Add material that belongs to the same business process but is not necessarily needed for the assigned conclusion: prior-period columns, dormant accounts, inactive assets, unrelated email replies, administrative pages, historical tabs, blank form sections, or routine policy text.

### Layer D — working and review residue

Add bounded side calculations, checks, temporary tabs, review questions, copied-forward labels, version remnants, print quirks, or clearly superseded work. The governing answer must remain identifiable.

### Layer E — physical or transmission history

Use restrained scan, print, routing, or handwritten residue only when the lifecycle supports it. Do not age every file.

## 4. Internal and external profiles

### Internal working files

Internal artifacts may be longer, denser, less normalized, and more visibly handled. Appropriate features include:

- extra tabs used for exports, mappings, bridges, checks, prior periods, or scratch work;
- repeated rows and dormant records that reflect the system population;
- email chains with quoted context, attachments, shorthand, and partial replies;
- draft memos with appendices, source lists, unresolved questions, and review marks;
- local formatting differences caused by pasted data, legacy templates, or multiple owners.

### External, executed, or regulator-facing files

External artifacts should usually be more formal, complete, and restrained. Depth comes from clauses, exhibits, schedules, definitions, execution blocks, and standard boilerplate—not casual clutter. Visible working residue is exceptional and must follow a credible retained-copy lifecycle.

### System exports

System exports are repetitive, consistent, and sometimes awkward. They may contain unused fields, nulls, status codes, truncation, and machine timestamps. Do not add analyst styling inside a source-system export.

## 5. Authority classes

- **Authoritative:** controls the treatment or amount within its scope.
- **Supporting:** corroborates or decomposes an authoritative record.
- **Contextual:** explains workflow or intent but does not control the answer.
- **Superseded:** retained prior work whose non-governing status is unmistakable.
- **Question-only:** flags a gap or review issue without supplying the resolution.
- **Incidental:** ordinary business content outside the task's decision path.

Incidental content may create retrieval cost; it may not contradict the authoritative record merely to trap the solver.

## 6. Archetype footprints

Footprints are ranges to calibrate, not quotas.

| Archetype | Typical internal footprint | Natural depth sources | Common failure |
| --- | --- | --- | --- |
| Close or reconciliation workbook | 5–15 tabs; tens to thousands of rows | exports, mappings, entries, bridges, checks, prior periods | tiny polished workbook with only answer-bearing rows |
| Operational register | 2–8 tabs; population plus lookups | inactive records, codes, notes, source IDs, status history | summary-only table without population texture |
| Internal decision memo | 3–12 pages plus appendices | background, options, exhibits, source list, unresolved items | one-page answer sheet written for the evaluator |
| Email thread | 3–12 messages or a short message with real attachments | quoted context, forwarding, partial replies, attachment references | perfectly composed standalone paragraph |
| Policy or procedure | 4–20 pages | definitions, scope, ownership, controls, examples, revision history | one-page rule list with no operational detail |
| Executed agreement | 8–60+ pages including exhibits | definitions, covenants, schedules, signatures, boilerplate | compressed summary masquerading as an agreement |
| External notice or approval | 1–6 pages plus referenced schedules | formal basis, conditions, dates, contacts, exhibits | internal scratch marks without a retained-copy story |
| System export | population-dependent | repeated fields, nulls, codes, timestamps, dormant rows | hand-designed dashboard styling |

## 7. Package composition

A package should distribute evidence and realism across different sources:

- 20–35% authoritative or executed records;
- 25–40% operational populations and system exports;
- 20–35% internal analysis, correspondence, and working records;
- 5–20% contextual or incidental material.

These ranges are diagnostic, not mandatory. The task and world remain controlling.

Use a variation matrix across producer, lifecycle, medium, cleanliness, density, typography, date format, and review residue. Repeated quirks across unrelated sources are a generator tell.

## 8. Reusable pattern rule

Extract patterns before templates. A foundation card must state:

- the source locator and exact hash;
- the reusable structural pattern;
- the facts and identities that must not be copied;
- known defects or rejection history;
- the safe transformation needed before templating;
- integrity and visual proof required for release.

A template is released only after facts, names, dates, amounts, hidden metadata, formulas, links, comments, and execution residue are independently reviewed.

### Submission-aware reuse

A submission profile is a public-safe metadata registry, never a submission copy. It may bind reviewed Foundation Cards to their Blueprints and exact Released Template versions, release records, native hashes, and evidence hashes. It must not contain source bytes, source locators, world facts, prompts, rubrics, manifests, provenance cards, solutions, or runtime usage history. JSON is canonical; generated indexes or YAML views are projections only.

Organization form, reporting basis, authority family, business function, artifact family, producer role, lifecycle, authority class, and medium are independent facets. Their controlled vocabulary is owned by [`schemas/submission-profile.schema.json`](schemas/submission-profile.schema.json); a new value requires reviewed schema change. Authority family does not imply authority class, and organization form does not imply reporting basis. Broad applicability describes a reusable structure, not a claim about its source, and must carry explicit confidence limits and transformation obligations.

A diversity fingerprint is derived from six stable dimensions—structure, visual language, producer workflow, population shape, medium, and handling history—plus the unique reviewed semantic IDs of its resolved Pattern Invariants. [`schemas/submission-profile.schema.json`](schemas/submission-profile.schema.json) owns both the closed semantic vocabulary and each ID's reviewed Foundation Card, JSON-pointer, and statement-hash bindings. Semantic IDs remain independent of Foundation Card wording and lineage in the fingerprint, while a new meaning, paraphrase, or allowed source binding requires reviewed schema change. The fingerprint excludes submission, Foundation Card, and template identities so materially similar releases can be recognized across lineages.

Recommendation uses only reviewed submission profiles and caller-supplied, package-local recent usage. It returns each profile's confidence limits rather than converting review status into a claim of universal fitness. Consecutive exact reuse is always blocked by recommendation. Exact reuse elsewhere in the recent window requires a target-bound material-transformation plan; cosmetic-only changes never qualify. Recommendation returns that plan as `planned_not_validated`, and the consumer must bind it to the package-local Template Binding and validate the resulting candidate artifact before claiming the transformation occurred. Repeated submission lineage and diversity fingerprints rank behind fresh alternatives. Recommendation is action-free and never changes exact selection, instantiation, validation, or package authority.

The consumer workflow owns the completeness and oldest-to-newest ordering of recent usage. Recommendation reports how many supplied identities it considered but cannot infer omitted, reordered, or external package history and never claims a global usage ledger.

## 9. Anti-patterns

- thin files decorated with handwriting, stains, or old fonts;
- every workbook having a `Scratch` tab, the same colors, or the same reviewer initials;
- arbitrary extra pages or tabs with no source-owned purpose;
- hidden answer keys, rubric language, or notes that solve the task;
- copied prior submission facts inside a supposedly reusable template;
- broken formulas, clipped content, unreadable scans, or false metadata used as difficulty;
- external agreements that read like internal summaries;
- pristine internal workpapers with only task-relevant rows;
- identical voices, timestamps, and formatting across independent producers.

## 10. Finish gate

An artifact passes only when:

1. its core record is complete and source-authorized;
2. its footprint fits the producer and workflow;
3. every noticeable imperfection has a credible cause;
4. authoritative and non-authoritative material remain distinguishable;
5. formulas, totals, links, and machine-readable content retain integrity;
6. rendered pages and sheets are readable and usable;
7. metadata and visible content reveal no generator or evaluation process;
8. package-level variation is explainable rather than randomized.
