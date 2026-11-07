# Worktree lifecycle

The driver owns a bounded pool of ticket checkouts. Slot 1 is the existing PR checkout; additional slots live at `.agent/worktrees/slot-N`, up to `--parallel`. The scheduler creates an additional slot only when a ready ticket first needs it. A serial dependency chain uses slot 1 alone, and a completed resume creates no slots.

Before scheduling, the driver validates existing slots. A slot must be a healthy checkout of this repository at the assigned path. Clean slots are detached for reuse; interrupted work stays in its recorded slot. A foreign checkout or a Git failure stops reuse rather than being treated as an empty status result.

New slots start at a captured PR tip. Slot 1 may already be on an unmerged ticket commit, so its current HEAD is not the preparation baseline. `--prepare` runs once per new slot; later tickets and fix rounds reuse that checkout and its installed dependencies.

A missing slot is recreated with one `git worktree add --force --detach` targeting its path and the captured tip. Git refuses a locked registration. The driver never runs `git worktree prune`: a session can see shared repository metadata while another active task's checkout is outside its mounts.

If preparation fails during scheduling, the driver starts no further tickets. Already running tickets finish and retain their progress. The failed slot is removed, a clean slot 1 returns to the PR branch, and the driver exits 3 with the preparation error. Fix the preparation command and rerun the same invocation to resume; the ticket whose slot failed remains pending.

Role agents reuse their assigned checkout. Read-only reviewers first consult existing gate evidence and create a temporary checkout only for a specific isolated check that the evidence cannot answer.
