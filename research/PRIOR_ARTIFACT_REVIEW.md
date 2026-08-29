# Prior artifact foundation review

**Review date:** 2026-08-29  
**Mode:** read-only structural and visual foundation review  
**Submission mutation:** none

## Scope

The inventory covered 65 records from the current Sub-003 upload ZIP, Sub-005 generated-v2 artifacts, Sub-006 generated artifacts, and two explicitly rejected Sub-003 realism workbooks:

| Type | Records |
| --- | ---: |
| XLSX | 31 |
| PDF | 28 |
| DOCX | 2 |
| CSV | 2 |
| EML | 1 |
| ZIP package | 1 |

The review combined native-structure inventory, existing submission QC, the Sub-003 rejection record, and direct visual inspection of representative workbook renders, DOCX renders, the engineering-change PDF, and the handwritten shift note.

## Strong foundations

### 1. Multi-tab system export — Sub-005 Kestrel close workbook

The 11-tab workbook is the strongest current foundation for separating source populations: item master, standard-cost history, production orders, inventory movements, manufacturing variance, allocation rules, posting detail, trial balance, and close preparation. It also carries source-like filters, frozen panes, and print areas.

Its weakness is substantive density. The 11 tabs contain only 591 populated cells, and several rendered tabs contain only a few rows. Reuse the source-system separation and field vocabulary; do not treat the current row count as the target for a full close export.

### 2. Dense operational population — Sub-006 workforce/payroll workbook

The three tabs contain 3,128 populated cells and roughly 300-row HR and payroll populations. This is a strong example of difficulty created by a realistic population rather than by hidden logic. It is suitable as a foundation for system evidence, not for a complete analyst workpaper: it has only one formula and little visible review history.

### 3. Small departmental working workbook — Sub-003 capital budget

The three-tab structure (`Capex Requests`, `dept scratch`, and `carryover`) is a useful compact internal-workbook pattern. The scratch and carryover tabs are workflow-owned rather than decorative. At 231 cells and 20 formulas, it is appropriately smaller than an enterprise close workbook.

### 4. Controlled engineering record — Sub-005 ECN

The two-page engineering-change record has a credible controlled-form structure: identifier, dates, affected component, change summary, role approvals, implementation, distribution, optional attachments, retention class, and superseded-record reference. The blank optional fields are plausible form space, although a richer example should sometimes include a process sketch, trial record, or referenced attachment.

### 5. Executive and approved-handoff memo styling — Sub-006 DOCX files

The board strategy memo and opening-balance approval memo are visually clean, legible one-page records with restrained headings, tables, footer furniture, and limited routing notes. They are good foundations for concise executive or approved handoff records.

They are not foundations for a full internal analysis memo. Their one-page footprint, polished layout, and minimal review residue would look too compressed if reused for a controller workpaper, policy analysis, or multi-issue decision file.

## Foundations requiring caution

### Sub-006 EML

The message contains full headers, 870 body characters, and four quoted-reply markers, which makes it better than a standalone synthetic paragraph. It has no attachments and remains a short operational chain. Use it as a compact email pattern; build separate archetypes for longer threads, forwarded chains, attachment corrections, and mixed internal/external correspondence.

### Sub-005 handwritten shift note

The operational handoff concept is valid, but the rendered page is extremely sparse and the handwriting is visually uniform, evenly spaced, and font-like. Retain the idea of a brief shift handoff; do not use this page as the handwriting or page-density template.

### Sub-003 rejected 29/34-tab realism workbooks

These are valuable forensic foundations for workbook architecture: raw imports, sources, assumptions, valuation schedules, tax, journal entries, checks, backups, old calculations, and named side work. They also establish the failure boundary. The retained rejection record states that they exposed neutral totals, lacked reconstruction support, contained unplanned defects, mirrored buyer and seller structures too closely, used mechanical filler, and lacked exact builder lineage.

Extract the layered workbook architecture. Reject the bytes, values, mirrored structures, answer-bearing totals, filler methods, and lineage practices.

## Missing archetypes to build next

1. A 5–10 page internal controller memo with appendices, cited source schedule, unresolved questions, and restrained review marks.
2. A 6–12 message email chain with attachments, partial replies, forwarding, and one corrected attachment.
3. A 10–20 page policy or procedure with definitions, controls, exceptions, examples, and revision history.
4. A 15–40 page executed agreement with exhibits and schedules whose depth comes from legal form rather than filler.
5. A full close workbook with realistic row populations, mappings, bridge tabs, checks, prior periods, and limited superseded work.
6. A mixed internal packet combining system export, analyst workbook, marked memo, email chain, and clean executed record without shared generator quirks.

## Sub-006 boundary

This review does not recommend changing Sub-006. Its current artifacts are useful evidence for the library, but adopting richer future patterns would alter frozen bytes and invalidate existing package and testing evidence. Apply the new system prospectively to Sub-005 and Sub-007 unless a separate submission-blocking defect is proven.

