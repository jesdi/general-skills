# <Feature title> — Design

## Decisions

One line each: the decision and its tradeoff.

- **<decision>** — <tradeoff, one line>.

## Data model

Tables/fields touched or added, and the invariants that go in DB constraints (not just app code).

| Table | Field | Constraint | Why |
|---|---|---|---|
| | | | |

## Identities

When this change widens a key, each identity built from the old key (file names, hashes,
dedupe keys, command args, dict keys): covered by which ticket, or out of scope and why. Otherwise
"None."

- `<identity>` — covered by <ticket> | out of scope: <why>.

## Seams

Where tests drive through: module, function, command, or endpoint. Mark each existing or new.
