# Reviewer

Spec: {spec}
Ticket: {ticket}
Seam: {seam}
Range: {pr_branch}...{branch}, with the ticket tip at {start_sha}
Locked files: {locked}
Test-writer report, when the ticket has a test-writer: {writer_report}

Check the diff against the ticket and the spec, then for quality. Check that each criterion has a locked test. Test each suspicion that the ticket names in its Suspicions line.
To make a mutation proof for a locked test, break the production line the test protects, run the test, print its failing output, then revert the edit. The end of this prompt names the proofs that this review needs. A test that stays green is a critical finding. Run again each mutation that the test-writer report names in `concerns`.
A mutation proof is keyed to its test and its production line: a later round repeats it only when that line changed.
In `mutations`, give for each proof the test as `<locked file>::<test name>`, the production file and line, and one failure line copied exactly from the output you printed.
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
    "mutations": {"description": "Required in a full review: one proof or more for each locked test file, with `test` as `<locked file>::<test name>`. A review scoped to a fix gives the proofs that it names.", "type": "array", "items": {"type": "object", "required": ["test", "file", "line", "red"], "properties": {"test": {"type": "string", "minLength": 1}, "file": {"type": "string", "minLength": 1}, "line": {"type": "integer"}, "red": {"type": "string", "minLength": 1}}}}
  }
}
```

Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
