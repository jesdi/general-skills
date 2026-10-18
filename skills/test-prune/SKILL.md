---
name: test-prune
description: >-
  Mutation-guided test pruning: a fixer proposes deleting redundant tests and strengthening
  weak ones, an independent reviewer accepts or rejects each proposal, and prune.py applies
  the accepted batch behind a cold mutation run and the ratchet. Use when the user says
  "prune tests", "test-prune", "mutation test pruning" or "strengthen weak tests", or when a
  scheduled job runs it with `--unattended`.
---

# test-prune: delete what the mutation ratchet proves redundant, strengthen what it proves weak

You are the orchestrator. `prune.py` owns every git, make and file operation and every safety
guard; the subagents only write JSON files or test commits. Each role is a fresh subagent with no
context from the others: the reviewer reads the fixer's proposals, reasons included, and judges
them against the code itself.

**Publishing belongs to prune.py.** In both modes you never run `git push`, `git merge`,
`gh pr create` or `gh pr merge` yourself, and neither does any subagent; `prune` and
`strengthen-finish` push their own `chore/test-*` branch and open its PR, and nothing merges.

The target repo provides the make targets `test`, `mutation`, `mutation-check`,
`mutation-baseline`, `test-candidates`, `prune-gate` and `crap`, and the layout `backend/tests/`,
`backend/mutation_baseline.json`. Every command:

```bash
python3 "<skill-dir>/prune.py" <command> [--state DIR] [--date YYYY-MM-DD]
```

Run it from inside the target repo. State lives in `<git dir>/test-prune/` (outside the working
tree); each command reads what the previous one wrote there. Exit 0 is done (including nothing to
do), 1 is a failed make target, gate or guard, 2 is bad input or an unmet precondition; the
message names the offending ids or files. On exit 2 fix the input and rerun the same command; on
exit 1 stop and report the output.

Every command also records itself in `<state>/status.json`, sticky across the run (`collect`
starts it afresh): `{"exit", "reason", "commands": [{"command", "exit", "reason"}]}`, where
`exit` is the highest exit code still open and `reason` the first line that command printed. An
exit 1 stays for the whole run; an exit 2 closes once the same command reruns with exit 0. A failed
make target's reason names it (`make test failed (exit 2)`); the summary below it carries the last
30 lines of its output.
End a headless session by reporting that `reason` as the final line. The session cannot set the
job's exit code: ticket 24's CI job reads `<state>/status.json` after the session and fails the
job when `exit` is not 0. Nothing to do is exit 0.

PR bodies and summary blocks stay under 60,000 characters (GitHub refuses a PR body over 65,536):
a longer one is cut with a line `… truncated, full text in <state>/<file>`. The prune PR carries
the gate's report (its ratchet block and verdict); the gate's full log is `<state>/prune-gate.log`.

When a push succeeded but `gh pr create` failed (exit 1, the branch is on origin with no PR),
open the PR by hand from `<state>/prune-pr.md` or `<state>/strengthen-pr.md` after fixing `gh`;
rerunning the command refuses because the branch exists.

**A run needs a checkout dedicated to it** for its whole duration: the CI checkout, or a worktree.
If anything else edits a file there mid-run, the run stops with exit 1 rather than overwrite it,
and nothing is lost; races beyond that check are out of scope.

**Runtime.** Each cold `make mutation` takes about 30 minutes on a real repo, and a run makes
several (collect, every bisect step, each strengthen proof). Run long commands with a timeout of
an hour or more, or in the background, and wait for them.

## Modes

- **Interactive** (default): nothing in the working tree changes before the human approves the
  batch. Stop after `batch`, show its output, and run `apply` only on an explicit yes.
- **Unattended** (`collect --unattended`, run headless by the weekly CI job): only protected
  candidates, at most 10 deleted test ids per run (a parametrized test goes whole, every case
  counted; one with more than 10 cases is skipped), work on a detached HEAD at the base commit, one PR from `chore/test-prune-<date>`
  and one from `chore/test-strengthen-<date>`, both from the base commit. The base branch stays
  where it was. A merge is the human's approval, so the run ends with open PRs. Leaving the detached
  HEAD, a command restores only the files that still hold what it wrote; any other tracked change,
  including an edit to one of those files mid-run or a baseline left by a failed
  `make mutation-baseline`, keeps HEAD detached and untouched, is named in the summary, and fails
  the command (exit 1). An unexpected error is reported as `test-prune: unexpected <Type>: <msg>`,
  exit 1. Keep or discard the change, then check out the base
  branch.

Two kinds of accepted proposal reach a human only: equivalent mutants (the skill writes no
equivalent id into the baseline) and "strengthen this candidate with this assertion" verdicts
(the skill applies no assertion to an existing candidate). `batch` prints both; unattended, every
PR body and the job summary carry them.

## Prune loop

1. `collect [--unattended | --protected-only]`. Needs a clean tracked tree on a branch. Runs the
   base checks, a cold mutation run and `make test-candidates`, then writes
   `fixer-input.json`. When it prints `no candidates` / `no protected candidates`, the weak spots
   may still need the fixer; continue.
