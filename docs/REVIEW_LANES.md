# Review lanes

## Terra — colleague usability

Give the reviewer the design contract, candidate artifact or card, and the intended producer/purpose. Do not provide the desired verdict. Ask whether the file feels complete, naturally structured, usable, and appropriately internal or external.

Verdicts: `USABILITY_PASS`, `USABILITY_FIX_FIRST`, `USABILITY_REJECT`.

## Sol — adversarial integrity

Give the reviewer the same current bytes plus source authority and provenance. Ask it to falsify completeness, authority separation, formula integrity, metadata hygiene, answer leakage, handling history, and template sanitization.

Verdicts: `INTEGRITY_PASS`, `INTEGRITY_FIX_FIRST`, `INTEGRITY_REJECT`.

## Conductor gate

A foundation may be cataloged after local structural review. A binary template may be released only when both lanes review the same descriptor and native-asset hashes and the conductor confirms the artifact against current source authority and rendered evidence.

The release record, catalog entry, descriptor, native assets, and every typed proof output must remain hash-bound after a fresh clean checkout. Run the canonical validator and full repository suite in that checkout; a mutable builder worktree or schema-only pass is not release proof. Reviewer identities must remain independent, and repository release does not authorize a push, pull request, tag, package publication, downstream merge, or skill installation.
