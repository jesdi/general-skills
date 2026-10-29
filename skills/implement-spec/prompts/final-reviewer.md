# Final reviewer

Spec: {spec}
Tickets: {tickets}
PR branch: {pr_branch}, at commit {start_sha}
Range: {range}
Gate record of the driver for {start_sha}: {gate}
Review method: {review_diff}
Ledger of the run, one sentence per event: {ledger}
Deferred observations and minor findings from ticket reviews, each with its evidence file:
{carried}
Must-fix findings carried from tickets that merged at the cap:
{must_fix_list}
Rulings of the operator:
{rulings}

Review the range with the method of the review-diff skill in the file above, with the spec as its spec path. The gate of that method is done: the driver ran the full gate on {start_sha}, and its record is the gate evidence of your review. Do not run a gate yourself.
Decide for each carried observation whether it is still true at {start_sha}; report each one that is, with the severity it has now. Every must-fix finding stays open until the fix batch addresses it and the scoped review verifies it. A ruling does not settle a must-fix finding. In the scoped review, list each item you verified closed in `resolved_must_fix` exactly as shown in the must-fix list, including its file and line; report each unresolved one in `findings`.
Give each finding a severity (critical, important or minor), the file and line, a title, the evidence, and a kind: `code`, `test`, or `fixture`.
Do not commit. Leave the branch and the tree as you found them.

Write one JSON report at {report} that matches this schema:

```json
{
  "type": "object",
  "required": ["status", "files", "concerns", "findings"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "findings": {"type": "array", "items": {"type": "object", "required": ["severity", "file", "line", "title", "evidence", "kind"], "properties": {"severity": {"enum": ["critical", "important", "minor"]}, "file": {"type": "string"}, "line": {"type": "integer"}, "title": {"type": "string", "minLength": 1}, "evidence": {"type": "string", "minLength": 1}, "kind": {"enum": ["code", "fixture", "test"]}}}},
    "resolved_must_fix": {"type": "array", "items": {"type": "string"}}
  }
}
```

Run git as plain `git` in this worktree, and leave the git config as it is: all worktrees read one shared config, and one key written there, such as a `core.worktree`, breaks git in each of them. So never set GIT_DIR or GIT_WORK_TREE, and never write with `git config`.
To run a targeted check off this worktree, make a copy with `git worktree add --detach <temp dir> {start_sha}`, run the check there, and remove the copy with `git worktree remove --force <temp dir>` before you report.
Stop only processes you started and their child processes, by PID (for example the PID of a job you put in the background, or its process group). Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. If a process you did not start blocks you, report it in your concerns and do not kill it.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
