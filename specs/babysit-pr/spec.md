# babysit-pr — Spec

Terms:

- **`pr-risk`** and **`babysit-pr`** are the two new skills in `skills/`. `/pr` is the external
  skill that writes the PR body.
- The **block** is the fenced ```` ```merge-risk v1 ```` block under `## Merge Danger` in a PR
  body.
- The **policy** is `.merge-risk.json` in the repo root, merged with the defaults of `pr-risk`.
- A **category** is one item of the fixed list: `docs, tests, ci, deps, config,
  schema/migration, auth/security, public-api, infra, money, data-delete, ui, logic`.
- The **gate** is `merge_risk.py` (in `pr-risk`): it parses the block, loads the policy, maps
  the diff paths to categories and gives a verdict `auto | human`.
- The **judge** is a fresh agent with no context from the session, from the other vendor when it
  is installed.
- The **babysitter** is the agent session that runs `babysit-pr` on one PR.
- A **handoff** is the end of a babysit with the verdict human: a PR comment, a label, a
  notification, and the babysitter stops.
- The **lease** is the claim of one babysitter on one PR, stored on GitHub.
- The **base** is the PR's `baseRefName`. No step uses `main` as a fixed value.
- The **owner** is the login of `gh api user --jq .login`.

Unless a scenario says otherwise: the repo is `jesdi/demo` on GitHub; the owner is `jesdi`; the
base is `main`; the policy file is `{"agentMerge": true}`; CI has the jobs `test` (`pytest`) and
`lint` (`ruff check .`); the repo allows rebase and merge commits; the agent is Claude Code with
both skills installed globally; `codex` is installed.

## Prerequisites

These changes are in the `claude-config` repo, in a separate PR. That PR must merge before the
scenarios in "the base moves" can pass. This spec does not write tickets for them.

- P1. `block-dangerous-git.sh` allows one forced push form, and only this form:
  `git push --force-with-lease=<branch>:<sha> origin <branch>`, where `<branch>` is not
  `main`, `master` or the base of the PR, and `gh` shows an open PR from `<branch>` whose author
  is the owner. Every other forced push stays blocked.
- P2. The same script is a PreToolUse hook on `Bash` in Codex too (`~/.codex/hooks.json`). It
  denies with the JSON output `permissionDecision: deny`, which works in both agents. The exit
  code 2 form is not verified in Codex.
- P3. The force-push rule in `CLAUDE.md` names the P1 exception, and Codex sessions get the same
  git rules.
- P4. A Codex profile `[profiles.babysit]` in `~/.codex/config.toml` turns on
  `sandbox_workspace_write.network_access`, so `gh` and `git push` need no approval prompt.
  Other sessions keep network off. The owner starts Codex with `--profile babysit` to babysit.
  The `babysit-pr` skill says this, and without network it hands off at once with "no network
  for gh" instead of waiting on an approval.

## Requirements

### CLI: hooks that a skill ships

1. A skill folder can contain `hooks.json` in the agent hook format (event → matcher → command).
   `install` registers each hook in the config of each agent the skill is installed for, at the
   skill's scope: global in `~/.claude/settings.json` and `~/.codex/hooks.json`, project in
   `<project>/.claude/settings.json` and `<project>/.codex/hooks.json`. A global command runs
   the script in `~/.my-skills/<skill>/hooks/`. A project command finds the script through
   `git rev-parse --show-toplevel` and exits 0 with no output when the script is missing, so a
   committed config works on a machine without the store. A second `install` adds nothing.
   The `.agents/skills` target (the agent-agnostic standard, for OpenCode later) gets no hooks;
   the install prints that the skill starts by hand there.
2. `uninstall` removes the hooks of the skill and only those. `update` keeps the command string
   of an unchanged hook the same, so Codex does not ask for trust again.
3. After `install`, and after an `update` that changes a command string, the CLI prints the Codex
   trust steps. The CLI never writes `trusted_hash` or any other key in `~/.codex/config.toml`.

### The block and the policy

4. The parser accepts only this subset: one `key: value` per line, inline lists `[a, b]`, a
   `- item` list only under `reasons`, `#` comments. Fields: `door` (`one-way | two-way`),
   `blast_radius` (`isolated | module | service | consumers`), `touches` (categories),
   `rollback` (`revert | revert+redeploy | data-fix | impossible`), `verdict` (`auto | human`),
   `reasons` (one or more). Each other form, an unknown key, a missing key, or a second block is
   invalid.
