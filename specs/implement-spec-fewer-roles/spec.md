# implement-spec fewer roles — Spec

Source: [general-skills issue #146](https://github.com/jesdi/general-skills/issues/146),
user stories 21, 22, 24, 25 and 26, implementation increment 4. Story 23 (the ticket reviewer
on another provider than the implementer) is a check that stays as it is. Increments 1–3
(five-ticket limit, review scope and cap, gate tiers) are on main. Closes
[issue #141](https://github.com/jesdi/general-skills/issues/141): with no mutation proof, a
manual e2e test file no longer refuses a reviewer report.

Terms: a **lens** is one of the three reviewers of the review-diff method: **spec** (did we
build what the spec asked, no more, no less), **correctness** (races, idempotency, money, time,
authorization, migrations, error paths) and **structure** (deep-quality-review). A **full
review** is a ticket review with no fix range (round 1, and the review after a test-writer
correction).

## Requirements

1. The driver has no `correctness` role. A ticket review runs exactly one agent, the reviewer,
   for a ticket with or without a Risk line. The `correctness` prompt, its model list, its
   timeout and `--only correctness` are gone; `--only correctness` exits 3 as an unknown role.
2. The reviewer's prompt carries the ticket's Risk line as one more criterion to check, and
   says so when the ticket has none. A risk the diff does not handle is a finding with basis
   `criterion`, under the same round and cap rules as any unmet criterion: before the cap it
   starts a fix round, at the cap it blocks the ticket.
3. No ticket review asks for or validates a mutation proof. The reviewer schema has no
   `mutations` field, the `full-review` note and the re-review's list of proofs due again are
   gone, and report validation rejects no reviewer report for a missing proof. A reviewer
   report that still carries a `mutations` field is not rejected for it.
4. The test-writer's `fixtures` field is gone: its only reader was the mutation proof. A
   test-writer report that still carries it is not rejected for it. The test-writer's
   `red` lines and a correction's `red` lines are still checked against the agent's log.
5. A test that is green before any implementation is never accepted as the test-writer's
   proof: the test-writer rewrites it until it fails. The way out of naming a production-code
   mutation in `concerns` is gone from the test-writer's prompt, and so is the reviewer's
   instruction to rerun those mutations. This is a prompt rule: the driver does not check that
   each locked test has a `red` line, as today.
6. The final review runs three agents at the same time on the tidied tip, one per lens, each
   with its own prompt and its own report: `final-spec`, `final-correctness`,
   `final-structure`. The spec and structure agents run on the first `final-reviewer` entry;
   the correctness agent runs on the first `final-reviewer` entry on another provider than the
   first implementer entry. A `final-reviewer` list with no such entry exits 3 before
   any agent, naming the list and the implementer entry. The three agents never see each
   other's reports.
7. The spec agent follows review-diff's `spec.md` with the run's spec; the correctness agent
   follows review-diff's `correctness.md`, inlined into its prompt; the structure agent follows
   the deep-quality-review skill. A run whose review-diff `spec.md` or `correctness.md`, or
   whose deep-quality-review skill, is missing exits 3 before any agent and names the missing
   file. Each agent gets the full gate record of the tip as its gate evidence and runs
   no gate.
8. The carried observations and must-fix findings from ticket reviews go to all three agents.
   A must-fix finding stays open until the fix batch addresses it and the scoped review
   verifies it, as today.
9. The findings of the three agents and the driver's locked-file findings merge into one list
   in `findings-0.json`: each finding names its lens (`spec`, `correctness`, `structure` or
   `driver`), and two findings with the same file, line and title are one finding: the more
   severe one stays, a tie goes to the earlier lens in the order spec, correctness, structure. The ledger line
   of each finding names its lens. Critical and important findings of the merged list feed one
   fix batch, as today.
10. After the fix batch, one scoped re-review runs: one agent, on the correctness agent's
    entry (the provider the implementer did not use, so not the fixer's either when the fixer
    shares the implementer's provider), that checks each finding of the merged list was fixed,
    verifies each must-fix item, and checks the fix commits break nothing else. No third
    review round starts, as today.
11. When one of the three agents reports `BLOCKED` or `NEEDS_CONTEXT`, or the driver rejects
    its report and its rerun, the final review stops the way a stopped or rejected final
    reviewer stops it today, after the other agents have ended, and no fix batch runs on that
    round. The reports of the lenses that finished are kept: when the review runs again on the
    same tip, only the lens that stopped runs again. A review of another tip runs all three.
12. The `explorer` model list is gone. A `--models` file that names a role the driver does not
    have exits 3 before any agent and names the role. For `correctness` and `explorer` the
    error also says the role was removed and the key should be deleted from the file.
13. The preflight and the summary's agent counts follow the new roles: the preflight checks
    the entries of `final-spec`, `final-correctness` and `final-structure` and no correctness
    entry; `summary.md` counts each final lens agent under its own role name.
14. The README, SKILL.md and ARCHITECTURE.md describe one ticket reviewer, no mutation proof
    and the three-lens final review. The README's checks table loses the mutation-proof and
    correctness-pass rows and gains a row for the final correctness agent on another provider;
    the known issue about a `codex` reviewer that cannot make a mutation proof is gone.

## Scenarios

### Scenario: a risky ticket gets one reviewer

- **Given** ticket 01 has `**Risk:** two runs at once can both write the ledger` and its
  implementer reports DONE on `a1b2c3d` with a green fast gate
- **When** the driver reviews `a1b2c3d`
- **Then** exactly one agent runs in the review, the reviewer, its prompt contains the Risk
  line as a criterion, and no `correctness-1.json` report or correctness log exists

### Scenario: a ticket without a Risk line

- **Given** ticket 02 has no Risk line
- **When** the driver reviews its candidate
- **Then** the reviewer's prompt says the ticket names no risk, and the review runs one agent

### Scenario: an unhandled risk is an unmet criterion

- **Given** ticket 01 at round 0 with the Risk line above, and the reviewer reports one
  critical finding with basis `criterion` titled `concurrent writes interleave ledger lines`
- **When** the driver acts on the review
- **Then** fix round 1 starts with that finding

### Scenario: an unhandled risk at the cap blocks

- **Given** ticket 01 has completed fix rounds 1 and 2 with a green fast gate, and the
  round-2 re-review still reports the `criterion` finding on the risk; independent ticket 03
  is ready
- **When** the driver acts on the re-review
- **Then** ticket 01 is blocked without merging, and ticket 03 continues

### Scenario: a full review without mutation proofs is accepted

- **Given** ticket 14's only locked file is `scripts/test_e2e_manual.py`, which the gate does
  not run, and the round-1 reviewer report has `findings: []` and no `mutations` field
- **When** the driver validates the report
- **Then** the report is accepted on its first run, the ledger has no `no mutation proof` line,
  and the ticket proceeds to landing

### Scenario: a leftover mutations field is not a rejection

- **Given** a reviewer report that carries `mutations: [{"test": "tests/test_csv.py::test_quote",
  "file": "csv.py", "line": 12, "red": "AssertionError"}]` whose `red` line is not in its log
- **When** the driver validates the report
- **Then** the report is accepted and the field is ignored

### Scenario: prompts ask for no proof

- **Given** a run of two tickets with a round-1 review, a fix round and a re-review
- **When** the driver writes the reviewer's prompts
- **Then** no reviewer prompt contains `mutation`, and the re-review prompt names no
  production lines due again

### Scenario: no escape for a test green before implementation

- **Given** a run of one ticket with a test-writer and a full review
- **When** the driver writes the test-writer's and the reviewer's prompts
- **Then** the test-writer prompt tells it to rewrite a test that is green before any
  implementation until it fails, and offers no mutation named in `concerns` instead; the
  reviewer prompt has no instruction to rerun a mutation from the test-writer report

### Scenario: the correctness role is gone from the command line and models

- **Given** a clean PR worktree and three valid tickets
- **When** the operator starts the driver with `--only correctness`
- **Then** it exits 3 naming `correctness` as an unknown role and launches no agent

### Scenario: a models file with a removed role is refused

- **Given** a `--models` file `{"correctness": ["openai/gpt-6-astra@medium"]}`
- **When** the operator starts the driver
- **Then** it exits 3 with an error naming the role `correctness`, saying it was removed and
  its key should be deleted, launches no agent, and the preflight does not run

### Scenario: the final review runs three lenses

- **Given** two merged tickets, the tidied tip `t1t2t3t` with a green full gate record, the
  implementer list starting with `claude-sonnet-5-5@medium` and the `final-reviewer` list
  `["claude-opus-5-5@medium", "openai/gpt-6-astra@medium"]`
- **When** the driver runs the final review
- **Then** `final-spec` and `final-structure` run on `claude-opus-5-5@medium`,
  `final-correctness` runs on `openai/gpt-6-astra@medium`, all three get the gate record of
  `t1t2t3t`, each writes its own report, and no prompt of one names another's report

### Scenario: no final-reviewer entry on another provider

- **Given** the implementer list starts with `claude-sonnet-5-5@medium` and a `--models` file
  sets `final-reviewer` to `["claude-opus-5-5@medium"]`
- **When** the operator starts the driver
- **Then** it exits 3 before any agent, naming the `final-reviewer` list and the implementer
  entry `claude-sonnet-5-5@medium`

### Scenario: a missing lens method stops the run before any agent

- **Given** review-diff is installed but the deep-quality-review skill is not
- **When** the operator starts the driver
- **Then** it exits 3 naming the missing deep-quality-review skill and launches no agent

### Scenario: the lens findings merge into one batch

- **Given** `final-spec` reports one important finding `missing --dry-run flag` at `cli.py:40`,
  `final-correctness` reports one critical finding `ledger write not atomic` at
  `ledger.py:12`, and `final-structure` reports one important finding `ledger write not atomic`
  at `ledger.py:12` and one minor finding
- **When** the driver settles the round
- **Then** `findings-0.json` holds three findings, the `ledger.py:12` one once, each naming its
  lens; the fix batch gets the two critical or important ones; and the ledger line of each
  finding names its lens

### Scenario: must-fix findings reach every lens and the fix batch

- **Given** ticket 02 merged at the cap with the must-fix finding `quote escaping` at `csv.py:30`
- **When** the final review runs
- **Then** each of the three prompts lists `quote escaping`, the fix batch gets it, and the
  scoped review must list it in `resolved_must_fix` or the run blocks, as today

### Scenario: one scoped re-review after the batch

- **Given** the fix batch of round 1 made `f1f2f3f` with a green full gate
- **When** the driver reviews `f1f2f3f`
- **Then** exactly one agent runs, on `openai/gpt-6-astra@medium`, its range is
  `t1t2t3t..f1f2f3f`, and no third round starts whatever it finds

### Scenario: a clean final review

- **Given** all three lenses report no critical or important finding and no must-fix finding
  is carried
- **When** the driver settles the round
- **Then** no fix batch and no scoped review run, and the tidied tip is the reviewed tip

### Scenario: a lens that stops halts the round

- **Given** `final-structure` reports `BLOCKED` and the other two report findings
- **When** the driver settles the round
- **Then** the run stops as a stopped final reviewer stops it today, no fix batch runs, and
  the ledger names `final-structure`

### Scenario: a retry reruns only the lens that stopped

- **Given** the round on `t1t2t3t` stopped because `final-structure` reported `BLOCKED`, and
  `final-spec-0.json` and `final-correctness-0.json` were accepted for `t1t2t3t`
- **When** the operator answers `retry` and the driver reviews `t1t2t3t` again
- **Then** only `final-structure` runs, and `findings-0.json` merges its new report with the
  two kept ones

### Scenario: the summary counts the lens agents

- **Given** a run whose final review ran the three lenses and one scoped review
- **When** the run writes `summary.md`
- **Then** the agent counts have no `correctness` row and one row for each lens role

## Out of scope

- Question kinds, locked-test arbitration, the rerun of a stopped agent on its second model,
  the landing locked-file check and the restore machinery (increment 5).
- Any mutation step in the driver.
- Changing the demonstrated red plus green standard for locked tests.
- The 5-ticket cap in to-openspec and to-tickets (issue #162).
- More than one PR from a run.
