---
name: pr-risk
description: >-
  Merge-risk block of a PR body. Use when a PR body needs its ```merge-risk v1
  block parsed or validated, with every error named.
---

# pr-risk

`python3 merge_risk.py parse <body-file>` prints the block as JSON, with an `errors` list.
`python3 merge_risk.py validate <body-file>` prints the errors and exits non-zero when the block is invalid.

The block is the one fenced ```` ```merge-risk v1 ```` block in the body. Keys: `door`, `blast_radius`,
`touches`, `rollback`, `verdict`, `reasons`.

`python3 merge_risk.py gate --body <file> --base <ref> --head <sha>` prints JSON with `verdict` (`auto | human`),
`reasons`, `categories` and `countedLines`. The policy is read from `origin/<base>`; the block's `verdict` is ignored.

## Writing the block

In a repo with `.merge-risk.json`, a hook denies `gh pr create` unless the body, passed with `--body` or
`--body-file`, holds a valid block. Write the body with `/pr`, then run `/pr-risk` to add the block.

Read `.merge-risk.json` for the policy, then fill the block from the diff:

- `door`: `one-way` if the change cannot be undone once shipped, else `two-way`.
- `blast_radius`: how far a bad change reaches: `isolated`, `module`, `service` or `consumers`.
- `touches`: inline list of categories the diff touches, e.g. `[docs, tests]`.
- `rollback`: `revert`, `revert+redeploy`, `data-fix` or `impossible`.
- `verdict`: `auto` only when the policy allows an agent to merge this change, else `human`.
- `reasons`: one quoted line per reason for the verdict.

Check it with `python3 merge_risk.py validate <body-file>` before opening the PR.
