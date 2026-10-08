# History by ticket

Tidy the unpublished integration branch into one coherent commit per ticket,
ordered so each ticket follows its dependencies. Each commit contains that
ticket's acceptance tests, implementation, and all review or integration fixes.
The reviewer's commit list should tell the story of the tickets, with the
final behavior at each step.

## Before rewriting

- Confirm all ticket worktrees and branches were cleaned up after their merges,
  and that the integration worktree is clean.
- Confirm the branch has never been pushed. Once pushed, preserve its history
  and add reviewed follow-up commits; rewriting a published branch requires
  explicit user authorization.
- Record the current tip and `HEAD^{tree}` in the ledger as the recovery point.
  Use the ticket reports and ledger to map every working commit and every fix
  to its owning ticket before editing history.

## Group the changes

Use an interactive rebase or reconstruct the commits in an isolated worktree,
operating only on branch-owned commits after the base. Fold red-test commits,
implementation commits, and later fixes into their ticket commit. Flatten
branch-only merge commits while preserving their conflict-resolution changes.
Keep the test-writer's acceptance files unchanged from their locked versions.

A fix touching several tickets is split by ownership when the resulting
commits can stand alone in dependency order. If it cannot be split safely,
combine the affected tickets into one coherent commit, name them in the
message, and record the reason in the ledger. Every final change has a ticket
owner; an out-of-scope change needs a ruling before inclusion.

Use a commit subject that names the ticket's delivered behavior and put its
ticket number or file pointer in the body. Keep a map from the old working
commits to the final ticket commits in the ledger; ticket reports and locked
test SHAs still refer to the original history.

## Completion

The rewritten tip's tree must equal the saved tree: history cleanup changes
the presentation of the work, not its files. A mismatch must be resolved
before review. Inspect each final commit's diff against its ticket to confirm
the tests, implementation, and fixes stayed together, dependencies precede
their consumers, and no temporary red-test or fix-only commits remain.

Use targeted checks where needed to verify a regrouped ticket. Run the full
gate once at the completed branch tip, through /review-diff. Rewriting changes
the review endpoints, so the next review covers the full rewritten branch;
mark the old coverage superseded even if the tip's tree is identical. After a
review requests fixes, finish the whole fix batch, fold it into these same
ticket commits, and repeat this completion check before the next review.
