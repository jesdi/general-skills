# implement-spec driver

`run.sh` turns a spec and its tickets into one reviewed PR branch. It is a script, not a session: it walks the ticket graph, starts one agent for each role, runs the gate, merges, and stops only at a bounded question or a BLOCKED ticket. [SKILL.md](SKILL.md) only starts it.

This file is written from the code. Where it and `specs/implement-spec-driver/` differ, the code is right.

## Run it

Run it in the PR worktree, on the PR branch, with a clean tree:

```sh
sh skills/implement-spec/run.sh --gate 'pnpm check' --spec specs/<slug>/spec.md
```

Before any agent starts, the driver checks these. Each failed check prints one line, and the run then ends with exit 3.

- `.agent/` is gitignored and the worktree has no uncommitted change.
- Each ticket file `NN-name.md` has a title, a **Blocked by** line and a **Seam** line, and the graph has no cycle (`driver_tickets.py`). A ticket needs one criterion or more, unless its Seam is `none — refactor`. A ticket needs a **Touches** line when the set has more than one ticket. The [to-tickets skill](../to-tickets/SKILL.md) has the line format.
- Each model entry is valid, and the CLI of the first entry of each role is on PATH. For the correctness role, the entry is the first of its list on another provider than the first implementer entry: that list must have such an entry, and its CLI must be on PATH. This applies to every run, also when no ticket has a **Risk** line.
- The `review-diff` skill is installed next to this one: the final review of every run reads it, and so does the correctness pass.
- With `--push` or `--unattended`, the repository has an `origin` remote.
- Each model that the run can use answers one short test prompt (the preflight). A model that fails gives one line with its error. `--dry-run` skips the preflight.

A run that stopped is continued with the same command. `state.json` is the only input of a resume.

One driver runs in a repository at a time. The driver holds a kernel file lock on `implement-spec.lock` in the git common directory (`git rev-parse --git-common-dir`) for the whole run. Each worktree of the repository shares that directory, so a second driver in the same worktree or in another one ends with exit 3 and names the lock file. The file is not in a worktree, so `git status` never shows it. The lock ends with its process, so a driver that was killed leaves no lock behind. `--dry-run` takes no lock. A wrong call in a worktree that has no `.agent/implement/` takes no lock and makes no file; where an earlier run left that folder, the driver takes the lock first, then makes the checks.

## Flags

| Flag | Meaning |
|---|---|
| `--gate COMMAND` | Required. The check command of the repository, as one shell line. The driver runs it on each commit it is about to trust, with `IMPLEMENT_SPEC_BASE` set to the PR branch. |
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
| `--dry-run` | Validate, print `max frontier width: N`, change nothing. |
| `--runner SCRIPT` | Test only. The driver calls `<script> <role> <prompt> <report>` in the slot, in place of the CLI. |
| `--only ROLE` | Test only. Run one role (`test-writer`, `implementer`, `reviewer` or `correctness`) on the first ticket, with no gate and no merge. |
| `--help`, `-h` | Print the flags and exit. |

Default timeouts in minutes: explorer 30, test-writer 30, implementer 90, reviewer 45, correctness 30, fixer 60, final-reviewer 60, conflict 30, pr-writer 20.

