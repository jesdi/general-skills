# implement-spec: measured runs

One row per run, so each change to the driver can be judged against real numbers. A "run" is one PR's worth of tickets. Hand-built baselines go in the same table so the driver is compared against the alternative, not only against itself.

## Runs

| Date | Project · PR | Method | Tickets | Wall time | Human stops | Cost | Criteria met | Escaped to the PR |
|---|---|---|---|---|---|---|---|---|
| 2026-11-04 | agent-ops 8a · [#269](https://github.com/jesdi/agent-ops/pull/269) | driver | 2 | 3 h 13 m | 3 | **$25.08** + 7 Codex | 17 / 17 | unsigned commits; a stored-PAT fallback (`access=None`) |
| 2026-11-04 | agent-ops 8a · none (archived) | by hand, one session | 2 | 40 m | 0 | **$7.25** | 11 met, 5 partial, 1 missing / 17 | `mint([])` grants every repository; own 23-line spec |
| 2026-11-04 | agent-ops 8b · [#270](https://github.com/jesdi/agent-ops/pull/270) | driver | 4 | ≈ 6 h 55 m | 2 | **$42.77** + 10 Codex | not graded | unsigned commits with `Co-Authored-By`; a PR base already merged |

Column meanings:
- **Wall time:** from the session loading the handoff to the PR (or the archive). It includes waits on the operator.
- **Human stops:** each time the run needed an operator answer before it could go on, driver questions or not.
- **Cost:** the driving session, plus its subagents, plus the driver's own Claude agents. Codex runs are counted, not priced (the driver doesn't record their usage).
- **Criteria met:** the tickets' acceptance criteria plus extra checks, graded by a blind reviewer that didn't know which branch came from which method.
- **Escaped to the PR:** defects in the pushed branch that the run's own reviews missed.

## Run details

### 2026-11-04 · agent-ops 8a (GitHub App, onboarding side) · driver

- Spec `specs/2026-11-04-onboarding-github-app/spec.md`, 166 lines, reviewed in two lavish rounds. Tickets 01 (App module) and 02 (onboarding uses the App).
- Flags: `--gate 'make gate' --tickets .agent/tickets-8a --models <reviewer/final-reviewer/test-writer on Claude> --base feat/onboarding-board --push`.
- Cost:

  | Part | $ |
  |---|---:|
  | Driving session | 5.38 |
  | 4 review subagents | 2.03 |
  | Driver | 17.67 |

- Driver agents: 7 test-writer, 2 implementer, 8 reviewer, 4 fixer, 3 final-reviewer, 7 Codex correctness. Fix rounds: 01 = 2, 02 = 1.
- Human stops:
  1. Codex allowed? The driver requires a correctness pass on another provider.
  2. `01-locked-test-again`: a fake answered both the user and the org endpoint, so the test hid the wrong endpoint.
  3. `final-finding`: the access recheck let a non-`AppError` abort every project's run.
- After the driver: the final finding was fixed by hand. review-diff (internal) then raised 2 structural blockers, which were fixed, and a re-review left only minor findings. The 4 driver commits were re-signed before the push.
- Blind grade: 17 / 17. Gaps: `Run.access=None` still falls back to the stored PAT; the access check sits in 3 places; a transient 1Password error reads as "not registered".
- Production / test lines: ~358 / ~890. Full suite: 3683 passed.

### 2026-11-04 · agent-ops 8a · by hand (baseline)

- Session `0b282cb7`, Opus 5.5, one session with 3 review subagents. Its spec was its own, 23 lines: field names `APP_ID`/`PRIVATE_KEY`, one token per repository.
- Cost: session $5.61, subagents $1.64.
- Blind grade: 11 met, 5 partial, 1 missing (`mint([])` sends an empty list, which GitHub reads as every repository).
- Its engine tests run the real App behind a stateful fake of GitHub's App API, a stronger seam than the driver run's duck-typed `FakeApp`.
- Production / test lines: ~275 / ~470. Full suite: 3635 passed.
- Not like-for-like: most of its misses trace to the thinner spec, not to building by hand. The next baseline should build by hand against the same reviewed spec.

### 2026-11-04 · agent-ops 8b (dispatcher and containers) · driver

- Tickets 03–06.
- Cost:

  | Part | $ |
  |---|---:|
  | Driving session, incl. the re-sign and the PR edit | 6.48 |
  | Driver | 36.29 |

- Driver agents: 9 test-writer, 5 implementer, 13 reviewer, 7 fixer, 2 final-reviewer, 1 pr-writer, 10 Codex correctness. Fix rounds: 03 = 1, 04 = 2, 05 = 1, 06 = 2.
- Human stops:
  1. `04-locked-test-1`: an impossible assertion. The filter keeps mounts ending in `:/root/.config/gh`, but the expected mount ends in `:ro`.
  2. The resume path wasn't covered by any ticket, so the operator added a criterion to ticket 05.
- Final review: round 1 found 1 critical and 17 important findings, mostly structural. Round 2 left 0 open.
- Escaped:
  - All 10 commits were pushed unsigned, 5 with `Co-Authored-By`. The fix was a re-sign and an operator-run force-push.
  - The PR base `feat/onboarding-board` had already merged to main, so a follow-up PR was needed: [#271](https://github.com/jesdi/agent-ops/pull/271).

## How to add a run

1. Measure the driving session and the driver ledger:

   ```sh
   python3 docs/implement-spec/measure.py <session-id> --since <start, UTC ISO> --until <PR createdAt> \
     --ledger <worktree>/.agent/implement/ledger.md
   ```

   Add `--project-dir` when the session ran outside agent-ops. The PR's start time is `gh pr view <n> --json createdAt`.
2. Count the human stops: the driver's `Question …` lines in its output, plus any question the driving session asked.
3. Grade criteria with a blind reviewer. Give it the branch, the tickets' criteria and the spec, but not which method built it. Keep its table with the run.
4. Add the row at the bottom of **Runs** and a section under **Run details**.
