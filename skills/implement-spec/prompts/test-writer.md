# Test-writer

Spec: {spec}
Ticket: {ticket}
Seam: {seam}
Branch: {branch}, at commit {start_sha}

Read the spec, the ticket, CONTEXT.md and the ADRs when they exist, and the public interface of the Seam. Do not read an implementation plan or implementation code.
Write one black-box acceptance test for each criterion of the ticket, through the Seam only. Define the interface when it does not exist.
Each fixture sets up exactly the precondition of its scenario. Check it against the rules of the spec and against the locked tests of merged tickets: {protected}.
Run each test and confirm that it fails because the behavior is missing, not because of an import error or a syntax error. Print that failing output.
A test that is green before any implementation is not a proof. Rewrite it until it fails, or name in `concerns` the production-code mutation that turns it red.
Commit the tests red on the checked-out branch. Do not write implementation code and do not dispatch subagents.
In `locked`, list each test file of your commit: test file paths only, not test node ids (`tests/x.py`, not `tests/x.py::test_a`). In `red`, give for each test one failure line, copied exactly from the output you printed.

Write one JSON report at {report} that matches this schema:

```json
{
  "type": "object",
  "required": ["status", "commits", "files", "concerns", "locked", "red"],
  "properties": {
    "status": {"enum": ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]},
    "commits": {"type": "array", "items": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"}},
    "files": {"type": "array", "items": {"type": "string"}},
    "concerns": {"type": "array", "items": {"type": "string"}},
    "locked": {"type": "array", "items": {"type": "string"}},
    "red": {"type": "array", "items": {"type": "object", "required": ["test", "line"], "properties": {"test": {"type": "string", "minLength": 1}, "line": {"type": "string", "minLength": 1}}}}
  },
  "if": {"properties": {"status": {"enum": ["DONE", "DONE_WITH_CONCERNS"]}}},
  "then": {"properties": {"commits": {"minItems": 1}, "locked": {"minItems": 1}, "red": {"minItems": 1}}}
}
```

Stop only processes you started and their child processes, by PID (for example the PID of a job you put in the background, or its process group). Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. If a process you did not start blocks you, report it in your concerns and do not kill it.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
