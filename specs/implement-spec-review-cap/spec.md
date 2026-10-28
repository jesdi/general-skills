# implement-spec review scope and cap — Spec

Source: [general-skills issue #146](https://github.com/jesdi/general-skills/issues/146),
user stories 3–10 and implementation increment 2. The first increment's five-ticket
input limit is already on main.

## Requirements

1. A ticket review spends fix rounds only on a red gate, an unmet acceptance
   criterion, or a regression of a merged ticket's behavior. Every other
   observation is retained for the final review and does not start a ticket
   fix round or an operator question.
2. A re-review checks whether its own open findings were closed. It may add a
   new blocking finding only for a regression introduced by the intervening
   fix. Other newly noticed observations go to the final review.
3. A ticket gets at most two fix rounds. Round 2 uses the second implementer
   model entry; a model list without that entry is invalid input. At the cap,
   a red gate or unmet acceptance criterion blocks that ticket; tickets that
   do not depend on it continue.
4. At the cap, a ticket with a green gate and no unmet criterion merges even
   when another finding remains open. Every such finding is recorded as
   must-fix, without an individual operator question.
5. The final review sees every deferred observation and must-fix finding. The
   final fix batch addresses every must-fix finding; a prior ruling or the
   ticket's merge cannot make it disappear.

## Scenarios

### Scenario: an unrelated observation waits for the final review

- **Given** ticket 01 has a green gate, its CSV header criterion is met, and
  the reviewer observes duplicated code unrelated to its criteria or any
  merged ticket
- **When** ticket 01 is reviewed and the run reaches final review
- **Then** ticket 01 uses no fix round or operator question for the duplicate,
  and the final review receives that observation with its evidence

### Scenario: an unmet criterion starts a fix round

- **Given** ticket 01 promises a CSV header, its gate is green, and the
  reviewer shows that the header is absent
- **When** ticket 01 is reviewed
- **Then** the unmet criterion is sent to its fixer before ticket 01 can merge

### Scenario: re-review converges on its own findings

- **Given** the first review found a missing CSV header, and fix round 1 adds
  the header without changing another behavior
- **When** the reviewer re-reviews the fix and notices a pre-existing naming
  issue for the first time
- **Then** the header finding is closed, the naming observation is retained for
  final review, and no second fix round starts for that observation

### Scenario: a fix-induced regression can be found on re-review

- **Given** fix round 1 closes a missing CSV header but makes a merged ticket's
  quoted-field behavior fail
- **When** the reviewer re-reviews the changed behavior
- **Then** the quoted-field regression is an open ticket finding eligible for
  round 2

### Scenario: the second round changes model and the cap blocks a broken ticket

- **Given** ticket 01 still lacks its promised CSV header after round 1, the
  implementer list has model A then model B, ticket 02 depends on 01, and
  independent ticket 03 is ready
- **When** round 2 also leaves the header absent
- **Then** round 2 ran on model B, ticket 01 is blocked without a third round,
  ticket 02 waits, and ticket 03 continues

### Scenario: a red gate at the cap blocks

- **Given** ticket 01 has completed fix rounds 1 and 2
- **When** its gate is red on the round-2 candidate
- **Then** ticket 01 is blocked and does not merge

### Scenario: the second fix model is required before work starts

- **Given** a valid ticket set and a model list with only model A for the
  implementer role
- **When** the operator starts the driver
- **Then** it exits 3 with an error naming the missing second implementer
  entry and launches no agent

### Scenario: an open non-criterion finding is fixed at the end

- **Given** ticket 01 has completed two fix rounds, its gate is green, every
  criterion is met, and a merged-behavior regression remains open
- **When** ticket 01 reaches the cap and the run reaches final review
- **Then** ticket 01 merges without an operator question, the regression is on
  the must-fix list, and the final fix batch addresses it before publication

### Scenario: a must-fix finding survives a prior ruling

- **Given** a green ticket merged at the cap with one must-fix finding and the
  run has an earlier ruling about that finding
- **When** final review and the fix batch run
- **Then** the final fixer still receives the finding, and the run cannot
  publish while it remains unresolved

## Out of scope

- Fast and full gate tiers, the separate correctness role, mutation proof
  removal, locked-test dispute arbitration, and question-kind consolidation;
  those are later increments of issue #146.
- More than one PR from a run.
