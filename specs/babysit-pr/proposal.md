# babysit-pr

## Why

After an agent opens a PR, the work is not finished. CI fails, lint fails, e2e fails, the base
branch moves, and somebody must watch the PR, fix it, and decide whether it can merge. At the
moment the user does this by hand, one PR at a time. Agents that push without a local test run
are common: in 7 days of the user's sessions, 53% of the pushes after a code edit had no test run
after the last edit, and no agent ran the exact command of the failing CI job. Most of these PRs
are low risk and could merge without a human. A small number are one-way doors that must never
merge without one. The PR body from `/pr` already says which is which (`## Merge Danger`), but in
prose that a script cannot read.

## For whom

- The **owner**: the GitHub login that runs the agent (`gh api user --jq .login`). They open PRs
  through Claude Code or Codex and want only the risky ones back.
- The **agent session** that opened the PR. It has the context of the change, so it is the one
  that fixes CI.

## Goal

After an agent opens a PR in a repo that has `.merge-risk.json`, the same session fixes the PR
until all checks are green, without the owner. Then it merges the PR when a deterministic risk
gate and an independent judge both allow it, and else hands the PR to the owner with the reason.

## Non-goals

- Fork or edit the external `/pr` skill (`mattpocock/skills`). `pr-risk` adds to its output.
- GitHub's own auto-merge: the repo setting "Allow auto-merge", `gh pr merge --auto`, and
  changes to branch protection. The agent runs `gh pr merge` itself.
- A babysitter that runs detached (`claude -p` in the background) or in the cloud (a GitHub
  Action or webhook). A live trigger for `@babysitter` comments comes with that, later.
- Handling of review-bot comments (CodeRabbit, Copilot review). The owner uses no bot now. Every
  review comment that is not an owner `@babysitter` comment goes to a human.
- A rerun of a failed job to test for flakiness. A failure goes straight to the fix loop.
- Fixes that change CI config, or that skip, disable or `.only` a test.
- Fixes to the base branch when a check also fails there.
- Writing Codex hook trust entries (`trusted_hash`). The owner trusts each new hook in the
  Codex TUI.
- Better commit messages from `implement-spec` in general (a separate follow-up).
- Windows.
- Hooks for OpenCode or the `.agents/skills` target. Only Claude Code and Codex get hooks now.
- The `claude-config` changes (the force-with-lease exception, the git guard in Codex, the
  `CLAUDE.md` rule). They are a prerequisite PR in that repo; see `spec.md`, Prerequisites.
