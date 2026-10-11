# implement-spec driver

## Why

The implement-spec skill asks an LLM session to orchestrate a whole spec: walk the ticket
graph, dispatch a test-writer, an implementer and reviewers per ticket, run gates, merge,
keep a ledger, and finally review and push. The measured Stage 2 run (Codex session
01a111b7, six tickets, 44 hours) shows what that costs: 65 full gates plus 15 scoped where
the skill asks for about 13, 27 % of sub-agent turns spent polling logs, 17 compactions, one
corrupted fact after compaction, 15 preparation agents outside the skill's roles, dispatch
prompts that invented artifact demands the skill never made, and every agent re-discovering
the same environment setup. The orchestrator improvises the control flow, and improvisation
is where the time and the money went. The control flow has a right answer that a script can
compute from files and exit codes; only the reading of code and specs needs a model.

## For whom

- The operator who runs implement-spec from a terminal on a repository with a spec and
  tickets, and wants a PR without babysitting a 44-hour session.
- The agent-ops box, whose implement stage runs the same thing unattended and parks on the
  operator's decisions through the console's review page.

## Goal

A deterministic command, shipped with the implement-spec skill, that turns a ticket set into a
merged, reviewed, tidied PR branch by launching one bounded agent session per role, running
the gate itself, and stopping only on bounded human choices or a BLOCKED ticket. The five
non-negotiable checks hold by construction: locked tests with a demonstrated red, one mutation
proof per locked test, one independent correctness pass per risky ticket, one full
coverage-backed gate per immutable candidate, and a post-merge check on the PR branch.

## Non-goals

- Claude Code's Workflow tool. Declined by the operator.
- Living in the agent-ops dispatcher. The driver is a skills-repo command; agent-ops calls it.
- A model vocabulary of its own. Model entries use the agent-ops grammar `provider/model@effort`.
- A free-text question channel to the human. A stop is a bounded choice or a BLOCKED ticket.
- A ruling at fix round 2. Rounds 3-4 run with escalation; round 4 ends the ticket.
- Folding final-review fixes into the ticket commits and re-reviewing the rewritten branch.
- Knowing Python or Node. Gate and slot preparation are repo-owned commands.
- Gate dedupe inside the repo's gate command (one pytest run under coverage). Agent-ops work.
- The agent-ops side: implement stage as a command, parking on `awaiting-review` from a
  non-session stage, the `implement-answers` kind, AGENTS.md pointers. A follow-up PR there.
- An eval harness. The driver measures itself; the comparison with Stage 2 is by hand.
- Windows.
