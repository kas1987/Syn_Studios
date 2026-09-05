# Artifact library

This directory separates reusable knowledge from releasable bytes:

- `foundations/` records reviewed structural observations about authorized
  synthetic sources; it never stores source bytes.
- `templates/` contains independently created, fact-free native templates and
  their descriptors.
- `releases/` contains hash-bound evidence records for selectable template
  versions.
- `submissions/` contains public-safe metadata profiles that bind reviewed
  submission lineages to exact releases; it never contains submission bytes or
  package-owned facts.
- `catalog.json` is the discovery surface used by consumers.

A catalog entry is discoverable, not automatically safe to instantiate.
Consumers must resolve an exact released version and validate its release
record. World facts, task answers, private manifests, and build residue belong
outside this library.

Submission profiles are canonical JSON validated by
`schemas/submission-profile.schema.json`. Their controlled facets and derived
diversity fingerprints support package-local recommendation without changing
catalog discovery or exact selection. Producer-role or lifecycle applicability
outside a blueprint's reviewed direct binding is explicit and requires a
matching package-local material-transformation plan.

Versioning and lifecycle behavior are defined in `docs/VERSIONING.md`.
