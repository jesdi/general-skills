# Reviewer

Spec: {spec}
Ticket: {ticket}
Seam: {seam}
Range: {pr_branch}...{branch}, with the ticket tip at {start_sha}
Locked files: {locked}
Test-writer report, when the ticket has a test-writer: {writer_report}

Check the diff against the ticket and the spec. Put only an unmet ticket criterion or a regression of a merged ticket's behavior in `findings`; set `basis` to `criterion` or `regression` accordingly; put every other observation in `observations` for final review. A red gate is a finding made by the driver. Check that each criterion has a locked test. Test each suspicion that the ticket names in its Suspicions line.
{risk}
Give each finding and observation a severity (critical, important or minor), the file and line, a title, the evidence, and a kind: `fixture` when a locked test encodes a stale path, format or setup detail; `test` when a test is weak or missing; `code` for anything else. The test-writer corrects each `fixture` finding and each critical or important `test` finding on a locked file, and the fixer gets the rest.
Do not commit and do not dispatch subagents. Leave the tree as you found it.

Write one JSON report at {report}. When your sandbox is read-only, give the JSON as your whole final message and the driver saves it there. The report matches this schema:

```json
{
  "type": "object",
  "required": ["status", "files", "concerns", "findings"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "findings": {"type": "array", "items": {"type": "object", "required": ["severity", "file", "line", "title", "evidence", "kind"], "properties": {"severity": {"enum": ["critical", "important", "minor"]}, "file": {"type": "string"}, "line": {"type": "integer"}, "title": {"type": "string", "minLength": 1}, "evidence": {"type": "string", "minLength": 1}, "kind": {"enum": ["code", "fixture", "test"]}, "basis": {"enum": ["criterion", "regression"]}}}},
    "observations": {"type": "array", "items": {"type": "object", "required": ["severity", "file", "line", "title", "evidence", "kind"], "properties": {"severity": {"enum": ["critical", "important", "minor"]}, "file": {"type": "string"}, "line": {"type": "integer"}, "title": {"type": "string", "minLength": 1}, "evidence": {"type": "string", "minLength": 1}, "kind": {"enum": ["code", "fixture", "test"]}}}},
    "regressions": {"description": "After a fix: new regressions introduced by that fix. Set basis to criterion when a ticket criterion fails, or regression when merged-ticket behavior fails.", "type": "array", "items": {"type": "object", "required": ["severity", "file", "line", "title", "evidence", "kind"], "properties": {"severity": {"enum": ["critical", "important", "minor"]}, "file": {"type": "string"}, "line": {"type": "integer"}, "title": {"type": "string", "minLength": 1}, "evidence": {"type": "string", "minLength": 1}, "kind": {"enum": ["code", "fixture", "test"]}, "basis": {"enum": ["criterion", "regression"]}}}}
  }
}
```

Run git as plain `git` in this worktree, and leave the git config as it is: all worktrees read one shared config, and one key written there, such as a `core.worktree`, breaks git in each of them. So never set GIT_DIR or GIT_WORK_TREE, and never write with `git config`.
To run a check off this worktree, read the gate record of the driver, {gate_record}, or make a copy with `git worktree add --detach <temp dir> {start_sha}`, run the check there, and remove the copy with `git worktree remove --force <temp dir>` before you report.
Stop only processes you started and their child processes, by PID (for example the PID of a job you put in the background, or its process group). Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. If a process you did not start blocks you, report it in your concerns and do not kill it.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
