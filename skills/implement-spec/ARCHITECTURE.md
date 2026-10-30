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
| `driver_dispute.py` | A disputed locked test: the arbitration, its correction, the once-per-file record, `spec-ambiguous` |
| `driver_final.py` | The end of run: tidy, rebase, gate, final review, one fix batch, push |
| `driver_tickets.py`, `driver_graph.py` | Ticket files in, tickets or errors out; which tickets can start now |
| `driver_agents.py` | Model entries, the CLI command of each provider, prompt templates, the blocking launch |
| `driver_report.py` | Validation of one agent report against its schema, git and the agent's log |
| `driver_locked.py` | The one locked-file rule: which files are locked, the baseline of each agent and of the landing, the diff that finds a change |
| `driver_review.py` | The routing of findings; the slot and the shared git config that a read-only role must leave as they were |
| `driver_merge.py` | Merges by git plumbing, the move of the PR ref |
| `driver_gate.py` | One gate of a commit: the command, once more when a signal ended it, and its record |
| `driver_slots.py` | The worktrees that tickets run in, and the `git` helper |
| `driver_state.py` | `state.json` (the resume contract) and the kernel lock |
| `driver_names.py` | The names of one run: its ticket branches, its recovery ref and its lock file |
| `driver_legacy.py` | The take-over of a run that an older driver started; delete it when no such run is left |
| `driver_decisions.py`, `driver_answers.py`, `driver_review_page.py` | Operator questions, their answers, the page that shows them |
| `driver_tidy.py`, `driver_summary.py` | The history with one commit per ticket; `summary.md` |

`driver.py`, `driver_fixloop.py`, `driver_dispute.py` and `driver_final.py` decide what comes next. The other modules do one thing
and do not know about the run, except `driver_run.py`, which the deciding modules share.

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
  as a string (`driver_merge.land`), or what they found (`driver_merge.refused`); the deciding module raises.
- A slot is a worktree. Slot 1 is the PR worktree; the others are in `.agent/worktrees/`.
- A number of a ticket is a string (`"03"`). `FINAL` is used in its place for the end of run.

## Rules the code keeps

### Trust

- An agent report is a claim. `driver_report.validate` accepts it only when git and the agent's log show it.
  The schema of a role is the JSON block of its prompt template, so the agent reads the text the driver enforces.
- The driver writes no prose of its own into a prompt: a prompt is a template with its pointers filled. The same
  holds for each title, question and ruling in `driver_decisions.py`.
- The ledger is written only by the driver, and the driver reads no fact from it.
- A check that git cannot do is a failed check, never a passed one (`driver_locked.errors`).

### Locked tests

- `driver_locked` is the one place of the locked-file rule: which files are locked (`every`, `others`), the
  baseline of each agent (`for_ticket`, `for_end_of_run`, `landing_errors`) and the diff. `driver_report.validate`
  calls `driver_locked.errors` once, with the `Locked` check that `_attempts` reads when the agent ends: the
  `Call`'s own `locked`, else that of a ticket agent.
- Only the test-writer changes a locked file, and it lists each one it changes in `locked`, of its own ticket or of
  another (`driver_locked._unlisted_errors`, `driver_report._correction_errors`). A correction may change only the
  files it lists. Its commit becomes the baseline of the ticket's own locked files (`driver_locked.written`).
- After the implementer, a fixer and the ticket conflict agent, the driver checks the locked files with a diff
  (`driver_locked._own_errors`, `_others_errors`): the ticket's own
  against its last test-writer commit, every other ticket's whose test-writer has run, merged or not, against the
  merge base of the ticket branch and the PR tip. Another ticket's file as the PR tip has it is no change; the
  ticket's own when it is the clean three-way merge (`git merge-file`) of its test-writer's version and the PR tip's,
  from their merge base, so a conflict merge takes in what landed and keeps the test-writer's tests, and no role can
  put the file back to the PR tip's version. The baseline is stateless, so a breach left on the tip by a rejected rerun is found again after a `retry`. A changed locked file
  rejects the report naming the file, so the rerun on the next entry and `ticket-blocked` apply as to any rejection.
- At landing, under the merge lock and before the full gate, `driver._refuse_breach` compares the commit that
  would land with the PR tip on every other ticket's locked files (but those the ticket lists as its own,
  `driver_locked.landing_errors`): a change, or a git error, blocks the ticket with one `ticket-blocked` question
  and the PR branch does not move. Its `retry` resumes at `implement`, whose check then names the file.
