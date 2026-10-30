# Conflict

Spec: {spec}
Tickets: {tickets}
PR branch: {pr_branch}
Base branch: {base}, at commit {start_sha}
Files in conflict: {files}

A rebase of the PR branch onto the base branch is open in this worktree, on a detached HEAD, and it stopped with conflicts in those files. The commit that stopped is the one commit of a ticket: its message names the ticket file.
Resolve each conflict so that the behavior of both sides holds: the spec and the ticket say what each side is for. Change nothing that the resolution does not need.
Run the targeted tests of the files you resolved. The driver runs the gate on the rebased tip.
Stage the files you resolved, then go on with `git rebase --continue`. Do not abort the rebase and do not dispatch subagents.
You resolve one stop only. When the rebase stops again at a later commit, leave that stop as it is and write your report.
When the driver rejected an earlier attempt after the rebase went on, the rebase is done: fix what it names in the commit at HEAD with `git commit --amend`, and do not start the rebase again.
In `commits`, give `git rev-parse HEAD` as it is when you are done. In `files`, give each file you resolved.

Write one JSON report at {report} that matches this schema:

```json
{
  "type": "object",
  "required": ["status", "commits", "files", "concerns"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "commits": {"type": "array", "items": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"}},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}}
  },
  "if": {"properties": {"status": {"enum": ["DONE", "DONE_WITH_CONCERNS"]}}},
  "then": {"properties": {"commits": {"minItems": 1}}}
}
```

Stop only processes you started and their child processes, by PID (for example the PID of a job you put in the background, or its process group). Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. If a process you did not start blocks you, report it in your concerns and do not kill it.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
