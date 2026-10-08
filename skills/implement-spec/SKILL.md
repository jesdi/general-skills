---
name: implement-spec
description: "Implement a specification in code."
disable-model-invocation: true
---

<!--
Modified copy of https://github.com/mattpocock/skills/tree/5b15a47f2d7150f545fbcacbfe381787fc0230dc/skills/in-progress/implement-spec
(in-progress, after mattpocock-skills v1.2.3). Copyright (c) 2026 Matt Pocock, MIT — see LICENSE.
Changes: a model tier per subagent; a context-isolated test-writer commits
locked acceptance tests at the ticket's Seam, the implementer turns them green
with /tdd and reports to a contract; per-ticket review before merge, fix loop
capped at 4 rounds with escalation; a ruling ledger and four stop conditions;
a per-ticket gate (check command + crap-gate) before DONE, re-run by the reviewer;
already-green locked tests need a demonstrated mutation; a Codex correctness
pass per risky ticket; tickets touching the same functions run in sequence;
check command after each merge; final /review-diff on the most capable model, one
fixer; ticket-grouped history with fixes folded in before review; closes
tickets only if they are issues; no push and no PR until every
ticket is merged and the final review has passed. Review loop and ledger adapt
superpowers' subagent-driven-development (obra/superpowers, MIT).
-->

You have been provided a spec. This spec should have tickets associated with it, describing how to implement the spec.

The goal is a PR which implements the entire spec on a single branch.

The tickets are not a list of steps. They are a **task graph** with blocking relationships between them. This means there is always a **frontier** of tickets which are ready to be grabbed.

Communication to and from subagents should be sparse. Communicate primarily through **context pointers**: to the spec, tickets, research notes, and previous commits. Don't duplicate information already available via pointers.

Run ticket subagents in the background where possible for **maximum concurrency**.

**Every dispatch prompt says: never `git stash`, reset or check out other branches; worktrees share one stash and one ref store.**

**Name the model on every dispatch.** Omitted, it inherits yours. Exploration, test-writer, reviewer: mid tier (reviewer higher for risky diffs). Implementer: mid tier; cheapest for a small, fully specified ticket; most capable for design judgment or broad codebase work. Merger: cheapest. Escalations: one tier above the agent that got stuck. Final reviewer: most capable.

**Rule, don't stall.** Keep a **ledger** file in the notes directory: every decision as `Ruling: <what> — <why> — <cost if wrong>`, and each ticket's state, so you can resume after compaction. Don't check in between tickets. Stop and ask only for: an irreversible or destructive operation, a security-sensitive action, a side effect outside the worktrees (a push to a shared branch, a publish), or a spec so broken that every path forward is a guess.

## Steps

1. Read the spec and tickets. Read enough to understand the task graph.

2. Create a notes directory outside the repo, readable by all subagents, and start the ledger there. (optional) An **exploration subagent** saves its notes on relevant code and docs there, so implementers focus on implementation.

3. Create the PR branch locally. Do **not** push it and do **not** open a PR yet: nothing leaves the
   machine until every ticket is merged and the final review has passed (step 10). Intermediate
   states (red tests, half-merged tickets) are never pushed. If the spec and tickets are issues,
   note their numbers in the ledger so the PR can close them later.

4. Each ticket gets its own worktree and branch. For a ticket with behavioral criteria, a **test-writer subagent** goes first. It sees ONLY the spec, the ticket, CONTEXT.md/ADRs and the public interface of the ticket's **Seam** line (pointers, not summaries), never the implementer's plan or code. It writes one black-box acceptance test per criterion through that interface (defining the interface if it doesn't exist yet), confirms each fails for the right reason (missing behavior, not an import or syntax error), commits them red, and reports the SHA and files. A test that is already green before any implementation is vacuous until proven otherwise (an earlier guard or early return often satisfies it for an unrelated reason): the test-writer rewrites it to fail, or, when the criterion truly holds already, reports the production-code mutation that turns it red and the red output. Those files are now **locked**. A ticket with no behavioral criteria (Seam: `none — refactor`) skips this: its existing tests are the contract, unmodified.

5. An **implementer subagent** starts from that commit and makes the locked tests green, using /tdd vertical slices for anything internal. It never edits locked files and never dispatches subagents. Before reporting DONE it runs the **gate** in its worktree: the repo's check command (lint, typecheck, full test suite: `make check`, or what AGENTS.md documents) and, when the repo has `.crap-gate.json`, crap-gate scoped to the ticket (`run.sh --base <PR branch>`, or the repo's `make crap-gate` wrapper). DONE means both are green; a gate it can't turn green is BLOCKED, with the output. It reports: status (DONE, DONE_WITH_CONCERNS, NEEDS_CONTEXT or BLOCKED), commits, red→green evidence for locked tests and its own slices, the gate's final lines, files touched, concerns. If a locked test contradicts the spec or ticket, it reports BLOCKED with evidence; you rule, and either send the test-writer a correction or overrule the implementer. Handle any BLOCKED by changing something: more context, a stronger model, a split ticket, or a ruling. Never retry unchanged.

