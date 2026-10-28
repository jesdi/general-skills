---
name: implement-spec
description: "Implement a specification in code."
disable-model-invocation: true
---

<!--
Modified copy of https://github.com/mattpocock/skills/tree/5b15a47f2d7150f545fbcacbfe381787fc0230dc/skills/in-progress/implement-spec
(in-progress, after mattpocock-skills v1.2.3). Copyright (c) 2026 Matt Pocock, MIT — see LICENSE.
Changes: the method is carried by a deterministic driver (run.sh, driver*.py), not by
this session; this file only starts the driver. The method text is in the prompt
templates (prompts/*.md) and in README.md. The driver keeps what this fork added
to upstream: a model list per role; a context-isolated test-writer that commits
locked acceptance tests at the ticket's Seam; a review before each merge with a
mutation proof per locked test file; at most 2 fix rounds with escalation at round 2;
a correctness pass on another provider for a ticket with a Risk line; one gate
record per trusted commit, run by the driver; tickets that touch the same function
run in sequence; a gate on each merge commit; one commit per ticket before the
final review; no push and no PR until every ticket is merged and the final review
has passed. The review rounds adapt superpowers' subagent-driven-development
(obra/superpowers, MIT).
-->

Run the driver. It makes every choice of the run; you start it and relay its result.

1. In the PR worktree, run `sh <skill-dir>/run.sh` with the flags the operator gave, unchanged. `<skill-dir>` is the directory of this file. The driver needs `--gate` and `--spec`: ask the operator for a flag that is missing. A run can take hours: run the command in the background and wait for it to exit.
2. When it exits, relay `.agent/implement/summary.md` and the exit code: 0 is done, 1 is a BLOCKED ticket, 2 is parked on questions, 3 is invalid input, 4 is stopped by a CLI failure (after the fix, the same command resumes). Relay the lines the driver printed after the summary too. With exit 0 and a PR URL such as `https://github.com/jesdi/demo/pull/61`, tell the operator to start the babysitter: run `/babysit-pr https://github.com/jesdi/demo/pull/61`.
3. With exit 2, and with exit 1 when the driver printed questions, show the operator those questions. When the operator answers, run the same command again with one `--answer <question id>=<option id>` for each answer.

Do not answer a question for the operator. Do not edit `.agent/implement/`, the code or the branches during a run.

[README.md](README.md) has each flag, the exit codes, the files of a run and the method.
