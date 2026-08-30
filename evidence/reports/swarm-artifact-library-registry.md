# Artifact library swarm registry

Mission: close the P0-P2 artifact-library backlog identified on 2026-08-29.

| Agent | Role | Slice | Owned files | Expected output | Status | Last evidence | Next gate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AGENT-01 / control-plane | build | Machine-enforced lineage, blueprint integrity, and template release contract | `schemas/**`, `scripts/validate_library.py`, `examples/blueprints/**`, `tests/test_library_control_plane.py` | Committed branch plus schema/validator proof | COMPLETE | Integrated through `b85b3c0`; empty-workbook and absent/generic/stale/forged technical-result arms fail closed | Re-run canonical validator and full suite on the final integrated clean checkout |
| AGENT-02 / consumer-adapter | build | ANNA, Holodeck, world-exploration, and realism consumer interface | `CONTEXT.md`, `docs/INTEGRATIONS.md`, `skill/**`, `integrations/**`, `tests/test_consumer_integration.py` | Committed branch plus consumer-contract proof | COMPLETE | Consumer head `5c09d2c`; exact-version, fail-closed, authority, provenance, and atomic materialization tests | Downstream ANNA adapter remains a separate review and merge gate |
| AGENT-03 / template-library | build | Versioned catalog and three sanitized, fact-free native template vertical slices | `library/catalog.json`, `library/templates/**`, `examples/manifests/**`, `tests/test_template_assets.py` | Committed branch plus structural/render evidence | COMPLETE | Integrated through `5c74948`; exact native hashes plus 30-row and irregular 360-row CSV rebuild/render proof | Any new capacity or native bytes require a new version and fresh evidence |
| AGENT-04 / technical-results | build + verifier | Replace self-attested technical PASS records with executable category results | `scripts/run_template_technical_validation.py`, assembler/validator gates, technical-result evidence | Deterministic runner, sabotage arms, and rebound releases | COMPLETE | `879a3be`, `fc63afe`, `b85b3c0`, and release evidence `73ca0c4`; 24 machine results | Fresh-checkout full gate |
| AGENT-05 / tabular-conformance | build | Consumer-owned populated XLSX/CSV integrity, diversity, reconciliation, and exception lifecycle | `scripts/audit_tabular_package.py`, tabular and diversity tests | Reusable audit without acceptance authority | COMPLETE | `dbbaf28`, `b9d14c7`, and `5c74948`; 360-row/17-page proof plus negative arms | Consumer packages must supply their own policy and provenance reference |
| AGENT-06 / email-and-result-integrity | build | Count only real email-thread messages and preserve published technical-result bytes | `scripts/run_template_technical_validation.py`, `tests/test_technical_validation_runner.py` | Broken-arm tests plus immutable-result preflight | COMPLETE | Integrated as `1bb8980`; attachment/epilogue `From:` lines do not inflate depth and changed published results are refused before writes | Independent combined-head review |
| AGENT-07 / recalculation-proof-integrity | build | Preserve an existing machine recalculation proof unless a rerun is byte-identical | `scripts/generate_workbook_recalculation_proof.py`, `tests/test_recalculation_proof_immutability.py` | Atomic no-clobber publication plus changed/identical/missing-target arms | COMPLETE | Integrated as `c840028`; 259 tests passed with 3 expected optional-stack skips in the worker worktree | Independent combined-head review |
| AGENT-08 / OOXML-token-decoding | build | Reconstruct visible build tokens across decoded OOXML XML surfaces | `scripts/audit_tabular_package.py`, `tests/test_tabular_conformance.py` | Numeric-entity, text, tail, attribute, malformed-XML, and binary-control arms | COMPLETE | Integrated as `7bbb2b9`; 20 targeted and 260 full tests passed with 3 expected optional-stack skips in the worker worktree | Independent combined-head review |

Conductor integration assembled `REL-0001`, `REL-0002`, and `REL-0003` with
independent Terra and Sol records, executable typed technical evidence,
conductor records, and catalog bindings. PR #4 is published, the default-branch
ruleset is active, and the local skill was installed recoverably from the
verified `8fc069a` tree. The exact-head review then identified four additional
P1/P2 findings; AGENT-06 through AGENT-08 close those findings on the integrated
local head. Remaining gates are independent combined-head review, final
clean-checkout validation, PR refresh, and installation refresh from the final
reviewed head. The downstream ANNA adapter remains isolated and unmerged.
