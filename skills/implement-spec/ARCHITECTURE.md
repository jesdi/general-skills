# implement-spec driver: architecture

For a person or an agent who changes the driver. `README.md` tells how to use it. The spec is
`specs/implement-spec-driver/spec.md`; the tests in `tests/` are named after its scenarios.

The code has few comments on purpose. A rule that more than one function keeps is written here, one time.
A comment in the code is for a line that looks wrong or removable without it.

## Modules

| Module | It owns |
|---|---|
| `driver.py` | The command line, the checks before the first agent, the walk of the ticket graph, one ticket from its phase to merged or blocked, the landing on the PR branch, the exit code |
| `driver_run.py` | `Run` (what all ticket threads share), one agent with its one rerun, the gate call, the ledger, `Blocked` |
| `driver_fixloop.py` | A gated ticket tip in, a reviewed tip out: review, test-writer correction, fix rounds |
| `driver_final.py` | The end of run: tidy, rebase, gate, final review, one fix batch, push |
| `driver_tickets.py`, `driver_graph.py` | Ticket files in, tickets or errors out; which tickets can start now |
| `driver_agents.py` | Model entries, the CLI command of each provider, prompt templates, the blocking launch |
| `driver_report.py` | Validation of one agent report against its schema, git and the agent's log |
| `driver_review.py` | The locked-file check of a ticket's own tests, the routing of findings, mutation proofs |
| `driver_merge.py` | Merges by git plumbing, the locked-file check of a landing, the move of the PR ref |
| `driver_gate.py` | One run of the gate command and its record |
| `driver_slots.py` | The worktrees that tickets run in, and the `git` helper |
| `driver_state.py` | `state.json` (the resume contract) and the lock of one driver per repository |
| `driver_decisions.py`, `driver_answers.py`, `driver_review_page.py` | Operator questions, their answers, the page that shows them |
| `driver_tidy.py`, `driver_summary.py` | The history with one commit per ticket; `summary.md` |

`driver.py`, `driver_fixloop.py` and `driver_final.py` decide what comes next. The other modules do one thing
and do not know about the run, except `driver_run.py`, which the three deciding modules share.

## Conventions

- **A step that cannot go on raises `Blocked`.** The message says why. `with during("the merge"):` puts the step
  in front. Only two places catch it for good: `driver._run_ticket` (the ticket becomes BLOCKED) and
  `driver_final.run` (the end of run becomes BLOCKED). Another `except Blocked` only cleans up and raises again.
- **A CLI that fails with no report raises `Halted`**, not `Blocked`: the setup of the run is wrong, not the
  ticket. `driver._run_ticket` and `driver._work` catch it: the run starts no other agent and ends with exit 4,
  and the ticket keeps its phase for the next run. A cleanup of an `except Blocked` that leaves git as it was (an
  open merge, an open rebase, a rejected fix batch) also runs for `Halted`.
- **A check before the first agent returns a list of errors**, so the operator sees all of them at one time
  (exit 3). Examples: `precondition_errors`, `driver_tickets.parse`, `driver_report.validate`.
- **The modules that do not know about the run return a result, not `Blocked`.** Some return `""` or the reason
  as a string (`driver_merge.refused`, `driver_merge.land`); the deciding module raises.
- A slot is a worktree. Slot 1 is the PR worktree; the others are in `.agent/worktrees/`.
- A number of a ticket is a string (`"03"`). `FINAL` is used in its place for the end of run.

## Rules the code keeps

### Trust

- An agent report is a claim. `driver_report.validate` accepts it only when git and the agent's log show it.
  The schema of a role is the JSON block of its prompt template, so the agent reads the text the driver enforces.
- The driver writes no prose of its own into a prompt: a prompt is a template with its pointers filled. The same
  holds for each title, question and ruling in `driver_decisions.py`.
- The ledger is written only by the driver, and the driver reads no fact from it.
- A check that git cannot do is a failed check, never a passed one (`locked_changes`, `refused`, `written`).