6. On DONE, a **reviewer subagent** re-runs the gate first (a red gate is a Critical finding; don't trust the report), then checks the diff against the ticket and spec, then for quality. It also checks that `git diff <test-writer-sha>..HEAD -- <locked files>` is empty and that every criterion has a locked test. For each locked test, it breaks the production line the test exists to protect (a temporary edit, reverted after) and confirms that test goes red; a test that stays green is Critical. It re-runs every mutation the test-writer reported. Name your specific suspicions in the reviewer prompt (a sibling caller, a key built from the old identity, a guard another ticket added): named suspicions find regressions a generic review misses.

   A **risky** ticket touches shared state, persistence keys or identities, concurrency, money or auth. For those, run the correctness pass from /review-diff yourself, in parallel with the reviewer: `codex exec` with that skill's `correctness.md` on range `<PR branch>...<ticket branch>` (three dots: from the merge-base, so tickets merged meanwhile don't show up), in quick mode (the command is in the review-diff skill). Its findings join the reviewer's, with the same severities.

   Critical or Important findings start a fix loop of at most 4 rounds, each a fix plus a re-review scoped to that fix: rounds 1-2 resume the implementer; rounds 3-4 go to a fresh implementer one tier up. After round 4, rule on each open finding in the ledger and move on. Minor findings go to the ledger for the final review.

7. Once a ticket passes review, a **merger subagent** merges it to the PR branch and runs the repo's check command (lint, typecheck, full suite). If it fails, an implementer fixes the PR branch before the next merge. Record the ticket's commits and any integration fixes in the ledger. After the merge and checks pass, remove that ticket's worktree and delete its branch with `git branch -d` from the PR branch, while its commits are still ancestors of the PR tip. Cleanup is part of completing the ticket, before any rebase.

8. If this changes the **frontier** of available tickets, start the next tickets. This allows for maximum concurrency. Exception: two ready tickets that change the same function never run at once. Compare their **Touches** lines (or ask the exploration subagent where each will land); on overlap, start one and hold the other until the first merges, then branch it from the updated PR branch. When in doubt, hold: a ticket you suspect builds on another ready or running ticket waits for it, even without a declared edge. Record the hold as a ruling.

9. Once all tickets are complete, fetch and rebase the unpublished PR branch onto the latest base, then tidy its history by ticket using [history.md](history.md). A reviewer on the most capable model runs /review-diff on the resulting PR branch, given the ledger's minor findings and rulings. Record its resolved range and reviewed tip in the ledger. One **implementer subagent** fixes every finding as one batch, which may contain several working commits; it records the owning ticket for each fix. Use targeted checks during the fixes. Once the whole batch is complete, tidy it into the ticket commits, then run /review-diff on the full rewritten branch. If history was preserved, a scoped /review-diff on `<last-reviewed-tip>..HEAD` suffices. Its gate runs once for the completed batch. Record the new review's range and tip, marking superseded coverage, or `not needed` if there were no fixes. Rule on anything left.

10. Before pushing, compare `git rev-parse HEAD` with the last reviewed tip and confirm the worktree is clean. Every commit through the branch tip must be covered by the full review plus any scoped re-reviews. Collect any later commits, including orchestrator changes, into one batch and follow step 9's tidy-and-review procedure. A rebase or history rewrite invalidates the recorded coverage: review the full rewritten branch, with one full gate for that completed batch. The push is ready only when the history is grouped by ticket, the clean branch tip equals the last reviewed tip, and every finding has a fix or a ledger ruling. If the review cannot finish, report the blocker and each unreviewed commit with its subject, and keep the branch local.

    Now push the PR branch once and open the PR, ready for review (not draft), using /pr for the body when installed. If the spec and tickets are issues, mark the PR as 'closing' them. In your final message, list every ruling, the final ticket-to-commit map, and the review coverage: the latest full review's range and tip, any scoped re-review ranges and tips since it (or `not needed`), the current branch tip, and whether it equals the last reviewed tip. Earlier reviews of superseded history stay in the ledger.

11. Confirm every ticket worktree and branch was removed in step 7.