2. **Fixer.** Dispatch a subagent with `<state>/fixer-input.json`, read access to the repo, and
   this brief: for every candidate decide `keep`, `delete` into one named test that contains it,
   or `strengthen` with one named assertion; for every weak-spot group decide `strengthen`
   (which test to add to or strengthen) or `equivalent` (with the argument why no test can tell
   the mutant apart). A group with both kinds of mutant takes two entries, one `strengthen` and
   one `equivalent`, each listing its own disjoint `mutant_ids`. Compare assertions, not coverage: a candidate whose assertions pin a value,
   boundary or error the container never checks is `keep` or `strengthen`. It writes:

   ```json
   {"candidates": [{"test_id": "...", "verdict": "keep|delete|strengthen",
                    "into": "<test id, delete only>", "assertion": "<strengthen only>", "reason": "..."}],
    "weak_spots": [{"group": "...", "verdict": "strengthen|equivalent",
                    "mutant_ids": ["<subset of the group's ids, omit for all>"],
                    "test": "<test id or test file>", "reason": "..."}]}
   ```

   One entry per candidate, one per group (or the strengthen + equivalent pair), no others.
3. `review-input --proposals <fixer file>` validates the file and writes
   `<state>/review-input.json`: each proposal with the candidate's source and assertions and,
   for a deletion, the containing test's source and assertions. A proposal's `id` is the
   candidate's test id or the group; a split group's two proposals are `<group>:strengthen` and
   `<group>:equivalent`.
4. **Reviewer.** Dispatch a *different* fresh subagent with `<state>/review-input.json` and read
   access to the repo. Brief: accept a deletion only when every assertion of the candidate is
   implied by an assertion of the `into` test (same inputs or a superset, an equal or stricter
   check); covering the same lines is not enough. Accept a strengthen proposal only when the
   named assertion checks behaviour a user would notice. Accept an equivalent only with a
   concrete argument that no input distinguishes the mutant. Every reason names the assertion or
   input it rests on. It writes, one entry per proposal:

   ```json
   {"reviews": [{"id": "<proposal id>", "verdict": "accept|reject", "reason": "..."}]}
   ```

5. `batch --reviews <reviewer file>` writes `batch.json` and prints the accepted deletions (each
   with its containing test and the reviewer's reason), strengthen items, equivalent proposals,
   skipped deletions with the reason, and rejections. It edits nothing.
   - Interactive: show that output to the human and stop. After approval, `apply` applies the
     deletions, runs the safety check, restores every culprit and commits on the current branch.
   - Unattended: `prune` applies the first 10, runs the safety check, `make test`, commits,
     runs `make prune-gate` and, only when it passes, pushes the branch and opens the PR. When a
     step after the safety check fails, its summary still carries the safety-check result
     (restored tests, ratchet lines), headed by where the deletions are: not committed, or on the
     unpushed (or pushed, PR-less) branch.

**Safety check** (inside `apply` and `prune`): a cold mutation run plus `make mutation-check`
on the batch; when a module gains survivors it bisects (a parametrized test is one unit), restores each culprit and reports it by
test id with the module, the survivors with it deleted, the baseline and the survivors after
restoring it.

## Strengthen loop

Runs after the prune loop, also in unattended mode, independently of the prune PR: run it even
when `prune` exited 1 (the status keeps that failure, so the job still goes red).

1. `strengthen-start` writes `<state>/strengthen-input.json` (each accepted weak-spot strengthen
   item with the mutant ids it must kill) and prints the commit to build on; unattended it
   detaches HEAD at the base commit. `nothing to strengthen` ends the loop.
2. **Strengthen agent.** Dispatch a fresh subagent with `strengthen-input.json`. Brief: per item,
   test-first, write the assertion or test that fails against each named mutant and passes on
   the real code; touch only `backend/tests/`; make one or more commits per item, each ending
   with the trailer line `Test-Prune-Item: <item id>`; leave the tree clean.
3. `strengthen-verify` rejects commits without a known trailer or outside `backend/tests/`, then
   proves each item on a cold run: every named id survived on the base and is killed on the
   head. Unproven items are reverted and closed: a later commit for a closed item is refused. It
   writes `<state>/strengthen-proof.json`, including the proven head. On failure HEAD stays
   where the commits are (detached, unattended): fix and rerun. To abandon the loop instead,
   `git checkout <base branch>`; the item commits stay reachable through `git reflog`.
4. **Strengthen reviewer.** Dispatch a fresh subagent with `strengthen-proof.json` and the diff
   of the proven items (`git log -p <start>..HEAD`). Brief: accept an item only when its test
   asserts observable behaviour through the public interface, rather than restating the
   implementation; one review per proven item, same schema as the prune reviewer.
5. `strengthen-finish --reviews <file>` refuses a HEAD other than the proven one (rerun
   `strengthen-verify` after any new commit); on any exit 2 HEAD stays at the proven head, so fix
   the input and rerun it. It reverts rejected items, reruns the proof cold, runs
   `make mutation-check`, `make test`, `make crap` and `make mutation-baseline`, refuses a
   baseline that gained an equivalent id, and commits the lower baseline. Unattended it opens the
   strengthen PR listing each changed test with the ids it now kills; interactive it leaves the
   commits on the current branch for the human.

## Report

End with what the last command printed: deleted, restored, skipped and left-for-next-week tests,
strengthened tests with their killed ids, the PR links, and the equivalent proposals. In CI the
same summaries land in `$GITHUB_STEP_SUMMARY`.
