# Consumer integrations

Syn Studios exposes one small consumer interface: `discover -> select -> instantiate -> validate`. Its machine-readable definition is [`integrations/consumer-profile.v1.json`](../integrations/consumer-profile.v1.json). The interface hides library layout and release evidence details from consumers while keeping package authority with the package workflow.

The read-only resolver is executable and returns JSON:

```powershell
python integrations/query_catalog.py discover --artifact-type xlsx --consumer holodeck-file-generation
python integrations/query_catalog.py select --template-id TMPL-0001 --version 1.0.0
python integrations/query_catalog.py instantiate --template-id TMPL-0001 --version 1.0.0 --package-root .\package --output-location working_world --provenance-reference manifest.md#workbook --manifest-approved --write-authorized --source-authorized
python integrations/query_catalog.py validate --template-id TMPL-0001 --version 1.0.0
```

Discovery excludes unreleased entries. Selection requires the exact recorded version; aliases such as `latest`, `current`, `stable`, and `*` are rejected. Producer role and medium are checked against the descriptor and blueprint, while knowledge and authority constraints must be compatible with the package provenance request. Instantiation defaults to a no-write plan. `--materialize` copies validated template bytes into the contained package output only after all authorization flags pass; it never edits library bytes or overwrites an existing output. Validation checks catalog, descriptor, native file hashes, blueprint, same-hash reviews, conductor approval, and release evidence. A successful command exits `0`, invalid input exits `2`, and a valid exact selection with no compatible match exits `3`.

## Authority split

| Concern | Owner |
| --- | --- |
| Durable artifact design | [`SYNTHETIC_DESIGN.md`](../SYNTHETIC_DESIGN.md) |
| Agent routing | [`skill/SKILL.md`](../skill/SKILL.md) |
| Released-resource discovery | `library/catalog.json` |
| Release evidence | `library/releases/` |
| World facts, provenance cards, manifest, prompt, and template binding | Consumer package |
| Generation approval and write authority | Consumer workflow |
| Acceptance | Downstream reviewer |

The profile is a routing and compatibility adapter. It never converts discovery into approval, a passing validation result into acceptance, or an ANNA handoff into write authorization.

## Consumer flow

1. **Discover** reads the catalog and filters on artifact type, blueprint, producer, medium, lifecycle, authority, capabilities, and release status.
2. **Select** chooses only a released, compatible resource. No match is an explicit result; the consumer must not silently fall back to foundation bytes or an unreleased template.
3. **Instantiate** combines a released template with an approved package manifest, provenance card, and package-owned world facts. It writes only to the authorized package output location.
4. **Validate** applies the release's validation profile and returns evidence records plus `pass`, `fix_first`, or `reject`. Validation evidence is not downstream acceptance.

Any operation that changes artifact bytes must use the optional toolchain contract in [`docs/DOCUMENT_STACK.md`](DOCUMENT_STACK.md). The design and finish rules themselves remain in [`SYNTHETIC_DESIGN.md`](../SYNTHETIC_DESIGN.md); this document does not restate them.

## Holodeck compatibility

Holodeck continues to own world building, provenance planning, user gates, manifests, and generation. The adapter maps provenance constraints into blueprint selection and a package-local template binding:

- author/source and tool/system constrain the blueprint producer and medium;
- source knowledge, prohibited knowledge, difficulty constraints, seed conventions, and planned imperfections remain package-local;
- realism checks select validation capabilities;
- world facts become ephemeral instance inputs and are forbidden from reusable template storage.

For world exploration, the profile declares compatible artifact-map fields and world-brief sections. Observed facts and inferences remain distinguishable, and concrete claims retain evidence references. Exploration does not authorize design or generation.

## Human artifact realism compatibility

The consumer supplies the released template's blueprint constraints alongside the package-local provenance card. `human-artifact-realism` may produce a realism plan or audit evidence within that envelope. Its origin, lifecycle, authority, handling, residue, footprint, layout, and forbidden-residue decisions belong to the package-local plan, not the reusable template.

## ANNA compatibility

ANNA should route synthetic-artifact work through its Holodeck Bridge and then call the Syn Studios interface at the selected stage. The interface deliberately preserves ANNA's existing stop conditions: create still requires an approved manifest, write authorization, and validated assignment; review still requires a complete package and its own reviewer controls. No private world content or path is copied into ANNA or this repository.

This repository does not install or modify ANNA. ANNA registration is a separate change under ANNA's own catalog authority.
