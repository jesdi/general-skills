---
name: to-openspec
description: >-
  Turn settled discovery notes and a sketched data model into a spec folder
  in openspec shape (proposal, spec, design, tasks) under specs/<slug>/.
  Runs in two stages with a human review between them. No interview, just
  synthesis of decisions already made. Trigger on "write the spec",
  "to-openspec", or "/to-openspec <slug> [stage 2]".
---

# To openspec

Synthesize, don't decide. Every decision comes from `discovery.md`, the design sketch, or the
conversation. If one is missing, list it under **Open questions** at the end of your reply
instead of inventing it. Never invent prices, policies, deadlines, or permissions.

## Input

- `<slug>`: kebab-case, becomes `specs/<slug>/`. Derive one from the goal if not given.
- Sources: `discovery.md` (verdicts, non-goals, open questions), the data-model sketch, and the
  conversation.
- Shape: read every file in the repo's `specs/_template/`, or in this skill's `templates/` when
  the repo has none. Keep the headings exactly; write "None." rather than dropping a section.
- Check command: the one the repo documents (`make check`, or what AGENTS.md names).

## Stage 1: what and why

Write `specs/<slug>/proposal.md` and `specs/<slug>/spec.md`.

- **Goal** is one or two sentences. Every later task links back to it.
- **Non-goals** carry over every "later" or "never" verdict from discovery.
- **Requirements** cover the v1 slice only: the smallest set that meets the goal. Each one is
  testable and maps to at least one scenario.
- **Scenarios** are Given/When/Then with concrete values (quantities, amounts, times, roles).
  Every invariant (overselling, expiry, refund permissions, and so on) gets a scenario for the
  boundary and one for the violation.

Then **stop**. Print the requirement list and any open questions so the human can cut or correct
the spec before the design is written. Don't write code.

## Stage 2: how

Run only when asked (for example "/to-openspec <slug> stage 2"), after the human has reviewed
stage 1. Re-read `proposal.md` and `spec.md` first; the human may have edited them.

1. Run the `red-team-data-model` skill against the data model. Print the top 5; don't write them
   to a file.
2. Write `specs/<slug>/design.md`:
   - **Decisions**: one line each with its tradeoff. Include the ones the red-team findings force.
   - **Data model**: every invariant a DB constraint can enforce (`CHECK`, `UNIQUE`, `NOT NULL`,
     FK, conditional `UPDATE ... WHERE`) goes in the table, not only in app code.
   - **Seams**: the endpoint, function, or command the tests drive through, marked existing or
     new. Prefer one high seam.
3. Write `specs/<slug>/tasks.md`: vertical slices, each small enough for one commit and one green
   check command. Tests come first in each slice.
   - Every task has its `_Goal:_` line, its **Seam** (one of the seams from `design.md`, precise
     enough to write a black-box test against) and its **Blocked by** (task IDs, or none). That
     makes the list a task graph `implement-spec` can run directly.
   - A slice adds only the tables and columns its own scenarios need, not the whole model up front.
   - No standalone "run the checks" task: every task already ends with the check command green.

Print the task list, then **Open questions**: every red-team finding the design leaves
unresolved, and every input the design assumes but the sources don't provide. Then stop. Don't
write code.