`run.sh` downloads `uv`, Python 3.13 and `jsonschema` once into `~/.cache/implement-spec`. `IMPLEMENT_SPEC_CACHE_DIR` moves that cache. `IMPLEMENT_SPEC_OFFLINE=1` forbids the download. `run.sh` supports macOS and Linux only, on x86_64 or ARM64, and needs `curl`, `tar` and `sha256sum` or `shasum` for the first download.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Done: every ticket is merged and no question is open. Also the end of a valid `--dry-run`. In local mode without `--push`, nothing is pushed. |
| 1 | A ticket is BLOCKED, or the end of run is BLOCKED. The other tickets ran as far as they could. |
| 2 | Parked: a question of kind `locked-test` or `final-finding` is open. Exit 2 wins over exit 1. |
| 3 | Invalid input or a failed precondition: a usage error, a ticket error, a model error, an uncommitted change, a slot that cannot be made, another driver that runs in the same repository. No agent starts. The one exception: a review page that cannot be written gives exit 3 at the end of a run, after agents ran. |
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
| `<git common directory>/implement-spec.lock` | An empty file, not in the worktree. The driver that runs holds a kernel file lock on it (`driver_state.py` `lock`). Do not remove it during a run. |
| `.agent/implement/ledger.md` | One plain sentence for each event. Only the driver writes it, and it reads no fact from it. |
| `.agent/implement/summary.md` | The result in sentences, and the measurement table: gates, agents, wall time, fix rounds, human stops, cost. |
| `.agent/implement/decisions.json` | The open questions and their `revision`. Written only after a stop. |
| `.agent/implement/<NN>/` | One folder for each ticket. |
| `.agent/implement/<NN>/<role>-<K>.json`, `.log`, `.prompt.md` | The report, the log and the prompt of one agent. K is the fix round. |
| `.agent/implement/<NN>/gate-<sha7>.log` | One gate record: start, commit, status, command, output, exit, the state after the command, end. |
| `.agent/implement/<NN>/findings-<K>.json` | The merged findings of the review after fix round K, or the red gate of round K. |
| `.agent/implement/final/` | The same files for the end of run, and `pr-body.md`: the pr-writer's body, then the facts of the run. |
| `.agent/implement/pycache/` | Compiled Python of the gate, kept out of the slots. |
| `.agent/implement-review.html` | The page with the open questions. |
| `.agent/implement-answers.json` | The answers: `{"v": 1, "stage": "implement", "submitted": …, "revision": …, "answers": {…}}`. |
| `.agent/worktrees/slot-K/` | Slots 2..N. Slot 1 is the PR worktree. |
| `refs/implement/recovery` | A git ref: the PR tip before the tidy. |

## Roles of one ticket

A ticket is ready when each ticket in its **Blocked by** line is merged and no running ticket touches the same place. The places are the backticked entries of the **Touches** lines: `file` or `file: function`. Two entries are the same place when they name the same file and the same function, or when one of them names the whole file. Each ready ticket takes the lowest free slot.

1. **test-writer** (`prompts/test-writer.md`). It writes one black-box test for each criterion through the Seam and commits them red. Those files are locked. A ticket with Seam `none — refactor` has no test-writer.
2. **implementer** (`prompts/implementer.md`). It starts from that commit and makes the locked tests green.
3. **Gate.** The driver runs `--gate` on the ticket tip.
4. **Review.** The driver compares each locked file of the ticket with the test-writer commit. A file that differs is a critical finding `locked file changed: <path>`. Then the **reviewer** (`prompts/reviewer.md`) runs, and for a ticket with a **Risk** line the **correctness** pass (`prompts/correctness.md`) runs at the same time in the same slot.
5. **Merge.** The driver merges the reviewed tip into the PR branch with git plumbing, then deletes the ticket branch. A conflict goes to the **conflict** agent (`prompts/conflict.md`) once. Its resolution may change no locked file of the ticket. A second conflict blocks the ticket.
   **The landing check.** Merges are serial. Before the PR branch moves, the driver compares the tree that is about to land (the ticket tip of a fast-forward, or the merge commit) with the PR tip: each locked file of each merged ticket must be the same tree entry in both: the same blob with the same mode, so a file that became a symlink is a difference. A file that only one of them has is a difference. A file that the landing ticket lists in its own `locked` is not checked: the test-writer of a later ticket can add a test to a file that an earlier ticket locked, and must list that file. A test-writer report that changes such a file and does not list it is rejected, with the usual one rerun. A difference refuses the landing: the ticket is BLOCKED with `locked file changed: <path>` in the ledger, the PR branch does not move, and no fix round runs. The PR branch moves only from the tip that the check read; when another hand moved the branch in between, the driver makes the merge, the check and the gate again, one time. Only that case starts the landing again: a refused landing and a red merge gate block the ticket. The second try runs no conflict agent, so a conflict there blocks the ticket, and so does a branch that moved again.
   **Before a correction.** A test-writer that corrects locked tests (after `overrule-test`, or for a `fixture` finding or a `test` finding on a locked file) starts from the locked versions: the driver puts back, in the tree, each locked file of the ticket and each locked file of a merged ticket that the ticket changed, and the ledger names each file. A file of a merged ticket goes back to the version of the PR branch.

