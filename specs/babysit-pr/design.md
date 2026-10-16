# babysit-pr — Design

## Decisions

One line each: the decision and its tradeoff.

- **The babysitter runs in the session that opened the PR** — it has the context of the change;
  the terminal stays busy until merge or handoff.
- **A PostToolUse hook starts it; a PreToolUse hook enforces the block** — the same script and
  JSON output in Claude Code and Codex (verified on Codex 0.162.1); OpenCode and the `.agents`
  target get no hooks.
- **`/pr` stays external; `pr-risk` adds the block** — no fork to maintain; two steps an agent
  can split, so the PreToolUse hook enforces the second.
- **A deterministic floor decides first, an LLM can only make it stricter** — the gate is
  `merge_risk.py`; the judge can turn auto into human, never the reverse.
- **Every decision that a script can make is in a script, not in prose** — the lease, the round
  counter, the failure signatures, the merge method, the merge itself, the handoff comment and
  the owner-comment filter are `babysit.py` subcommands; the agent only reproduces, fixes and
  starts the judge. More Python, but each rule has a unit test.
- **Python standard library only, pytest tests, as `crap-gate`** — no YAML parser, so the block
  is a strict subset.
- **`pr-risk` owns `merge_risk.py`; `babysit-pr` finds it with the `review-diff` lookup** —
  one copy of the rules; `babysit-pr` cannot run without `pr-risk`.
- **The merge is bound to the gated commit** (red-team 1) — `gh pr merge --match-head-commit
  <sha>` with the SHA the gate and the judge saw; a new head commit fails the merge, and the
  babysitter gates again.
- **An owner `@babysitter merge` approves the head commit at the time of the comment**
  (red-team 2) — any later commit voids it and the babysitter hands off with "approval was for
  <sha>"; the owner approves again after a fix round.
- **The gate reads the policy from `origin/<base>`, not from the PR head** (red-team 3) — a PR
  cannot loosen its own rules; a PR that changes `.merge-risk.json` is `config` and human; a
  repo turns on agent merges only after the policy file is on the base. The hooks look for the
  file in the working tree, so the PR that adds it is still enforced.
- **The lease winner is the oldest valid lease comment, by GitHub comment id** (red-team 4) —
  after it posts, a babysitter reads all lease comments and keeps the lease only when its
  comment has the lowest id of the valid ones; expiry uses GitHub's `updated_at`, not the host
  clock. One extra read per claim; no CAS needed.
- **The round counter and the failure signatures are in the lease comment** (red-team 5) —
  a hidden JSON next to the marker; they survive compaction, crash and resume. A resume after a
  handoff keeps the counter; only the owner's resume after "5 fix rounds" or "same failure
  twice" starts a new budget of 5 and clears the signatures — the owner's resume is consent to
  more CI spend.
- **A failure signature is the job name plus the first failing test id or the first error line,
  with numbers, hex ids and paths under temp folders removed** — stable across runs; two
  different errors with the same first line count as the same.
- **The merge method order is policy, then rebase, then merge** — keeps the implementation
  commits; a branch with a merge commit cannot rebase-merge, so it uses merge.
- **Base updates decide with `git merge-tree` before git changes anything** — no `--abort`,
  which `resolving-merge-conflicts` forbids.
- **The judge is the other vendor's CLI** (`codex exec` from Claude, `claude -p` from Codex),
  **with a same-model fallback that the merge comment names** — independence when installed,
  no dead end when not.
- **Project hook commands resolve the store with `git rev-parse --show-toplevel` and exit 0
  when the script is missing** — committed configs work on any clone; one `git` call per Bash
  command.
- **Hook command strings stay the same across updates; logic is in the script** — no Codex
  re-trust on update; a script change reaches Codex without a review, which the update log
  lists.
- **Uninstall prints the Codex trust note when it moved later hook groups** — Codex keys trust
  by position (`<file>:<event>:<group>:<index>`), which the CLI does not own.
- **Codex babysitting uses `--profile babysit` with network on** — normal sessions keep
  network off; without network the babysitter hands off at once.

## Data model

There is no database. The state lives in GitHub and in JSON files. Each row is an invariant and
the code that enforces it, since no DB constraint exists.

