# Final review: spec lens

Spec: {spec}
Tickets: {tickets}
PR branch: {pr_branch}, at commit {start_sha}
Range: {range}
Gate record of the driver for {start_sha}: {gate}
Ledger of the run, one sentence per event: {ledger}
Deferred observations and minor findings from ticket reviews, each with its evidence file:
{carried}
Must-fix findings carried from tickets that merged at the cap:
{must_fix_list}
Rulings of the operator:
{rulings}

Review the range with the method in {spec_method}, with the spec above as its spec: did the range build what the spec asked, no more, no less? The output section of the method does not apply here: your findings go into the report.
The gate is done: the driver ran the full gate on {start_sha}, and its record is the gate evidence of your review. Do not run a gate yourself.
Two other reviewers review the same range at the same time, each for its own question; you never see their reports, and they never see yours.
Decide for each carried observation whether it is still true at {start_sha} and in your question; report each one that is, with the severity it has now. Report each must-fix finding that is in your question and still open at {start_sha}, with its title, file and line as the must-fix list shows them: it stays open until the fix batch addresses it and the scoped review verifies it. A ruling does not settle a must-fix finding.
Give each finding a severity (critical, important or minor), the file and line, a title, the evidence, and a kind: `code`, `test`, or `fixture`.
Do not commit and do not dispatch subagents. Leave the branch and the tree as you found them.

Write one JSON report at {report}. When your sandbox is read-only, give the JSON as your whole final message and the driver saves it there. The report matches this schema:

```json
{
  "type": "object",
  "required": ["status", "files", "concerns", "findings"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "findings": {"type": "array", "items": {"type": "object", "required": ["severity", "file", "line", "title", "evidence", "kind"], "properties": {"severity": {"enum": ["critical", "important", "minor"]}, "file": {"type": "string"}, "line": {"type": "integer"}, "title": {"type": "string", "minLength": 1}, "evidence": {"type": "string", "minLength": 1}, "kind": {"enum": ["code", "fixture", "test"]}}}}
  }
}
```

Run git as plain `git` in this worktree, and leave the git config as it is: all worktrees read one shared config, and one key written there, such as a `core.worktree`, breaks git in each of them. So never set GIT_DIR or GIT_WORK_TREE, and never write with `git config`.
To run a targeted check off this worktree, make a copy with `git worktree add --detach <temp dir> {start_sha}`, run the check there, and remove the copy with `git worktree remove --force <temp dir>` before you report.
Stop only processes you started and their child processes, by PID (for example the PID of a job you put in the background, or its process group). Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. If a process you did not start blocks you, report it in your concerns and do not kill it.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
