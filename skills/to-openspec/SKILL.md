---
name: to-openspec
description: >-
  Turn settled discovery notes and a sketched data model into a spec folder
  in openspec shape (proposal, spec, design) under specs/<slug>/, plus ticket
  files under .agent/tickets/ that implement-spec runs directly. Two stages:
  stage 1 alone by default, with a human review before stage 2, or both at
  once with "all". No interview, just synthesis of decisions already made.
  Trigger on "write the spec", "to-openspec", or
  "/to-openspec <slug> [stage 2 | all]".
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
  Ignore a `tasks.md` template: tickets replace it.
- Check command: the one the repo documents (`make check`, or what AGENTS.md names).
- Stages: no argument runs stage 1 and stops. `stage 2` runs stage 2 only. `all` runs both
  without the stop in between.

## Stage 1: what and why

Write `specs/<slug>/proposal.md` and `specs/<slug>/spec.md`.

- **Goal** is one or two sentences. Every ticket serves it.
- **Non-goals** carry over every "later" or "never" verdict from discovery.
- **Requirements** cover the v1 slice only: the smallest set that meets the goal. Each one is
  testable and maps to at least one scenario.
- **Scenarios** are Given/When/Then with concrete values (quantities, amounts, times, roles).
  Every invariant (overselling, expiry, refund permissions, and so on) gets a scenario for the
  boundary and one for the violation.

Then **stop**, unless invoked with `all`. Print the requirement list and any open questions so
the human can cut or correct the spec before the design is written. Don't write code.

## Stage 2: how

Run only when asked: "/to-openspec <slug> stage 2" after the human has reviewed stage 1, or
`all` straight after stage 1. On `stage 2`, re-read `proposal.md` and `spec.md` first; the
human may have edited them.

1. Run the `red-team-data-model` skill against the data model. Print the top 5, and its identity
   inventory when the change widens a key; don't write them to a file.
2. Write `specs/<slug>/design.md`:
   - **Decisions**: one line each with its tradeoff. Include the ones the red-team findings force.
   - **Data model**: every invariant a DB constraint can enforce (`CHECK`, `UNIQUE`, `NOT NULL`,
     FK, conditional `UPDATE ... WHERE`) goes in the table, not only in app code.
   - **Identities**: when the change widens a key, every item of the red-team's identity
     inventory, marked covered (ticket numbers filled in step 3) or out of scope (why). Otherwise "None." Add the
     heading when the repo's template lacks it.
   - **Seams**: the endpoint, function, or command the tests drive through, marked existing or
     new. Prefer one high seam.
   - No implementation code anywhere in the spec folder. `implement-spec`'s test-writer reads it
     and must not know the solution. A decision's shape (a type, a schema, a state machine, a
     seam's signature) may appear; the function bodies that make the tickets pass may not.
3. Run the `to-tickets` skill on `specs/<slug>/`, skipping its quiz (step 4): it writes
   `.agent/tickets/<NN>-<slug>.md`, the task graph `implement-spec` runs directly. Don't write a
   `tasks.md`. On top of its rules:
   - Every scenario in `spec.md` is an acceptance criterion of some ticket.
   - Each ticket's **Seam** is one of the seams from `design.md`.
   - A slice adds only the tables and columns its own scenarios need, not the whole model up front.
   - No standalone "run the checks" ticket: every ticket already ends with the check command green.
   - Then fill the ticket numbers into `design.md`'s **Identities**: every identity marked covered
     names the ticket that rekeys it. A covered identity with no ticket is a missing ticket; add it.

Print the ticket list (number, title, blocked by, seam, touches), then **Open questions**: every
red-team finding the design leaves
unresolved, and every input the design assumes but the sources don't provide. Then stop. Don't
write code.