| Table | Field | Constraint | Why |
|---|---|---|---|
| PR body | ```` ```merge-risk v1 ```` block | Exactly one; every key present; enums and categories from the fixed lists; `reasons` not empty; else invalid → human (`merge_risk.py parse`) | The gate never guesses |
| PR body | `verdict` | Ignored by the gate | The author grades its own work |
| `.merge-risk.json` on `origin/<base>` | all keys | Unknown key or wrong type → human; `paths` only adds to defaults; `alwaysHuman` replaces the default list | A PR cannot loosen its own rules |
| `.merge-risk.json` | `agentMerge` | Default `false` | A repo opts in on purpose |
| Lease comment | `<!-- babysit-pr lease: <session-id> <host> <expires-at> -->` + hidden JSON `{rounds, signatures[], approvedSha?, handoffAt?}` | One per session; winner = lowest comment id among comments with `updated_at` + 90 min > now (GitHub time) | No CAS on GitHub; host clocks drift |
| Lease comment | `rounds` | 0..5; a push with `rounds = 5` is refused by `babysit.py` | Bounded CI spend across sessions |
| Lease comment | `signatures` | A new signature equal to one already in the list → handoff | "Same failure twice" across sessions |
| Label | `babysitting` | Present only while a lease is valid; removed at merge or handoff | Human filter, not the lock |
| Label | `needs-human` | Added at handoff; removed when a babysitter takes the lease | `gh pr list --label needs-human` |
| Owner comment | `@babysitter ...` | Author login = `gh api user`; no edit by another account; created after the last handoff; `merge` binds to the head SHA at `createdAt` | Identity and approval of the human |
| Merge call | `--match-head-commit` | Equals the SHA the gate and judge saw | No unreviewed commit merges |
| Push | `--force-with-lease=<branch>:<sha>` | Only on the PR branch, only after a rebase, `<sha>` = the remote head before the rebase | No overwrite of commits the babysitter did not fetch |
| Agent hook config | entry | Identified by event + matcher + command; one entry per command; the CLI never writes `~/.codex/config.toml` | Idempotent install, clean uninstall, no trust bypass |

## Identities

The hook identity widens from "a command in `SessionStart`" to "event + matcher + command, per
skill".

- `cli/src/hook.ts` `register` / `unregister` (keyed by command in `SessionStart` only) —
  covered by ticket 01.
- `ensureSessionHook` / `removeSessionHook` / `setAutoUpdate` and their callers
  (`ops.ts:117`, `ops.ts:145`, `program.ts:81`) — covered by ticket 01 (behavior unchanged).
- Codex trust key `<file>:<event>:<group>:<index>` — out of scope: Codex owns it and the CLI
  never writes trust; ticket 02 prints the trust note when uninstall moves later groups.
- `.session-update.sh` command string — out of scope: unchanged.

## Seams

- **`merge_risk.py`** (new, `skills/pr-risk/`): `parse <body-file>`, `validate <body-file>`,
  `gate --body <file> --base <ref> --head <sha>` → JSON `{verdict, reasons[], categories[],
  countedLines}`. Pytest, with temp git repos.
- **Hook scripts** (new): `skills/pr-risk/hooks/pre-pr-create.sh` and
  `skills/babysit-pr/hooks/post-pr-create.sh`; stdin = the agent payload JSON, stdout = the
  agent output JSON. Pytest with Claude and Codex payload fixtures.
- **`babysit.py`** (new, `skills/babysit-pr/`): `lease acquire|renew|check|release`,
  `checks`, `round start|record-failure`, `owner-comments`, `update-plan` (merge-tree → rebase
  | merge | conflict), `judge`, `merge-method`, `merge`, `handoff`. Pytest with a fake `gh` on
  `PATH` that serves and records GitHub state, and fake `codex` / `claude` CLIs for `judge`.
- **`skills-cli install | update | uninstall`** (existing): vitest with a fixture home, as
  `cli/tests/session-hook.test.ts` and `ops.install.test.ts`.
- **`implement-spec` driver `_write_body` / `_open_pr`** (existing): pytest in
  `skills/implement-spec/tests/test_final.py`.
- **`scripts/e2e-babysit.sh <scenario>`** (new): runs one real session (`claude -p`, then
  `codex exec --profile babysit`) against the private repo `jesdi/babysit-pr-sandbox` and checks
  the result on GitHub. Manual; not part of `pnpm check`.
