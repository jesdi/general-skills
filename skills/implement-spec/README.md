# implement-spec driver

`run.sh` turns a spec and its tickets into one reviewed PR branch. It is a script, not a session: it walks the ticket graph, starts one agent for each role, runs the gates, merges, and stops only at a bounded question or a BLOCKED ticket. [SKILL.md](SKILL.md) only starts it.

This file is written from the code. Where it and `specs/implement-spec-driver/` differ, the code is right.

## Run it

Run it in the PR worktree, on the PR branch, with a clean tree:

```sh
sh skills/implement-spec/run.sh --gate 'pnpm check' --fast-gate 'pnpm check:fast' --spec specs/<slug>/spec.md
```

Before any agent starts, the driver checks these. Each failed check prints one line, and the run then ends with exit 3.

- `.agent/` is gitignored and the worktree has no uncommitted change.
- Each ticket file `NN-name.md` has a title, a **Blocked by** line and a **Seam** line, and the graph has no cycle (`driver_tickets.py`). A ticket needs one criterion or more, unless its Seam is `none — refactor`. A ticket needs a **Touches** line when the set has more than one ticket. The [to-tickets skill](../to-tickets/SKILL.md) has the line format.
- The `--models` file names only roles of the skill's `models.json`. A `correctness` or `explorer` key is a role that was removed: the error says so, and that the key should be deleted from the file.
- Each model entry is valid, and the CLI of the first entry of each role is on PATH.
- The `final-reviewer` list has an entry on another provider than the first `implementer` entry: the correctness lens of the final review and the scoped review run on it. The error names the list and the implementer entry.
- The `review-diff` skill is installed next to this one, with its `spec.md` and `correctness.md`, and the `deep-quality-review` skill is next to this one, in `${CLAUDE_CONFIG_DIR:-~/.claude}/skills` or in `~/.agents/skills`: the lenses of the final review follow them. One error per lens names its skill and its missing file.
- With `--push`, `--unattended` or `--base`, the repository has an `origin` remote.
- Each model that the run can use answers one short test prompt (the preflight). A model that fails gives one line with its error. `--dry-run` skips the preflight.

A run that stopped is continued with the same command. `state.json` is the only input of a resume.

One driver runs on a PR branch at a time. The driver holds a kernel file lock on `implement-spec/<key>.lock` in the git common directory (`git rev-parse --git-common-dir`) for the whole run. `<key>` is the PR branch as one name component: `%` becomes `%25` and `/` becomes `%2F`, so `feat/x` gives `feat%2Fx`. Each worktree of the repository shares that directory, so a second driver on the same PR branch, in the same worktree or in another one, ends with exit 3 and names the lock file. Drivers on different PR branches run at the same time: each ticket branch is `ticket/<key>/<NN>` and the recovery ref is `refs/implement-spec/<key>/recovery`, so two runs share no ref. Git refs are a tree, and one component per PR branch keeps the refs of one run from being a folder of the refs of another. The file is not in a worktree, so `git status` never shows it. The lock ends with its process, so a driver that was killed leaves no lock behind. `--dry-run` takes no lock. A wrong call in a worktree that has no `.agent/implement/` takes no lock and makes no file; where an earlier run left that folder, the driver takes the lock first, then makes the checks.

A driver before this change named the ticket branches `ticket/<NN>` and the recovery ref `refs/implement/recovery`. An older driver holds `implement-spec.lock` in the git common directory alone for its whole run. Each newer driver also holds that file, shared, for its whole run: newer drivers do not stop each other, but while an older driver runs in the repository, a newer one ends with exit 3 and names that lock, and while a newer one runs, no older driver starts. A run that a newer driver started has a `schema` in `state.json`. When the newer driver resumes a run with no `schema`, it renames each `ticket/<NN>` that holds the start commit of the run and the tip that the run recorded, and moves `refs/implement/recovery` when it holds the recovery point of the run. It writes one ledger sentence for each, and then `schema`. A ref that git cannot rename ends the run with exit 3 and git's error, and nothing is lost.

## Flags