5. The policy has these keys: `agentMerge` (default `false`), `alwaysHuman` (default
   `schema/migration, auth/security, money, data-delete, infra`; the repo list replaces it),
   `paths` (category → globs, added to the default globs, never removing one), `mergeMethod`
   (`rebase | merge | squash`, default none), `judgeMaxLines` (default 1500), `waitMinutes`
   (default 60). An unknown key or a wrong type is an error, and the verdict is human. The gate
   reads the policy from `origin/<base>`, never from the PR head. A diff that changes
   `.merge-risk.json` has the category `config` and is human.
6. Default paths: `docs` is `docs/**`, `README*` and `*.md` in the root. `**/SKILL.md`,
   `AGENTS.md`, `CLAUDE.md` and `**/prompts/**` are `logic`, never `docs`. Defaults also cover
   migrations (`**/migrations/**`), lockfiles (`deps`), `.github/workflows/**` (`ci`) and
   `Dockerfile*` (`infra`). A changed file that matches no glob is `logic`.
7. The gate gives human when any of these is true, and else auto: no block or an invalid block;
   `agentMerge` is false; `door` is `one-way`; `rollback` is not `revert`; `touches` or the diff
   has a category in `alwaysHuman`; the diff has a category that `touches` does not list; the
   counted lines are more than `judgeMaxLines`. The gate never uses the `verdict` field of the
   block. Each result lists every reason that made it human.
8. Counted lines are the added plus removed lines of files that are not `docs` and not
   generated (lockfiles, snapshot files, files with a `@generated` marker).

### pr-risk: the gate at creation

9. In a repo with `.merge-risk.json`, a PreToolUse hook on `Bash` denies `gh pr create` when the
   body from `--body` or `--body-file` has no valid block, and names the fix: "/pr, then
   /pr-risk". It also denies `--fill`, `--fill-first`, `--web`, and a `gh pr create` with no
   body flag. It allows every command in a repo without `.merge-risk.json`, and every command
   that is not `gh pr create`. The same script works for Claude Code and Codex.
10. `pr-risk` writes the block from the policy: it lists the categories of the diff against the
    base and fills each field, with at least one reason.

### babysit-pr: start, lease, base

11. In a repo with `.merge-risk.json`, a PostToolUse hook on `Bash` adds the context "run
    `/babysit-pr <url>`" after a `gh pr create` that printed a PR URL. It adds nothing for
    `--draft`, for a failed create, and in a repo without the file.
12. `babysit-pr` finds `pr-risk` in `.my-skills/pr-risk`, `.agents/skills/pr-risk`,
    `.claude/skills/pr-risk`, `~/.my-skills/pr-risk`, in this order. If it is in none, the
    babysitter stops and prints the install command.
13. On start, the babysitter takes the lease: the label `babysitting` and one PR comment with
    `<!-- babysit-pr lease: <session-id> <host> <expires-at> -->`, expiry 90 minutes after the
    comment's GitHub `updated_at`. When more than one valid lease comment exists, the one with
    the lowest comment id holds the lease. The lease comment also keeps the round count and the
    failure signatures, so they survive compaction, crash and resume. It renews the expiry on
    each round and reads the lease again before each push. If another session holds the lease,
    the babysitter stops without a PR comment and without a push. On merge or handoff it removes
    the label and marks the lease ended.
14. Every step uses the base: the diff, the categories, `git merge-tree`, the rebase, the
    `update-branch`, and the check "fails on the base too".

### babysit-pr: the fix loop

15. A failed check starts a fix round. Before an edit, the babysitter runs the exact command of
    the failing job (from the workflow file or `gh run view --log-failed`) and sees it fail. If
    it cannot reproduce the failure, it hands off with "cannot reproduce".
16. Before each push: the failing test passes; the related tests of each file the fix changed
    pass (`vitest related`, `jest --findRelatedTests`, or for pytest the test files that import
    the changed modules and the test file next to them); lint and typecheck pass on the changed
    files. The full command of each affected CI job runs too when the fix changed a fixture,
    `conftest.py`, a config file, a dependency file, or a module with more than 10 importers.
    No push while a test fails.
17. A round ends with one commit per fixed cause. The message names the check and the cause
    (e.g. `fix(ci): login e2e waited for a removed spinner`).
18. The babysitter hands off when: 5 rounds are done and a check still fails; the same check
    fails with the same error signature in two rounds; the fix needs a change to CI config or
    would skip, disable or `.only` a test; the failing command also fails on the base in a clean
    worktree.