Rules for each agent:

- A call blocks until the agent ends. The driver does not poll.
- An agent gets one rerun: after a rejected report, after a timeout, or after it left the ticket branch. The rerun prompt is the first prompt plus the reason.
- A report with status `BLOCKED` or `NEEDS_CONTEXT` blocks the ticket.
- A BLOCKED ticket stops the tickets that it blocks. All other tickets run on.

### Findings and fix rounds

| Finding | What the driver does |
|---|---|
| kind `fixture`, any severity, or kind `test`, `critical` or `important`, on a locked file of the ticket | One test-writer correction, outside the fix rounds. The fixer may not edit a locked file, so a fix round cannot close a `test` finding there. The corrected files are the new locked versions, and the next review is a full review. A finding of this kind that is open after the correction blocks the ticket. |
| `critical` or `important` | One fix round. |
| `minor` | It stays in the ledger and in the findings file. The final reviewer gets the list. |

A fix round is one **fixer** (`prompts/fixer.md`), then the gate on the new tip, then a re-review of the range of the fix. A mutation proof is repeated only when the fix changed its production line.

A red gate on the commit of a fixer is a critical finding with source `gate`. Its file is the gate record, and the record holds the output. No review runs on that tip, and the next round starts. The re-review after that round covers each fix since the last review and repeats each proof that one of them made due. A red gate after round 4 blocks the ticket, as does a red gate on the commit of the implementer or of a test-writer correction.

- Rounds 1 and 2 run on the first entry of the implementer list.
- Rounds 3 and 4 run on the second entry of the implementer list. When the list has one entry, they stay on it and the ledger says so.
- There is no ruling at round 2.
- Findings that are open after round 4 make the ticket BLOCKED. The run ends with exit 1, and the driver writes one question of kind `finding` for each.

### End of run

The end of run starts only when every ticket is merged and every slot is idle. Its agents work in slot 1.

1. **Tidy.** The driver saves the PR tip in `refs/implement/recovery`. It then makes one commit for each ticket, in merge order, named `feat(NN): <title>`. Each commit holds the tests, the implementation and the fixes of its ticket. The tree of the new tip must equal the tree of the old tip, or the branch stays as it was.
2. **Rebase** onto the fetched tip of the default branch of `origin`. A conflict goes to the conflict agent (`prompts/rebase-conflict.md`) once, and the rebased tip is gated.
3. **Gate**, then the **final-reviewer** (`prompts/final-reviewer.md`) on the whole range, with the method of the `review-diff` skill. It gets the minor findings of the tickets and the rulings of the operator.
4. **Fix batch.** Critical and important findings go to one fixer (`prompts/final-fixer.md`). The driver names each of its commits `fix(NN): …` from the ticket in the report. Then the gate, then one scoped review of those commits.
5. Findings that are open after the second review become questions of kind `final-finding`. There is no third review.
6. **Push.** With `--push` or `--unattended`, the driver pushes the reviewed tip once, with no force, and opens the PR with `gh`, ready for review. The **pr-writer** (`prompts/pr-writer.md`) writes the body with the method of the `pr` skill; the driver puts the facts of the run after it: the ticket-to-commit map, the fix commits, the rulings and the review coverage (`prompts/pr-body.md`). A push stops before any agent when no `pr` skill is next to this skill, in `${CLAUDE_CONFIG_DIR:-~/.claude}/skills` or in `~/.agents/skills`. Without them it prints the two tips and the command to run again with `--push`.

History rules:

- The branch is rewritten once, before the first review, and never after it. Fixes of the final review stay as `fix(NN)` commits.
- A history that `origin` already has is never rewritten. When `origin` has the PR branch or one of its commits, the driver does no tidy and no rebase. The gate, the final review and a fast-forward push still run.
- The push happens only when the branch tip is the reviewed tip and that tip has a green gate record.

A repository with no `origin` remote skips only step 2 and step 6. The tidied commits stay on the commit that the PR branch had when the run started, and the review range starts there. One ledger sentence says that the rebase and the push were skipped. The driver prints the two tips and ends with exit 0. When you add `origin` after such an end of run, `--push` does not push: the branch is on no base of that remote, so the end of run is BLOCKED.