| Flag | Meaning |
|---|---|
| `--gate COMMAND` | Required. The full gate: the whole check command of the repository, as one shell line. The driver runs it on each commit that would land on the PR branch and at the end of the run, with `IMPLEMENT_SPEC_BASE` set to the PR branch and no `IMPLEMENT_SPEC_LOCKED`. No agent is told this command. |
| `--fast-gate COMMAND` | Required. The fast gate: the ticket's locked tests, lint and types, as one shell line. The driver runs it on each candidate (the tip an implementer, a fixer or a test-writer correction made) before its review, with `IMPLEMENT_SPEC_BASE` set to the PR branch and `IMPLEMENT_SPEC_LOCKED` set to the space-separated locked file paths it checks: the ticket's locked files. Another ticket's locked files are not in a candidate's list. A green record is settled per tier and commit, whatever the list was: a commit is fast gated in one context. The implementer, the fixers and the final fixer run it on their own work: their prompts name this command and the same locked list, and for the final fixer the list is every merged ticket's locked files. No agent prompt names the `--gate` command. What it checks is the operator's choice. |
| `--spec FILE` | Required without `--dry-run`. The spec file the prompts point at. Its first heading is the PR title. |
| `--tickets DIR` | The directory of the ticket files. Default: `.agent/tickets`. |
| `--models FILE` | A JSON file with one ordered list of `provider/model@effort` for each role. A role in this file replaces the list of that role in `models.json`. |
| `--parallel N` | The number of tickets in flight at once. Default: 2. |
| `--prepare COMMAND` | A shell command that the driver runs once in each new slot 2..N, in `.agent/worktrees/slot-K/`. Use it to install or link the dependencies. |
| `--timeout ROLE=MINUTES` | The timeout of one role. Repeatable. The rerun after a timeout gets twice the time. |
| `--answer QUESTION=OPTION` | The answer to one open question. Repeatable. The driver writes it to `.agent/implement-answers.json`. |
| `--unattended` | Print no question. Write `--stage-json` for the dispatcher, and push without `--push` when the final review has passed. |
| `--stage-json FILE` | With `--unattended`: the file that says how the run ended. |
| `--push` | Local mode: when the final review has passed, push the PR branch and open the PR. Without it nothing is pushed. |
| `--base BRANCH` | The branch of `origin` that the end of run rebases onto and that the PR goes into. Default: the default branch of `origin`. |
| `--dry-run` | Validate, print `max frontier width: N`, change nothing. |
| `--runner SCRIPT` | Test only. The driver calls `<script> <role> <prompt> <report>` in the slot, in place of the CLI. |
| `--only ROLE` | Test only. Run one role (`test-writer`, `implementer` or `reviewer`) on the first ticket, with no gate and no merge. |
| `--help`, `-h` | Print the flags and exit. |

Default timeouts in minutes: test-writer 30, implementer 90, reviewer 45, fixer 60, final-reviewer 60, final-spec 60, final-correctness 60, final-structure 60, conflict 30, pr-writer 20.

`run.sh` downloads `uv`, Python 3.13 and `jsonschema` once into `~/.cache/implement-spec`. `IMPLEMENT_SPEC_CACHE_DIR` moves that cache. `IMPLEMENT_SPEC_OFFLINE=1` forbids the download. `run.sh` supports macOS and Linux only, on x86_64 or ARM64, and needs `curl`, `tar` and `sha256sum` or `shasum` for the first download.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Done: every ticket is merged and no question is open. Also the end of a valid `--dry-run`. In local mode without `--push`, nothing is pushed. |
| 1 | A ticket is BLOCKED, or the end of run is BLOCKED. The other tickets ran as far as they could. |
| 2 | Parked: a question of kind `spec-ambiguous` or `ticket-blocked` is open. Exit 2 wins over exit 1. |
| 3 | Invalid input or a failed precondition: a usage error, a ticket error, a model error, an uncommitted change, a slot that cannot be made, another driver that runs on the same PR branch, an open question of a kind this driver no longer has (see "Resume"). No agent starts. The one exception: a review page that cannot be written gives exit 3 at the end of a run, after agents ran. |
| 4 | Stopped: the CLI of an agent failed two times and wrote no report (for example, the account cannot use the model). The driver starts no other agent and lets the running agents end. No ticket is BLOCKED by it: fix the CLI or the model entry, then run the same command again to resume. |
| 130 | The operator interrupted the run. The driver stops each agent. The work stays in the slots for the next run. |

With `--unattended`, the `--stage-json` file holds `{"stage": "implement", "status": …}`:

| Exit | `status` | Other fields |
|---|---|---|
| 2 | `awaiting-review` | `artifact` (the review page), `open_questions` (a count) |
| 1 | `blocked` | `blocked` (ticket numbers, and `final` for the end of run) |
| 0, pushed | `done` | none |

## Layout

Everything is under `.agent/`, which is never committed.