19. A check that stays pending for `waitMinutes` hands off with "CI stalled". A head commit with
    no checks at all hands off with "no checks". Green means every check of the head commit
    passed, not only the required ones.

### babysit-pr: the base moves

20. When the base has commits that the branch does not have, the babysitter runs
    `git merge-tree --write-tree origin/<base> HEAD` first. No conflict, or 2 or fewer branch
    commits that touch the conflicting files: it rebases and pushes with
    `git push --force-with-lease=<branch>:<sha> origin <branch>`. More than 2: it merges
    `origin/<base>` into the branch, and this PR then uses the merge method.
21. A conflict is resolved with the `resolving-merge-conflicts` skill. When the resolution changed
    a file that is not `tests` or `docs`, or a lockfile by hand, the verdict is human after CI is
    green.

### babysit-pr: reviews and the owner

22. A review comment, an unresolved review thread, or a "changes requested" review hands off.
    The exception is a comment that starts with `@babysitter` whose author login is the owner and
    that no other account edited: the babysitter acts on it. Its code changes go through the
    ladder and the gate.
23. An owner comment `@babysitter merge` is the human approval of the head commit at the time
    of the comment: the babysitter merges a PR with the verdict human when every check of that
    commit is green and there is no conflict. A later commit (a fix, a rebase, a merge from the
    base) voids the approval, and the babysitter hands off with "approval was for <sha>". That
    handoff comment lists each commit after the approved one (short SHA, subject, author) and
    links the GitHub compare view `<approved sha>...<head sha>`, so the owner reviews only the
    new commits before approving again.

### babysit-pr: judge, merge, handoff

24. When the gate gives auto, the judge gets only the diff against the base of the counted
    files, the names and line counts of the other files, the block and the categories. It
    answers `{"verdict": "agree" | "disagree", "reason": "..."}`. Disagree, invalid JSON, or no
    answer in 10 minutes is human. From Claude Code the judge is `codex exec`; from Codex it is
    `claude -p`. When that CLI is missing or fails, a fresh agent of the same vendor judges and
    the merge comment says "judge: same-model".
25. The merge method is the policy's `mergeMethod` when set; else rebase when the repo allows it
    and the branch has no merge commit; else merge. The babysitter merges with `gh pr merge`
    and `--match-head-commit <sha>` with the SHA that the gate and the judge saw, never with
    `--auto`. A squash merge uses the PR title with `(#<n>)` as subject and the
    PR summary plus a one-line block digest as body. After a merge, it posts
    `🤖 Merged by babysit-pr: gate passed, judge <agree|same-model agree>`.
26. A handoff posts one comment with the heading `## 🧑 Needs human: <reason>`, the state (CI,
    rounds, rebase), the gate and judge results, the next step for the owner, the fix log, and
    the resume command `/babysit-pr <url>`. It adds the label `needs-human` (created when
    missing), sends a push notification in Claude Code, and stops. A failed `gh pr merge` (for
    example a required approval) is a handoff.
27. `/babysit-pr <url>` on a PR that was handed off reads the owner's `@babysitter` comments
    newer than the last handoff first, then continues the loop. When the last handoff was
    "5 fix rounds" or "same failure twice", the resume starts a new budget of 5 rounds and
    clears the failure signatures; after any other handoff the count continues.

### implement-spec

28. The driver finds `pr-risk` next to `pr` and gives its path to the `pr-writer` agent. If
    `pr-risk` is missing, the run is blocked with its install command. Before `gh pr create`
    the driver runs `merge_risk.py validate` on the body; when the block is invalid the
    `pr-writer` runs one more time; when it is still invalid, the run is blocked.
29. `implement-spec/SKILL.md` tells the operator session: with exit 0 and a PR URL, run
    `/babysit-pr <url>`.

## Scenarios

### CLI hooks

#### Scenario: global install registers the hooks in both agents

- **Given** `~/.claude/` and `~/.codex/` exist and `babysit-pr` ships `hooks.json` with one
  PostToolUse hook, matcher `Bash`
- **When** the owner runs `skills-cli install babysit-pr --global --agent claude,codex`
- **Then** `~/.claude/settings.json` and `~/.codex/hooks.json` each have one PostToolUse entry
  whose command runs `~/.my-skills/babysit-pr/hooks/<name>.sh`, and the output prints the Codex
  trust steps

#### Scenario: a second install adds nothing

- **Given** the hooks of `babysit-pr` are registered
- **When** the owner runs the same install again
- **Then** each config file has exactly one entry for that command

