# Consumer integrations

Syn Studios exposes one small consumer interface: `discover -> recommend -> select -> instantiate -> validate`. Recommendation is optional; the other four operation shapes and trajectories are unchanged. Its machine-readable definition is [`integrations/consumer-profile.v1.json`](../integrations/consumer-profile.v1.json). The interface hides library layout and release evidence details from consumers while keeping package authority with the package workflow.

The read-only resolver is executable and returns JSON:

```powershell
python integrations/query_catalog.py discover --consumer-id holodeck-file-generation --artifact-type xlsx
python integrations/query_catalog.py recommend --requirements .\package\syn-requirements.json --recent-usage .\package\syn-recent-usage.json
python integrations/query_catalog.py select --consumer-id holodeck-file-generation --template-id TMPL-0001 --version 1.0.0
python integrations/query_catalog.py instantiate --consumer-id holodeck-file-generation --template-id TMPL-0001 --version 1.0.0 --package-root .\package --output-location working_world --provenance-reference manifest.md#workbook --manifest-approved --write-authorized --source-authorized
python integrations/query_catalog.py validate --consumer-id holodeck-file-generation --template-id TMPL-0001 --version 1.0.0
```

Every operation first requires the canonical repository validator to pass. Consumer identifiers are exact, case-sensitive IDs from the consumer profile. Discovery and exact selection include `released` and `deprecated` versions while excluding `draft`, `candidate`, and `withdrawn` entries. Recommendation applies the reuse contract owned by [`SYNTHETIC_DESIGN.md`](../SYNTHETIC_DESIGN.md) to exact profiled versions and returns `next_operation: select`; its request and facet vocabulary are owned by [`schemas/submission-profile.schema.json`](../schemas/submission-profile.schema.json). A catalog-known but unprofiled historical identity is counted without invented lineage or fingerprint metadata, while an identity absent from the selectable catalog is invalid. Unprofiled releases remain discoverable and selectable but are not recommendable. Selection requires the exact recorded version; aliases such as `latest`, `current`, `stable`, and `*` are rejected. Producer role and medium are checked against the descriptor and blueprint, while knowledge and authority constraints must be compatible with the package provenance request. Instantiation rechecks those constraints, requires a nonempty provenance reference, and defaults to a no-write plan. `--materialize` stages validated template bytes and atomically commits them only if a new, contained package output still does not exist; it never edits library bytes, overwrites a race winner, or leaves its staging copy after a handled failure. Validation delegates release schemas, evidence semantics, and lineage integrity to the canonical library validator, then returns the exact bound release and native-asset hashes. A successful command exits `0`, invalid input exits `2`, and a valid exact selection or recommendation with no compatible match exits `3`.

## Authority split

| Concern | Owner |
| --- | --- |
| Durable artifact design | [`SYNTHETIC_DESIGN.md`](../SYNTHETIC_DESIGN.md) |
| Agent routing | [`skill/SKILL.md`](../skill/SKILL.md) |
| Released-resource discovery | `library/catalog.json` |
| Submission-aware recommendation metadata and vocabulary | `library/submissions/` and `schemas/submission-profile.schema.json` |
| Release evidence | `library/releases/` |
| World facts, provenance cards, manifest, prompt, and template binding | Consumer package |
| Generation approval and write authority | Consumer workflow |
| Acceptance | Downstream reviewer |

The profile is a routing and compatibility adapter. It never converts discovery into approval, a passing validation result into acceptance, or an ANNA handoff into write authorization.

## Consumer flow

1. **Discover** reads the catalog and filters on artifact type, blueprint, producer, medium, lifecycle, authority, capabilities, and release status.
2. **Recommend** ranks compatible exact versions from reviewed submission profiles against package-local recent usage and returns profile status, reuse reasons, contributing profile and Foundation Card IDs, semantic Pattern Invariants, fingerprint and lineage counts, confidence limits, and transformation obligations. When recent exact reuse is eligible, the result carries the validated declaration as `planned_not_validated`; the consumer binds it into the package-local Template Binding and candidate-artifact validation owns proof that the material change occurred. Recommendation has no write or global-history behavior.
3. **Select** chooses only an exact released or deprecated compatible resource. No match is an explicit result; the consumer must not silently fall back to foundation bytes, drafts, candidates, or withdrawn templates.
4. **Instantiate** combines a released template with an approved package manifest, provenance card, and package-owned world facts. It writes only to the authorized package output location.
5. **Validate** checks the exact immutable release, descriptor, blueprint, native hashes, and typed release evidence and returns `pass`, or `status: error` with exit code `2` on failure. Candidate-artifact, realism, and cross-file review remain with the consumer package workflow; release validation is not downstream acceptance.

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

ANNA uses the canonical consumer identifier `anna`; Holodeck workflows use `holodeck-file-generation`, including world-exploration modes. ANNA should route synthetic-artifact work through its Holodeck Bridge and then call the Syn Studios interface at the selected stage. The interface deliberately preserves ANNA's existing stop conditions: create still requires an approved manifest, write authorization, and validated assignment; review still requires a complete package and its own reviewer controls. No private world content or path is copied into ANNA or this repository.

This repository does not install or modify ANNA. ANNA registration is a separate change under ANNA's own catalog authority.
