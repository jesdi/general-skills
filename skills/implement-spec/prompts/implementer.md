# Implementer

Spec: {spec}
Ticket: {ticket}
Seam: {seam}
Start commit, which holds the locked tests when the ticket has them: {start_sha}
Locked files: {locked}

Make the locked tests green and meet each criterion of the ticket. For internal code, work in vertical test-first slices: one failing test, the smallest code that passes it, then the next slice.
Never edit a locked file. If a locked test contradicts the spec or the ticket, stop and report BLOCKED with a concern that starts with `locked test contradicts spec:`, then the test and the evidence.
Commit your work on the checked-out ticket branch. Do not dispatch subagents.
Use targeted tests while you work.
Before you report DONE or DONE_WITH_CONCERNS, make the gate green on your final commit: run `{gate}` with IMPLEMENT_SPEC_BASE={pr_branch} in the environment, then fix, commit, and run it again until it passes. The driver runs the gate again on that commit.
In `green`, give the summary lines of the passing test output.

Write one JSON report at {report} that matches this schema:

```json
{
  "type": "object",
  "required": ["status", "commits", "files", "concerns", "green"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "commits": {"type": "array", "items": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"}},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "green": {"type": "array", "items": {"type": "string"}}
  },
  "if": {"properties": {"status": {"enum": ["DONE", "DONE_WITH_CONCERNS"]}}},
  "then": {"properties": {"commits": {"minItems": 1}}}
}
```

Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
