---
name: babysit-pr
description: Babysit an open pull request until it is merged or handed to a human. Started by the PostToolUse hook after `gh pr create`, or by hand with `/babysit-pr <pr-url>`.
---

# babysit-pr

Run with `/babysit-pr <pr-url>`. The PostToolUse hook in `hooks/post-pr-create.sh` asks for this after a ready PR is opened in a repo with `.merge-risk.json`.
