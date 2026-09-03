---
name: crap-gate
description: >-
  Score every function a branch changed with CRAP (cyclomatic complexity ×
  untested fraction), fail on existing functions > 15 and new functions > 9,
  and drive a fixer + independent reviewer loop until the gate passes. Use
  when implementation is done and before pushing, when the user says "run the
  crap gate", "check CRAP", "is this change tested enough", or when a push
  gate reports CRAP violations.
---

# crap-gate — untested complexity is the thing you may not add

`CRAP(f) = CC(f)² · (1 − cov(f))³ + CC(f)`. One number per function: how much
complexity did this change add that tests do not verify. A CC-2 helper passes
untested; a CC-9 function needs 100 % coverage; a CC-40 function fails even
when fully tested (CRAP ≥ CC always). Thresholds come from the project's
`.crap-gate.json` (defaults: existing changed function > 15 fails, new
function > 9 fails). There are no pragmas and no allow-lists — the only bypass
is the human approval gate at push time.

## Running the engine

The engine ships with this skill and is stdlib-only Python (plus `node` and
the project's own `typescript` package for TS/TSX targets). Run it from
anywhere inside the repo — it walks up to `.crap-gate.json`:

```bash
python3 <skill-dir>/crap.py            # human table, exit 1 on violations
python3 <skill-dir>/crap.py --json     # same data for a subagent prompt
python3 <skill-dir>/crap.py --no-run   # reuse the last coverage reports
python3 <skill-dir>/crap.py --all      # whole-repo baseline (exploration only)
python3 <skill-dir>/crap.py --base main --threshold-new 7   # local what-ifs
```

`<skill-dir>` is wherever this skill is installed (`.agents/skills/crap-gate`,
`~/.my-skills/crap-gate`, …). Projects usually
wrap it (`make crap`); prefer the wrapper when one exists.

When you point a wrapper at an agent symlink, that symlink only exists after
`skills-cli sync` if the matching agent is declared for the skill in
`.my-skills.json` — `claude` → `.claude/skills/crap-gate`, `opencode` →
`.agents/skills/crap-gate`. A wrapper that runs `.agents/skills/crap-gate/crap.py`
therefore needs `"agents": ["opencode"]` (or `["claude", "opencode"]`) on the
`crap-gate` entry, or a fresh checkout/worktree will lack that path after `sync`.
The store copy `.my-skills/crap-gate/crap.py` is always materialized regardless
of declared agents, so wrapping against it avoids the agent/path coupling
entirely.

By default the engine runs each target's coverage command only when that
target has changed files, so a backend-only change never runs the frontend
suite. Coverage command output goes to `.crap/<target>.log`.

Reading a line:

```
FAIL  backend/routers/stock.py:120  get_pe_history  CC 22  cov 0%  CRAP 506.0  limit 15 (existing)  → split to CC ≤ 3
```

The hint is the cheapest way out: `cover ≥ N%` when tests alone can fix it,
`split to CC ≤ k` when the function is too complex to pass at its current
coverage, both when both work. `Not measured:` lists changed code files that
no target covers (never a failure, never silent).

## Project setup (once per repository)

Create `.crap-gate.json` at the repo root:

```json
{
  "thresholds": { "existing": 15, "new": 9 },
  "base": "origin/main",
  "targets": [
    {
      "name": "backend",
      "paths": ["backend/**/*.py"],
      "exclude": ["backend/tests/**"],
      "complexity": { "tool": "python" },
      "coverage": {
        "format": "coverage.py",
        "root": "backend",
        "report": "backend/.crap/coverage.json",
        "command": "cd backend && pytest --cov=src --cov-report=json:.crap/coverage.json -q"
      }
    },
    {
      "name": "frontend",
      "paths": ["frontend/src/**/*.ts", "frontend/src/**/*.tsx"],
      "exclude": ["frontend/src/**/*.test.*"],
      "complexity": { "tool": "typescript", "cwd": "frontend" },
      "coverage": {
        "format": "istanbul",
        "report": "frontend/coverage/coverage-final.json",
        "command": "cd frontend && vitest run --coverage --coverage.reportOnFailure=true"
      }
    }
  ]
}
```

- `thresholds` / `base` are optional (defaults shown). Flags override them.
- `complexity.tool`: `python` (stdlib `ast`) or `typescript` (compiler API,
  `cwd` = directory whose `node_modules/typescript` to use).
- `coverage.format`: `coverage.py` (`root` = directory its file paths are
  relative to, i.e. where pytest ran; run with `branch = true`) or `istanbul`
  (absolute paths, e.g. vitest/jest `json` reporter).
- `coverage.command` runs via `sh -c` from the repo root. It must (re)write
  `coverage.report`. If it exits non-zero but still wrote a report (failing
  tests), the engine warns and continues; no report → exit 2.

## What counts as a function

- Named functions: `def`s and function declarations, class methods
  (`Class.method`), nested named functions (`outer.inner`), and
  functions/arrows that directly initialise a variable or `export default`.
  At module level, `export const X = memo(forwardRef(() => …))` is `X`.
- Anonymous callbacks (`.map(() => …)`, `useMemo(() => …)`, handlers,
  lambdas) **fold into the nearest named function**: a React component's
  render callbacks are its complexity. The remedy is to extract a named
  subcomponent, hook or helper — which is then scored on its own.
- CC counts `if`/`elif`, loops, `except`/`catch`, ternaries, `case`, each
  `&&`/`||`/`??` (and their assignment forms), comprehension `for`/`if`.
  Optional chaining is not counted.
- A function is **new** when its qualified name did not exist in the base
  version of the file (renames count as new). Everything else that intersects
  a diff hunk is **existing**. Untouched functions are not reported.

## The loop — when the gate fails

You are the orchestrator. Do not fix violations yourself in the same context
that wrote the code; a fresh pair of eyes per role is the point.

1. Run the engine with `--json` and keep the output.
2. **Fixer.** Dispatch a subagent with: the JSON findings, the spec of the
   change being made (the user's intent), and these rules:
   - Tests first. Add or extend tests that prove the function's observable
     behaviour through its public interface. Tests that merely assert the
     implementation's text, tokens or incidental snapshots are not evidence.
   - Refactor only when the hint says `split to CC ≤ k` and coverage alone
     cannot reach the limit (CC > limit), or when the function is genuinely
     doing two things. Extract named functions; keep behaviour identical.
   - Touch nothing outside the listed functions and their tests. No drive-by
     cleanups, no unrelated refactors.
   - Re-run the engine before finishing and report the before/after lines.
3. **Reviewer.** Dispatch a *different* subagent with the fixer's diff and the
   original findings. It answers three questions with evidence: Do the new
   tests exercise behaviour rather than restate the code? Is every refactor
   behaviour-preserving (same inputs → same outputs, same side effects)? Did
   the change stay inside the listed functions? Verdict: approve, or request
   changes with specifics.
4. **Adjudicate.** If changes are requested, send the review to the fixer
   (same subagent if it still has context, otherwise a new one with the
   review attached) and go back to step 3. If approved, re-run the engine.
5. Stop after **3 rounds** with violations remaining. Report to the user: the
   residual findings, what the fixer tried, what the reviewer objected to, and
   your recommendation (usually: the function needs a design change, not more
   tests). Do not loosen thresholds and do not add suppressions.

Exit 0 from the engine ends the loop. Only then push.

## Push gate

Projects that gate pushes with `no-mistakes` run the same engine as the lint
command with auto-fix disabled, so a violation that reaches the push parks for
a human decision instead of being patched by an unreviewed fix agent:

```yaml
# .no-mistakes.yaml (must be on the default branch)
allow_repo_commands: true
commands:
  lint: "make crap-gate"
auto_fix:
  lint: 0
```

If a push is parked on a CRAP finding, run the loop above on the branch, then
`no-mistakes rerun`.
