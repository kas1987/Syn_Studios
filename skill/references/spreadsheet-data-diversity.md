# Spreadsheet and row-carrier diversity

Use this reference for workbook families, Excel workpapers, and CSV or other
row-level source carriers. `SYNTHETIC_DESIGN.md` remains the durable design
authority; this reference applies that contract to scalable tabular packages.

## Package footprint

A workflow may naturally require several formula-heavy workbooks and several
row-level exports. Counts are diagnostic, not quotas. A substantial package can
reasonably contain roughly five to seven workbooks and six to ten CSV carriers
when independent systems, owners, periods, or calculations produce them.

Workbook depth should come from input populations, mappings, calculations,
reconciliations, review checks, exceptions, prior-period context, and bounded
side work. CSV depth should come from source-owned populations, field order,
identifiers, status history, null behavior, precision, timestamps, and dormant
or irrelevant records. Do not split one thin dataset into many files merely to
reach a count.

## Expansion contract

Every reusable workbook or CSV pattern must state:

- the source carrier and exact required or mapped fields;
- minimum, reference, and supported population sizes;
- how tables, formulas, validations, named ranges, and checks expand;
- how print areas and pagination are rebuilt after expansion;
- what happens above native capacity;
- which cross-file identifiers and totals must reconcile; and
- which structural, computational, leakage, and rendered evidence is refreshed.

Fixed ranges must never silently omit additional rows. When native capacity is
exceeded, rebuild the table and every dependent formula and render boundary,
then issue new hashes and proof. A successful import must still fail readiness
when required fields, mappings, authority, or reconciliations are incomplete.

## Diversity matrix

Vary independent carriers only where their producer or system explains it:

- source system and owner;
- field names, order, codes, and null conventions;
- row volume, inactive records, and exception rate;
- date, number, sign, and precision conventions;
- formula density and review behavior;
- typography, gridlines, filters, freeze panes, and print setup;
- filename, export timestamp, and period conventions; and
- cleanliness, working residue, and lifecycle state.

Expected shared identities and balances stay consistent. Independent systems
should not acquire the same colors, tab order, scratch-sheet habit, timestamp
cadence, or reviewer language simply because they were generated together.

## Release checks

For each workbook and carrier family, prove a small, reference, and expanded
population. Include at least one incomplete-row arm, one over-capacity arm, one
formula-propagation arm, one cross-file reconciliation arm, and fresh rendered
evidence for the expanded footprint. Package review must also check value,
formatting, and voice convergence across otherwise independent sources.

