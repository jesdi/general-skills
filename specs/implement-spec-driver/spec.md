# implement-spec driver — Spec

Terms:

- The **driver** is `skills/implement-spec/run.sh` and the Python it bootstraps.
- The **PR worktree** is the git worktree the driver is started in; its branch is the
  **PR branch**.
- A **slot** is a worktree an agent runs in. Slot 1 is the PR worktree; slots 2..N live under
  `.agent/worktrees/slot-K/`.
- A **role** is one of explorer, test-writer, implementer, reviewer, correctness, fixer,
  final-reviewer, conflict. An **agent** is one blocking CLI session for one role.
- A **report** is the JSON file an agent writes at the path the driver names.
- A **gate record** is the driver's log of one run of the `--gate` command on one commit.
- A **model entry** is `provider/model@effort`, the agent-ops grammar; the provider picks
  the CLI (`anthropic` runs `claude -p`, `openai` runs `codex exec`).
- A **stop** is a bounded human choice; a **question** is one entry of the decisions file.

Unless a scenario says otherwise: the repository is `demo`, the PR branch is `feat/csv`,
`.agent/` is gitignored, the gate is `make check`, the models file lists `claude-opus-5@medium` first for every
role and `openai/gpt-sol@medium` first for correctness, `--parallel` is 2,
and `.agent/tickets/` holds three tickets: `01-export-rows.md` (Blocked by: None, Seam
`export_csv()`, Touches `demo/export.py: export_csv`, two criteria), `02-quote-fields.md`
(Blocked by: 01, Seam `export_csv()`, Touches `demo/export.py: export_csv`, one criterion,
Risk: persistence), `03-cli-flag.md` (Blocked by: None, Seam `demo export` CLI, Touches
`demo/cli.py: main`, one criterion).

## Requirements

1. **Ticket validation before any agent.** The driver reads every ticket file, lists every
   error with file and line, and exits 3 without launching an agent when any is found: a
   missing `# NN:` title, `Blocked by`, `Seam`, or criterion (unless Seam is `none — refactor`),
   a missing `Touches` when the set has more than one ticket, a `Blocked by` cycle, or a
   number that names no ticket. `Risk` and `Suspicions` are optional. The driver never edits
   a ticket file.
2. **Preconditions.** The driver exits 3 before any agent when `--gate` is missing, when
   `.agent` is not gitignored, when the CLI of any role's first model entry is not on PATH
   (for the correctness role: the entry requirement 13 selects), or when the PR worktree is
   not clean.
3. **Frontier and holds.** A ticket is ready when every `Blocked by` ticket is merged and no
   running ticket's `Touches` names a function it names. The driver runs at most
   `--parallel` tickets at once, default 2, and prints the graph's maximum frontier width at
   start.
4. **Slots.** Slot 1 is the PR worktree. Slots 2..N are created once per run, prepared once
   with `--prepare` when given, and reused across tickets. A non-zero `--prepare` fails the
   run with exit 3 before any agent.
5. **Role sequence per ticket.** Test-writer (skipped for `none — refactor`), implementer,
   gate by the driver on the implementer's final tip, reviewer and, for a ticket with a
   `Risk` line, a correctness pass in parallel, fix loop, merge. The driver verifies `HEAD`
   of the slot before and after each agent and resets the slot to the recorded tip only when
   `HEAD` moved to a commit it did not expect.
6. **Report contract.** Each agent writes one JSON report at
   `.agent/implement/<NN>/<role>-<round>.json`. The driver validates the common fields
   (`status` in DONE, DONE_WITH_CONCERNS, NEEDS_CONTEXT, BLOCKED; `commits`; `files`;
   `concerns`) and the role's fields (test-writer `locked`, `red`; implementer `green`;
   reviewer and correctness `findings` with `severity`, `file`, `line`, `title`, `evidence`,
   `kind`, and round-1 `mutations`; fixer `ticket` per commit), and cross-checks every SHA in
   `commits` against the branch, every locked file against the test-writer's commit, and
   every `red` line against the agent's log. A violation reruns the agent once with the error
   appended; a second violation marks the ticket BLOCKED.