| Path | Content |
|---|---|
| `.agent/implement/state.json` | The resume contract: the run record and one record for each ticket (`driver_state.py`). |
| `<git common directory>/implement-spec/<key>.lock` | An empty file, not in the worktree. The driver that runs holds a kernel file lock on it (`driver_state.py` `lock`). Do not remove it during a run. |
| `.agent/implement/ledger.md` | One plain sentence for each event. Only the driver writes it, and it reads no fact from it. |
| `.agent/implement/summary.md` | The result in sentences, and the measurement table: fast gates and full gates, agents, wall time, fix rounds, human stops, cost. |
| `.agent/implement/decisions.json` | The open questions and their `revision`. Written only after a stop. |
| `.agent/implement/<NN>/` | One folder for each ticket. |
| `.agent/implement/<NN>/<role>-<K>.json`, `.log`, `.prompt.md` | The report, the log and the prompt of one agent. K is the fix round. |
| `.agent/implement/<NN>/gate-<tier>-<sha7>.log` | One gate record, `<tier>` is `fast` or `full`: start, tier, commit, status, command, output, exit, the state after the command, end. A command that a signal ended, with the tree clean and HEAD unmoved, did not judge the commit: the driver runs it once more, that run overwrites the record, and the ledger says the gate was killed by the signal and runs again. A signal ended the command when its exit is negative, or when it is 128+N for N in SIGHUP (1), SIGINT (2), SIGKILL (9) or SIGTERM (15): the gate runs in `/bin/sh -c`, and a shell that does not exec the command, as Ubuntu's dash does not, outlives its killed child and exits with 128+N. Any other exit, 142 (128+SIGALRM) among them, is a red record with no second run. A second signal leaves the record red. |
| `.agent/implement/<NN>/findings-<K>.json` | The merged findings of the review after fix round K, or the red fast gate of round K. A red full gate at landing adds its finding to the findings of the last review. |
| `.agent/implement/final/` | The same files for the end of run, and `pr-body.md`: the pr-writer's body, then the facts of the run. |
| `.agent/implement/pycache/` | Compiled Python of the gate, kept out of the slots. |
| `.agent/implement-review.html` | The page with the open questions. |
| `.agent/implement-answers.json` | The answers: `{"v": 1, "stage": "implement", "submitted": …, "revision": …, "answers": {…}}`. |
| `.agent/worktrees/slot-K/` | Slots 2..N. Slot 1 is the PR worktree. |
| `refs/implement-spec/<key>/recovery` | A git ref: the PR tip before the tidy. |
| `ticket/<key>/<NN>` | The branch of one ticket. The driver deletes it when the ticket is merged. |

## Roles of one ticket

A ticket is ready when each ticket in its **Blocked by** line is merged and no running ticket touches the same place. The places are the backticked entries of the **Touches** lines: `file` or `file: function`. Two entries are the same place when they name the same file and the same function, or when one of them names the whole file. Each ready ticket takes the lowest free slot.

