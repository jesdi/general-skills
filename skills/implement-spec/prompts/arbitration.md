# Arbitration

Spec: {spec}
Ticket: {ticket}
Branch: {branch}, at commit {start_sha}
Locked files: {locked}
Report of the {role} that disputes them: {dispute_report}

The {role} reports that these locked tests contradict the spec or the ticket:
{disputed}
Rule on each of them. Read the test, the requirement of the spec or the criterion of the ticket that it checks, and the concern of the {role}.
`test-wrong`: the test asserts what the spec and the ticket do not say. The test-writer corrects it to your reason, then the {role} runs again.
`code-wrong`: the test asserts what the spec and the ticket say. The {role} runs again with your reason and makes the test green.
In `test`, copy the test id as the list gives it. In `requirement`, name the requirement or the criterion that the test checks. In `reason`, say what it requires, so that the next agent can act on your reason alone.
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
    "rulings": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["test", "requirement", "ruling", "reason"], "properties": {"test": {"type": "string", "minLength": 1}, "requirement": {"type": "string", "minLength": 1}, "ruling": {"enum": ["test-wrong", "code-wrong"]}, "reason": {"type": "string", "minLength": 1}}}}
  }
}
```

Run git as plain `git` in this worktree, and leave the git config as it is: all worktrees read one shared config, and one key written there, such as a `core.worktree`, breaks git in each of them. So never set GIT_DIR or GIT_WORK_TREE, and never write with `git config`.
Stop only processes you started and their child processes, by PID (for example the PID of a job you put in the background, or its process group). Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name or pattern: other slots and sessions share the machine. If a process you did not start blocks you, report it in your concerns and do not kill it.
Never run `git stash`, never reset, and never check out another branch: worktrees share one stash and one ref store.
