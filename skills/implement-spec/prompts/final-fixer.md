# Fixer of the final review

Spec: {spec}
Tickets: {tickets}
PR branch: {pr_branch}, at commit {start_sha}
Locked files: {locked}
Findings file of the final review: {findings}
Findings to fix:
{findings_list}
Must-fix findings carried from ticket reviews:
{must_fix_list}

Fix each finding in the list as one batch, in one commit or in several. List every must-fix item you addressed in `addressed_must_fix` exactly as shown in the must-fix list, including its file and line; an omitted item rejects the batch. Leave the minor findings of the findings file.
Each commit belongs to one ticket: the ticket whose criterion or code the commit changes. A fix for two tickets is two commits.
Never edit a locked file: each one stays as it is at {start_sha}, and a locked file that differs is a critical finding of the next review.
Use targeted tests while you work.
Before you report DONE or DONE_WITH_CONCERNS, make the fast gate green on your final commit: run `{gate}` with IMPLEMENT_SPEC_BASE={pr_branch} and IMPLEMENT_SPEC_LOCKED='{locked}' (the locked files above) in the environment, then fix, commit, and run it again until it passes. The driver runs its full gate on that commit, and a scoped review reads your commits.
Commit your work on the checked-out PR branch and change no commit that is on it now. Do not dispatch subagents.
In `commits`, give every commit you made with the number of its ticket: the driver names each commit `fix(NN): <your subject>` from it, and a commit with no ticket is a violation. In `green`, give the summary lines of the passing test output.

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
    "addressed_must_fix": {"type": "array", "items": {"type": "string"}},
    "green": {"type": "array", "items": {"type": "string"}}
  },
  "if": {"properties": {"status": {"enum": ["DONE", "DONE_WITH_CONCERNS"]}}},
  "then": {"properties": {"commits": {"minItems": 1}}}
}
```

Stop only processes you started and their child processes, by PID (for example the PID of a job you put in the background, or its process group). Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. If a process you did not start blocks you, report it in your concerns and do not kill it.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