7. **Locked tests.** The driver records the test-writer's commit and locked files and, before
   every review, checks `git diff <test-writer-sha>..HEAD -- <locked files>` is empty. A
   non-empty diff is a critical finding added to the review. The locked files of tickets
   that are already merged are protected at the landing: under the merge lock, each such
   file must be the same in the tree that is about to land as in the PR tip at that moment,
   unless the landing ticket's own test-writer or sanctioned correction lists the file as
   locked. A difference refuses the landing with `locked file changed: <path>`, and the PR
   branch does not move. A test-writer report that changes a merged ticket's locked file
   without listing it is a violation.
8. **Gate by the driver.** The driver runs the `--gate` command on a clean tree at the commit
   it is about to trust, with `IMPLEMENT_SPEC_BASE=<PR branch>` and `PYTHONPYCACHEPREFIX` in
   the environment, and writes a gate record with start and end time, commit, status, exit
   code and the full log. A green record for a commit is never repeated. A red gate on an
   implementer tip marks the ticket BLOCKED with the record.
9. **Fix loop.** Critical or important findings start a round: a fix, a gate on the round's
   tip, a re-review scoped to the changed lines. Rounds 1 and 2 resume the implementer on the
   same model; rounds 3 and 4 use a fresh implementer on the next entry of the implementer's
   model list. Mutation proofs repeat only
   for a changed production line. After round 4 with open findings the ticket is BLOCKED and
   the run continues on tickets it does not block. Minor findings go to the ledger.
10. **Fixture corrections.** A finding with `kind: fixture` dispatches one test-writer
    correction, records the file and reason in the ledger, and does not count as a round.
11. **Merge.** A ticket that passes review is merged into the PR branch through git plumbing,
    and the PR branch is never checked out in a slot that runs a ticket (the merge gate may
    check a merge commit out, detached, in the merging ticket's own slot after its agents
    ended). A fast-forward of a tip with a green gate record skips the
    merge gate; any other merge runs it. A conflict dispatches the conflict agent once, then
    the gate. The ticket branch is deleted while its commits are ancestors of the PR tip.
12. **Timeouts.** Each role has a timeout in minutes (test-writer 30, implementer 90,
    reviewer 45, correctness 30, fixer 60, final reviewer 60, conflict 30, explorer 30),
    overridable by a flag. A timeout is not an agent fault: the driver keeps the slot as it
    is and reruns the role once with double the timeout and a line saying the earlier
    attempt timed out and its uncommitted work is in the tree. A second timeout marks the
    ticket BLOCKED.
13. **Models.** `--models <file>` names a JSON file with one ordered list of model entries
    per role; the skill ships a default file. A role runs on the first entry of its list. The
    correctness pass runs on the first entry of its list whose provider differs from the
    implementer's. The driver exits 3 before any agent when the list has no such entry or
    when that entry's CLI is not on PATH: the correctness role is not exempt from
    requirement 2, and the pass is never folded into the reviewer. The driver records every agent's model entry and cost when the CLI reports it.
14. **Fixed prompts.** Each role's prompt is a template file in the skill directory filled
    with pointers and SHAs only; the driver composes no prose. Every prompt ends with the
    report path, the report schema, and the rule never to stash, reset or check out.
15. **State and resume.** `state.json` under `.agent/implement/` is written atomically after
    every transition and is the only resume input. Rerunning the driver in a PR worktree that
    has one resumes: a recorded fact that git contradicts (a tip not on its branch, a missing
    slot) returns that ticket to its last verified phase; a recorded green gate on an
    existing commit stays settled.
16. **Ledger.** `ledger.md` gets one plain sentence per event, written by the driver from
    report fields and rulings. No agent writes it, and the driver never reads it.
17. **Human stops.** A contradicted locked test (implementer BLOCKED with evidence), open
    findings after round 4, and final-review triage each become questions
    in `.agent/implement/decisions.json` with id, title, why, evidence pointers, 2-4 options
    with id and description, and a recommended option. The driver renders
    `.agent/implement-review.html` from the review-page template and reads answers from
    `.agent/implement-answers.json` (`v`, `stage`, `submitted`, `answers` by question id).
    Locally `--answer <qid>=<optid>` writes the same file. Each answer becomes a ledger
    ruling.
