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
