---
name: implement-spec
description: "Implement a specification in code."
disable-model-invocation: true
---

<!--
Modified copy of https://github.com/mattpocock/skills/tree/5b15a47f2d7150f545fbcacbfe381787fc0230dc/skills/in-progress/implement-spec
(in-progress, after mattpocock-skills v1.2.3). Copyright (c) 2026 Matt Pocock, MIT — see LICENSE.
Changes: an explicit model tier for every subagent; implementers use /tdd and
report red→green evidence in a fixed report shape; a reviewer per ticket
before merge, with a fix loop capped at 5 rounds and escalation; a ruling
ledger and four stop conditions instead of check-ins; the merger runs the
full suite after each merge; the final review runs on the most capable model
with one fixer for all findings; the PR closes tickets only when they are
issues. The review loop and ledger adapt ideas from superpowers'
subagent-driven-development (obra/superpowers, MIT).
-->

You have been provided a spec. This spec should have tickets associated with it, describing how to implement the spec.

The goal is a PR which implements the entire spec on a single branch.

The tickets are not a list of steps. They are a **task graph** with blocking relationships between them. This means there is always a **frontier** of tickets which are ready to be grabbed.

Communication to and from subagents should be sparse. Communicate primarily through **context pointers**: to the spec, tickets, research notes, and previous commits. Don't duplicate information already available via pointers.

**Implementer subagents** should be run in the background where possible for **maximum concurrency**.

**Name the model on every dispatch.** An omitted model inherits yours. Exploration: mid tier. Implementer: mid tier; cheapest when the ticket carries the complete code; most capable for design judgment or broad codebase work. Ticket reviewer: mid tier, higher for risky diffs. Merger: cheapest. Escalations: one tier above the agent that got stuck. Final reviewer: most capable.

**Rule, don't stall.** Keep one **ledger** file in the notes directory. Record every decision there as `Ruling: <what> — <why> — <cost if wrong>`, and every ticket's state (implementing, fix round N/5, merged), so you can resume after compaction. Don't check in between tickets. Stop and ask only for: an irreversible or destructive operation, a security-sensitive action, a side effect outside the worktrees (a push to a shared branch, a publish), or a spec so broken that every path forward is a guess.

## Steps

1. Read the spec and tickets. Read enough to understand the task graph.

2. Create a notes directory outside the repo, accessible by all future subagents, and start the ledger in it. (optional) Use an **exploration subagent** to conduct any exploration required by the tickets - relevant codebase files or external documentation - saving its markdown notes there. This lets **implementer subagents** focus on implementation rather than exploration.

3. Create a branch, and a draft PR. If the spec and tickets are issues, mark the PR as 'closing' them.

4. Use **implementer subagents** to implement each ticket. Each implementer subagent should work in its own worktree, on its own branch. It uses /tdd for every ticket with acceptance criteria, never dispatches subagents itself, and reports: status (DONE, DONE_WITH_CONCERNS, NEEDS_CONTEXT or BLOCKED), commits, red→green test evidence, files touched, and concerns.

5. Handle BLOCKED by changing something: more context, a stronger model, a split ticket, or a ruling on the spec. Never retry unchanged.

6. Once an implementer reports DONE, a **reviewer subagent** checks its diff against the ticket and spec, then for quality. Critical or Important findings start a fix loop of at most 5 rounds, each a fix plus a re-review scoped to that fix: rounds 1-3 resume the implementer; rounds 4-5 go to a fresh implementer one tier up. After round 5, rule on each open finding in the ledger and move on. Minor findings go to the ledger for the final review.

7. Once a ticket passes review, merge its work to the PR branch with a **merger subagent**. The merger runs the full test suite after each merge. If it fails, an implementer fixes the PR branch before the next merge.

8. If this changes the **frontier** of available tickets, kick off more **implementer subagents** to work on the new tickets. This allows for maximum concurrency.

9. Once all tickets are complete, a reviewer subagent on the most capable model runs /code-review on the PR branch, given the ledger's minor findings and rulings. Fix all issues raised by the code review in a single **implementer subagent**, then re-review that fix once. Rule on anything left.

10. Mark the PR as ready for review. List every ruling from the ledger in your final message.

11. Clean up all **implementer subagent** worktrees.