- The reviewer gets the diff of each locked file of another ticket that the ticket changed
  (`driver_locked.changed_elsewhere`, `prompts/elsewhere.md`): only the test-writer can have made it, and the
  reviewer judges it against the spec. A review finding on such a file goes to a correction, since a fixer may not
  change it (issue #116).
- The final fixer and the rebase conflict agent get the same check over every ticket's locked files
  (`driver_locked.for_end_of_run`): against the tidied tip, or for the rebase that makes it, the recovery point. When
  the rebase conflict agent ends, the driver goes on with the rebase it left open (`driver_final._rebase_check`, the
  `locked` of its `Call`), so the check reads the
  rebased tip, and a rejected agent's rerun amends that tip. A `git rebase --continue` that fails at the same stop
  rejects the report with git's error, so the agent reruns on the open rebase. A rebase that stops at a later conflict is checked by
  nobody, as the driver aborts it. The final review has no locked-file findings of the driver.

### The PR branch

- No slot has the PR branch checked out while tickets run. A merge moves the ref and changes no tree.
- A landing holds `Run.merging` from the merge commit to the move of the ref. The gate of a merge commit and
  the conflict agent run under that lock, so the commit that the gate saw is the commit that lands.
- `git update-ref <new> <old>` moves the ref only from the tip the check read. When another hand moved the
  branch, the landing starts again, one time.
- The gate runs only on a commit the driver is about to trust, and one green record settles that commit.
  A gate on a dirty tree, or one that leaves the tree changed or HEAD moved, is red.
- The end of run rewrites the branch one time, before the first final review, and never after a review. A history
  that origin has is never rewritten. `refs/implement-spec/<key>/recovery` holds the tip before the rewrite.
- A rewrite never changes a file: the driver compares trees (`driver_tidy.same_tree`) before it moves the branch.
- The push sends the reviewed commit, not the branch name, with no force.

### Work is never dropped

- A role that commits keeps its tree after a timeout or an interrupt; its rerun continues from it.
- A read-only role (reviewer, final-reviewer, the final lenses, pr-writer) has no work to keep. When it changes its slot or
  the shared git config, the driver restores them and runs the review one more time (`Run.read_only`). The config is
  the keys of that file before the role (`driver_review.shared_config`): every worktree reads that one file, so a key a
  role writes there, such as a `core.worktree` on a temp directory, breaks git in all of them. Other sessions write
  it too, so the tracking keys of a new branch (`driver_review._tracking`) do not count and only the changed keys go
  back, checked after; the whole file goes back only when git cannot write them.
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
- The lock of one driver per PR branch is a kernel lock on a file in the git common directory. The kernel frees
  it when the process ends, so it is never stale. Every ref that a run writes has the PR branch in its name
  as one escaped name component. `driver_names.Names` (`Run.names`) makes every name of a run, so runs on
  different PR branches share no ref. `driver_legacy.resume` takes over a run that an older driver started, once,
  after the lock and before any check reads a ref.

### Agents

- Each agent runs in its own process group. `driver_agents._LIVE` makes three events exclusive: an agent starts,
  an agent is reaped, the run stops. No group gets a signal after its leader is reaped.
- A role gets one rerun. The rerun prompt is the first prompt, the rejection, then the same ending again.
  A timeout doubles the time of the rerun.
- Fix rounds stop at 2, and round 2 goes to the next implementer entry.
- A ticket review runs one agent, the reviewer. Its prompt carries the ticket's Risk line as one more criterion
  (`driver._risk`), so a risk that the diff does not handle is an unmet criterion under the usual round and cap rules.
- A `--models` file may name only the roles of the skill's `models.json` (`driver_agents.load_models`): a removed
  role (`correctness`, `explorer`) or an unknown one stops the run before the preflight.
- The full final review is three lens roles, `final-spec`, `final-correctness` and `final-structure`, with no model
  list. `driver_agents.LENSES` is the one table of a lens: the `source` of its findings, the method file it follows,
  whether its lines of the one lens prompt `prompts/final-lens.md` inline that file, and whether it runs on another
  provider. Its lines are `prompts/lens-<source>.md`, with `prompts/lens-copy.md` as `{copy}`. The entry, the
  prompt values (`driver_final._lens_values`), the precondition errors and the source tags read it, and `TIMEOUTS` and
  `READ_ONLY` take its roles. `driver_agents.lens_entries` takes the entries from `final-reviewer`, and
  `other_provider_entries` keeps a lens, its rerun too, on another provider than the first implementer entry;
  `load_models` refuses a list with none. `driver_final._run_lenses` runs them at the same time in slot 1 inside one
  `Run.read_only`, so a lens that changes the slot makes all three run again. A lens that stops or is rejected
  twice stops the round only after every lens has ended, with one `final-ticket-blocked` question for each such lens
  (`Run.ask` adds them to one stop); an accepted lens report is kept in `run.final.lenses` with
  its tip and reused for that tip only. `driver_final._merged` makes the (file, line, title) of a finding unique,
  keeping the more severe one, then the earlier lens; must-fix matching keeps its own (file, title) key. The
  findings of the scoped review get the source `final-reviewer`; a findings file of an older driver, with no
  `source`, is read only by the scoped review agent. The lens reports go to
  `run.final.lenses` only once `Run.read_only` returns, so a report of a slot that a lens changed is never kept.
- The scoped review after the fix batch is one `final-reviewer` agent on `other_provider_entries`, never the three
  lenses again. Each finding it leaves open asks one `final-ticket-blocked` question (`driver_final._settle_review`);
  only a `retry` runs another fix batch and scoped review, and `edit` keeps the end of run BLOCKED
  (`driver_final._parked`).
- A red gate on the implementer's or a fixer's commit is a finding of the next round (`driver_fixloop._gate`), so
  it blocks after round 2. A red gate on a test-writer correction blocks.
- The implementer and each fixer get the `--gate` command and run it before they report. The gate of the driver
  stays the source of truth: an agent's run of the gate counts for nothing.
- A finding that a correction reports in `not_addressed` goes to the fixer of the same round as kind `code`
  (`driver_fixloop._uncorrected` rewrites it in the findings file): the test needs a production change first, so a
  second correction could not close it.
- Every test-writer correction, after a `test-wrong` ruling, a review finding or a `correct-test` answer, is one
  `driver_dispute.correct`: it runs the test-writer on the findings it is given, makes its commit the baseline,
  keeps the reading of each file it corrected in `corrected` (none after an answer) and logs one line per finding.
  The callers differ only by the findings and the readings they pass.
- No test-writer correction runs after round 2 (`driver_fixloop._act_on`): a red gate or an unmet criterion blocks
  the ticket. Other open findings go to the final must-fix list when the gate is green. A correction at the cap
  would add locked tests that no fix round could make green.
- A locked file is arbitrated once and corrected once per ticket. The ticket record keeps them in `disputes` and
  `corrected`, keyed by the file (the part of a test id before `::`, resolved against the ticket's `locked` by
  `driver_report._locked_file`), so a renamed or parametrized test cannot dodge the rule. Only `driver_dispute._settle`
  and `driver_dispute.correct` write them; no answer does, so no answer resets them. A second dispute or a second
  correction asks `spec-ambiguous` (`driver_dispute.ambiguous`); its evidence file is the findings file a
  `correct-test` correction reads (`driver_dispute.correct_as_answered`), and its files are the ones `keep-test` closes for later
  reviews (`driver_dispute.kept_files`, `driver_fixloop._still_to_correct`).
- `driver_run._attempts` treats a stop (BLOCKED or NEEDS_CONTEXT, not a dispute of a locked test) of any agent
  as a rejected attempt: the rerun runs on the next entry of the role's list (`_rerun_entry`; the ticket fixer's list
  is the implementer's), with the concerns or the driver's error in its prompt. A second stop or rejection asks a
  `ticket-blocked` question (`_ask_blocked`); its `retry` sets the `resume` the question holds (`retry_point`), and the
  stop of a fixer sets the round back by one, so the same round runs again after a review. The end of run asks the
  same kind with the ticket `final` (`final-ticket-blocked-<n>`), whose `resume` is the phase `pending` of `run.final`;
  the end of run goes on from its state once that phase is `pending` again. An agent that runs for another
  role (an arbitration, a dispute's correction, a review correction) passes that role as `retry`: its `retry` runs that
  role again from the commit the agent started on, so its own work is not kept.
- A dispute (an implementer or a fixer that reports BLOCKED with `locked test contradicts spec: <file>::<test>:
  <reason>`) is the one stop that `driver_run._stop` accepts, and only from a `Call` with `may_dispute`, which only
  `driver_dispute.agent` sets: it runs the implementer and each ticket fixer and reads the accepted report's
  disputes (`driver_report.disputes`). The reviewer arbitrates with `prompts/arbitration.md` in `Run.read_only` (not a fix round), a test ruled
  `test-wrong` gets one correction, and the role runs again from the tip in the same round, with the ticket's rulings
  in `prompts/ruled.md`. `driver_report._dispute_errors` rejects a dispute that names no locked file of the ticket or
  leaves uncommitted changes: the arbitration is read-only and the correction commits.
- An implementer or a fixer with nothing to change reports DONE with no commit (`driver_report._unchanged_errors`):
  the branch did not move, the tree is clean, and a concern says why. The driver gates and reviews that tip as it
  would a new one.

### Questions

- A stop is a bounded question in `state.json`. Only `driver_decisions.stop` adds one, through `Run.block`, the one
  path that blocks a ticket or the end of run: it makes the record (`driver_run.record_of`, a ticket's or `run.final`)
  BLOCKED, gives it the tip and the round of the question's `Resume`, and adds the questions. An answer that goes on
  sets the record's fields in one write: the whole `resume` for `retry` and `keep-test`, the option's own fields for
  `correct-test`. The end of run is recorded BLOCKED only at a question; with none, the next run tries it again.
  An answered question is never open again.
- An open question of a merged ticket is dropped (`driver_decisions.drop_merged`), at the start of a run and after
  each landing, with a ledger line: a ticket the operator set to `merge` by hand leaves its stop behind.
- An answers file counts only when it is bound to the revision of the current `decisions.json`.
- An answered question keeps its ruling sentence in `ruling`, so the PR body never looks up the kind of an answered
  question. `driver_legacy.resume` stops a run before schema 4 that has an open question of a removed kind (exit 3),
  and gives each answered question of such a run its `ruling`.
- An open question gives exit 2. A third round of the final review starts only after a `retry` of
  a `final-ticket-blocked` question of an open finding.
