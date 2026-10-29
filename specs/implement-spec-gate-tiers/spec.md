# implement-spec gate tiers — Spec

Source: [general-skills issue #146](https://github.com/jesdi/general-skills/issues/146),
user stories 16–20 and 35, implementation increment 3. Increments 1 (five-ticket limit) and
2 (review scope and two-round cap) are on main.

Terms: the **full gate** is `--gate COMMAND`, the repo's whole check. The **fast gate** is
`--fast-gate COMMAND`: the ticket's locked tests, lint and types. A **candidate** is a ticket
tip that a role (implementer, fixer, test-writer correction) produced and the driver is about
to review. **Landing** is the moment the PR branch moves to a ticket's commit (a fast-forward
to the ticket tip, or a merge commit).

## Requirements

1. The driver takes `--fast-gate COMMAND` beside `--gate COMMAND`. A run without
   `--fast-gate` exits 3 during input validation, names the missing flag, and launches no
   agent.
2. The implementer, fixer and final-fixer prompts name only the fast gate command as the
   check to make green before reporting. No agent prompt names the full gate command or asks
   the agent to run it.
3. The driver runs the fast gate on every candidate before its review. A red fast gate is a
   gate finding with the same round rules as today's red gate: before the cap it starts the
   next fix round, at the cap (round 2) or on a test-writer correction it blocks the ticket.
   The driver runs no full gate on a candidate.
4. Before the PR branch moves, the driver runs the full gate on the exact commit that would
   land, for a fast-forward as well as a merge commit. A green fast record never counts as a
   green full record. The PR branch moves only to a commit with a green full record.
5. A red full gate at landing is a gate finding inside the two-round cap: the PR branch does
   not move; before the cap the ticket gets the next fix round with the full gate record as
   its finding; at the cap the ticket is blocked, its dependents wait and independent tickets
   continue.
6. At the end of the run the driver runs the full gate after the rebase and after the final
   fix batch, as today. The push still requires the reviewed tip to have a green full record.
   The driver runs no fast gate at the end of the run; only the final fixer runs it, on its
   own work.
7. A green record is settled per tier and commit: the driver never runs the same tier twice
   on a commit that has a green record of that tier.
8. The fast gate runs with `IMPLEMENT_SPEC_LOCKED` in its environment, beside
   `IMPLEMENT_SPEC_BASE`: the space-separated locked file paths it checks. For a ticket's
   candidate and that ticket's agents, the ticket's locked files. For the driver's restore
   commit before a test-writer correction, the ticket's locked files plus each restored file;
   that restore commit gets the fast gate, not the full gate. For the final fixer, every
   merged ticket's locked files. The prompts tell each agent the same list.
9. Each gate record names its tier and commit; the ledger line of each gate names its tier.
   The `summary.md` measurement table reports gates run split by tier, one row for fast and
   one for full, in place of the single "gates run" row.

## Scenarios

### Scenario: the fast gate flag is required

- **Given** a clean PR worktree, three valid tickets, `--gate 'make check'` and no
  `--fast-gate`
- **When** the operator starts the driver
- **Then** it exits 3 with an error naming `--fast-gate`, launches no agent and leaves the
  run state unchanged

### Scenario: agents are told only the fast gate

- **Given** a run with `--gate 'make check'` and `--fast-gate 'make fast'`
- **When** the driver starts the implementer, a fixer and the final fixer
- **Then** each prompt names `make fast` as the check to make green, and no agent prompt of
  the run contains `make check`

### Scenario: a candidate gets the fast gate, not the full gate

- **Given** ticket 01's implementer reports DONE on commit `a1b2c3d`
- **When** the driver gates the candidate and starts the review
- **Then** exactly one fast gate record names `a1b2c3d`, no full gate record names
  `a1b2c3d` before landing begins, and the reviewer's prompt points at the fast record

### Scenario: the fast gate gets the ticket's locked files

- **Given** ticket 02 locks `tests/test_csv.py` and `tests/test_quote.py`, and merged ticket
  01 locks `tests/test_header.py`
- **When** the driver fast-gates ticket 02's candidate and starts its fixer
- **Then** the gate's environment has `IMPLEMENT_SPEC_LOCKED='tests/test_csv.py
  tests/test_quote.py'`, and the fixer's prompt names the same two files

### Scenario: the restore commit gets the fast gate with the restored files