### Locked tests

- The commit of the test-writer is the reference of the ticket's locked files. Before each review, a locked file
  that differs from its reference is a critical finding (`driver_review.locked_findings`).
- A correction of the test-writer is a new reference. For that reason the driver first puts each locked file back
  to its locked version (`Run.restore_locked_files`), and a correction may change only the files it lists, after
  `overrule-test` and in the fix loop. Its commit becomes the reference of each file it lists. A locked file that it
  does not list and that differs from the old reference keeps the old reference in the ticket's `held`, so a
  change of the implementer stays a finding (`driver_review.written`).
- The locked files of merged tickets are checked when a ticket lands (`driver_merge.refused`): the tree that lands
  must have each of them as the PR tip has it. A ticket's test-writer may change one only when it lists it.
- A conflict resolution, of a merge or of the rebase, may not change a locked file.

### The PR branch

- No slot has the PR branch checked out while tickets run. A merge moves the ref and changes no tree.
- A landing holds `Run.merging` from the merge commit to the move of the ref. The gate of a merge commit and
  the conflict agent run under that lock, so the commit that the gate saw is the commit that lands.
- `git update-ref <new> <old>` moves the ref only from the tip the check read. When another hand moved the
  branch, the landing starts again, one time.
- The gate runs only on a commit the driver is about to trust, and one green record settles that commit.
  A gate on a dirty tree, or one that leaves the tree changed or HEAD moved, is red.
- The end of run rewrites the branch one time, before the first final review, and never after a review. A history
  that origin has is never rewritten. `refs/implement/recovery` holds the tip before the rewrite.
- A rewrite never changes a file: the driver compares trees (`driver_tidy.same_tree`) before it moves the branch.
- The push sends the reviewed commit, not the branch name, with no force.

### Work is never dropped

- A role that commits keeps its tree after a timeout or an interrupt; its rerun continues from it.
- A read-only role (reviewer, correctness, final-reviewer, pr-writer) has no work to keep. When it changes its slot, the
  driver restores the slot and runs the review one more time (`Run.read_only`).
- A slot with uncommitted work of an interrupted or parked ticket is kept for that ticket (`driver_state.kept`).
- A blocked ticket keeps its slot when the slot is not clean.

### State and resume

- `state.json` is the only input of a resume. Each write is a read-modify-write under one lock, published by a
  rename. Ticket threads share one `State`.
- When two writes belong together, the order is chosen so that a stop between them is safe. The ledger line of
  a ruling comes before the state change. The record of the tidied tip comes before the move of the branch, and
  `finish_interrupted_move` completes it at the next start.
- At a resume, git must confirm each recorded tip (`driver_state.verified`). A ticket that git contradicts goes
  back to `implement` from its test-writer commit.
- The lock of one driver per repository is a kernel lock on a file in the git common directory. The kernel frees
  it when the process ends, so it is never stale.

### Agents

- Each agent runs in its own process group. `driver_agents._LIVE` makes three events exclusive: an agent starts,
  an agent is reaped, the run stops. No group gets a signal after its leader is reaped.
- A role gets one rerun. The rerun prompt is the first prompt, the rejection, then the same ending again.
  A timeout doubles the time of the rerun.
- Fix rounds stop at 4, and round 3 goes to the next implementer entry. The correctness pass never runs on the
  provider of the implementer.
- A red gate on the implementer's or a fixer's commit is a finding of the next round (`driver_fixloop._gate`), so
  it blocks only after round 4. A red gate on a test-writer correction blocks.
- The implementer and each fixer get the `--gate` command and run it before they report. The gate of the driver
  stays the source of truth: an agent's run of the gate counts for nothing.

### Questions

- A stop is a bounded question in `state.json`. Only `driver_decisions.stop` adds one. An answered question is
  never open again.
- An answers file counts only when it is bound to the revision of the current `decisions.json`.
- Only a question whose kind parks gives exit 2. A third round of the final review is never started: its open
  findings become questions.