### Resume

- A green gate record is settled. The driver never runs the gate again for that commit.
- A ticket whose recorded tip is not on its branch goes back to `implement`, from the test-writer commit. A merged ticket whose tip is not in the PR branch does the same.
- A slot with the uncommitted work of an interrupted role stays as it is. The role runs there again and is told so.

## Models

`models.json` holds the default list for each role. An entry is `provider/model@effort`; an entry with no provider is `anthropic`.

| Provider | CLI | Efforts |
|---|---|---|
| `anthropic` | `claude -p` | low, medium, high, xhigh, max |
| `openai` | `codex exec` | minimal, low, medium, high, xhigh |

The driver uses the first entry of a role. The fixer of a ticket runs on the `implementer` list; the `fixer` list is only for the fix batch of the final review. The driver uses a later entry in two cases only: fix rounds 3 and 4 (the implementer list), and the correctness pass, which takes the first correctness entry of another provider than the first implementer entry. The reviewer never does the correctness pass: when the list has no such entry, or the CLI of that entry is not on PATH, the run ends with exit 3 before any agent. The `explorer` list is read and checked, but the driver starts no explorer.

The defaults need both `claude` and `codex` on PATH. The `openai` defaults name the full Codex model names (`gpt-6-astra`, `gpt-6.1-sol`), because a Codex CLI that is signed in with a ChatGPT account does not accept the short aliases. Read "Known limits" before you use them as they are.

## Report schema per role

Each agent writes one JSON report. The schema of a role is the `json` block of its prompt template, so the agent reads the text that the driver enforces. `driver_report.py` `validate` checks the report against the schema, against git and against the log of the agent. A field that the schema does not name is ignored.

