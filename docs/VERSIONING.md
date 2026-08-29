# Library versioning and lifecycle

`SYNTHETIC_DESIGN.md` remains the artifact-design authority. This document
defines how reusable library records change after publication.

## Identities and versions

- Foundation IDs (`FOUND-*`), blueprint IDs (`BP-*`), and template IDs
  (`TPL-*`) are permanent and are never reassigned.
- Published template versions use semantic versioning. A breaking slot,
  authority-boundary, file-format, or consumer-contract change increments the
  major version. Backward-compatible capability additions increment the minor
  version. Evidence-only or non-behavioral corrections increment the patch
  version.
- A released native asset is immutable. Any byte change creates a new version,
  new hashes, and a new release record.
- Schema versions describe record shape and are independent of template
  versions.

## Lifecycle

Templates move through `draft`, `released`, `deprecated`, and `withdrawn`.
Only `released` and `deprecated` versions are selectable. A deprecated version
remains reproducible but points consumers to its replacement. A withdrawn
version remains in history for provenance but must not be selected or
instantiated.

Withdrawal is required when an integrity, authorization, provenance, leakage,
or unsafe-generation defect invalidates the release evidence. Deprecation is
used for supported migrations that do not invalidate the historical release.

## Compatibility and migration

Every breaking release supplies a migration entry in `docs/MIGRATIONS.md`.
The entry identifies the old and new template versions, affected slots and
consumers, whether regeneration is required, and the validation commands.
Consumers must resolve an exact template version; floating `latest` references
are prohibited in manifests and release evidence.

## Release evidence

The machine-readable release record is authoritative for a published version.
It binds exact native-file hashes, blueprint and foundation lineage, review
verdicts, and proof artifacts. `CHANGELOG.md` is the human-readable index and
does not replace that evidence.

