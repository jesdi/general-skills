# implement-spec driver — Design

## Decisions

- **Lives in `skills/implement-spec/` in the crap-gate shape** (`run.sh` uv bootstrap copied,
  Python 3.13, `driver_*.py` with `test_*.py` beside them) — one more copy of the bootstrap,
  against one less cross-skill dependency; prompts, schema and loop version together.
- **Stdlib plus `jsonschema`** for report validation — one pinned dependency, against a
  hand-written validator that would grow with every role field.
- **One process, one state writer.** Parallel tickets run as threads inside the driver; every
  `state.json` write is a read-modify-write under one lock, atomic through rename — no
  cross-process locking of writers, against the lost-update race the red-team found. A second
  driver process is kept out as a whole: the driver holds one exclusive `flock` on
  `.agent/implement/lock` for its run, and a second one exits 3 — the kernel frees the lock
  with the process, against two drivers that write one state and move the same refs.
- **End-of-run work starts only when every slot is idle** and the PR branch is never checked
  out in slot 1 while a ticket occupies it; merges into the PR branch use plumbing — serial
  tail, against a tidy rewriting history under a running ticket.
- **Branch hygiene after every agent**: the driver confirms the ticket branch exists and is
  checked out in its slot, and never deletes a branch that any slot has checked out — a few
  `git` calls per agent, against detached-head commits that fail the SHA cross-check.
- **Reports are a trust boundary**: every SHA in `commits` must be new since the role started
  and on the ticket branch; an implementer report with no new commit is a violation; every
  claimed red line (test-writer `red`, reviewer `mutations`) must appear in that agent's log —
  against a ticket that merges nothing and a mutation proof that was never run.
- **Answers are bound to a decisions revision**: `decisions.json` carries a `revision`
  (SHA-256 of its own content without that field), question ids are unique per run
  (`<NN>-<kind>-<n>`, and `<kind>-<n>` for a question of the end of run, such as
  `final-finding-1`; `n` counts the questions of that ticket and kind), and an answers file whose `revision` differs is ignored with a
  ledger line — the PR 172 rule, against an old answer applied to a new question.
- **Model entries in the agent-ops grammar** `provider/model@effort`, one ordered list per
  role in `--models <file>` — no vocabulary of the driver's own; escalation is "next entry".
- **Blocking agent calls**, `claude -p` and `codex exec -o`, with a per-role timeout that
  doubles on the one rerun and keeps the tree — no polling, against the 27 % of turns Stage 2
  spent polling; a hard ticket costs one extra attempt, not a reset.
- **Codex without `--ephemeral`**, sandbox `workspace-write` for committing roles and
  `read-only` for reviewers — rollouts stay on disk as the trace.
- **The driver runs the gate**, one record per trusted commit, never repeated for a green
  commit — against 65 gates for six tickets; the record is trusted by construction.
- **Gate is one repo-owned command** with `IMPLEMENT_SPEC_BASE` in its environment — the
  driver knows no crap-gate, Python or Node.
- **Slots, not worktrees per ticket**: slot 1 is the PR worktree, slots 2..N created and
  prepared once by a repo-owned `--prepare` — dependencies installed at most N-1 times.
- **No ruling at round 2**; escalation at round 3; BLOCKED after round 4 and the run goes on
  — against an LLM saying "continue" twice.
- **Human stops are bounded questions** rendered through the review-page template, answers in
  PR 172's schema; anything else is BLOCKED — one format for terminal and console.
- **Local never pushes without `--push`; unattended pushes by itself** once validated —
  nothing leaves the machine before the final review in either mode.
- **Tidy by script, no rewrite after the final review** — fixes stay as `fix(NN)` commits;
  one top-model review instead of two.
- **Ledger is write-only prose; `state.json` is the resume contract** — against resuming from
  prose that compaction had corrupted.
- **`SKILL.md` becomes a launcher**; method text moves to the prompt templates and a README.

## Data model

No database. Every record is a file under `.agent/implement/` or a git object; the
"constraints" are the checks the driver runs before it trusts a record.

