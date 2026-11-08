# Fixer of the full integration gate

Spec: {spec}
Tickets: {tickets}
PR branch: {pr_branch}, at commit {start_sha}
Locked files: {locked}
Failure findings: {findings}
{findings_list}

Read the exact gate log and repair the failure as one batch. Each commit belongs to one ticket; list its SHA and ticket number in `commits`. The driver names it `fix(NN): <subject>`.
If no code change is needed, leave the branch and tracked files unchanged, report no commits, and give a concern naming "{gate_title}" with the evidence explaining why. This still consumes the repair attempt. The driver reruns the full gate on the unchanged commit; another failure stops for the operator.
Never edit a locked file: each one stays as it is at {start_sha}. Report a needed test correction in your concerns and stop BLOCKED for the operator.
Use targeted tests while you work. Before you report DONE or DONE_WITH_CONCERNS, make the fast gate green on your final commit: run `{gate}` with IMPLEMENT_SPEC_BASE={pr_branch} and IMPLEMENT_SPEC_LOCKED='{locked}' in the environment. Give its summary lines in `green`.
Commit on the checked-out PR branch, changing no existing commit. Do not dispatch subagents. The full gate and final review follow this batch.

Write one JSON report at {report} that matches this schema:

```json
{
  "type": "object",
  "required": ["status", "commits", "files", "concerns", "green"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "commits": {"type": "array", "items": {"type": "object", "required": ["sha", "ticket"], "properties": {"sha": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"}, "ticket": {"type": "string", "pattern": "^[0-9]+$"}}}},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "green": {"type": "array", "items": {"type": "string"}}
  }
}
```

Stop only processes you started and their child processes, by PID (for example the PID of a job you put in the background, or its process group). Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. If a process you did not start blocks you, report it in your concerns and do not kill it.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
