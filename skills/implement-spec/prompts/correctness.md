# Correctness pass

Spec: {spec}
Ticket: {ticket}
Seam: {seam}
Range: {pr_branch}...{branch}, with the ticket tip at {start_sha}
Locked files: {locked}
Checks: {correctness}

Read the checks file and apply each of its checks to the diff of the range, with the Risk line of the ticket first: races, idempotency, time, authorization, persistence keys and identities, migrations, error paths.
The output section of the checks file does not apply here: your findings go into the report.
Read file content from git at {start_sha} (`git diff`, `git show {start_sha}:<path>`), never from the working tree, and run no tests: another agent makes temporary edits in this worktree while you work.
The one exception is the input of the driver, which git can lack because `.agent/` is ignored: read the spec, the ticket and the findings file from the working tree. The other agent does not edit them.
Report only a defect that you can show with a concrete sequence of events. `mutations` is optional for this pass.
Give each finding a severity (critical, important or minor), the file and line, a title, the evidence, and a kind: `fixture` when a locked test encodes a stale path, format or setup detail; `test` when a test is weak or missing, as a locked test that stays green when you break the line it protects; `code` for anything else. The test-writer corrects each `fixture` finding and each critical or important `test` finding on a locked file, and the fixer gets the rest.
Do not commit and do not dispatch subagents. Leave the tree as you found it.

Write one JSON report at {report}. When your sandbox is read-only, give the JSON as your whole final message and the driver saves it there. The report matches this schema:

```json
{
  "type": "object",
  "required": ["status", "commits", "files", "concerns", "findings"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "commits": {"description": "Only the commits you created. A review creates none, so this list is empty: never list a commit of the range.", "type": "array", "items": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"}},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "findings": {"type": "array", "items": {"type": "object", "required": ["severity", "file", "line", "title", "evidence", "kind"], "properties": {"severity": {"enum": ["critical", "important", "minor"]}, "file": {"type": "string"}, "line": {"type": "integer"}, "title": {"type": "string", "minLength": 1}, "evidence": {"type": "string", "minLength": 1}, "kind": {"enum": ["code", "fixture", "test"]}}}},
    "mutations": {"description": "Optional for this pass.", "type": "array", "items": {"type": "object", "required": ["test", "file", "line", "red"], "properties": {"test": {"type": "string", "minLength": 1}, "file": {"type": "string", "minLength": 1}, "line": {"type": "integer"}, "red": {"type": "string", "minLength": 1}}}}
  }
}
```

Run git as plain `git` in this worktree, and leave the git config as it is: all worktrees read one shared config, and one key written there, such as a `core.worktree`, breaks git in each of them. So never set GIT_DIR or GIT_WORK_TREE, and never write with `git config`.
The gate record of the driver for {start_sha} is {gate_record}.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