#### Scenario: install for Claude only

- **Given** the owner runs `skills-cli install babysit-pr --global --agent claude`
- **Then** `~/.claude/settings.json` has the hook and `~/.codex/hooks.json` does not, and no
  Codex trust steps are printed

#### Scenario: project install writes project configs

- **Given** a project `demo` with `.claude/` and `.codex/`
- **When** the owner runs `skills-cli install babysit-pr --agent claude,codex` in `demo`
- **Then** `demo/.claude/settings.json` and `demo/.codex/hooks.json` have the hook, and the
  global configs are unchanged

#### Scenario: a committed project hook on a machine without the store

- **Given** `demo/.claude/settings.json` (committed) has the project hook of `babysit-pr`, and a
  teammate's clone has no `demo/.my-skills/`
- **When** the teammate's agent runs `ls` in `demo/src`
- **Then** the hook exits 0 with no output, and the command runs as normal

#### Scenario: a project hook from a subfolder

- **Given** the project hook is installed and the store exists
- **When** the agent runs `gh pr create ...` with the working folder `demo/src/lib`
- **Then** the hook finds `demo/.my-skills/babysit-pr/hooks/<name>.sh` and runs it

#### Scenario: the .agents target gets no hooks

- **When** the owner installs `babysit-pr` for the `.agents/skills` target only
- **Then** no hook config is written, and the output says to start `/babysit-pr <url>` by hand

#### Scenario: uninstall removes only that skill's hooks

- **Given** `~/.claude/settings.json` has the hook of `babysit-pr`, the hook of `pr-risk`, and
  the user's own `PreToolUse` hook `rtk hook claude`
- **When** the owner runs `skills-cli uninstall babysit-pr --global`
- **Then** only the `babysit-pr` entry is gone, and the file is otherwise byte-equal

#### Scenario: an update with the same command asks for no new trust

- **Given** `babysit-pr` 0.1.0 is installed and 0.1.1 changes only the content of its hook script
- **When** the skill updates to 0.1.1
- **Then** the command string in `~/.codex/hooks.json` is unchanged and no trust steps are
  printed

#### Scenario: an update that changes the command prints trust steps

- **Given** 0.1.1 renames the hook script from `start.sh` to `on-pr-create.sh`
- **When** the skill updates to 0.1.1
- **Then** the configs have the new command and not the old one, and the Codex trust steps are
  printed

#### Scenario: the CLI never writes Codex trust

- **Given** `~/.codex/config.toml` has a `[hooks.state]` table
- **When** the owner installs, updates and uninstalls `babysit-pr`
- **Then** `~/.codex/config.toml` is byte-equal before and after

### The block and the policy

#### Scenario: a valid block parses

- **Given** the block
  ```
  door: two-way            # one-way | two-way
  blast_radius: isolated
  touches: [docs, tests]
  rollback: revert
  verdict: auto
  reasons:
    - "Only skill prose and its tests change"
  ```
- **When** the gate parses it
- **Then** it returns `door=two-way`, `touches=[docs, tests]`, one reason, no errors

#### Scenario: an unknown category is invalid

- **Given** a block with `touches: [docs, styles]`
- **When** the gate parses it
- **Then** it is invalid with the error "unknown category: styles"

#### Scenario: a YAML form outside the subset is invalid

- **Given** a block where `touches` is a `- item` list on separate lines
- **When** the gate parses it
- **Then** it is invalid, and the verdict is human

#### Scenario: two blocks are invalid

