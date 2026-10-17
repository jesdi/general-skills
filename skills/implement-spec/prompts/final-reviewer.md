# Final reviewer

Spec: {spec}
Tickets: {tickets}
PR branch: {pr_branch}, at commit {start_sha}
Range: {range}
Gate record of the driver for {start_sha}: {gate}
Review method: {review_diff}
Ledger of the run, one sentence per event: {ledger}
Minor findings that the ticket reviews left for this review, each with its findings file:
{minors}
Rulings of the operator:
{rulings}

Review the range with the method of the review-diff skill in the file above, with the spec as its spec path. The gate of that method is done: check that the gate record names {start_sha}, a clean status and exit 0, and run the gate yourself, in a copy as below, only when it does not.
Decide for each minor finding in the list whether it is still true at {start_sha}; report each one that is, with the severity it has now. A ruling is settled: do not report what a ruling accepted.
Give each finding a severity (critical, important or minor), the file and line, a title, the evidence, and a kind: `code`, `test`, or `fixture`.
Do not commit. Leave the branch and the tree as you found them.

Write one JSON report at {report} that matches this schema:

```json
{
  "type": "object",
  "required": ["status", "commits", "files", "concerns", "findings"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "commits": {"type": "array", "items": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"}},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "findings": {"type": "array", "items": {"type": "object", "required": ["severity", "file", "line", "title", "evidence", "kind"], "properties": {"severity": {"enum": ["critical", "important", "minor"]}, "file": {"type": "string"}, "line": {"type": "integer"}, "title": {"type": "string", "minLength": 1}, "evidence": {"type": "string", "minLength": 1}, "kind": {"enum": ["code", "fixture", "test"]}}}}
  }
}
```

Run git as plain `git` in this worktree, and leave the git config as it is: all worktrees read one shared config, and one key written there, such as a `core.worktree`, breaks git in each of them. So never set GIT_DIR or GIT_WORK_TREE, and never write with `git config`.
To run a check off this worktree, read the gate record of the driver, {gate}, or make a copy with `git worktree add --detach <temp dir> {start_sha}`, run the check there, and remove the copy with `git worktree remove --force <temp dir>` before you report.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
