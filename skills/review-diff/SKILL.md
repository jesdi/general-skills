---
name: review-diff
description: >-
  Review a branch before merge: first a gate (the repo's check command plus
  crap-gate), then three independent reviewers in parallel — spec (did we build
  what was asked, no more, no less), correctness via Codex (races, idempotency,
  money, time, authz, indexes, N+1, migrations, error paths, test quality) and
  structure (deep-quality-review). `--quick` runs only spec and correctness,
  shortened, for a live interview. Use when the user says "/review-diff",
  "review this branch", "review this slice", "review before merge", or before
  closing out a spec task.
---

# /review-diff

One gate, then three reviewers that never see each other's output. Each
answers one question; their reports stay separate, because a strong answer
on one axis must not hide a failure on another.

| Reviewer | Question | Runs as |
|---|---|---|
| Spec | Did we build what the spec asked, no more, no less? | subagent, `spec.md` |
| Correctness | Can this lose money or corrupt data? | `codex exec`, `correctness.md` |
| Structure | Is the code getting messier? | subagent, `deep-quality-review` |

`spec.md` and `correctness.md` sit next to this file (`<skill-dir>`).

## Arguments

- A range (`A..B`) or a base ref. Default: the merge-base with the repo's
  default branch (`origin/main`, else `main`). Review `git diff <base>...HEAD`.
- A spec path, optional.
- `--quick`: interview mode. Gate, then spec + correctness only; each report
  keeps its top 3 findings in under 150 words. Default: all three, full
  reports.

Before anything else, confirm the range resolves and the diff is non-empty. A
bad ref fails here, not inside three reviewers.

## Step 0 — gate (sequential, a precondition, not a reviewer)

1. Run the repo's check command: `make check` if the Makefile has it,
   otherwise the one AGENTS.md / CONTRIBUTING.md / package.json documents. None
   documented → say so and continue.
2. Run crap-gate if the repo has `.crap-gate.json`: prefer its wrapper
   (`make crap-gate` / `make crap`), else `sh <crap-gate-dir>/run.sh`, where
   the dir is the first of `.my-skills/crap-gate`, `.agents/skills/crap-gate`,
   `.claude/skills/crap-gate`, `~/.my-skills/crap-gate` that exists. No
   config → "crap-gate: not configured, skipped". Config but skill missing →
   "crap-gate: skill not installed, skipped".

Either red → **stop**. Report the failing command and the first failures, and
no review: reviewing red code wastes the reviewers, and "is there a test for
this scenario" means nothing while tests fail.

## Steps 1–3 — reviewers, in parallel

Launch all of them in one go; don't let one wait for another. Give each only
the range, the commit list (`git log --oneline <range>`) and its brief. Never
paste one reviewer's output into another's prompt.

- **Spec.** Find the spec: the path argument, then a spec under `specs/`
  matching the branch or feature (skip templates), then issue refs in the
  commits (`#123`, fetched with `gh issue view`). None → skip: "Spec: no spec
  found, skipped". Otherwise spawn a subagent: "Read and follow
  `<skill-dir>/spec.md`. Range: <range>. Commits: <list>. Spec: <path or fetched text>."
- **Correctness.** If `codex` is on PATH, run it (in the background while
  the others run):

  ```bash
  out=$(mktemp) && codex exec -s read-only --ephemeral -o "$out" \
    "Read and follow <skill-dir>/correctness.md. Range: <range>. Commits: <list>.<quick>" \
    >/dev/null 2>&1; cat "$out"
  ```

  No Codex → spawn a subagent with the same instruction and note "Correctness:
  Codex unavailable, ran on <current model>". The second model is the point;
  never drop the note.
- **Structure** (skipped in `--quick`). Spawn a subagent: "Use the
  `deep-quality-review` skill on range <range>." Skill not installed → skip:
  "Structure: deep-quality-review not installed, skipped".

In `--quick`, append to each brief: "Quick mode: top 3 findings, under 150
words." No subagents in this harness → run them one after another, each in a
fresh context if you can, and say so.

## Output

```
Gate: `make check` green · crap-gate green

## Spec
<report, verbatim or lightly cleaned>

## Correctness (Codex)
<report>

## Structure
<report>

Spec: 3 findings — worst: <one line>
Correctness: 1 finding — worst: <one line>
Structure: REQUEST CHANGES, 2 findings — worst: <one line>
```

A skipped step keeps its place with its one-line note (in the gate line, or
as the section body and summary line). `--quick` has no Structure section and
no Structure summary line. A clean reviewer's summary line is `0 findings`.

Do not merge, dedupe or re-rank findings across reviewers, and do not pick an
overall winner. A finding two reviewers both raise stays in both reports; that
is signal.

## Dependencies

Needs the `deep-quality-review` and `crap-gate` skills (install them with the
skills CLI next to this one) and, for the second model, the `codex` CLI. Any
of them missing skips that step with a one-line note; it never fails the
review.

The spec axis is an idea from mattpocock/skills `code-review`; the wording
here is ours.
