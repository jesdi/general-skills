# Conflict

Spec: {spec}
Ticket: {ticket}
Branch: {branch}, at commit {start_sha}
PR branch: {pr_branch}, at commit {pr_sha}
Files in conflict: {files}

The merge of the PR branch into the ticket branch is open in this worktree and it has conflicts in those files.
Resolve each conflict so that the behavior of both sides holds: the spec and the ticket say what each side is for. Change nothing that the resolution does not need.
Run the targeted tests of the files you resolved. The driver runs the gate on your commit.
Finish the merge with `git commit` on the checked-out ticket branch. Do not abort the merge and do not dispatch subagents.
In `files`, give each file you resolved.

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

Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
