---
name: red-team-data-model
description: >-
  Attack a proposed or existing data model for the ways it breaks under real
  conditions — concurrency, time windows, money, permissions — without
  proposing a redesign. Use right after sketching a data model, before
  writing the spec, so the top failure scenarios shape the spec's
  requirements and tests instead of getting discovered mid-build. Trigger on
  "red-team my data model", "what breaks in this schema", or "/red-team".
---

# Red-team the data model

## Rule

Don't improve or redesign. Attack it. The output is a list of ways it fails, not a fix.

## Prompt

Given the tables/fields (from `design.md` or a sketch), produce:

**Top 5 failure scenarios**, ranked worst-consequence-first. Each one:

- **Table/field** — exactly which part of the model is implicated.
- **Scenario** — the concrete sequence of events that breaks it (two users, two
  requests, a clock, a race — be specific, not "concurrency issues could
  occur").
- **Consequence** — what actually goes wrong in business terms (double-sold
  stock, a refund that never happens, a stranger seeing another customer's
  order, money debited twice).

## Focus areas, in this order

1. **Concurrency** — two actors racing the same row (two checkouts, one unit
   of stock; two edits, one record).
2. **Time windows** — expiry, deadlines, "usually ready within X", reminder
   timing, what happens exactly at the boundary and one tick after it.
3. **Money** — partial payments, refund correctness, rounding, double-charge
   on retry, what happens if a webhook never arrives.
4. **Permissions** — who can see or mutate what; staff vs. customer vs. owner
   boundaries; what an authenticated-but-wrong-role request can still reach.

## What NOT to do

- Don't suggest schema changes, indexes, or constraints — that's the design
  session's job, after this list exists.
- Don't hedge with "it depends" — pick the worst plausible interpretation and
  state its consequence.
- Don't produce more than 5. Rank and cut; the top failure matters more than
  a complete list.