- **Given** a PR body with two ```` ```merge-risk v1 ```` blocks
- **When** the gate parses it
- **Then** it is invalid with the error "more than one block"

#### Scenario: no block means human

- **Given** a PR body from `/pr` without a block
- **When** the gate runs
- **Then** the verdict is human with the reason "no merge-risk block"

#### Scenario: the repo adds paths and keeps the defaults

- **Given** the policy `{"agentMerge": true, "paths": {"money": ["src/billing/**"]}}`
- **When** the diff changes `src/billing/invoice.py` and `db/migrations/0042_add.sql`
- **Then** the categories are `money` and `schema/migration`

#### Scenario: the repo removes a default always-human category

- **Given** the policy `{"agentMerge": true, "alwaysHuman": ["money"]}` and a block with
  `touches: [infra]`, two-way, revert
- **When** the diff changes only `Dockerfile`
- **Then** the gate verdict is auto

#### Scenario: the PR cannot loosen its own policy

- **Given** `origin/main` has `{"agentMerge": true}` with the default `alwaysHuman`
- **When** PR #62 adds `db/migrations/0042_add.sql` and sets `"alwaysHuman": []` in its own
  `.merge-risk.json`, with a block that lists `schema/migration` and `config`
- **Then** the gate uses the policy of `origin/main`, and the verdict is human with the reasons
  "schema/migration is always human" and "config: .merge-risk.json changed"

#### Scenario: the first policy file is not used by its own PR

- **Given** `origin/main` has no `.merge-risk.json`
- **When** PR #63 adds `.merge-risk.json` with `{"agentMerge": true}`
- **Then** the PreToolUse hook enforces the block (the file is in the working tree), and the gate
  verdict is human with the reason "agentMerge is off"

#### Scenario: an unknown policy key is an error

- **Given** the policy `{"agentMerge": true, "autoMerge": true}`
- **When** the gate runs
- **Then** the verdict is human with the reason "policy: unknown key autoMerge"

#### Scenario: SKILL.md is logic, not docs

- **Given** a block with `touches: [docs]`
- **When** the diff changes only `skills/pr-risk/SKILL.md`
- **Then** the verdict is human with the reason "diff touches logic, not in touches"

#### Scenario: one-way door is human

- **Given** a valid block with `door: one-way` and `verdict: auto`
- **When** the gate runs
- **Then** the verdict is human with the reason "door one-way", whatever the `verdict` field says

#### Scenario: rollback other than revert is human

- **Given** a valid two-way block with `rollback: revert+redeploy`
- **When** the gate runs
- **Then** the verdict is human

#### Scenario: an undeclared migration is human

- **Given** a block with `touches: [logic, tests]`
- **When** the diff also changes `db/migrations/0042_add.sql`
- **Then** the verdict is human with the reasons "diff touches schema/migration, not in touches"
  and "schema/migration is always human"

#### Scenario: agentMerge false is human

- **Given** the policy `{"agentMerge": false}` and a valid two-way docs block
- **When** the gate runs
- **Then** the verdict is human with the reason "agentMerge is off"

#### Scenario: counted lines at the limit

- **Given** `judgeMaxLines` is 1500 and the diff has 1500 counted lines plus 4000 lines in
  `pnpm-lock.yaml` and 900 lines in `docs/guide.md`
- **When** the gate runs on an otherwise valid block that lists `deps`, `docs` and `logic`
- **Then** the counted lines are 1500 and the verdict is auto

#### Scenario: counted lines over the limit

- **Given** the same diff with 1501 counted lines
- **When** the gate runs
- **Then** the verdict is human with the reason "1501 counted lines > 1500"

### pr-risk: the gate at creation

#### Scenario: create without a block is denied

- **Given** a repo with `.merge-risk.json` and a body file `/tmp/body.md` without a block
- **When** the agent runs `gh pr create --title x --body-file /tmp/body.md`
- **Then** the PreToolUse hook denies it with a reason that names "/pr, then /pr-risk", and no PR
  is created

#### Scenario: create with a valid block is allowed

- **Given** the same repo and a body with a valid block
- **When** the agent runs `gh pr create --title x --body "$(cat /tmp/body.md)"`
- **Then** the hook allows it

#### Scenario: a body that cannot be read is denied

- **Given** a repo with `.merge-risk.json`
- **When** the agent runs `gh pr create --fill`
- **Then** the hook denies it with the reason "the body must come from --body or --body-file"

#### Scenario: repo without a policy is not touched

- **Given** a repo without `.merge-risk.json`
- **When** the agent runs `gh pr create --fill`
- **Then** the hook allows it and prints nothing

#### Scenario: the same hook works in Codex

- **Given** the Codex PreToolUse payload `{"tool_name": "Bash", "tool_input": {"command": "gh pr
  create --fill"}, "cwd": "<repo with policy>"}`
- **When** the hook script reads it
- **Then** it prints `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny", ...}}`

### babysit-pr: start and lease

#### Scenario: the babysitter starts after create

- **Given** a repo with `.merge-risk.json`
- **When** `gh pr create` prints `https://github.com/jesdi/demo/pull/58`
- **Then** the PostToolUse hook adds the context "run `/babysit-pr
  https://github.com/jesdi/demo/pull/58`"

#### Scenario: a draft does not start the babysitter

- **When** `gh pr create --draft ...` prints a PR URL
- **Then** the PostToolUse hook adds no context

#### Scenario: a failed create does not start the babysitter

- **When** `gh pr create` exits 1 and prints no URL
- **Then** the PostToolUse hook adds no context

#### Scenario: pr-risk is missing

- **Given** `babysit-pr` is installed and `pr-risk` is in none of the four places
- **When** the agent runs `/babysit-pr <url>`
- **Then** it stops and prints `npx @jesdi/skills-cli install pr-risk`, and posts nothing on
  the PR

#### Scenario: a second babysitter stops at a valid lease

- **Given** PR #58 has a lease from session `s1` on host `mac` that expires in 40 minutes
- **When** session `s2` runs `/babysit-pr <url of #58>`
- **Then** `s2` stops with "already babysat by mac", adds no PR comment and pushes nothing

#### Scenario: an expired lease is taken over

- **Given** PR #58 has a lease from `s1` that expired 1 minute ago
- **When** `s2` runs `/babysit-pr <url of #58>`
- **Then** `s2` holds the lease with an expiry 90 minutes from now

#### Scenario: two babysitters claim at the same time

- **Given** sessions `s1` and `s2` post lease comments on PR #58 within one second, with the
  comment ids 1001 (`s1`) and 1002 (`s2`)
- **When** each reads the lease comments after it posts
- **Then** `s1` holds the lease, and `s2` marks its lease ended and stops without a push

#### Scenario: the expiry uses GitHub time

- **Given** the lease comment of `s1` has `updated_at` 10:00 GitHub time, and the clock of the
  host of `s2` is 95 minutes fast
- **When** `s2` runs `/babysit-pr` at 10:05 GitHub time
- **Then** `s2` sees the lease as valid and stops

#### Scenario: the round count survives a new session

- **Given** session `s1` did 3 fix rounds on PR #58 and crashed
- **When** session `s2` takes the expired lease and CI fails
- **Then** `s2` starts round 4, and after round 5 it hands off with "5 fix rounds"

#### Scenario: the owner's resume after the round limit starts a new budget

- **Given** PR #58 was handed off with "5 fix rounds"
- **When** the owner runs `/babysit-pr <url of #58>` and CI is still red
- **Then** the babysitter starts round 1 of a new budget, with no failure signatures

#### Scenario: a resume after another handoff keeps the count

- **Given** PR #58 was handed off with "door one-way" after 2 fix rounds
- **When** the owner runs `/babysit-pr <url of #58>` and CI turns red
- **Then** the babysitter starts round 3

#### Scenario: the same failure across sessions

- **Given** `s1` recorded the signature of `test: test_empty AssertionError` and crashed
- **When** `s2` takes the lease and the same failure comes back after its fix
- **Then** `s2` hands off with "same failure twice"

#### Scenario: the lease is lost before a push

- **Given** `s1` has a fix commit ready and, before the push, the lease comment names `s2`
- **When** `s1` reads the lease before the push
- **Then** `s1` does not push and stops

#### Scenario: a stacked PR uses its own base

- **Given** PR #60 has the base `feat/parent`
- **When** the babysitter computes the diff, the categories and the "fails on the base too" check
- **Then** each one uses `origin/feat/parent`, and none uses `main`

### babysit-pr: the fix loop

#### Scenario: a broken test is fixed and merged

- **Given** PR #58 (block: two-way, isolated, `[logic, tests]`, revert) and the `test` job fails
  in `tests/test_parse.py::test_empty`
- **When** the babysitter runs
- **Then** it runs `pytest` with the job's command and sees the failure, fixes the code, runs
  `tests/test_parse.py::test_empty`, then the related tests, then `ruff check` on the changed
  files, pushes one commit `fix(ci): ...`, and after all checks are green and the judge agrees,
  merges with rebase

#### Scenario: a failure that cannot be reproduced

- **Given** the `test` job fails and the job's command passes locally
- **When** the babysitter runs
- **Then** it hands off with the reason "cannot reproduce" and pushes nothing

#### Scenario: a shared-ground fix runs the full job

- **Given** the fix changes `tests/conftest.py`
- **When** the babysitter prepares the push
- **Then** it runs the full `pytest` command of the `test` job before the push

#### Scenario: the same failure two times

- **Given** round 1 fixed `test_empty` and CI fails again in `test_empty` with the same error
- **When** round 2 ends
- **Then** the babysitter hands off with "same failure twice" and does not start round 3

#### Scenario: round 5 is the last

- **Given** rounds 1 to 5 each fixed a different failure and CI fails again
- **When** round 5 ends
- **Then** the babysitter hands off with "5 fix rounds" and the fix log of 5 rounds

#### Scenario: a fix that needs CI config

- **Given** the `lint` job fails because the workflow pins an old `ruff`
- **When** the babysitter finds that the fix is in `.github/workflows/ci.yml`
- **Then** it does not change the file and hands off with "fix needs CI change"

#### Scenario: a fix may not skip a test

- **Given** `test_empty` fails
- **Then** no commit of the babysitter adds `skip`, `xfail`, `.only` or removes the test

#### Scenario: the failure is also on the base

- **Given** `test_clock` fails on PR #58, and the same command fails on `origin/main` in a clean
  worktree
- **When** the babysitter checks the base
- **Then** it hands off with "fails on main too" and the two outputs

#### Scenario: CI stalls

- **Given** `waitMinutes` is 60 and the `e2e` check is queued for 60 minutes
- **When** the wait ends
- **Then** the babysitter hands off with "CI stalled"

#### Scenario: no checks

- **Given** the head commit of PR #58 has no checks after the start
- **Then** the babysitter hands off with "no checks"

#### Scenario: a red check that is not required

- **Given** the required check `test` passed and the not-required check `lint` failed
- **Then** the PR is not green and the babysitter starts a fix round

### babysit-pr: the base moves

#### Scenario: base moved without conflict

- **Given** `main` has 3 new commits and `git merge-tree` shows no conflict
- **When** the babysitter updates the branch
- **Then** it rebases on `origin/main` and pushes with
  `git push --force-with-lease=<branch>:<old sha> origin <branch>`, and CI runs again

#### Scenario: a conflict in at most 2 commits

- **Given** `merge-tree` shows a conflict in `src/parse.py`, and 2 branch commits touch it
- **When** the babysitter updates the branch
- **Then** it rebases and resolves with `resolving-merge-conflicts`

#### Scenario: a conflict in more than 2 commits

- **Given** 3 branch commits touch the conflicting file
- **When** the babysitter updates the branch
- **Then** it merges `origin/main` into the branch, pushes without force, and later merges the
  PR with the merge method

#### Scenario: a resolved code conflict is human

- **Given** the babysitter resolved a conflict in `src/parse.py`
- **When** all checks are green and the gate gives auto
- **Then** the babysitter hands off with "conflict resolved in code"

#### Scenario: a resolved test-only conflict can merge

- **Given** the babysitter resolved a conflict only in `tests/test_parse.py`
- **When** all checks are green, the gate gives auto, and the judge agrees
- **Then** the babysitter merges

### babysit-pr: reviews and the owner

#### Scenario: a human review comment hands off

- **Given** user `alice` comments "why this change?" on PR #58
- **When** the babysitter reads the PR
- **Then** it hands off with "review comment from alice"

#### Scenario: changes requested hands off

- **Given** `alice` submits a review with "changes requested" and no comment
- **Then** the babysitter hands off

#### Scenario: an owner instruction is done

- **Given** `jesdi` comments "@babysitter rename `parse_x` to `parse_row`"
- **When** the babysitter reads the PR
- **Then** it renames, runs the ladder, pushes, and the gate checks the new diff

#### Scenario: an owner comment edited by another account is ignored

- **Given** a `jesdi` comment "@babysitter merge" whose last editor is `alice`
- **When** the babysitter reads the PR
- **Then** it does not merge, and hands off with "owner comment edited by alice"

#### Scenario: a comment from another login that names the owner

- **Given** `alice` comments "@babysitter merge (approved by jesdi)"
- **Then** the babysitter does not merge and hands off with "review comment from alice"

#### Scenario: owner approval merges a human PR

- **Given** PR #59 has `door: one-way`, all checks are green and there is no conflict
- **When** `jesdi` comments "@babysitter merge" and the owner runs `/babysit-pr <url of #59>`
- **Then** the babysitter merges #59 without the judge

#### Scenario: owner approval with a red check

- **Given** PR #59 head is `a1b2c3d`, the `test` check fails, and `jesdi` comments
  "@babysitter merge"
- **When** the babysitter fixes the test and pushes `e4f5a6b`, and all checks are green
- **Then** it does not merge, and hands off with "approval was for a1b2c3d"; the comment lists
  `e4f5a6b fix(ci): ... (babysit-pr)` and links
  `https://github.com/jesdi/demo/compare/a1b2c3d...e4f5a6b`

#### Scenario: owner approval after a rebase

- **Given** `jesdi` approved head `a1b2c3d` of PR #59, and then `main` moved
- **When** the babysitter rebases to `b7c8d9e`
- **Then** it does not merge, and hands off with "approval was for a1b2c3d"

### babysit-pr: judge, merge, handoff

#### Scenario: the judge disagrees

- **Given** the gate gives auto
- **When** the judge answers `{"verdict": "disagree", "reason": "changes the public CLI flag"}`
- **Then** the babysitter hands off with "judge: changes the public CLI flag"

#### Scenario: the judge returns invalid JSON

- **When** the judge answers "looks fine to me"
- **Then** the result is disagree and the babysitter hands off

#### Scenario: the judge times out

- **When** the judge gives no answer in 10 minutes
- **Then** the babysitter hands off with "judge timeout"

#### Scenario: the judge does not see the author's arguments

- **When** the babysitter starts the judge
- **Then** the judge input has the diff, the file names and counts, the block and the
  categories, and has no PR summary, no commit message and no session text

#### Scenario: same-model fallback

- **Given** `codex` is not installed
- **When** the gate gives auto and a fresh Claude agent agrees
- **Then** the babysitter merges and the merge comment says "judge: same-model agree"

#### Scenario: a new commit after the gate

- **Given** the gate and the judge passed on head `a1b2c3d` of PR #58
- **When** `alice` pushes `f0e1d2c` before the merge
- **Then** `gh pr merge` with `--match-head-commit a1b2c3d` fails, nothing merges, and the
  babysitter starts again with the new head

#### Scenario: rebase is the default method

- **Given** the policy has no `mergeMethod` and the repo allows rebase
- **When** the babysitter merges
- **Then** it runs `gh pr merge 58 --rebase --match-head-commit <gated sha>` and not `--auto`, and posts
  `🤖 Merged by babysit-pr: gate passed, judge agree`

#### Scenario: a squash policy keeps the why

- **Given** the policy `"mergeMethod": "squash"` and PR #58 titled "feat: parse empty rows"
- **When** the babysitter merges
- **Then** the commit subject is "feat: parse empty rows (#58)" and the body has the PR summary
  and the line `merge-risk v1: two-way, isolated, touches [logic, tests], rollback revert`

#### Scenario: a required approval blocks the merge

- **Given** branch protection requires one approval
- **When** `gh pr merge` fails
- **Then** the babysitter hands off with "merge blocked: approval required"

#### Scenario: the handoff comment

- **Given** PR #59 has `door: one-way` and CI is green after 2 rounds
- **When** the babysitter hands off
- **Then** the PR has one comment that starts `## 🧑 Needs human: door one-way`, has the state
  `CI green · 2 fix rounds`, the gate and judge lines, the fix log, and
  `/babysit-pr https://github.com/jesdi/demo/pull/59`; the PR has the label `needs-human`; the
  label `babysitting` is gone; Claude Code sent a push notification; the session stops

#### Scenario: resume after a handoff

- **Given** PR #59 was handed off and `jesdi` commented "@babysitter add a test for the empty case"
- **When** the owner runs `/babysit-pr <url of #59>`
- **Then** the babysitter takes the lease, does the instruction first, then continues the loop

### implement-spec

#### Scenario: the pr-writer writes the block

- **Given** `pr` and `pr-risk` are installed and a run reaches the PR body
- **When** the driver starts the `pr-writer`
- **Then** its prompt has the path of `pr-risk/SKILL.md`, and the PR body has a valid block under
  `## Merge Danger`, above the driver's facts

#### Scenario: pr-risk is missing in a run

- **Given** `pr-risk` is not installed
- **When** a run reaches the PR body
- **Then** the run is blocked with the install command, and nothing is pushed

#### Scenario: an invalid block is written one more time

- **Given** the first `pr-writer` body has `touches: [styles]`
- **When** the driver validates it
- **Then** the `pr-writer` runs once more; with a valid block the PR opens; with an invalid block
  again the run is blocked and no PR opens

#### Scenario: the operator starts the babysitter

- **Given** the driver exits 0 and printed `https://github.com/jesdi/demo/pull/61`
- **When** the operator session follows `implement-spec/SKILL.md`
- **Then** it runs `/babysit-pr https://github.com/jesdi/demo/pull/61`

## Out of scope

Forking `/pr`; GitHub's own auto-merge; a detached or cloud babysitter and live `@babysitter`
triggers; review-bot handling; flaky reruns; CI config changes; fixes on the base; writing Codex
hook trust; `implement-spec` commit quality; Windows. See `proposal.md`.
