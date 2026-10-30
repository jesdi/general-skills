# implement-spec questions and locked tests — Spec

Source: [general-skills issue #146](https://github.com/jesdi/general-skills/issues/146),
user stories 11–15 and 27–35, implementation increment 5. Increments 1–3 are on main;
increment 4 (one ticket reviewer, three-lens final review) is
[PR 169](https://github.com/jesdi/general-skills/pull/169).

Terms: a **locked file** is a file a test-writer listed in `locked`. A ticket's **locked set**
is its own locked files plus the locked files of every other ticket whose test-writer has
run, merged or not. A **dispute** is an implementer or fixer
report with status `BLOCKED` and a concern that starts with `locked test contradicts spec:`.
A **stop** is an accepted report with status `BLOCKED` or `NEEDS_CONTEXT` that is not a
dispute. A **rejection** is a report the driver refuses. A role's **second entry** is the
entry after the one that ran in the role's model list, wrapping to the first. The **end of
run** is the tidy, the final review, its fix batch, the rebase and the pr-writer; for
questions it is the ticket `final`.

## Requirements

1. The driver has exactly two question kinds, `spec-ambiguous` and `ticket-blocked`, with ids
   `<NN>-spec-ambiguous-<n>` and `<NN>-ticket-blocked-<n>` (`final-…` for the end of run).
   The kinds `finding`, `locked-test`, `locked-test-again`, `review-stopped`,
   `review-rejected`, `implementer-stopped`, `fixer-stopped`, `merged-locked-file` and
   `final-finding` are gone, with their options, prompts and rulings.
2. An open question blocks only its ticket and the tickets that depend on it. Every other
   ticket runs and lands in the same run. The end of run waits until every ticket has merged.
   The run exits 2 when it ends with an open question; it ends only when no ticket can
   proceed. The driver never waits for an answer during a run: when the run ends, every open
   question is in `decisions.json` and on one review page, each with its evidence (the
   agents' reports and ledger lines), so the operator answers them all in one pass. An
   answer resumes only the ticket of that question.
3. A dispute goes to the reviewer for arbitration before any question. The arbitration runs
   the reviewer on the ticket's reviewer entry with the ticket, the spec, the disputed tests
   and the concern; its report gives, for each disputed test, the `requirement` it checks, a
   `ruling` of `test-wrong` or `code-wrong`, and a `reason`. Arbitration is not a fix round
   and does not count against the cap.
4. On `test-wrong`, one test-writer correction runs with the ruling as its finding, then the
   role that disputed runs again on the corrected tests: the implementer, or the fixer of the
   same round. On `code-wrong`, the role that disputed runs again with the ruling in its
   prompt and must make the locked test green.
5. A test is arbitrated once per ticket, counted by its locked file (the part of the test
   id before `::`), so a renamed or parametrized test of an arbitrated file counts as the same
   test. A dispute that names no locked file of the ticket is rejected. A second dispute on the same test in the same ticket
   raises one `spec-ambiguous` question that names the test, the requirement from the first
   ruling and the two readings: the first ruling's reason and the second dispute's concern.
   Its options are `keep-test` (the role that disputed runs again against the test),
   `correct-test` (one more correction, then the role runs again) and `edit-spec` (the ticket
   stays blocked, with the ruling in the ledger). It has no recommended option.
6. A test is corrected at most once per ticket, whether the correction came from arbitration
   or from a review finding of kind `fixture` or `test`. A second correction needed for the
   same test raises `spec-ambiguous` as in requirement 5, with the review finding as the
   second reading.
7. Any agent that stops or is rejected runs once more, on its role's second entry, with the
   reason in its prompt: the agent's concerns for a stop, the driver's error for a rejection.
   This replaces today's rerun of a rejected report on the same entry. A timeout or a moved
   branch is still rerun on the same entry, as today.
8. When the rerun of requirement 7 stops or is rejected too, the ticket blocks with one
   `ticket-blocked` question whose subject names the role and the agent's reason. Its options
   are `retry` (recommended: the role runs again on its first entry from the tip the ticket
   left, with the same one rerun) and `edit` (the ticket stays blocked, with the ruling in the
   ledger, so the operator can edit the spec or ticket).
9. After the implementer, a fixer, the ticket conflict agent, the final fixer and the rebase
   conflict agent report, the driver checks the locked set at the role's tip: the ticket's own
   locked files against its last test-writer commit, every other ticket's against the merge
   base of the ticket branch and the PR tip (for the end of run, every ticket's locked files
   against the tidied tip). The baseline does not move with a rerun or a `retry`, so a breach
   left on the tip is still found. A changed locked file rejects the
   report, naming the file, so requirements 7 and 8 apply: one rerun on the second entry,
   then `ticket-blocked`. The check runs on DONE, BLOCKED and NEEDS_CONTEXT reports alike.
10. Only the test-writer may change a locked file, its own ticket's or another ticket's,
    merged or not, and it must list each changed locked file in `locked`; a report that changes one and does not
    list it is rejected, as today. The reviewer gets the diff of every other ticket's locked
    file the test-writer changed and judges it against the spec: a change the spec does not
    support is a `fixture` finding.
11. Gone: the locked-file findings before each review, the merged-ticket findings before each
    review, `merged-locked-file` with `restore` and `adopt`, the restore commits before a
    correction and their gate, the `held` references, restored files in
    `IMPLEMENT_SPEC_LOCKED`, and the final review's driver locked-file findings. A finding
    names its lens `spec`, `correctness` or `structure`. At landing, under the merge lock, the
    driver runs the check of requirement 9 on the commit about to land against every other
    ticket's locked files as the PR tip has them: a changed file blocks the ticket with one
    `ticket-blocked` question naming it, and the PR branch does not move. A ticket's earlier
    edit of a file that another ticket locked later cannot land unseen.
12. When a finding is still open after the second round of the final review, the end of run
    raises one `final-ticket-blocked-<n>` question per finding. `retry` runs one more fix
    batch and scoped review on the open findings; `edit` keeps the end of run blocked.
    Nothing is pushed while such a question is open.
13. `summary.md` keeps its measurement table: fast gates, full gates, agents per role and
    entry, wall time per role, fix rounds per ticket, human stops, cost. Arbitration runs
    count as `reviewer` agents.
14. `state.json` schema is 4. A run started by an older driver with an open question of a
    removed kind exits 3 before any agent, naming each such question and saying to answer it
    with the driver that asked, or to start the run again. Answered questions of removed kinds
    keep their ruling sentences in the ledger and the PR body.
15. The README's question-kinds table lists only the two kinds and says how `final-finding`
    became a `ticket-blocked` question of the end of run. Its non-negotiable checks are
    exactly: demonstrated red plus green for locked tests; locked files changed only by the
    test-writer; the full gate at landing and at the end of run; the ticket reviewer and the
    final correctness lens on another provider than the implementer. SKILL.md and
    ARCHITECTURE.md describe the same.

## Scenarios

### Scenario: only two kinds exist

- **Given** the driver's question kinds
- **When** a test lists them
- **Then** they are exactly `spec-ambiguous` and `ticket-blocked`

### Scenario: an open question does not stop an independent ticket

- **Given** tickets 01 and 02 with no edge between them, and ticket 01's implementer and its
  rerun on the second entry both report `BLOCKED: the ledger schema is not in the spec`
- **When** the run ends
- **Then** ticket 01 is blocked with one question `01-ticket-blocked-1`, ticket 02 has merged
  into the PR branch, no end of run ran, and the driver exits 2

### Scenario: a dependent ticket waits on the question

- **Given** ticket 03 is blocked by 01, and 01 has the open question `01-ticket-blocked-1`
- **When** the run ends
- **Then** ticket 03 never started, and the driver exits 2

### Scenario: an answer resumes only its ticket

- **Given** `01-ticket-blocked-1` is open, ticket 02 has merged and ticket 03 waits on 01
- **When** the operator runs the driver with `--answer 01-ticket-blocked-1=retry`
- **Then** ticket 01's implementer runs on its first entry from the tip it left, ticket 02
  runs no agent, and ticket 03 starts after 01 merges

### Scenario: arbitration rules the test wrong

- **Given** ticket 01's implementer reports `BLOCKED` with the concern
  `locked test contradicts spec: tests/test_csv.py::test_quote: the spec quotes with '`
- **When** the reviewer arbitrates with ruling `test-wrong`, requirement `R3`, reason
  `R3 says single quotes`
- **Then** one test-writer correction runs with that ruling as its finding, then the
  implementer runs again, no question is asked, and the fix round count is still 0

### Scenario: arbitration rules the code wrong

- **Given** the same dispute
- **When** the reviewer rules `code-wrong` with reason `R3 says double quotes`
- **Then** no test-writer runs, the implementer runs again with `R3 says double quotes` in its
  prompt, and no question is asked

### Scenario: a fixer's dispute

- **Given** ticket 01 in fix round 2, and the fixer disputes `tests/test_csv.py::test_quote`
- **When** the reviewer rules `test-wrong`
- **Then** one correction runs, then the fixer of round 2 runs again, and no round 3 starts

### Scenario: a second dispute on the same test is a spec question

- **Given** ticket 01's dispute on `tests/test_csv.py::test_quote` was ruled `code-wrong` with
  reason `R3 says double quotes`
- **When** the implementer disputes the same test again with `the example in R3 uses '`
- **Then** no arbitration runs, and one question `01-spec-ambiguous-1` names the test, `R3`,
  the reading `R3 says double quotes` and the reading `the example in R3 uses '`, with the
  options `keep-test`, `correct-test` and `edit-spec` and none recommended

### Scenario: a renamed test of the same file is the same test

- **Given** ticket 01's dispute on `tests/test_csv.py::test_quote` was arbitrated
- **When** the implementer disputes `tests/test_csv.py::test_quote[single]`
- **Then** no arbitration runs, and one `spec-ambiguous` question is raised

### Scenario: a breach survives a retry

- **Given** ticket 01 is blocked with a `ticket-blocked` question naming `tests/test_csv.py`,
  which its implementer changed
- **When** the operator answers `retry` and the implementer commits nothing
- **Then** the report is rejected naming `tests/test_csv.py`

### Scenario: an in-flight ticket's locked file

- **Given** ticket 01 has locked `tests/test_ledger.py` and has not merged, and ticket 02's
  test-writer changes it without listing it
- **When** it reports
- **Then** the report is rejected naming the file

### Scenario: a dispute on another test is arbitrated

- **Given** ticket 01's dispute on `test_quote` was arbitrated
- **When** the implementer disputes `tests/test_escape.py::test_escape`, another locked file
- **Then** the reviewer arbitrates it, and no question is asked

### Scenario: a test gets one correction per ticket

- **Given** ticket 01's `test_quote` was corrected after a `test-wrong` ruling
- **When** the next review reports a `fixture` finding on `test_quote`
- **Then** no correction runs, and one `spec-ambiguous` question names the test with the
  review finding as the second reading

### Scenario: a stopped agent reruns on its second entry

- **Given** the implementer list `["claude-sonnet-5-5@medium", "claude-opus-5-5@medium"]` and
  ticket 01's implementer reports `NEEDS_CONTEXT: which CSV dialect?`
- **When** the driver acts on the report
- **Then** the implementer runs again on `claude-opus-5-5@medium` with `which CSV dialect?` in
  its prompt, and no question is asked

### Scenario: a rejected report reruns on the second entry

- **Given** the reviewer list `["openai/gpt-6-astra@medium", "claude-opus-5-5@medium"]` and a
  reviewer report with no `findings`
- **When** the driver rejects it
- **Then** the reviewer runs again on `claude-opus-5-5@medium` with the error in its prompt

### Scenario: a second stop blocks the ticket with one question

- **Given** ticket 01's implementer stopped and its rerun on the second entry reports
  `BLOCKED: no write access to fixtures/`
- **When** the driver acts on the rerun
- **Then** ticket 01 is blocked with one `ticket-blocked` question whose subject names the
  implementer and `no write access to fixtures/`, options `retry` (recommended) and `edit`

### Scenario: edit keeps the ticket blocked

- **Given** `01-ticket-blocked-1` is open
- **When** the operator answers `edit`
- **Then** ticket 01 stays blocked, the ledger has the ruling, and no agent of 01 runs

### Scenario: an implementer that changes a locked file is rerun

- **Given** ticket 01 locks `tests/test_csv.py`, and its implementer's commits change it
- **When** the implementer reports DONE
- **Then** the report is rejected naming `tests/test_csv.py`, and the implementer runs again on
  its second entry

### Scenario: a second breach blocks the ticket

- **Given** the rerun of that implementer leaves `tests/test_csv.py` changed since the role's
  start commit
- **When** it reports
- **Then** ticket 01 is blocked with one `ticket-blocked` question naming `tests/test_csv.py`

### Scenario: a fixer that changes a merged ticket's locked file

- **Given** ticket 01 merged with `tests/test_ledger.py` locked, and ticket 02's fixer changes it
- **When** the fixer reports
- **Then** the report is rejected naming `tests/test_ledger.py`, and the fixer runs again

### Scenario: a conflict agent that changes a locked file

- **Given** ticket 02's merge into the PR branch conflicts, and the conflict agent's commit
  changes ticket 01's locked `tests/test_ledger.py`
- **When** it reports
- **Then** the report is rejected, and the conflict agent runs again on its second entry

### Scenario: an untouched locked file passes

- **Given** ticket 01 locks `tests/test_csv.py`, and its implementer's commits change only
  `csv.py`
- **When** the implementer reports DONE
- **Then** the report is accepted, and the ledger has no locked-file line

### Scenario: the test-writer extends a merged ticket's test

- **Given** ticket 01 merged with `tests/test_ledger.py` locked, and ticket 02's test-writer
  changes it and lists it in `locked`
- **When** ticket 02 is reviewed
- **Then** the reviewer's prompt holds the diff of `tests/test_ledger.py`, no question is
  asked, and the landing check passes because the ticket lists the file

### Scenario: the test-writer must list a merged locked file it changes

- **Given** ticket 02's test-writer changes ticket 01's `tests/test_ledger.py` and does not list
  it in `locked`
- **When** it reports
- **Then** the report is rejected naming the file, and the test-writer runs again on its second
  entry

### Scenario: landing refuses a change to a file another ticket locked later

- **Given** ticket 01's implementer changed `tests/test_ledger.py` before any ticket locked it,
  then ticket 02's test-writer locked it and ticket 02 merged
- **When** ticket 01 lands
- **Then** the PR branch does not move, and ticket 01 is blocked with one `ticket-blocked`
  question naming `tests/test_ledger.py`

### Scenario: no restore before a correction

- **Given** ticket 01 at a `test-wrong` ruling
- **When** the correction runs
- **Then** the branch has no commit `implement-spec: restore the locked files before the
  correction`, and no gate ran between the ruling and the correction

### Scenario: an open final finding becomes a ticket-blocked question

- **Given** the scoped review after the final fix batch reports the important finding
  `ledger write not atomic` at `ledger.py:12`, not a must-fix one
- **When** the driver settles the round
- **Then** one question `final-ticket-blocked-1` names that finding, options `retry`
  (recommended) and `edit`, nothing is pushed, and the driver exits 2

### Scenario: retry on a final finding runs one more batch

- **Given** `final-ticket-blocked-1` is open on `ledger write not atomic`
- **When** the operator answers `retry`
- **Then** one fix batch runs on that finding, then one scoped review

### Scenario: the final fixer may not change a locked file

- **Given** ticket 01's `tests/test_csv.py` is locked, and the final fixer's commit changes it
- **When** it reports
- **Then** the report is rejected naming the file, the final fixer runs again on its second
  entry, and `findings-0.json` has no finding with lens `driver`

### Scenario: the summary keeps its table

- **Given** a run with one arbitration, two fast gates and one full gate
- **When** the run writes `summary.md`
- **Then** the table has the rows `fast gates run` 2, `full gates run` 1, the `reviewer` count
  includes the arbitration, and the rows for wall time, fix rounds, human stops and cost

### Scenario: an older run with an open removed question

- **Given** a `state.json` of schema 3 with the open question `01-locked-test-1`
- **When** the operator starts the driver
- **Then** it exits 3 before any agent, naming `01-locked-test-1`

### Scenario: an older run with answered removed questions

- **Given** a `state.json` of schema 3 whose only `review-stopped` question is answered `retry`
- **When** the operator starts the driver
- **Then** the run resumes, and the PR body's rulings keep that answer's ruling sentence

## Out of scope

- Changing the demonstrated red plus green standard for locked tests.
- Several PRs from one run; any mutation step in the driver.
- The 5-ticket cap in to-openspec and to-tickets (issue #162), and issues #165 and #166.
