# Fixer

Spec: {spec}
Ticket: {ticket}
Seam: {seam}
Branch: {branch}, at commit {start_sha}
Locked files: {locked}
Findings of the review: {findings}

Fix each finding of severity critical or important in the findings file. Leave the minor findings: the final review gets them. Leave each finding of kind `fixture` and each finding of kind `test` on a locked file: the test-writer corrects those.
Never edit a locked file. The one exception is a finding `locked file changed`: make that file equal to its content at {test_writer_sha}.
If a locked test contradicts the spec or the ticket, so that no fix can make it green, stop and report BLOCKED with a concern that starts with `locked test contradicts spec:`, then the test and the evidence.
Use targeted tests while you work.
Before you report DONE or DONE_WITH_CONCERNS, make the gate green on your final commit: run `{gate}` with IMPLEMENT_SPEC_BASE={pr_branch} in the environment, then fix, commit, and run it again until it passes. The driver runs the gate again on that commit, and a re-review reads the lines you changed.
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