18. **Modes.** Without `--unattended` the driver pauses at a stop, prints the questions, and
    exits 2; the operator answers and reruns. When everything is validated it stops before
    the push with exit 0, prints the branch state and the push command, and pushes only
    when rerun with `--push`. With `--unattended` it writes `stage.json`
    (`awaiting-review`, the page path, the question count) and exits 2 at a stop; on rerun
    with a submitted answers file it continues; it pushes and opens the PR by itself when
    every ticket is merged, no finding is open, the last gate is green and the tip equals the
    reviewed tip. Nothing is pushed before that in either mode.
19. **End of run.** Rebase onto the fetched base tip (conflict agent once, then gate); tidy by
    script into one squashed commit per ticket in merge order with a tree-equality check;
    gate on the tidied tip; final review through review-diff on the final-reviewer's model with ledger
    minors and rulings as pointers; one fixer batch with each commit named `fix(NN): …`;
    scoped review of `<reviewed tip>..HEAD` and one gate; at most two review rounds; no
    history rewrite after the final review. A repository with no `origin` remote skips only
    the rebase and the push: the tidy, the gate and the final review still run. With
    `--unattended` or `--push`, a missing `origin` exits 3 before any agent.
20. **Outputs.** Exit 0 when everything merged (and pushed when unattended), 1 with BLOCKED
    tickets, 2 parked, 3 usage or validation error. `summary.md` and stdout carry plain
    sentences plus a table: gates run, agents per role and model entry, wall time per role,
    fix rounds per ticket, human stops, cost summed from the CLIs' usage output.
21. **Skill and tickets.** `SKILL.md` becomes a launcher that runs the driver and relays its
    summary; the method moves to the prompt templates and a README. The to-tickets template
    gains optional `Risk` and `Suspicions` lines.

## Scenarios

### Scenario: a ticket without Touches stops the run before any agent

- **Given** `02-quote-fields.md` has no `Touches` line
- **When** the driver starts
- **Then** it prints `02-quote-fields.md: missing Touches` with the line of the `Seam` entry,
  launches no agent, creates no branch, and exits 3

### Scenario: a Blocked-by cycle is rejected

- **Given** `01` is `Blocked by: 03` and `03` is `Blocked by: 01`
- **When** the driver starts
- **Then** it prints one error naming both files and exits 3

### Scenario: an unignored .agent directory is refused

- **Given** `.gitignore` does not match `.agent`
- **When** the driver starts
- **Then** it exits 3 with `.agent is not gitignored` and launches no agent

### Scenario: the frontier respects blocking and Touches

- **Given** the three default tickets and `--parallel 2`
- **When** the run starts
- **Then** it prints `max frontier width: 2`, starts `01` in slot 1 and `03` in slot 2, and
  holds `02` until `01` is merged

### Scenario: a Touches overlap holds a ready ticket

- **Given** `03` is changed to touch `demo/export.py: export_csv` and `--parallel 2`
- **When** the run starts
- **Then** only `01` starts; `03` starts after `01` is merged, branched from the updated PR tip

### Scenario: slot 2 is prepared once and reused

- **Given** `--prepare "ln -s ../../../node_modules node_modules"` and tickets `03` then `02`
  both land in slot 2
- **When** the run completes
- **Then** the prepare command ran exactly once, in `.agent/worktrees/slot-2/`

### Scenario: a failing prepare stops the run

- **Given** `--prepare "false"`
- **When** the driver creates slot 2
- **Then** it exits 3 before any agent, naming the slot and the command

### Scenario: the test-writer's red is cross-checked

- **Given** the test-writer report for `01` lists `red` line
  `FAILED tests/test_export.py::test_rows - AttributeError` and the agent's log does not
  contain it
- **When** the driver validates the report
- **Then** it reruns the test-writer once with the message `red line not found in log`, and
  on a second mismatch marks `01` BLOCKED

### Scenario: a schema violation gets one retry

- **Given** the implementer for `01` writes a report with `status: "FINISHED"`
- **When** the driver validates it
- **Then** it reruns the implementer once with the validation error appended, and a second
  violation marks `01` BLOCKED with that error in the ledger

### Scenario: a timeout keeps the work and doubles the limit

- **Given** the implementer for `01` hits its 90-minute timeout with uncommitted edits in
  slot 1
- **When** the driver continues
- **Then** slot 1 keeps the edits, the implementer reruns once with a 180-minute timeout and
  a prompt line saying the earlier attempt timed out and its work is in the tree, and a
  second timeout marks `01` BLOCKED

