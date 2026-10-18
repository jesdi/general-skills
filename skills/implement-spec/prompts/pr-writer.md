# PR writer

Spec: {spec}
Tickets: {tickets}
PR branch: {pr_branch}, at commit {start_sha}
Range: {range}
Gate record of the driver for {start_sha}: {gate}
PR method: {pr_skill}
Merge-risk block: {pr_risk_skill}
Facts of the run, which the driver puts after your body:
{facts}

Write the body of the PR of the range with the method of the pr skill in the file above: its sections, in its order. Put the merge-risk block, as the pr-risk skill describes it, under `## Merge Danger`, and check it with `python3 merge_risk.py validate <body-file>` from that skill's folder. Read the spec, the tickets and the range first. The gate record and the tests of the range are the execution-based evidence; you cannot take screenshots. Do not repeat the facts of the run: the driver puts them after your body.
{risk_errors}Do not commit. Leave the branch and the tree as you found them.

Write one JSON report at {report} that matches this schema:

```json
{
  "type": "object",
  "required": ["status", "files", "concerns", "body"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "body": {"type": "string", "minLength": 1}
  }
}
```

Run git as plain `git` in this worktree, and leave the git config as it is: all worktrees read one shared config, and one key written there, such as a `core.worktree`, breaks git in each of them. So never set GIT_DIR or GIT_WORK_TREE, and never write with `git config`.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