1. **test-writer** (`prompts/test-writer.md`). It writes one black-box test for each criterion through the Seam and commits them red. Those files are locked. Its prompt names the locked tests of other tickets, merged or not: each fixture must set up the precondition of its scenario under the spec rules and those tests. When the ticket must change one of those files, the test-writer changes it and lists it in `locked`: it is the one role that may change a locked file. A ticket with Seam `none — refactor` has no test-writer.
2. **implementer** (`prompts/implementer.md`). It starts from that commit and makes the locked tests green. It makes the fast gate (`--fast-gate`, with the ticket's locked files in `IMPLEMENT_SPEC_LOCKED`) green on its final commit before it reports DONE or DONE_WITH_CONCERNS.
3. **Fast gate.** The driver runs `--fast-gate` on the ticket tip. No full gate runs on a candidate.
4. **Review.** The **reviewer** (`prompts/reviewer.md`) runs, the one agent of a ticket review. Its prompt holds the diff of each locked file of another ticket that the ticket's test-writer changed (`prompts/elsewhere.md`), to judge against the spec: a change that the spec does not support is a `fixture` finding. Its prompt carries the ticket's **Risk** line as one more criterion, or says that the ticket names no risk: a risk that the diff does not handle is a finding with basis `criterion`, under the same round and cap rules as any unmet criterion. A read-only role (reviewer, final-reviewer, the three lenses of the final review, pr-writer) leaves its slot and the git config as it found them: all worktrees share one config, and one key written there, such as a `core.worktree` on a temp directory, breaks git in each of them. To run a check off the slot, a reviewer reads the driver's gate record or makes its own `git worktree add --detach` copy and removes it. After a read-only role, the driver checks the slot and the keys of the shared config (`git rev-parse --git-common-dir`, then `config`) against what they were before. One change does not count: a new `branch.<name>` section with only `remote` on a configured remote and `merge` on `refs/heads/...`, which another session's `git worktree add -b` writes while a review runs. When the slot or another key changed, the driver restores the slot and only the changed keys, so other sessions' writes stay; when git cannot write them back (a held `config.lock`, a valueless key), the whole file goes back, and when that fails too the ticket is blocked, writes `a read-only role broke its rule: ...` to the ledger, and runs the review one more time; a second change blocks the ticket.

The ticket reviewer puts unmet criteria and merged-ticket regressions in `findings`; other observations go to final review. A re-review repeats its own open findings and can add regressions caused by the fix. Newly noticed issues outside that scope are deferred. The driver stores deferred observations in `observations-<round>.json` and gives them to each lens of the final review.
5. **Merge.** The driver merges the reviewed tip into the PR branch with git plumbing, then deletes the ticket branch. A conflict goes to the **conflict** agent (`prompts/conflict.md`) once. Its resolution may change no locked file of the ticket. A second conflict blocks the ticket. Before the PR branch moves, the driver runs the full gate (`--gate`) on the exact commit that would land: the ticket tip of a fast-forward, or the merge commit. A green fast record never counts for it.
   **The landing.** Merges are serial. The PR branch moves only from the tip that the merge was made on; when another hand moved the branch in between, the driver makes the merge and the gate again, one time. A red full gate at landing is a gate finding inside the two rounds (see Findings and fix rounds). The second try runs no conflict agent, so a conflict there blocks the ticket, and so does a branch that moved again. Before the full gate, the commit that would land must leave every other ticket's locked file as the PR tip has it, but those the ticket lists in its own `locked`: a changed file, or a git error, blocks the ticket with one `ticket-blocked` question naming it, and the PR branch does not move. Its `retry` runs the implementer again from the ticket tip, whose locked-file check names the file until the ticket branch leaves it as the merge base has it. So a ticket's edit of a test file that another ticket locked later cannot land unseen.
   **The locked-file check.** After the implementer, a fixer and the conflict agent report (DONE, BLOCKED or NEEDS_CONTEXT), the driver diffs the locked files: the ticket's own against its last test-writer commit, every other ticket's whose test-writer has run, merged or not, against the merge base of the ticket branch and the PR tip. Another ticket's file as the PR tip has it is no change; the ticket's own file is no change when it is the clean three-way merge (`git merge-file`) of its test-writer's version and the PR tip's, from their merge base, so a conflict merge can take in another ticket's listed change and keeps the test-writer's tests, and neither a conflict agent nor an implementer can put the file back to the PR tip's version. A changed locked file rejects the report with `locked file changed: <path>`, so the role runs once more on its next entry and then asks `ticket-blocked`. The baseline does not move with a rerun or a `retry`, so a breach left on the tip is found again. A test-writer correction (`prompts/correction.md`: after a `test-wrong` ruling, after `correct-test`, or for a `fixture` finding or a `test` finding on a locked file) changes only the files that it lists in `locked`. When it adds a requirement, it updates each shared fake or fixture that other locked tests use, runs each whole locked file and each locked test of merged tickets, and names each red test in `concerns`.

Rules for each agent:

- A call blocks until the agent ends. The driver does not poll.
- An agent stops only the processes it started and their child processes, by PID, and never kills a process it did not start, nor uses `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. A process it did not start that blocks it goes into its concerns.
- An agent gets one rerun: after a rejected report, after a timeout, or after it left the ticket branch. The rerun prompt is the first prompt plus the reason. A ticket agent (test-writer, implementer, reviewer, fixer, conflict) whose report is rejected, or that reports `BLOCKED` or `NEEDS_CONTEXT`, reruns on the next entry of its model list, wrapping to the first (a list of one entry reruns on that entry); the reason is the driver's error or the agent's concerns. An agent of the end of run (a lens, the scoped final-reviewer, the final fixer, the rebase conflict agent, the pr-writer) does the same; the correctness lens and the scoped review rerun only on `final-reviewer` entries on another provider than the first `implementer` entry. After a timeout or a moved branch, the rerun is on the same entry.
- A ticket agent whose rerun stops or is rejected too blocks the ticket with a `ticket-blocked` question; an agent of the end of run blocks the end of run with a `final-ticket-blocked-<n>` question. An implementer or a fixer that disputes a locked test (see Disputed locked tests) gets no rerun: the reviewer arbitrates. Nothing left to change is no stop: the implementer and the fixer report it as DONE with no commit.
- A BLOCKED ticket stops the tickets that it blocks. All other tickets run on.

### Findings and fix rounds

| Finding | What the driver does |
|---|---|
| kind `fixture`, any severity; kind `test`, `critical` or `important`, on a locked file of the ticket; or any kind, `critical` or `important`, on a locked file of another ticket | One test-writer correction, outside the fix rounds. The fixer may not edit a locked file, so a fix round cannot close a `test` finding there. Only a test-writer may change a locked file of another ticket, and it lists the file in `locked`: a ticket whose Touches line names such a file gets its change this way, where a fixer's change would be rejected (issue #116). The corrected files are the new locked versions, and the next review is a full review. A finding that the correction reports in `not_addressed` (it needs a production change first) becomes a finding of kind `code`, with the reason as its evidence, and goes to a fix round at once; the ledger says it was not corrected. A locked file is corrected at most once per ticket, by a review finding or after a `test-wrong` ruling: a finding that needs a second correction of it asks `spec-ambiguous`, with the finding as the second reading. A correction that is rejected twice asks `ticket-blocked`, whose `retry` reviews the tip again. No correction runs after round 2. |
| `critical` or `important` | One fix round. |
| `minor` | It stays in the ledger and in the findings file. Each lens of the final review gets the list. |

### Disputed locked tests

An implementer or a fixer that finds a locked test contradicting the spec or the ticket commits its work and reports BLOCKED with one concern per test: `locked test contradicts spec: <locked file>::<test>: <evidence>`. The driver rejects a dispute that names no locked file of the ticket (a path that ends the locked path counts, so `test_csv.py` names `tests/test_csv.py`) or that leaves uncommitted changes; the role then reruns on its next entry, as after any rejection.

The **arbitration** is the reviewer on its entry with `prompts/arbitration.md`: the ticket, the spec, the disputed tests and the dispute report. It is read-only, counts as a `reviewer` agent in `summary.md`, and is not a fix round. Its report `arbitration-<n>.json` gives per disputed test the `requirement` it checks, a `ruling` and a `reason`. On `test-wrong`, one test-writer correction (`correction-<n>.json`) runs with the ruling as a finding of kind `ruling` (`ruled-<n>.json`), then the role that disputed runs again on the corrected tests: the implementer, or the fixer of the same round. On `code-wrong`, that role runs again with the ruling in its prompt (`prompts/ruled.md`) and makes the test green. The review after a correction is a full review.

A locked file is arbitrated once and corrected once per ticket, keyed by the file, so a renamed or parametrized test of a ruled file is the same test. `state.json` keeps the ruling in the ticket's `disputes` and the correction in its `corrected`; no answer changes them. A second dispute on a ruled file, or a `test-wrong` ruling on a file corrected before, asks one `spec-ambiguous` question naming the test, the requirement and the two readings.

A fix round has three steps: the **fixer** (`prompts/fixer.md`) makes the fast gate green on its final commit, as the implementer does; the driver runs the fast gate on the new tip; a re-review reads the range of the fix. A fixer whose findings need no change, for example because a test-writer correction before it already resolved them, makes no commit and gives in `concerns` a reason for each finding; the round still counts, and the re-review of the same tip checks each reason.

A red fast gate on the commit of the implementer or of a fixer is a critical finding with source `gate`, titled `the fast gate is red on <sha7>`. Its file is the gate record, and the record holds the output. No review runs on that tip, and the next round starts: a red gate on the commit of the implementer starts round 1. The review after that round covers each fix since the last review. A red fast gate after round 2 or on a test-writer correction blocks the ticket.

A red full gate on the commit that would land is a gate finding too, inside the same two rounds. The PR branch does not move and the merge lock is released. Before round 2, the driver adds a critical finding with source `gate`, titled `the full gate is red on <sha7>`, to the findings file of the last review, its file the full record, and the next round starts: the fixer (with `prompts/landing-red.md`) works on the ticket branch, its commit gets the fast gate and a re-review of the fix, and the landing runs a new full gate on the new commit that would land. When the red commit is a merge commit, the fixer's prompt also names that merge commit (`prompts/landing-merge.md`), so it can merge the PR branch to reproduce the failure. After round 2 a red full gate at landing blocks the ticket, with no third round.

- Round 1 runs on the first entry of the implementer list; round 2 runs on the second. Both entries are required.
- After round 2, a red gate or an unmet ticket criterion blocks the ticket and its dependents. Independent tickets continue. When the gate is green and criteria are met, other open findings move to the final must-fix list; the ticket can merge.

### End of run

The end of run starts only when every ticket is merged and every slot is idle. Its agents work in slot 1.

1. **Tidy.** The driver saves the PR tip in `refs/implement-spec/<key>/recovery`. It then makes one commit for each ticket, in merge order, named `feat(NN): <title>`. Each commit holds the tests, the implementation and the fixes of its ticket. The tree of the new tip must equal the tree of the old tip, or the branch stays as it was.
2. **Rebase** onto the fetched tip of the `--base` branch, or without it of the default branch of `origin`. A conflict goes to the conflict agent (`prompts/rebase-conflict.md`) once, and the rebased tip gets the full gate. When the agent leaves the rebase open with no conflict, the driver goes on with it, then checks the rebased tip: a report whose rebased tip changes a locked file of any ticket from the recovery point is rejected, and the rerun fixes that tip with `git commit --amend`. A second conflict leaves the branch at its recovery point.
3. **Full gate**, then the **final review**: three agents at the same time in slot 1, one for each lens of the `review-diff` method, on the whole range, each with the one lens prompt `prompts/final-lens.md` and its own method lines in it. `final-spec` follows review-diff's `spec.md` with the run's spec; `final-correctness` has review-diff's `correctness.md` inlined, reads files from git at the reviewed tip and runs no tests; `final-structure` follows the `deep-quality-review` skill. `final-spec` and `final-structure` run on the first `final-reviewer` entry, `final-correctness` on the first `final-reviewer` entry on another provider than the first `implementer` entry. Each reads the driver's full gate record of the reviewed tip as its gate evidence and runs no gate. Each gets ticket observations, minor findings, the must-fix list and the rulings of the operator, writes its own report and never sees another's. The driver merges their findings into one `findings-0.json`: each finding has a `source` (`spec`, `correctness` or `structure`), and two findings with the same file, line and title are one, the more severe one, or on a tie the earlier one in the order spec, correctness, structure. The ledger line of each finding names its source. A lens that stops or is rejected runs once more on the next entry; when the rerun stops or is rejected too, the round stops once the other lenses have ended, no fix batch runs, and the end of run asks `final-ticket-blocked-<n>`. The driver keeps each accepted lens report with the tip it covers (`run.final.lenses`): the next review of that tip runs only the lenses with no kept report, and a review of another tip runs all three. A lens that changes the slot makes all three run again.
4. **Fix batch.** Critical and important findings, including every must-fix item, go to one fixer (`prompts/final-fixer.md`), which makes the fast gate green on its final commit, with every merged ticket's locked files in `IMPLEMENT_SPEC_LOCKED`. A report whose commits change a locked file of any ticket from the tidied tip is rejected naming the file, so the fixer runs once more on its next `fixer` entry. The driver names each of its commits `fix(NN): …` from the ticket in the report. Then the full gate, then one scoped review of those commits: the **final-reviewer** (`prompts/final-reviewer.md`), one agent on the entry of `final-correctness`, so on another provider than the first `implementer` entry. It reads the merged findings, checks that each critical and important one is fixed and that the fix commits break nothing else, and lists each must-fix item it verified in `resolved_must_fix`; a must-fix item it leaves out stays open. The driver runs no fast gate at the end of the run.
5. Each critical or important finding the scoped review leaves open, a must-fix item too, asks one `final-ticket-blocked-<n>` question, and nothing is pushed while one is open. `retry` runs one more fix batch and one more scoped review on the open findings; `edit` keeps the end of run BLOCKED. There is no third review unless the operator answers `retry`.
6. **Push.** With `--push` or `--unattended`, the driver pushes the reviewed tip once, with no force, and opens the PR into the branch of step 2 with `gh`, ready for review. The **pr-writer** (`prompts/pr-writer.md`) writes the body with the method of the `pr` skill; the driver puts the facts of the run after it: the ticket-to-commit map, the fix commits, the rulings and the review coverage (`prompts/pr-body.md`). The body carries the merge-risk block of the `pr-risk` skill under `## Merge Danger`, above the facts; the driver validates it, runs the pr-writer once more with the errors when it is invalid, and blocks the run, opening no PR, when the second body is invalid too. A push stops before any agent when no `pr` skill or no `pr-risk` skill is next to this skill, in `${CLAUDE_CONFIG_DIR:-~/.claude}/skills` or in `~/.agents/skills`. Without them it prints the two tips and the command to run again with `--push`.

History rules:

- The branch is rewritten once, before the first review, and never after it. Fixes of the final review stay as `fix(NN)` commits.
- A history that `origin` already has is never rewritten. When `origin` has the PR branch or one of its commits, the driver does no tidy and no rebase. The full gate, the final review and a fast-forward push still run.
- The push happens only when the branch tip is the reviewed tip and that tip has a green full gate record.
- With `commit.gpgsign` on, the driver signs every commit it makes (ticket merges, tidied and renamed commits) with the configured key. The push happens only when every commit that `origin` lacks has a good signature (`%G?` is `G`); otherwise the end of run is BLOCKED and nothing is pushed.

A repository with no `origin` remote skips only step 2 and step 6. The tidied commits stay on the commit that the PR branch had when the run started, and the review range starts there. One ledger sentence says that the rebase and the push were skipped. The driver prints the two tips and ends with exit 0. When you add `origin` after such an end of run, `--push` does not push: the branch is on no base of that remote, so the end of run is BLOCKED.

### Resume

- A green gate record is settled per tier and commit. The driver never runs the same tier again on that commit, and a green fast record never stands in for a full one. A `state.json` before `schema` 3 has one untiered list of green commits and one gate count: every gate then was the full gate, so the resume makes them the full tier.
- A ticket whose recorded tip is not on its branch goes back to `implement`, from the test-writer commit. A merged ticket whose tip is not in the PR branch does the same.
- A red full gate at landing moves the ticket to `fix`, advances its round and records the red commit in `landing`, in one write of `state.json`. A resumed run starts that round's fixer and does not gate the red commit again; the fixer's report clears `landing`.
- A `state.json` before `schema` 4 can hold questions of kinds this driver no longer has (`finding`, `locked-test`, `locked-test-again`, `review-stopped`, `review-rejected`, `implementer-stopped`, `fixer-stopped`, `merged-locked-file`, `final-finding`). An open one ends the run with exit 3 before any agent, naming each such question: answer it with the driver version that asked it, or start the run again. An answered one keeps the ruling sentence the older driver wrote, in the ledger and in the PR body (`driver_legacy.py` keeps those sentences; only a kind or an answer it does not know gets `Ruling: <NN> <kind> (<answer>): <subject>.`). Every answered question keeps its ruling sentence in `state.json`.
- A run of an older driver that stopped after its first final review round goes on to its fix batch and the scoped review with its `findings-0.json`, whose findings have no `source`; the findings of the scoped review have the source `final-reviewer`. A run that stopped inside that round runs the three lenses.
- A slot with the uncommitted work of an interrupted role stays as it is. The role runs there again and is told so.

## Models

`models.json` holds the default list for each role. An entry is `provider/model@effort`; an entry with no provider is `anthropic`.

| Provider | CLI | Efforts |
|---|---|---|
| `anthropic` | `claude -p` | low, medium, high, xhigh, max |
| `openai` | `codex exec` | minimal, low, medium, high, xhigh |

The driver uses the first entry of a role; the rerun of an agent that stopped or was rejected uses the next entry (round 2's fixer wraps to the first `implementer` entry). The fixer of a ticket runs on the `implementer` list; the `fixer` list is only for the fix batch of the final review. Round 2 uses the second implementer entry, which the model list must supply. The three lenses of the final review have no list of their own: they run on `final-reviewer` entries, and a `--models` file that names a lens is refused as an unknown role.

The defaults need both `claude` and `codex` on PATH. The `openai` defaults name the full Codex model names (`gpt-6-astra`, `gpt-6.1-sol`), because a Codex CLI that is signed in with a ChatGPT account does not accept the short aliases. Read "Known limits" before you use them as they are.

## Report schema per role

Each agent writes one JSON report. The schema of a role is the `json` block of its prompt template, so the agent reads the text that the driver enforces. `driver_report.py` `validate` checks the report against the schema, against git and against the log of the agent. A field that the schema does not name is ignored.

Every report has `status` (`DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, `BLOCKED`), `files` and `concerns`. A role that commits also has `commits`. A read-only role (reviewer, final-reviewer, the three lenses, pr-writer) has none: a reviewer reads `commits` as the commits it reviews, and the driver would refuse each of them as not new. The driver checks instead that a read-only role leaves its slot on the tip it got, with a clean tree.

| Role | Template | More fields | Checks after the schema |
|---|---|---|---|
| test-writer | `prompts/test-writer.md` | `locked` (test file paths), `red` (`test`, `line`) | The driver reads a test node id `<file>::<test>` in `locked` as its file. With DONE: one commit, one locked file and one red line or more. A correction's test can be green on the implementer's code, so its red line comes from a production edit that the correction makes and reverts. No locked path contains whitespace, since `IMPLEMENT_SPEC_LOCKED` separates the paths by spaces. Each locked path is in a commit that the role made and is a file at the tip: a correction that deletes a locked file leaves it out of `locked`. Each `line` is in the log. A locked file of another ticket whose test-writer has run, merged or not, that the role changed is in `locked`. A correction changes only the files that it lists in `locked`. Each `not_addressed[].finding` is the title of a finding of the correction (the driver logs its reason); a DONE correction with each of its findings there needs no commit. |
| implementer | `prompts/implementer.md` | `green` | With DONE and no commit: no new commit on the branch, no uncommitted change, and a concern that says why the start needs no change. The driver then gates and reviews the start commit. With BLOCKED, a dispute names a locked file of the ticket and leaves no uncommitted change (also for the fixer). |
| reviewer | `prompts/reviewer.md` | `findings` (`severity`, `file`, `line`, `title`, `evidence`, `kind`) | None. |
| reviewer, arbitrating | `prompts/arbitration.md` | `rulings` (`test`, `requirement`, `ruling`: `test-wrong` or `code-wrong`, `reason`) | One ruling for each disputed test, its id as the dispute gives it. |
| fixer | `prompts/fixer.md` | `green`, `ticket` | With DONE and no commit: no new commit on the branch, no uncommitted change, a gate that is green on the start, and a concern that names each finding of the round. |
| final-spec, final-correctness, final-structure | `prompts/final-lens.md`, with the lines of `prompts/lens-<source>.md` | `findings` | None. |
| final-reviewer (the scoped review) | `prompts/final-reviewer.md` | `findings`, `resolved_must_fix` | None. |
| pr-writer | `prompts/pr-writer.md` | `body` | None. |
| fixer of the final review | `prompts/final-fixer.md` | `green`; `commits` is a list of (`sha`, `ticket`) | Each new commit on the branch is in `commits`, with a ticket of the run. |
| conflict | `prompts/conflict.md`, `prompts/rebase-conflict.md` | none | With DONE: one commit or more. No locked file may change. |

For each role that commits, every commit in `commits` must be new since the role started and on its branch. `severity` is `critical`, `important` or `minor`. `kind` is `code`, `test` or `fixture`. A read-only `codex` agent cannot write a file, so it gives the JSON as its last message and the CLI saves it as the report.

## Question kinds

A question is a bounded choice for the operator. `driver_decisions.py` `KINDS` holds each kind with its fixed sentences.

| Kind | Question id | Asked when | Run exit | Options |
|---|---|---|---|---|
| `spec-ambiguous` | `<NN>-spec-ambiguous-<n>` | A second dispute on a locked file that the reviewer ruled on in the ticket, or a second correction needed for a locked file of the ticket (a `test-wrong` ruling or a review finding on a file corrected before). The subject names the test, the requirement and the two readings; the evidence is `spec-ambiguous-<n>.json`. | 2 | No option is recommended. `keep-test`: the role that disputed runs again against the test; after a review finding, the ticket resumes from the review and findings on that file are closed. `correct-test`: one more correction, to the second reading, then the role that disputed runs again (the fixer of the same round after the review of its round); after a review finding, the review runs again. `edit-spec`: the ticket stays BLOCKED, with the ruling in the ledger. None of them changes the ticket's `disputes` or `corrected`. |
| `ticket-blocked` | `<NN>-ticket-blocked-<n>`, `final-ticket-blocked-<n>` | A ticket agent (test-writer, implementer, reviewer, fixer, conflict) or an agent of the end of run stopped with `BLOCKED` or `NEEDS_CONTEXT` or was rejected, and its rerun on the next entry of its list did too; or a finding is open after the scoped review of the final fix batch (one question per finding). The subject names the role and the reason, or the finding. | 2 | `retry` (recommended): the role runs again on its first entry from the tip the ticket left, with its one rerun; for a fixer, the round that stopped does not count: the driver gates and reviews the tip, and a fixer runs that round again on the review's findings. An arbitration or a correction runs for another role, so its work is not kept: after a dispute, the role that disputed runs again from the tip before them; after a review correction, the driver reviews that tip again. For the end of run, the stopped agent runs again from where the end of run is; for an open finding, one more fix batch and scoped review run. `edit`: the ticket, or the end of run, stays BLOCKED, with the ruling in the ledger, so the spec or the ticket can be edited. |

`final-finding` (fix or accept a finding open after the second round of the final review) became a `ticket-blocked` question of the end of run, id `final-ticket-blocked-<n>`: whether one more batch runs is the operator's call, and nothing ships with an open finding, so there is no `accept`.

`n` counts the questions of that ticket and kind in the run, so an id is never used twice. An open question of a ticket that has merged is dropped, with a ledger line: it leaves `decisions.json` and parks no run.

Answer with `--answer <question id>=<option id>` on the next run, or through the page, which gives `.agent/implement-answers.json`. An answers file counts only when it is submitted and its `revision` equals the `revision` of `decisions.json`. The driver ignores any other file or answer and says so in the ledger. Each answer becomes one `Ruling:` sentence in the ledger.

## The non-negotiable checks

| Check | Where it lives |
|---|---|
| Demonstrated red plus green for locked tests | `driver_report.py` `_red_errors`: each red line of a test-writer report (and of a correction) must be in its log. `driver_gate.py` `run`, called from `driver_run.py` `Run.gate` and `driver_fixloop.py` `_gate`: the fast gate gets the ticket's locked files in `IMPLEMENT_SPEC_LOCKED` and must be green on the candidate before its review. |
| Locked files changed only by the test-writer | `driver_locked.py` `errors`, one diff check after the implementer, a fixer, the ticket conflict agent, the final fixer and the rebase conflict agent report (the baseline from `for_ticket`, or at the end of run `for_end_of_run`): the ticket's own locked files against its last test-writer commit, every other ticket's against the merge base of the branch and the PR tip, at the end of run every ticket's against the tidied tip (for the rebase conflict agent, the recovery point). A changed file rejects the report. `driver.py` `_refuse_breach` (with `driver_locked.py` `landing_errors`), under the merge lock: the commit that would land against every other ticket's locked files as the PR tip has it; a changed file blocks the ticket with one `ticket-blocked` question. `driver_locked.py` `_unlisted_errors`: a test-writer that changes another ticket's locked file must list it in `locked`, and the reviewer gets its diff (`prompts/elsewhere.md`) to judge against the spec. |
| The full gate at landing and at the end of run | `driver.py` `_checked_landing`: the full gate runs on the commit that would land, a fast-forward as well as a merge commit, before the PR branch moves to it. `driver_final.py` `_review_until_reviewed` runs it after the tidy and after each fix batch. |
| The ticket reviewer and the final correctness lens (and the scoped review) on another provider than the implementer | `models.json`: the `reviewer` list starts on another provider than the `implementer` list, which a `--models` file can change. `driver_agents.py` `other_provider_entries`: the correctness lens and the scoped review run and rerun only on `final-reviewer` entries of another provider than the first `implementer` entry; `load_models` refuses a list with no such entry before any agent, and the preflight checks that entry. |

## Known limits

- **A `codex` agent cannot commit.** A role that commits (test-writer, implementer, fixer, conflict) on an `openai` entry runs with `--sandbox workspace-write`, and that sandbox cannot run `git commit`. If a custom `--models` file assigns such an entry to a committing role, replace it with an entry whose CLI can commit in the ticket worktree.
- **A ticket that is not merged stops the end of run.** While one ticket is BLOCKED, there is no tidy and no final review of the merged tickets.
- **A published PR branch keeps its history.** See "End of run".