### Scenario: a moved HEAD resets the slot

- **Given** the implementer for `01` exits with no report and `HEAD` of slot 1 is on
  `feat/csv` instead of `ticket/01`
- **When** the driver continues
- **Then** it resets slot 1 to the last recorded tip of `ticket/01`, records the broken rule
  in the ledger, and reruns the implementer once

### Scenario: the driver runs the gate once per trusted commit

- **Given** `01` is implemented at commit `a1`, reviewed clean, and merged as a fast-forward
- **When** the ticket completes
- **Then** exactly one gate record exists for `a1`, at
  `.agent/implement/01/gate-a1.log` with start time, end time, `a1`, an empty status, exit 0,
  and no merge gate was run

### Scenario: a red gate blocks the ticket

- **Given** the gate on the implementer's tip of `01` exits 1
- **When** the driver reads the record
- **Then** `01` is BLOCKED, the ledger names the record path, and no reviewer is launched

### Scenario: a locked file changed

- **Given** the implementer's tip of `01` modifies `tests/test_export.py`, a locked file
- **When** the driver prepares the review
- **Then** the review receives a critical finding `locked file changed:
  tests/test_export.py` before the reviewer runs

### Scenario: a risky ticket gets the cross-provider correctness pass

- **Given** `02` has `Risk: persistence`, the implementer ran on `claude-opus-5@medium`, the
  correctness list is `[openai/gpt-sol@medium, claude-opus-5@medium]`, and `codex` is on
  PATH
- **When** `02` reaches review
- **Then** the reviewer runs through `claude -p` and the correctness pass through
  `codex exec` on `gpt-sol` at the same time, and both reports' findings are merged with
  their severities

### Scenario: a missing correctness CLI stops the run

- **Given** the correctness list is `[openai/gpt-sol@medium, claude-opus-5@medium]`, the
  implementer runs on `claude-opus-5@medium`, and `codex` is not on PATH
- **When** the driver starts
- **Then** it exits 3 naming the correctness role and `codex`, and launches no agent

### Scenario: a repository with no origin still gets its final review

- **Given** the repository has no `origin` remote, every ticket merged, and no `--unattended`
  and no `--push`
- **When** the run ends
- **Then** no rebase ran, the branch is tidied into one commit per ticket, one gate ran on
  the tidied tip, the final review ran, nothing is pushed, and the driver exits 0

### Scenario: a push without an origin is refused at the start

- **Given** the repository has no `origin` remote and `--unattended`
- **When** the driver starts
- **Then** it exits 3 naming the missing remote and launches no agent

### Scenario: a fix loop escalates at round 3

- **Given** the implementer list is `[claude-opus-5@medium, claude-fable-5-1@high]`, `01`'s
  review reports one important finding, and the re-reviews after rounds 1 and 2 still
  report it
- **When** round 3 starts
- **Then** a fresh implementer runs on `claude-fable-5-1@high`, and the ledger records
  `01 round 3: escalated to claude-fable-5-1@high`

### Scenario: round 4 ends the ticket

- **Given** the re-review after round 4 of `01` still reports the finding
- **When** the driver reads it
- **Then** `01` is BLOCKED with the finding listed, `02` (blocked by `01`) never starts, `03`
  runs to merge, and the run exits 1

### Scenario: a fixture finding does not consume a round

- **Given** `01`'s review reports one finding with `kind: fixture` and no other
- **When** the driver handles it
- **Then** one test-writer correction runs, the ledger records the file and reason, the
  round counter stays at 0, and the review continues

### Scenario: mutation proofs repeat only for changed lines

- **Given** round 1's reviewer reported mutations for `demo/export.py:12` and
  `demo/export.py:30`, and round 2's fix changed only line 30
- **When** the round-2 re-review is prompted
- **Then** the prompt asks for the mutation proof of line 30 only

### Scenario: a merge conflict dispatches the conflict agent once

- **Given** `03` is merged first and `01`'s merge into `feat/csv` conflicts in `demo/cli.py`
- **When** the driver merges `01`
- **Then** the conflict agent runs once in slot 1, the gate runs on the merge commit, and the
  ledger records the resolved file

### Scenario: a contradicted locked test pauses locally

- **Given** the implementer for `01` reports BLOCKED with `locked test contradicts spec:
  tests/test_export.py::test_header`
