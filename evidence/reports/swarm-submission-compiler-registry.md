# Submission-aware artifact compiler swarm registry

Base: `78d9b3716a01cf175787af39c4729de551624c5f`

| Agent | Role | Slice | Worktree | Owned files | Expected output | Status | Last evidence | Next gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DESIGN-01 | explore | Minimal deep-module interface | `Syn_Studios-wt-design-minimal` | Read-only; no repository writes | Design report in conductor mailbox | complete | One-entry-point design at `42d3112e` | Reconciled into BUILD-01 packet |
| DESIGN-02 | explore | Extensible policy/taxonomy interface | `Syn_Studios-wt-design-flexible` | Read-only; no repository writes | Design report in conductor mailbox | complete | Flexible controlled-policy design at `42d3112e` | Larger caller-controlled policy surface rejected for v1 |
| DESIGN-03 | explore | Default consumer workflow interface | `Syn_Studios-wt-design-default` | Read-only; no repository writes | Design report in conductor mailbox | complete | Safe-default consumer design at `42d3112e` | Reconciled into BUILD-01 packet |
| BUILD-01 | build | Submission profiles and diversity-aware recommendation | `Syn_Studios-wt-submission-compiler` | Submission schema/profiles; validator; resolver; consumer contract; tests/docs/domain language; this registry | Reviewed implementation diff and proof | reviewing | Exact base `78d9b37`; `338` tests passed with `3` expected skips; targeted recommendation/profile suite passed `29`; canonical validator passed `41` records; technical dry run passed `24`; compileall and diff check passed | Rebase onto reviewed release head, then two fresh exact-head critics |
| CRITIC-TEST-01 | test-gate critic | Falsify task-relative proof for profile safety, semantic fingerprints, sector facets, and reuse blocking | `Syn_Studios-wt-submission-compiler` | Read-only; no repository writes | `TEST_PASS`, `TEST_FAIL`, or `TEST_SPLIT` with commands and reproducible gaps | superseded | `TEST_PASS` on the prior 336-test diff; later change-critic repairs require a fresh exact-head test verdict | Re-run after rebase |
| CRITIC-CHANGE-01 | change critic | Adversarial architecture, security, authority, and compatibility review | `Syn_Studios-wt-submission-compiler` | Read-only; no repository writes | `CHANGE_PASS`, `CHANGE_FIX_FIRST`, `CHANGE_REJECT`, or `CHANGE_SPLIT` with P0-P2 findings | fix-repaired | `CHANGE_FIX_FIRST` found semantic-ID laundering and NTFS junction escape; reviewed semantic bindings, resolved-root containment, and both regression arms now pass | Re-run after rebase |
| CRITIC-TEST-02 | test-gate critic | Falsify the final rebased compiler proof | `Syn_Studios-wt-submission-compiler` | Read-only; no repository writes | Exact-head test verdict | pending | Awaiting release-head rebase | Dispatch after final local gates |
| CRITIC-CHANGE-02 | change critic | Adversarially review the final rebased compiler interface and authority model | `Syn_Studios-wt-submission-compiler` | Read-only; no repository writes | Exact-head change verdict | pending | Awaiting release-head rebase | Dispatch after final local gates |

The conductor owns implementation, review intake, and all merge or publication decisions. Tool events and agent completion notices are not approval or proof.

BUILD-01 leaves a coherent uncommitted diff on the exact recorded base. It does not modify submission repositories or bytes, generate templates, alter the ANNA adapter, install the skill, push, merge, or claim downstream acceptance.