Every report has `status` (`DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, `BLOCKED`), `commits`, `files` and `concerns`.

| Role | Template | More fields | Checks after the schema |
|---|---|---|---|
| test-writer | `prompts/test-writer.md` | `locked` (test file paths), `red` (`test`, `line`) | The driver reads a test node id `<file>::<test>` in `locked` as its file. With DONE: one commit, one locked file and one red line or more. Each locked path is in a commit that the role made. Each `line` is in the log. A locked file of a merged ticket that the role changed is in `locked`. A correction (after `overrule-test`, or for a review finding) changes only the files that it lists in `locked` and the files that the driver restored before it. |
| implementer | `prompts/implementer.md` | `green` | With DONE: one commit or more. |
| reviewer | `prompts/reviewer.md` | `findings` (`severity`, `file`, `line`, `title`, `evidence`, `kind`), `mutations` (`test`, `file`, `line`, `red`); `commits` is empty | In the first review of a tip: `mutations` is required, with a proof for each locked test file (`test` is `<locked file>::<test name>`). Each `red` is in the log. |
| correctness | `prompts/correctness.md` | the same as the reviewer; `commits` is empty | `mutations` is optional. |
| fixer | `prompts/fixer.md` | `green`, `ticket` | With DONE: one commit or more. |
| final-reviewer | `prompts/final-reviewer.md` | `findings` | None. |
| pr-writer | `prompts/pr-writer.md` | `body`; `commits` is empty | None. |
| fixer of the final review | `prompts/final-fixer.md` | `green`; `commits` is a list of (`sha`, `ticket`) | Each new commit on the branch is in `commits`, with a ticket of the run. |
| conflict | `prompts/conflict.md`, `prompts/rebase-conflict.md` | none | With DONE: one commit or more. No locked file may change. |

For each role, every commit in `commits` must be new since the role started and on its branch. `severity` is `critical`, `important` or `minor`. `kind` is `code`, `test` or `fixture`. A read-only `codex` agent cannot write a file, so it gives the JSON as its last message and the CLI saves it as the report.

## Question kinds

A question is a bounded choice for the operator. `driver_decisions.py` `KINDS` holds each kind with its fixed sentences.

| Kind | Question id | Asked when | Run exit | Options |
|---|---|---|---|---|
| `locked-test` | `<NN>-locked-test-<n>` | An implementer or a fixer reports BLOCKED with a concern that starts with `locked test contradicts spec:`. | 2 | `overrule-test`: the test-writer corrects the test from that report, then the implementer runs again, and the fix rounds count from 0: the corrected tests are a new contract. `overrule-implementer` (recommended): the implementer runs again. |
| `finding` | `<NN>-finding-<n>` | A finding is open after fix round 4. One question for each finding. | 1 | `accept`: close the finding; the ticket merges when each of its findings is accepted. `split` (recommended) and `redesign`: the ticket stays BLOCKED, with the ruling in the ledger. |
| `final-finding` | `final-finding-<n>` | A finding is open after the second round of the final review. | 2 | `fix` (recommended): the finding stays open and nothing is pushed. `accept`: close the finding. |

`n` counts the questions of that ticket and kind in the run, so an id is never used twice.

Answer with `--answer <question id>=<option id>` on the next run, or through the page, which gives `.agent/implement-answers.json`. An answers file counts only when it is submitted and its `revision` equals the `revision` of `decisions.json`. The driver ignores any other file or answer and says so in the ledger. Each answer becomes one `Ruling:` sentence in the ledger.

## The five non-negotiable checks

| Check | Where it lives |
|---|---|
| Locked tests with a demonstrated red | `driver_report.py` `_proof_errors`: each red line of the test-writer report must be in its log. `driver_review.py` `locked_findings`: a locked file of the ticket that differs from the test-writer commit is a critical finding, before each review. `driver_merge.py` `refused`, called from `driver.py` `_checked_landing` under the merge lock: a locked file of a merged ticket that differs between the PR tip and the tree that is about to land refuses the landing, so a later ticket cannot change the locked test of an earlier one. `driver_report.py` `_merged_errors` rejects a test-writer report that changes such a file and does not list it. |
| One mutation proof for each locked test | `prompts/reviewer.md` asks for one proof for each locked test. `driver_report.py` `_locked_errors` and `_proof_errors` reject a first review with no proof for a locked test file, or with a red line that is not in the log. `driver_review.py` `follow` finds the proofs that a fix makes due again. |
| One independent correctness pass for each risky ticket | `driver_fixloop.py` `_review` adds the pass for a ticket with a **Risk** line. `driver_agents.py` `correctness_entry` picks the entry of another provider, and `load_models` stops the run when there is none or its CLI is not on PATH. |
| One full gate for each candidate | `driver_gate.py` `run`, called from `driver_run.py` `Run.gate`: one record for each commit the driver is about to trust. Green is exit 0 on a tree that is clean before and after, with HEAD not moved. |
| Post-merge check on the PR branch | `driver.py` `_checked_landing`: a merge commit is gated before the PR branch moves to it. A fast-forward of a gated tip uses the record of that tip. `driver_final.py` `_review_until_reviewed` gates the tip again after the tidy and after the fix batch. |

The driver enforces the mutation proof for each locked test file, not for each locked test.

## Known limits

- **A `codex` agent cannot commit.** A role that commits (test-writer, implementer, fixer, conflict) on an `openai` entry runs with `--sandbox workspace-write`, and that sandbox cannot run `git commit`. In the default `models.json` this is the second implementer entry, so fix rounds 3 and 4 fail. What to do: give a `--models` file with no `openai` entry in the `implementer`, `test-writer`, `fixer` and `conflict` lists.
- **A `codex` reviewer cannot make a mutation proof.** A reviewer on an `openai` entry runs with `--sandbox read-only` and cannot make the temporary edit. The default `models.json` has such an entry first for the reviewer, and the first review needs the proofs. What to do: give a `--models` file whose reviewer list starts with an `anthropic` entry.
- **The correctness pass reads a tree that the reviewer changes.** The two agents share one slot, and the reviewer makes temporary edits. `prompts/correctness.md` tells the pass to read from git at the reviewed commit and to run no tests. It reads only the spec, the ticket and the findings file from the working tree, because git can lack them: `.agent/` is ignored. That rule is the only guard: the driver cannot enforce it, so a pass that ignores it can report a false finding and start a fix round. What to do: when a correctness finding looks wrong, compare it with `git show <reviewed commit>:<file>` before you rule on it.
- **A ticket that is not merged stops the end of run.** While one ticket is BLOCKED, there is no tidy and no final review of the merged tickets.
- **A published PR branch keeps its history.** See "End of run".
