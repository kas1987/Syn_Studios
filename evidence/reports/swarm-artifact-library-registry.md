# Artifact library swarm registry

Mission: close the P0-P2 artifact-library backlog identified on 2026-08-29.

| Agent | Role | Slice | Owned files | Expected output | Status | Last evidence | Next gate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AGENT-01 / control-plane | build | Machine-enforced lineage, blueprint integrity, and template release contract | `schemas/**`, `scripts/validate_library.py`, `examples/blueprints/**`, `tests/test_library_control_plane.py` | Committed branch plus schema/validator proof | COMPLETE | Integrated through `b85b3c0`; empty-workbook and absent/generic/stale/forged technical-result arms fail closed | Re-run canonical validator and full suite on the final integrated clean checkout |
| AGENT-02 / consumer-adapter | build | ANNA, Holodeck, world-exploration, and realism consumer interface | `CONTEXT.md`, `docs/INTEGRATIONS.md`, `skill/**`, `integrations/**`, `tests/test_consumer_integration.py` | Committed branch plus consumer-contract proof | COMPLETE | Consumer head `5c09d2c`; exact-version, fail-closed, authority, provenance, and atomic materialization tests | Downstream ANNA adapter remains a separate review and merge gate |
| AGENT-03 / template-library | build | Versioned catalog and three sanitized, fact-free native template vertical slices | `library/catalog.json`, `library/templates/**`, `examples/manifests/**`, `tests/test_template_assets.py` | Committed branch plus structural/render evidence | COMPLETE | Integrated through `5c74948`; exact native hashes plus 30-row and irregular 360-row CSV rebuild/render proof | Any new capacity or native bytes require a new version and fresh evidence |
| AGENT-04 / technical-results | build + verifier | Replace self-attested technical PASS records with executable category results | `scripts/run_template_technical_validation.py`, assembler/validator gates, technical-result evidence | Deterministic runner, sabotage arms, and rebound releases | COMPLETE | `879a3be`, `fc63afe`, `b85b3c0`, and release evidence `73ca0c4`; 24 machine results | Fresh-checkout full gate |
| AGENT-05 / tabular-conformance | build | Consumer-owned populated XLSX/CSV integrity, diversity, reconciliation, and exception lifecycle | `scripts/audit_tabular_package.py`, tabular and diversity tests | Reusable audit without acceptance authority | COMPLETE | `dbbaf28`, `b9d14c7`, and `5c74948`; 360-row/17-page proof plus negative arms | Consumer packages must supply their own policy and provenance reference |

Conductor integration assembled `REL-0001`, `REL-0002`, and `REL-0003` with
independent Terra and Sol records, executable typed technical evidence,
conductor records, and catalog bindings. The remaining gates are final clean-checkout validation on
the integrated head and separately authorized external actions: GitHub
publication and repository rules, downstream ANNA adapter review/merge, and
local skill installation. No push, pull request, tag, downstream merge, or
installation is authorized by this registry.
