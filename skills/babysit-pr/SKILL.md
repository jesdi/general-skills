---
name: babysit-pr
description: Babysit an open pull request until it is merged or handed to a human. Started by the PostToolUse hook after `gh pr create`, or by hand with `/babysit-pr <pr-url>`.
---

# babysit-pr

Run with `/babysit-pr <pr-url>`. The PostToolUse hook in `hooks/post-pr-create.sh` asks for this after a ready PR is opened in a repo with `.merge-risk.json`. When a subagent opened the PR, the hook queues it instead, and the Stop hook in `hooks/stop-babysit-pending.sh` asks the main session for it when that session stops.

`babysit.py` is next to this file; every state change below goes through it (`python3 babysit.py <cmd> --repo <owner/name> --pr <n> ...`). Work in a checkout of the PR branch. The base is the PR's `baseRefName`, never a fixed `main`.

## Before anything

- `pr-risk` must be in `.my-skills/pr-risk`, `.agents/skills/pr-risk`, `.claude/skills/pr-risk` or `~/.my-skills/pr-risk`, in this order. If it is in none, stop and print the install command.
- Codex needs `--profile babysit` (network on). If `gh` cannot reach GitHub, hand off at once with reason `no network for gh`; never wait on an approval prompt.
- `lease acquire`. If another session holds it, stop with no comment and no push.

## The loop

1. `owner-comments`: act on the owner's `@babysitter` comments first. Any other review comment, unresolved thread or "changes requested" is a handoff. An instruction that changes code (for example "rename `a` to `b`") is a fix round without a failure: do it completely (every use), run the ladder below on the new diff, commit, push, and let the gate and judge see the new head. `@babysitter merge <sha>` approves that commit: pass it to `merge --approved-sha <sha>` once it is the head and green; there is no judge.
2. `update-plan --base <base>` when the base moved; follow its plan (see Base moved). Every step uses `origin/<base>` of the PR (a stacked PR's parent), never `main`.
3. `checks --wait-minutes <waitMinutes>` until the head is green, red, stalled (`CI stalled`) or empty (`no checks`). Green means every check passed.
4. Red: `round start --op <id>` (keep the id), then the fix round below. Renew the lease each round and `lease check` before each push.
5. Green: run the gate (`pr-risk` `merge_risk.py gate`), then `judge`, then `merge-method` and `merge`. Pass the judge's verdict label to `merge --judge agree` (or `--judge 'same-model agree'` for fallback), never an output file path. The merge pins the head SHA the gate and judge saw.

## A fix round

1. **Reproduce.** Take the exact command of the failing job from `.github/workflows/*.yml` or `gh run view --log-failed`, install the tool versions the job pins, run it and see it fail, then `round record-failure --op <id> --check <check> --error <error>` before touching code; if it stops with `same failure twice`, `handoff` with that reason. If it passes, `handoff` with `cannot reproduce`. Push nothing.
2. **Check the base.** Run the same command in a clean worktree of `origin/<base>`. If it fails there too, `handoff` with `fails on main too`, and put both outputs in the comment, each in its own fenced block.
3. **Fix the code** (the cause, not the symptom). Never edit `.github/workflows/*`: if the fix is there, `handoff` with `fix needs CI change`. Never add `skip`, `xfail` or `.only`, and never remove a test; that is a handoff too.
4. **Ladder before every push**, in order: the failing test alone; the related tests (for pytest, the test files that import the changed modules, and the test file next to them); `ruff check` or the repo's linter and typecheck on the changed files. When the fix changed a fixture, `conftest.py`, a config file, a dependency file or a module with more than 10 importers, run the full command of each affected job as well. No push while a test fails.
5. **One commit per cause**, `fix(ci): <check> <cause>`, e.g. `fix(ci): test empty input returned one blank row`. Then push.

Hand off when 5 rounds are done and a check still fails (`5 fix rounds`), or the same check fails with the same signature in two rounds (`same failure twice`).

## Base moved

`update-plan` prints `plan`:

- `rebase`: `git rebase origin/<base>`, then the printed `push`, which is `git push --force-with-lease=<branch>:<old head sha> origin <branch>`. That is the only forced form; never `--force`, `-f` or `+branch`.
- `rebase-with-conflict`: rebase and resolve each stop with the `resolving-merge-conflicts` skill. Never `--abort`. Push with the printed lease form.
- `merge`: `git merge origin/<base>`, resolve with the same skill, push without force. This PR now uses the merge method.

Push only after the ladder passes, then wait for CI on the new head. If a resolution changed a file whose categories (the pr-risk path classification, not its directory) are neither `tests` nor `docs`, or a lockfile by hand, the verdict is human: once CI is green, `handoff` with `conflict resolved in code` (no gate, judge or merge).

## Handoff

`python3 babysit.py handoff ...` posts `## 🧑 Needs human: <reason>`, adds `needs-human`, releases the lease, and prints JSON with `status: handoff`, `terminal: true`, and the reason. `merge` can also perform this handoff before exiting with an error (for example, judge disagreement or a blocked merge). This terminal JSON is the completion signal even on a nonzero exit; proceed straight to notification and end the turn.

Finish any worktree cleanup before calling `handoff` or `merge`, and run either command alone: a shell suffix would keep working after a terminal result. After a completed handoff, the only remaining tool call in Claude Code is `PushNotification` with the reason (notification-specific tool discovery is allowed). Then end the turn immediately. In Codex, end the turn immediately. This completion rule takes precedence over inherited instructions for memory writes, session summaries, cleanup or verification; those are work after handoff.

## Resume

`/babysit-pr <url>` on a handed-off PR reads the owner's `@babysitter` comments newer than the last handoff, then continues the loop.
