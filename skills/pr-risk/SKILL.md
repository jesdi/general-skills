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
`touches`, `rollback`, `risk`, `reasons`.

`python3 merge_risk.py gate --body <file> --base <ref> --head <sha>` prints JSON with `verdict` (`auto | human`),
`reasons`, `risk`, `maxAutoRisk`, `categories` and `countedLines`. The policy is read from `origin/<base>`.

## Risk

The gate scores the risk from 1 to 10 and gives `auto` only when `risk <= maxAutoRisk` and nothing else makes it human:

- **Floor**: the riskiest category of the diff and `touches` (`CATEGORY_RISK` in `merge_policy.py`, overridden per
  category by the policy's `categoryRisk`), plus 1 for `module`, 2 for `service`, 3 for `consumers`, plus 1 for
  `rollback: revert+redeploy`, capped at 10. The block's `risk` raises the score above the floor, never lowers it.
- **Limit**: the policy's `maxAutoRisk` (0 to 10, default 3). A public repo, or one whose visibility `gh` cannot tell,
  is capped at 3.
- **One-way is always human**: `door: one-way` or `rollback: data-fix | impossible`, at any risk and any limit. Only
  the owner's `@babysitter merge <sha>` merges it.

`babysit-pr`'s judge scores the risk too; the merge needs the higher of the two within the limit.

## Writing the block

In a repo with `.merge-risk.json`, a hook denies `gh pr create` unless the body, passed with `--body` or
`--body-file`, holds a valid block. Write the body with `/pr`, then run `/pr-risk` to add the block.

Read `.merge-risk.json` for the policy, then fill the block from the diff:

- `door`: `one-way` if the change cannot be undone once shipped, else `two-way`.
- `blast_radius`: how far a bad change reaches: `isolated`, `module`, `service` or `consumers`.
- `touches`: inline list of categories the diff touches, e.g. `[docs, tests]`.
- `rollback`: `revert`, `revert+redeploy`, `data-fix` or `impossible`.
- `risk`: a whole number from 1 to 10, your honest score of what a bad merge would cost (see Risk). Write it
  higher than the floor when the diff is riskier than its categories say.
- `reasons`: one quoted line per reason for the verdict.

Check it with `python3 merge_risk.py validate <body-file>` before opening the PR.
