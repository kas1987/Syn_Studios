# Artifact library

This directory separates reusable knowledge from releasable bytes:

- `foundations/` records reviewed structural observations about authorized
  synthetic sources; it never stores source bytes.
- `templates/` contains independently created, fact-free native templates and
  their descriptors.
- `releases/` contains hash-bound evidence records for selectable template
  versions.
- `catalog.json` is the discovery surface used by consumers.

A catalog entry is discoverable, not automatically safe to instantiate.
Consumers must resolve an exact released version and validate its release
record. World facts, task answers, private manifests, and build residue belong
outside this library.

Versioning and lifecycle behavior are defined in `docs/VERSIONING.md`.

