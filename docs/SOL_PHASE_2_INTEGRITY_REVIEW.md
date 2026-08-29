# Sol Phase 2 integrity review

**Verdict:** `INTEGRITY_FIX_FIRST`

**Bottom line:** The Phase 1 design direction is sound, the eight foundation-card source hashes are genuine, and no prior artifact bytes or binary templates are present in the reusable library. Phase 2 archetype expansion should wait for a small control-plane repair because the current schemas and tests cannot enforce the documented foundation-to-template boundary or the Sol/Terra same-hash release gate.

## Review boundary

- Review date: 2026-08-29
- Working tree at intake: `main`, no commits, all repository contents untracked
- Inspected-byte manifest: [SOL_PHASE_2_REVIEW_HASHES.sha256](SOL_PHASE_2_REVIEW_HASHES.sha256)
- Handoff hash: `c2bba249cd96fb5d19e739953cb48fe870c426ef2fb806ec680efdd1ef958da9`
- Submission mutation: none
- Binary template creation: none
- Sub-006 mutation: none

The hash manifest binds the review to the pre-review repository bytes. This review file and its manifest are the only files added after that baseline.

## Proof rerun

- `python -m unittest discover -s tests -v`: PASS, 9 tests
- `python scripts/validate_library.py`: PASS, 13 records
- `python scripts/inventory_artifacts.py --help`: PASS
- All eight foundation-card source hashes: MATCH
- Sub-003 ZIP member `inputs/FY2024_Capital_Budget_Working.xlsx`: MATCH

The source locators resolve through the reviewer-local `ANNA::` workspace mapping. That mapping is implicit rather than repository-defined, but the exact cited bytes were available and matched.

## Findings

### 1. High — the template release gate is prose-only

`foundation-card.schema.json` permits `status: template_ready`, but the card contract has no fields for a template hash, sanitized-output identity, Terra verdict, Sol verdict, same-hash proof, conductor confirmation, rendered evidence, metadata inspection, or independent reviewer identity. The validator checks only schema shape, and the tests check only that a hash-shaped string and nonempty proof gate exist.

An otherwise valid card can therefore be promoted to `template_ready` while bypassing the release rule in `SYNTHETIC_DESIGN.md` and `docs/REVIEW_LANES.md`.

**Smallest repair:** remove `template_ready` from the foundation-card lifecycle or require a separate release record that binds the template hash to sanitization evidence, required integrity categories, both review-lane verdicts on the same hash, and conductor approval. Add a negative test proving promotion without those records fails.

### 2. High — foundation-to-blueprint lineage is not traceable

None of the five blueprints identifies which foundation-card patterns it uses, which source hash was reviewed, which prohibited content was excluded, or which sanitization transformation applies. This breaks the requested card → blueprint → release trace. It also makes it impossible to prove that a rejected card contributed only its allowed abstract pattern rather than its rejected structure or defect mechanism.

**Smallest repair:** add structured foundation-lineage entries to the blueprint contract: card ID, reviewed card hash, named patterns used, prohibited-content acknowledgement, and transformation boundary. Validate referenced card status and hash; reject use of a rejected card unless the entry is explicitly pattern-only and independently reviewed.

### 3. High — blueprint integrity requirements are not machine-enforced

The blueprint schema accepts any two complexity-layer entries, including duplicates, and does not require explicit handling history, source authority, authority boundaries, footprint rationale, or typed proof gates. A blueprint can pass with vague strings while omitting core completeness, metadata hygiene, formula integrity where applicable, rendering, leakage review, or anti-filler proof. The current tests assert counts, not the design invariants.

**Smallest repair:** require unique ordered layers with `core`, explicit handling history (including a justified `none`), authority/source boundaries, and a source-owned footprint rationale. Replace free-form proof gates with applicable typed categories and add adversarial failing fixtures for duplicate layers, arbitrary footprint targets, missing authority separation, and omitted leakage/render checks.

### 4. Medium — provenance validation depends on an undocumented locator mapping

All cited hashes match, so this is not a provenance failure in the reviewed bytes. However, `ANNA::` is not defined by a repository resolver or tested mapping, and `synthetic_authorized: true` is a self-asserted boolean without an authorization-basis field. `validate_library.py` never resolves a source or recomputes its hash.

**Smallest repair:** document the locator scheme, add a deterministic opt-in source-verification mode that fails transparently when the external root is unavailable, and record a non-sensitive authorization basis or authority reference.

### 5. Medium — five blueprints are enough to start, not enough to claim Phase 2 coverage

The current set gives useful starting examples for a close workbook, controller memo, email chain, executed agreement, and mixed packet. It does not cover the Phase 2 plan's standalone policy/procedure, system export or operational register, operational note, or external notice/approval. No positive/anti-pattern fixture pairs exist yet.

**Smallest repair:** after Findings 1–3 are closed, expand the set in the missing-archetype order already named by `research/PRIOR_ARTIFACT_REVIEW.md`, and require one positive and one anti-pattern fixture per archetype before calling Phase 2 complete.

## Falsification result

No answer key, rubric language, private control, raw prior-artifact byte copy, binary template, broken source hash, or mutation of Sub-006 was found in the reviewed repository. The design contract expressly rejects arbitrary filler and distinguishes authoritative from non-authoritative material. Those are real strengths.

The verdict is nevertheless `INTEGRITY_FIX_FIRST` because the current validator can certify records that violate the system's most important reuse and release promises. This is repairable without redesigning the product: make lineage, authority, proof categories, and same-hash release evidence structural before adding more archetypes.

## Exit condition

Re-run Sol after the repaired schemas, validator, negative fixtures, and tests are bound to a new repository hash manifest. Phase 2 may proceed when the three high findings are closed. Finding 4 must close before any template release; Finding 5 is Phase 2 delivery scope rather than a rejection of the foundation.
