# Fixer

Spec: {spec}
Ticket: {ticket}
Seam: {seam}
Branch: {branch}, at commit {start_sha}
Locked files: {locked}
Findings of the review: {findings}

Fix each finding of severity critical or important in the findings file. Leave the minor findings: the final review gets them. Leave each finding of kind `fixture`: the test-writer corrects those.
Never edit a locked file. The one exception is a finding `locked file changed`: make that file equal to its content at {test_writer_sha}.
Use targeted tests while you work. The driver runs the gate on your final commit, and a re-review reads the lines you changed.
A finding from the `gate` is a red gate: its file is the gate record. Read the output there and make the gate green.
Commit your work on the checked-out ticket branch. Do not dispatch subagents.
In `ticket`, give the number of the ticket that owns your commits. In `green`, give the summary lines of the passing test output.

Write one JSON report at {report} that matches this schema:

```json
{
  "type": "object",
  "required": ["status", "commits", "files", "concerns", "green", "ticket"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "commits": {"type": "array", "items": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"}},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "green": {"type": "array", "items": {"type": "string"}},
    "ticket": {"type": "string", "pattern": "^[0-9]+$"}
  },
  "if": {"properties": {"status": {"enum": ["DONE", "DONE_WITH_CONCERNS"]}}},
  "then": {"properties": {"commits": {"minItems": 1}}}
}
```

Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