- **Given** ticket 02 locks `tests/test_csv.py`, and before a test-writer correction the
  driver restores merged ticket 01's `tests/test_header.py` in commit `r1r2r3r`
- **When** the driver gates `r1r2r3r`
- **Then** a fast record names `r1r2r3r` with `IMPLEMENT_SPEC_LOCKED` holding both
  `tests/test_csv.py` and `tests/test_header.py`, and no full record names `r1r2r3r`

### Scenario: the final fixer's fast gate covers every merged ticket

- **Given** merged tickets 01 and 02 lock `tests/test_header.py` and `tests/test_csv.py`
- **When** the driver starts the final fixer
- **Then** its prompt and its fast gate's `IMPLEMENT_SPEC_LOCKED` name both files

### Scenario: a red fast gate before the cap starts a fix round

- **Given** ticket 01 at round 0 and its candidate's fast gate exits 1
- **When** the driver acts on the gate
- **Then** no review runs on that candidate, round 1 starts with the fast gate record as its
  critical `gate` finding, and no full gate runs on that candidate

### Scenario: a red fast gate at the cap blocks

- **Given** ticket 01 has completed fix rounds 1 and 2
- **When** the fast gate on its round-2 candidate exits 1
- **Then** ticket 01 is blocked, does not reach landing, and no full gate runs on it

### Scenario: a fast-forward landing runs the full gate on the ticket tip

- **Given** ticket 01's reviewed tip `a1b2c3d` has a green fast record, and the PR branch
  has not moved since ticket 01 branched, so its landing is a fast-forward
- **When** the driver lands ticket 01
- **Then** a full gate record names `a1b2c3d` with exit 0 before the PR branch moves to
  `a1b2c3d`

### Scenario: a merge-commit landing runs the full gate on the merge commit

- **Given** ticket 02's reviewed tip has a green fast record and ticket 01 merged after
  ticket 02 branched, so its landing is a merge commit `m1m2m3m`
- **When** the driver lands ticket 02
- **Then** a full gate record names `m1m2m3m` with exit 0 before the PR branch moves, and no
  full gate ran on ticket 02's tip itself

### Scenario: a red full gate at landing before the cap starts a fix round

- **Given** ticket 01 at round 0 with a green fast record, and the full gate on its landing
  commit exits 1
- **When** the driver acts on the landing gate
- **Then** the PR branch does not move, round 1 starts with the full gate record as its
  critical `gate` finding, and after the fix the new candidate gets a fast gate, a re-review
  and a new full gate at landing

### Scenario: a red full gate at landing at the cap blocks

- **Given** ticket 01 has completed fix rounds 1 and 2, ticket 02 depends on 01, and
  independent ticket 03 is ready
- **When** the full gate on ticket 01's landing commit exits 1
- **Then** the PR branch does not move, ticket 01 is blocked without a third round, ticket
  02 waits, and ticket 03 continues

### Scenario: the end of the run uses the full gate

- **Given** every ticket is merged and the final fix batch made commit `f1f2f3f`
- **When** the driver gates the rebased tip and then `f1f2f3f`
- **Then** both records are full gate records, no fast gate record names either commit, and
  the push happens only when the reviewed tip has a green full record

### Scenario: a green record is not run again

- **Given** commit `a1b2c3d` has a green fast record and a green full record
- **When** the driver would gate `a1b2c3d` again on either tier, for example after a resumed
  run
- **Then** neither command runs again, and the tier counts in `state.json` do not change

### Scenario: a green fast record does not stand in for a full gate

- **Given** commit `a1b2c3d` has a green fast record and no full record
- **When** the driver lands it as a fast-forward
- **Then** the full gate runs on `a1b2c3d` before the PR branch moves

### Scenario: the summary and ledger split gates by tier

- **Given** a run of two tickets that ran three fast gates and three full gates
- **When** the run writes `summary.md` and its ledger
- **Then** the measurement table has a row for fast gates with 3 and a row for full gates
  with 3 and no single "gates run" row, and each gate's ledger line names `fast` or `full`

## Out of scope

- Removing the correctness role, mutation proofs, or reshaping the final review (increment 4).
- Question kinds, locked-test arbitration, the landing locked-file check and the restore
  machinery (increment 5).
- What the fast gate command runs: the operator supplies it.
- More than one PR from a run.
