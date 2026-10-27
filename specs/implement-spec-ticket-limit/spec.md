# implement-spec ticket limit — Spec

Source: [general-skills issue #146](https://github.com/jesdi/general-skills/issues/146),
user stories 1–2 and implementation increment 1.

## Requirements

1. A run accepts at most five valid ticket files. When its ticket set has six or more,
   the driver exits 3 and prints `split into increments` during input validation.
2. The ticket limit is checked before the driver launches an agent or changes a run's
   state, worktrees, branches, or PR.
3. A run with five or fewer tickets keeps the existing shape: one run works toward one
   PR to main. The driver does not split a run into multiple increments or PRs.

## Scenarios

### Scenario: six tickets are refused before work starts

- **Given** a clean PR worktree and six valid numbered ticket files, with the normal
  required driver flags and a scripted agent runner
- **When** the operator starts the driver
- **Then** it prints `split into increments`, exits 3, launches no agent, and leaves
  the run state, worktrees, branches, and PR unchanged

### Scenario: five tickets remain valid input

- **Given** a clean PR worktree and five valid numbered ticket files
- **When** the operator runs the driver's dry run with its required gate flag
- **Then** input validation succeeds without `split into increments`

### Scenario: an accepted run keeps one PR

- **Given** a clean PR worktree and five valid tickets whose scripted agents and
  gates complete successfully
- **When** the operator runs the driver through publication
- **Then** the run produces one PR branch and one PR to main, with no extra
  increment or PR created by the ticket limit

## Out of scope

- Dividing one run's tickets into several increments or PRs.
- The review, gate, role, question, and locked-test changes in issue #146's later
  four increments.