- **When** the driver runs without `--unattended`
- **Then** it writes `decisions.json` with one question `01-locked-test` offering
  `overrule-test` and `overrule-implementer` with `overrule-implementer` recommended, writes
  `implement-review.html` with one `data-q` block, prints the question, and exits 2

### Scenario: a local answer resumes the ticket

- **Given** the previous scenario's state
- **When** the operator runs the driver with `--answer 01-locked-test=overrule-test`
- **Then** `.agent/implement-answers.json` holds `{"01-locked-test": "overrule-test"}` with
  `submitted: "changes"`, the ledger records `Ruling: 01 locked test overruled`, one
  test-writer correction runs, and the implementer resumes

### Scenario: unattended mode parks on the review page

- **Given** the contradicted locked test and `--unattended --stage-json .agent/stage.json`
- **When** the driver stops
- **Then** `stage.json` is `{"stage": "implement", "status": "awaiting-review",
  "artifact": ".agent/implement-review.html", "open_questions": 1}` and the exit code is 2

### Scenario: local mode stops before the push

- **Given** every ticket merged, the final review reports no finding, the last gate is green,
  and no `--unattended` and no `--push`
- **When** the run ends
- **Then** nothing is pushed, the driver prints the branch tip, the reviewed tip and the
  rerun command with `--push`, and exits 0

### Scenario: local rerun with --push pushes once

- **Given** the previous scenario's state
- **When** the operator reruns the driver with `--push`
- **Then** the driver pushes `feat/csv` once, opens the PR with the deterministic body, and
  exits 0 without running any agent or gate

### Scenario: unattended mode pushes when clean

- **Given** every ticket merged, the final review reports no finding, the last gate is green,
  and `--unattended`
- **When** the run ends
- **Then** the driver pushes `feat/csv` once, opens a PR with a body listing the
  ticket-to-commit map and rulings, and exits 0

### Scenario: unattended mode does not push with a BLOCKED ticket

- **Given** `01` BLOCKED after round 4 and `--unattended`
- **When** the run ends
- **Then** nothing is pushed, `stage.json` has `status: "blocked"` naming `01`, and the exit
  code is 1

### Scenario: resume trusts only what git confirms

- **Given** `state.json` records `01` at phase `review` with tip `a1`, and `a1` is not on
  branch `ticket/01`
- **When** the driver reruns
- **Then** `01` returns to phase `implement` from the test-writer's commit, and the ledger
  records the reason

### Scenario: resume keeps a settled gate

- **Given** `state.json` records a green gate for `a1` and `a1` exists on `ticket/01`
- **When** the driver reruns
- **Then** no gate runs again for `a1`

### Scenario: the tidy preserves the tree

- **Given** `feat/csv` holds seven commits from tickets `01`, `03`, `02` merged in that order
- **When** the end of run tidies the branch
- **Then** it holds three commits titled from the ticket titles in order `01`, `03`, `02`,
  each body naming its ticket file, and `git rev-parse HEAD^{tree}` equals the tree before
  the tidy

### Scenario: the final review's fixes stay on top

- **Given** the final review reports two findings owned by `02`
- **When** the fixer batch completes
- **Then** the branch has the three ticket commits plus one or more `fix(02): …` commits, a
  scoped review covers `<reviewed tip>..HEAD`, one gate runs on the new tip, and no commit
  before the reviewed tip changed

### Scenario: the summary carries the measurements

- **Given** a completed run with three tickets, one fix round on `01`, and one stop
- **When** the driver exits
- **Then** `summary.md` holds a table with gates run, agents per role and model entry, wall time per
  role, fix rounds per ticket (`01: 1`, `02: 0`, `03: 0`), human stops (1), and total cost
  summed from the CLIs' usage output

### Scenario: the skill is a launcher

- **Given** an operator invokes `/implement-spec` in a session
- **When** the session follows `SKILL.md`
- **Then** it runs `run.sh` with the flags the operator gave and relays `summary.md`, making no
  decision of its own

## Out of scope

Carried from the proposal: the Workflow tool, a dispatcher-resident driver, per-role provider
mixing, a free-text question channel, a round-2 ruling, folding fixes after the final
review, language-specific environment knowledge, gate dedupe, the agent-ops side, an eval
harness, Windows.
