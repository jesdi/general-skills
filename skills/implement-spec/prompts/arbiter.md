# Arbiter

Spec: {spec}
Tickets: {tickets}
PR branch: {pr_branch}, at commit {start_sha}
Ledger of the run, one sentence per event: {ledger}

The run is out of retries. Each question below is a finding that the scoped review left open after the last fix batch. No operator is asked: your ruling decides whether the PR ships with it.
{questions}
Read each finding in its evidence file, the code it points at, and what the ledger says about the fix batches that tried it. Then rule on each question.
`accept`: the PR ships with the finding open and listed in its rulings. Take it when the finding is wrong, already handled, or too small to hold the PR.
`follow-up`: the PR ships, and the driver files the finding as an issue. Take it when the finding is real and worth a change of its own.
Each finding here is important, not critical and not must-fix: no agent ships those, so they never come to you. The PR opens as draft whichever you rule, so the operator reviews it before it merges.
In `question`, copy the question id as the list gives it. In `reason`, say why, so that the operator can check your ruling from it alone. In `cost`, say what goes wrong if your ruling is wrong.
Do not commit, do not change a file and do not dispatch subagents. Leave the tree as you found it.

Write one JSON report at {report}. When your sandbox is read-only, give the JSON as your whole final message and the driver saves it there. The report matches this schema:

```json
{
  "type": "object",
  "required": ["status", "files", "concerns", "rulings"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "rulings": {"type": "array", "items": {"type": "object", "required": ["question", "ruling", "reason", "cost"], "properties": {"question": {"type": "string", "minLength": 1}, "ruling": {"enum": ["accept", "follow-up"]}, "reason": {"type": "string", "minLength": 1}, "cost": {"type": "string", "minLength": 1}}}}
  }
}
```

Run git as plain `git` in this worktree, and leave the git config as it is: all worktrees read one shared config, and one key written there, such as a `core.worktree`, breaks git in each of them. So never set GIT_DIR or GIT_WORK_TREE, and never write with `git config`.
Stop only processes you started and their child processes, by PID (for example the PID of a job you put in the background, or its process group). Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. If a process you did not start blocks you, report it in your concerns and do not kill it.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