| Table | Field | Constraint | Why |
|---|---|---|---|
| `state.json` | `run.revision` | NOT NULL, random per run | ties decisions, answers and slots to one run |
| `state.json` | `run.pr_branch` | the branch the first run started on; a later run must be on it | slot 1 is off the branch while tickets run, so the record names it |
| `state.json` | `run.start` | the PR tip when the run began; must be an ancestor of the PR tip at the tidy | the parent of the first ticket commit |
| `state.json` | `run.landed[]` | `[ticket, the PR tip its merge made]`, in merge order, one entry per ticket | the tidy gives each ticket commit that tree |
| `state.json` | `run.questions[]` | every question of the run; `answer` is null while it is open, and is set once | `decisions.json` is derived from it |
| `state.json` | `run.gates`, `run.stops`, `run.agents[]` | counters and one `{ticket, role, entry, seconds, cost}` per agent run | the measurement table of the summary |
| `state.json` | `run.final` | `{skipped, recovery, tidied, onto, base, commits, reviewed, round, open, batch, green_gates, pushed, pr}`; `round` 0..2; every key is there from the start of the run | the resume contract of the end of run; a wrong key name fails |
| `state.json` | `tickets[NN].phase` | one of `pending, test-writer, implement, gate, review, fix, merge, merged, blocked` | the state machine; any other value fails resume |
| `state.json` | `tickets[NN].round` | 0..4 | the fix-loop budget |
| `state.json` | `tickets[NN].test_writer_sha`, `locked` | set together by the test-writer; a correction moves `test_writer_sha` to its commit and only adds to `locked` | the locked-file check needs both |
| `state.json` | `tickets[NN].held` | locked file -> the older test-writer commit that stays its reference after a correction that did not list it | a change the implementer made before the correction stays a finding |
| `state.json` | `tickets[*].locked` of each merged ticket | unchanged in a later ticket since that ticket's `test_writer_sha` (a refactor ticket: since the commit its branch started from), at each review; equal to the PR tip after a resolved merge conflict | only a test-writer changes a locked file, so a later ticket cannot weaken the locked test of an earlier one |
| `state.json` | `tickets[NN].note` | "" or the name of a prompt template | tells the next role what a ruling or an interrupt left |
| `state.json` | `tickets[NN].tip` | must be on `ticket/NN` at resume; for a merged ticket, in the PR branch | an unverifiable tip returns the ticket to `implement` from the test-writer commit |
| `state.json` | `tickets[NN].green_gates[]` | each names one commit and settles only that commit | a settled gate is never rerun |
| `state.json` | `tickets[NN].slot` | unique among running tickets | one ticket per slot |
| `state.json` | whole file | written under one lock, rename-atomic | no lost update between parallel tickets |
| `.agent/implement/lock` | whole file | exclusive `flock`, non-blocking, held by the one driver that runs; not taken by `--dry-run` | no second driver on the same state, slots and refs |
| report | `status` | enum DONE, DONE_WITH_CONCERNS, NEEDS_CONTEXT, BLOCKED | schema violation otherwise |
| report | `commits[]` | on `ticket/NN`, newer than the role's start | reports cannot claim old work |
| report (implementer) | `commits[]` | at least one | an empty implementation is a violation |
| report (test-writer) | `locked[]` | each path exists in `test_writer_sha` | locked set is verifiable |
| report (test-writer) | `red[].line` | substring of the agent's log | a red that was never seen is a violation |
| report (reviewer) | `mutations[].red` | substring of the agent's log | a mutation proof that was never run is a violation |
| report (reviewer) | `findings[].severity`, `kind` | enums `critical, important, minor` / `code, fixture, test` | routing keys |
| gate record | `commit`, `status`, `exit` | commit is the slot's `HEAD`, status empty | a record on a dirty tree is not a record |
| `decisions.json` | `revision` | SHA-256 of the file without the field | answers bind to it |
| `decisions.json` | `questions[].id` | unique per run, pattern `[a-z0-9_-]{1,64}` | PR 172's id grammar; the page's `data-q` |
| `decisions.json` | `questions[].options` | 2..4, `recommended` is one of them | the page renders radio groups |
| `implement-answers.json` | `revision` | equals `decisions.json.revision` | a stale answer is ignored |
| `implement-answers.json` | `answers[qid]` | one of that question's option ids | an unknown option is ignored and logged |
| ticket branch | `ticket/NN` | not checked out in any slot when deleted | no detached slot |
| PR branch | `feat/...` | never checked out in slot 1 while a ticket runs there; end-of-run only with all slots idle | no rewrite under a running ticket |

## Identities

None.

## Seams

- `sh skills/implement-spec/run.sh …` — new. The one high seam: every scenario in `spec.md`
  is driven by running the driver against a throwaway fixture repository (a tiny Python
  package plus the three tickets, built in a temporary directory by the tests) with a fake
  agent runner (`--runner <script>`, a test-only flag: the driver calls the script with the
  role, the prompt path and the report path, in the slot directory, and treats it exactly
  like a CLI; nothing more) or with the real CLIs.
- `driver_tickets.parse(path)` — new. Ticket files in, graph or error list out. Tested
  directly because requirement 1's error messages are the unit under test.
- `driver_report.validate(role, path, context)` — new. Report in, error list out.
- `driver_review_page.render(decisions) -> html` — new. Decisions in, page out, checked
  against `skills/review-page/schema.md`.
- `skills/to-tickets/SKILL.md` — existing. The template gains `Risk` and `Suspicions`.
- `skills/implement-spec/SKILL.md` — existing. Becomes the launcher.
