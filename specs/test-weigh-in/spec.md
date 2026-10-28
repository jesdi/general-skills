# test-weigh-in — Spec

Terms:

- The **launcher** is `shared/launcher.sh`, the one canonical runtime bootstrap. A **launcher
  copy** is `skills/<name>/launcher.sh`, byte-identical to it. A **wrapper** is
  `skills/<name>/run.sh`, which sets the skill's settings and runs its launcher copy.
- A skill's **prefix** is the wrapper's variable prefix: `CRAP_GATE`, `IMPLEMENT_SPEC`,
  `TEST_WEIGH_IN`.
- The **target repo** is the repository being measured. Its **interpreter** is the Python of its
  own environment.
- The **overlay** is a tool-owned directory holding the measurement packages, put on the
  interpreter's import path with `PYTHONPATH`.
- A **test** is a test function: a pytest node id without its `[parameters]` suffix. A **case**
  is one parametrized instance of a test. A test without parameters has one case.
- The **plain run** and the **coverage run** are the two pytest runs of the default tier.
- A **pillar** is one of the five scored principles: Protection against regressions,
  Resistance to refactoring, Fast feedback, Maintainability, Economy.
- The **rules file** is `rules.toml` inside the skill: every metric's pillar, weight, partial
  weight, target and bad anchor, and a `version`.
- A **snapshot** is one run's JSON file. The **home** is `~/.test-weigh-in/<repo-key>/`. The
  **repo key** is `<host>/<owner>/<repo>` from the target repo's `origin` remote, or
  `local/<directory name>` when it has no `origin`.
- A gauge or pillar is **partial** when some of its metrics could not be measured in this run.

Unless a scenario says otherwise: the target repo is `/work/demo`, a git repository on branch
`main` at commit `abc1234` with a clean tree, 300 commits of history, and the `origin` remote
`git@github.com:jesdi/demo.git`; it has `uv.lock` and
`.venv/` with Python 3.12 and pytest 8.3; `tests/` holds 40 tests in 8 files, all passing;
`/test-weigh-in` runs without flags; the skill is installed locally in `/work/demo`.

## Prerequisites

This change is in `skills/implement-spec`, in a separate PR to `main` that must be released
before the first session runs. This spec writes no ticket for it.

- P1. The implement-spec driver takes `--base <branch>`: its end-of-run rebase uses the fetched
  tip of `origin/<branch>` instead of the default branch, and `--push` opens the PR against
  `<branch>`. Without the flag, behaviour is unchanged. Every session of this spec runs with
  `--base feat/test-weigh-in`.

## Requirements

### Shared launcher

1. `shared/launcher.sh` is the only launcher source. `pnpm sync-launchers` writes it to every
   skill that has a wrapper using it. `pnpm check` fails, naming the file, when a launcher copy
   differs from `shared/launcher.sh` or a wrapper runs a launcher copy that does not exist.
2. A wrapper passes four settings: the skill name, its prefix, its setup-failure exit code, and
   its engine file. The launcher reads `<PREFIX>_CACHE_DIR` and `<PREFIX>_OFFLINE` and caches
   under `${XDG_CACHE_HOME:-$HOME/.cache}/<name>` by default.
3. The launcher keeps today's behaviour: uv 0.12.13 verified by SHA-256 for macOS and Linux on
   x86_64 and ARM64, managed Python 3.13.7, atomic publication of downloads, offline mode,
   symlinked skill directories, no PATH change and no venv activation, engine started with
   `-E -s`.
4. crap-gate runs on the launcher with no visible change: exit code 2 on setup failure, the
   `--json` setup error object, `CRAP_GATE_CACHE_DIR`, `CRAP_GATE_OFFLINE`, and its cache path.
5. A wrapper may pass a list of pinned packages. The launcher then builds one cached venv per
   pin set, published atomically, and runs the engine with that venv's Python.
6. implement-spec runs on the launcher with no visible change: exit code 3 on setup failure,
   `IMPLEMENT_SPEC_CACHE_DIR`, `IMPLEMENT_SPEC_OFFLINE`, and its pinned `jsonschema` venv with
   the same versions at the same path.

### Running against a target repo

7. `/test-weigh-in [repo-path] [--mutation] [--matrix-per-function N|all] [--python PATH]
   [--pytest-args "..."] [--no-open]` measures the repo at `repo-path` (default: the current
   repo). It exits 0 when a snapshot was written, and non-zero with a message naming the cause
   otherwise.
8. The interpreter is found in this order: `--python`; for a repo with `uv.lock`,
   `.venv/bin/python` after `uv sync --frozen` when `.venv/` is missing; `.venv/bin/python`;
   `venv/bin/python`. With none found, the run stops and names `--python`.
9. The run never modifies the target repo: `git status --porcelain`, the content of every
   tracked file, and the interpreter's `site-packages` are identical before and after.
   Measurement packages live in the overlay under the skill's cache, installed with `--no-deps`
   so pytest always comes from the target repo.
10. The run stops before measuring, naming the found version and the minimum, when the target
    repo's pytest is older than 7.0 or its Python is older than 3.10.
11. The plain run executes the selected tests once with `-p no:randomly` plus `--pytest-args`,
    and records each case's outcome (passed, failed, error, skipped, xfail) and duration. The
    coverage run executes the same selection with branch coverage and per-test contexts.
12. A test aggregates its cases: durations are summed, coverage is the union of its passing
    cases, and its outcome is failed when any case failed or errored, skipped when all cases were
    skipped, and passed otherwise. The details show each test's case count.
13. A test whose outcome differs between the plain run and the coverage run is recorded as
    flaky.
14. A suite that is not green is still measured. Failing, erroring and skipped tests count in
    cost metrics (lines, runtime, smells) and in no value metric (coverage, overlap, uniqueness,
    kill matrix). Every pillar that uses a value metric is partial, and the dashboard shows a
    banner naming the number of failing tests.

### Metrics

15. The snapshot records an inventory: number of tests, number of cases, number of test files,
    test lines of code, source lines of code, total runtime of the plain run.
16. Each pillar's metrics, weights, and anchors come from the rules file. Version 1 holds the
    starter set below. Weights in brackets apply when the mutation tier did not run; a metric
    marked "—" then does not count.

    | Pillar | Metric | Weight (partial) | Target | Bad |
    |---|---|---|---|---|
    | Regression | Mutation score | .45 (—) | ≥ 80% | ≤ 40% |
    | Regression | Branch coverage | .25 (.60) | ≥ 90% | ≤ 50% |
    | Regression | Hollow tests (% of tests) | .20 (.40) | 0% | ≥ 10% |
    | Regression | Pseudo-tested functions (% of covered functions) | .10 (—) | 0% | ≥ 15% |
    | Refactoring | Brittle test files (% of mapped test files) | .40 | ≤ 15% | ≥ 60% |
    | Refactoring | Tests with private access | .25 | ≤ 2% | ≥ 30% |
    | Refactoring | Mock-heavy tests | .20 | ≤ 5% | ≥ 30% |
    | Refactoring | Interaction-only tests | .15 | ≤ 5% | ≥ 30% |
    | Feedback | Total runtime (log scale) | .40 | ≤ 60 s | ≥ 600 s |
    | Feedback | p95 test duration (log scale) | .30 | ≤ 0.1 s | ≥ 2 s |
    | Feedback | Runtime share of slowest 10% of tests | .15 | ≤ 40% | ≥ 90% |
    | Feedback | Tests over 10 s | .15 | 0% | ≥ 2% |
    | Maintainability | Tests with at least one maintainability smell | .50 | ≤ 5% | ≥ 40% |
    | Maintainability | Tests longer than 50 lines | .20 | ≤ 2% | ≥ 20% |
    | Maintainability | Test : source lines-of-code ratio | .30 | ≤ 1.5 | ≥ 4.0 |
    | Economy | Tests with no unique coverage | .35 (.60) | ≤ 10% | ≥ 50% |
    | Economy | Tests subsumed in the kill matrix | .40 (—) | ≤ 5% | ≥ 35% |
    | Economy | Tests in a duplicate-body cluster | .25 (.40) | ≤ 2% | ≥ 20% |

    Without the mutation tier, a hollow test is a test with no assertion. With it, a hollow test
    is also a test that killed none of the sampled mutants on its lines, given at least 2 sampled
    mutants on them.
17. Every count-based metric is a percentage of all tests (or of the stated base), so repos of
    different size are comparable.
18. Smells are found from each test function's syntax tree, one test at a time:

    | Smell | Rule | Pillar |
    |---|---|---|
    | No assertion | no `assert`, no `pytest.raises`/`pytest.warns`, no mock `assert_*` call, no call to a helper named `assert_*`, `check_*` or `expect_*` | Regression |
    | Interaction-only | every assertion is on a mock (`assert_called*`, `call_args`, `call_count`) | Refactoring |
    | Mock-heavy | more than 3 of: `@patch` decorators, `patch(...)` context managers, `Mock()`/`MagicMock()`, `monkeypatch.setattr` | Refactoring |
    | Private access | imports or accesses a `_name` (not `__dunder__`) of a non-test module | Refactoring |
    | Eager test | 3 or more act-then-assert cycles | Maintainability |
    | Conditional test logic | `if`, `for`, or `while` in the test body | Maintainability |
    | Mystery guest | reads a file, an environment variable, or a home path not obtained from `tmp_path` or `monkeypatch` | Maintainability |
    | Swallowed exception | a `try`/`except` whose handler neither re-raises nor asserts | Maintainability |
    | Real sleep | `time.sleep` or `asyncio.sleep` with a non-zero argument | Feedback |
    | Duplicate body | the same normalized syntax-tree hash (names and literals replaced by placeholders) as another test | Economy |

19. A pinned ruff runs the `PT` rules listed in the rules file over the test files, outside the
    target repo's environment. Version 1 lists the rules that weaken a test: PT009, PT010,
    PT011, PT012, PT014, PT015, PT017, PT018, PT027, PT029, PT030, PT031. Each violation inside a
    test function counts as a maintainability smell of that test. Style-only `PT` rules (such as
    PT001 and PT023) are not run.
20. Brittleness maps each test file to every source file that receives at least 20% of the test
    file's covered lines. Over the last 12 months or the last 200 commits that change source
    files, whichever is fewer, and ignoring commits that change more than 20 files, a test file
    is brittle when it changed in more than half of the commits that changed its mapped source
    files. A repo with no git history leaves this metric unmeasured and the pillar partial.
21. A test has no unique coverage when every line and branch it covers is also covered by another
    passing test. The Overlaps tab lists test pairs with a Jaccard similarity of their coverage of
    at least 0.9.

### Mutation tier

22. `--mutation` refuses to run, naming the failing tests, when the plain run has a failing or
    erroring test, and refuses before running any test, naming the changed files, when the
    target repo's tree is dirty (`git status --porcelain` is non-empty, untracked files
    included).
23. `--mutation` runs a pinned mutmut in a temporary git worktree of the target repo's commit,
    which is removed afterwards, and takes the mutation score over all mutants from it.
24. The kill matrix samples up to 3 mutants per mutated function, on different lines, chosen
    from a fixed seed: the same commit and settings always pick the same mutants.
    `--matrix-per-function N` changes the number and `all` takes every mutant. For each sampled
    mutant, every test that covers its function runs with that mutant active and without
    stopping at the first failure, recording which tests kill it.
25. A test is subsumed when at least 2 sampled mutants are on lines it executes and every
    mutant it kills is also killed by another test. A test with fewer than 2 sampled mutants on
    its lines is "insufficient evidence": it is listed in the details and counts in no Economy
    metric.
26. A contract test in the skill's own suite fails when the pinned mutmut no longer exposes the
    mutant-selection variable or the covering-tests data the kill matrix relies on.

### Scoring

27. A metric's score is `100 / (1 + e^(−k·(x − mid)))` with `mid = (target + bad) / 2` and
    `k = 2·ln 9 / (target − bad)`, on the logarithm of `x` for log-scale metrics: the target
    scores 90 and the bad anchor 10. A pillar's score is the weighted mean of its measured
    metrics, with the weights re-normalized over those measured. Displayed scores are rounded
    and clamped to 1–99.
28. A pillar that is missing a metric it would use with the mutation tier, or that is affected by
    a red suite or by missing git history, is marked partial.

### Snapshot

29. Each run writes one new snapshot to the home, named `<UTC timestamp>_<short sha>.json`, and
    never overwrites one. It holds the commit, whether the tree was dirty, the tool version,
    the rules version, the Python and pytest versions, the platform, `--pytest-args`, the
    mutation settings, the inventory, every metric's raw value and score, every pillar score
    with its partial flag, and the per-test data the dashboard shows.
30. Two runs of the same commit, flags, and rules version produce identical snapshots except
    the timestamp and the duration-based metrics and scores.

### Dashboard and skill output

31. Each run writes `dashboard.html` to the home: one self-contained file that opens with no
    network and no server. It shows the inventory line, a row of five gauges, and below them
    the details of the selected gauge in the tabs Metrics, Worst tests, Smells and, for
    Economy, Overlaps and Map. A partial gauge says so on the gauge.
32. The Map tab draws each passing test as a dot placed by coverage similarity, coloured by
    unique coverage (none, 1–3 lines, 4 or more lines) and sized by runtime. Clicking a dot
    highlights and lists the tests with a similarity of at least 0.6 to it.
33. The Worst tests tab lists the 10 lowest-ranked tests for the selected pillar with the reason
    for each.
34. After the run the skill opens the dashboard, unless `--no-open` is given, and prints the five
    scores with their partial flags, the inventory line, any red-suite or flaky warning, the 3
    worst tests of the lowest-scoring pillar, and the paths of the snapshot and the dashboard.
    It prints no interpretation and suggests no change.

### Robustness

35. The snapshot records the rules file's version and the SHA-256 of its bytes. A snapshot
    name that already exists gets `-2`, `-3`, … appended.
36. Runs that start at the same time share one overlay safely: a run never uses an overlay that
    another run is still building.
37. An interrupted `--mutation` run leaves no worktree in the target repo's `git worktree list`
    once the next run starts, and no run changes the target repo's `.git/config`.
38. A rules file whose weights for a pillar do not sum to 1, or whose metric has equal target
    and bad anchors, stops the run with exit 2, naming the pillar or metric.

## Scenarios

### Shared launcher

#### Scenario: a drifted launcher copy fails the check

- **Given** `skills/crap-gate/launcher.sh` has one byte more than `shared/launcher.sh`
- **When** the maintainer runs `pnpm check`
- **Then** it fails and names `skills/crap-gate/launcher.sh`

#### Scenario: sync repairs every copy

- **Given** `shared/launcher.sh` changed and the copies in `crap-gate` and `test-weigh-in` are
  stale
- **When** the maintainer runs `pnpm sync-launchers` and then `pnpm check`
- **Then** both copies equal `shared/launcher.sh` and the launcher check passes

#### Scenario: a wrapper pointing at a missing copy fails the check

- **Given** `skills/test-weigh-in/run.sh` runs `launcher.sh` and the skill has no `launcher.sh`
- **When** the maintainer runs `pnpm check`
- **Then** it fails and names `skills/test-weigh-in`

#### Scenario: crap-gate keeps its setup failure contract

- **Given** `CRAP_GATE_OFFLINE=1` and an empty `CRAP_GATE_CACHE_DIR=/tmp/cg`
- **When** the user runs `sh skills/crap-gate/run.sh --json`
- **Then** it exits 2, stderr says uv is not cached, and stdout is the JSON object with
  `error.kind` `tooling`

#### Scenario: crap-gate keeps its cache path

- **Given** `XDG_CACHE_HOME=/tmp/x` and no `CRAP_GATE_CACHE_DIR`
- **When** crap-gate runs online for the first time
- **Then** uv 0.12.13 and Python 3.13.7 are cached under `/tmp/x/crap-gate`, and a second run
  downloads nothing

#### Scenario: a tampered uv download is refused

- **Given** the downloaded uv archive's SHA-256 does not match the pinned digest
- **When** any wrapper runs
- **Then** it exits with its setup-failure code, says "uv archive checksum mismatch", and caches
  no uv binary

#### Scenario: a relative cache dir is refused

- **Given** `TEST_WEIGH_IN_CACHE_DIR=cache`
- **When** `/test-weigh-in` runs
- **Then** it exits with test-weigh-in's setup-failure code and says the cache dir must be an
  absolute path

#### Scenario: implement-spec keeps its pinned venv

- **Given** `IMPLEMENT_SPEC_CACHE_DIR=/tmp/is`, empty
- **When** implement-spec runs online
- **Then** a venv with `jsonschema==4.26.0`, `attrs==26.1.0`, `referencing==0.37.0`,
  `rpds-py==2026.9.1` and `jsonschema-specifications==2025.9.1` exists under `/tmp/is/venv/`
  and runs `driver.py`

#### Scenario: implement-spec keeps its exit code offline

- **Given** `IMPLEMENT_SPEC_OFFLINE=1` and the cache has uv and Python but no venv
- **When** implement-spec runs
- **Then** it exits 3 and says jsonschema is not cached

#### Scenario: two first runs race for the venv

- **Given** an empty cache
- **When** two implement-spec runs start at the same moment
- **Then** both run `driver.py` with one complete venv, and no partial venv is left in the cache

### Running against a target repo

#### Scenario: a uv repo is measured without changes

- **Given** the default repo
- **When** the user runs `/test-weigh-in`
- **Then** it exits 0, one snapshot exists in `~/.test-weigh-in/github.com/jesdi/demo/`, `git status --porcelain`
  is empty, and `.venv/lib/python3.12/site-packages` lists the same files as before

#### Scenario: a uv repo without a venv gets one from its lock file

- **Given** the default repo without `.venv/`
- **When** the user runs `/test-weigh-in`
- **Then** `.venv/` is created by `uv sync --frozen`, the run exits 0, and `uv.lock` is unchanged

#### Scenario: a plain venv repo is measured

- **Given** a repo with `requirements.txt`, no `uv.lock`, and `venv/` with pytest 8.1
- **When** the user runs `/test-weigh-in`
- **Then** the tests run with `venv/bin/python` and the snapshot records pytest 8.1

#### Scenario: no interpreter found

- **Given** a repo with no `uv.lock`, no `.venv/` and no `venv/`
- **When** the user runs `/test-weigh-in`
- **Then** it exits non-zero, writes no snapshot, and the message names `--python`

#### Scenario: explicit interpreter wins

- **Given** the default repo
- **When** the user runs `/test-weigh-in --python /opt/py312/bin/python`
- **Then** the tests run with `/opt/py312/bin/python`

#### Scenario: pytest at the minimum is accepted

- **Given** the target repo has pytest 7.0.0
- **When** the user runs `/test-weigh-in`
- **Then** it exits 0

#### Scenario: pytest below the minimum is refused

- **Given** the target repo has pytest 6.2.5
- **When** the user runs `/test-weigh-in`
- **Then** it exits non-zero before running any test, and the message names 6.2.5 and 7.0

#### Scenario: Python below the minimum is refused

- **Given** the target repo's interpreter is Python 3.9
- **When** the user runs `/test-weigh-in`
- **Then** it exits non-zero before running any test, and the message names 3.9 and 3.10

#### Scenario: the repo's pytest is used, not the overlay's

- **Given** the target repo has pytest 7.4 and the overlay's plugin declares a newer pytest
- **When** the user runs `/test-weigh-in`
- **Then** both runs report pytest 7.4

#### Scenario: test selection is passed through and recorded

- **Given** the repo has 10 tests marked `e2e`
- **When** the user runs `/test-weigh-in --pytest-args "-m 'not e2e'"`
- **Then** the inventory counts 30 tests and the snapshot records `-m 'not e2e'`

#### Scenario: a parametrized test is one test

- **Given** `test_parse` has 14 cases taking 0.1 s each, one of which fails
- **When** the user runs `/test-weigh-in`
- **Then** the inventory counts `test_parse` as 1 test and 14 cases, its duration is 1.4 s, its
  outcome is failed, and its coverage is the union of its 13 passing cases

#### Scenario: a test that differs between runs is flaky

- **Given** `test_retry` passes in the plain run and fails in the coverage run
- **When** the user runs `/test-weigh-in`
- **Then** the snapshot marks `test_retry` flaky and the chat summary warns about 1 flaky test

#### Scenario: a red suite is measured with partial gauges

- **Given** 3 of the 40 tests fail
- **When** the user runs `/test-weigh-in`
- **Then** it exits 0; the 3 tests count in test lines, runtime and smells but not in coverage,
  overlap or uniqueness; Regression and Economy are partial; and the dashboard banner says 3
  tests are failing

#### Scenario: skipped tests cost without value

- **Given** 4 tests are skipped
- **When** the user runs `/test-weigh-in`
- **Then** they count in test lines and smells, add no coverage, and are listed in the details as
  skipped

### Metrics

#### Scenario: the inventory is recorded

- **Given** the default repo with 6,000 test lines and 4,000 source lines, and a plain run of 45 s
- **When** the user runs `/test-weigh-in`
- **Then** the snapshot's inventory holds 40 tests, 8 test files, 6,000 test lines, 4,000 source
  lines and 45 s, and the test-to-source ratio metric is 1.5

#### Scenario: a test without assertion is hollow

- **Given** `test_board_renders` calls `render()` and asserts nothing
- **When** the user runs `/test-weigh-in`
- **Then** it has the smell "No assertion" and counts as a hollow test

#### Scenario: a custom assertion helper counts as an assertion

- **Given** `test_board` only calls `assert_board_equal(a, b)` from `conftest.py`
- **When** the user runs `/test-weigh-in`
- **Then** it has no "No assertion" smell

#### Scenario: pytest.raises counts as an assertion

- **Given** `test_invalid` only runs `with pytest.raises(ValueError): parse("x")`
- **When** the user runs `/test-weigh-in`
- **Then** it has no "No assertion" smell

#### Scenario: mock-heavy boundary

- **Given** `test_a` uses 3 `@patch` decorators and `test_b` uses 3 `@patch` decorators and one
  `MagicMock()`
- **When** the user runs `/test-weigh-in`
- **Then** `test_b` is mock-heavy and `test_a` is not

#### Scenario: interaction-only test

- **Given** `test_notify` asserts only `sender.send.assert_called_once_with("hi")`
- **When** the user runs `/test-weigh-in`
- **Then** it has the smell "Interaction-only"

#### Scenario: dunder access is not private access

- **Given** `test_a` reads `obj.__dict__` and `test_b` imports `_requeue_locked` from
  `dispatcher.queue_ops`
- **When** the user runs `/test-weigh-in`
- **Then** only `test_b` has the smell "Private access"

#### Scenario: eager test boundary

- **Given** `test_flow_2` has 2 act-then-assert cycles and `test_flow_3` has 3
- **When** the user runs `/test-weigh-in`
- **Then** only `test_flow_3` has the smell "Eager test"

#### Scenario: a loop over literals is conditional logic

- **Given** `test_parse_all` runs `for s in ["a", "b"]: assert parse(s)`
- **When** the user runs `/test-weigh-in`
- **Then** it has the smell "Conditional test logic"

#### Scenario: tmp_path files are not a mystery guest

- **Given** `test_a` opens `tmp_path / "x.json"` and `test_b` opens `"~/.cache/app.json"`
- **When** the user runs `/test-weigh-in`
- **Then** only `test_b` has the smell "Mystery guest"

#### Scenario: a swallowed exception

- **Given** `test_load` wraps its call in `try: ... except Exception: pass`
- **When** the user runs `/test-weigh-in`
- **Then** it has the smell "Swallowed exception"

#### Scenario: a zero sleep is not a real sleep

- **Given** `test_a` calls `time.sleep(0)` and `test_b` calls `time.sleep(2)`
- **When** the user runs `/test-weigh-in`
- **Then** only `test_b` has the smell "Real sleep"

#### Scenario: copy-pasted tests form a duplicate cluster

- **Given** `test_intent_1` to `test_intent_4` differ only in a string literal and a variable
  name
- **When** the user runs `/test-weigh-in`
- **Then** they form one duplicate-body cluster of 4 tests and the Economy metric "Tests in a
  duplicate-body cluster" is 10%

#### Scenario: a ruff PT violation is a maintainability smell

- **Given** `test_raises` uses `pytest.raises(Exception)` without `match` (ruff PT011)
- **When** the user runs `/test-weigh-in`
- **Then** `test_raises` lists PT011 among its smells and counts in "Tests with at least one
  maintainability smell"

#### Scenario: a style-only PT rule is not a smell

- **Given** `test_board` uses the fixture decorator `@pytest.fixture()` with parentheses (ruff
  PT001) and has no other smell
- **When** the user runs `/test-weigh-in`
- **Then** `test_board` has no smell and does not count in "Tests with at least one
  maintainability smell"

#### Scenario: brittle test file

- **Given** `tests/test_claims.py` covers 80% of its lines in `claims.py`, and in the window 10
  commits changed `claims.py` of which 9 also changed `tests/test_claims.py`
- **When** the user runs `/test-weigh-in`
- **Then** `tests/test_claims.py` is brittle

#### Scenario: brittleness boundary at half

- **Given** 10 commits changed the mapped source file and 5 of them also changed the test file
- **When** the user runs `/test-weigh-in`
- **Then** the test file is not brittle

#### Scenario: a shared helper is not mapped

- **Given** `tests/test_board.py` covers 300 lines, 40 of them in `config.py`
- **When** the user runs `/test-weigh-in`
- **Then** `config.py` is not a mapped source file of `tests/test_board.py`

#### Scenario: sweeping commits are ignored

- **Given** a reformatting commit changed 140 files including `claims.py` and
  `tests/test_claims.py`
- **When** the user runs `/test-weigh-in`
- **Then** that commit is not counted for brittleness

#### Scenario: the history window is the smaller one

- **Given** 500 commits changed source files in the last 12 months
- **When** the user runs `/test-weigh-in`
- **Then** only the last 200 of them are counted

#### Scenario: no git history leaves refactoring partial

- **Given** the target directory is not a git repository
- **When** the user runs `/test-weigh-in`
- **Then** the brittleness metric is unmeasured, Resistance to refactoring is partial, and the
  snapshot records no commit

#### Scenario: a test with no unique coverage

- **Given** `test_board_empty` covers lines 10–20 of `board.py` and `test_board_none` covers
  lines 10–25
- **When** the user runs `/test-weigh-in`
- **Then** `test_board_empty` has no unique coverage and the pair appears in Overlaps only if
  their Jaccard similarity is at least 0.9

### Mutation tier

#### Scenario: mutation refused on a red suite

- **Given** `test_claim_flow` fails
- **When** the user runs `/test-weigh-in --mutation`
- **Then** it exits non-zero before mutating anything, names `test_claim_flow`, and writes no
  snapshot

#### Scenario: mutation refused on a dirty tree

- **Given** `board.py` has an uncommitted change
- **When** the user runs `/test-weigh-in --mutation`
- **Then** it exits non-zero before running any test, names `board.py`, and writes no snapshot

#### Scenario: an untracked test file also blocks mutation

- **Given** the only change is an untracked `tests/test_new.py`
- **When** the user runs `/test-weigh-in --mutation`
- **Then** it exits non-zero before running any test, names `tests/test_new.py`, and writes no
  snapshot

#### Scenario: mutation leaves the repo untouched

- **Given** the default repo
- **When** the user runs `/test-weigh-in --mutation`
- **Then** no `mutants/` directory exists in `/work/demo`, `git worktree list` shows only the
  main checkout, and `git status --porcelain` is empty

#### Scenario: the sample is reproducible

- **Given** the default repo and `--mutation`
- **When** the user runs the skill twice
- **Then** both snapshots list the same sampled mutant ids and the same kill matrix

#### Scenario: at most 3 mutants per function, on different lines

- **Given** `parse()` has 12 mutants on 6 lines and `noop()` has 1 mutant
- **When** the user runs `/test-weigh-in --mutation`
- **Then** 3 of `parse()`'s mutants are sampled, each on a different line, and `noop()`'s one
  mutant is sampled

#### Scenario: the full matrix on request

- **Given** `parse()` has 12 mutants
- **When** the user runs `/test-weigh-in --mutation --matrix-per-function all`
- **Then** all 12 are in the kill matrix

#### Scenario: the matrix does not stop at the first kill

- **Given** a sampled mutant of `parse()` is killed by `test_parse_ok` and `test_parse_edge`
- **When** the user runs `/test-weigh-in --mutation`
- **Then** the kill matrix records both tests for that mutant

#### Scenario: a subsumed test

- **Given** 3 sampled mutants are on lines `test_a` executes, `test_a` kills 2 of them, and
  `test_b` kills the same 2
- **When** the user runs `/test-weigh-in --mutation`
- **Then** `test_a` is subsumed

#### Scenario: one sampled mutant is insufficient evidence

- **Given** only 1 sampled mutant is on lines `test_parse_invalid` executes, and another test
  also kills it
- **When** the user runs `/test-weigh-in --mutation`
- **Then** `test_parse_invalid` is "insufficient evidence", is not subsumed, and counts in no
  Economy metric

#### Scenario: a test that kills nothing is hollow

- **Given** 4 sampled mutants are on lines `test_session_roundtrip` executes and it kills none
- **When** the user runs `/test-weigh-in --mutation`
- **Then** `test_session_roundtrip` counts as a hollow test

#### Scenario: a mutmut upgrade that breaks the coupling fails CI

- **Given** the mutmut pin is bumped to a version that renamed the mutant-selection variable
- **When** the maintainer runs `pnpm check`
- **Then** the contract test fails and names the missing variable

### Scoring

#### Scenario: the anchors score 90 and 10

- **Given** branch coverage of 90% and, in another run, 50%
- **When** the scores are computed
- **Then** the metric scores 90 and 10

#### Scenario: improvement beyond the bad anchor still moves the score

- **Given** total runtime falls from 1,200 s to 900 s
- **When** both runs are scored
- **Then** the Fast feedback score of the second run is higher than the first

#### Scenario: displayed scores never reach 0 or 100

- **Given** a repo with 0% hollow tests, 100% branch coverage, and 0% duplicate clusters
- **When** the dashboard is rendered
- **Then** no gauge shows 100, and a pillar computed below 0.5 shows 1

#### Scenario: a run without mutation is partial

- **Given** the default repo
- **When** the user runs `/test-weigh-in`
- **Then** Regression and Economy are partial, Regression is weighted .60 coverage and .40 hollow
  tests, and the dashboard marks both gauges partial

### Snapshot

#### Scenario: snapshots accumulate

- **Given** the home already holds one snapshot
- **When** the user runs `/test-weigh-in` again
- **Then** the home holds two snapshots and the first is unchanged

#### Scenario: two clones with the same folder name keep separate histories

- **Given** `/work/api` with `origin` `git@github.com:jesdi/api.git` and `/other/api` with
  `origin` `https://github.com/acme/api.git`
- **When** the user runs `/test-weigh-in` in each
- **Then** the snapshots land in `~/.test-weigh-in/github.com/jesdi/api/` and
  `~/.test-weigh-in/github.com/acme/api/`

#### Scenario: owner and repo names with hyphens never collide

- **Given** `/work/x` with `origin` `git@github.com:a-b/c.git` and `/work/y` with `origin`
  `git@github.com:a/b-c.git`
- **When** the user runs `/test-weigh-in` in each
- **Then** the snapshots land in `~/.test-weigh-in/github.com/a-b/c/` and
  `~/.test-weigh-in/github.com/a/b-c/`

#### Scenario: the same owner and repo on two hosts never collide

- **Given** `/work/x` with `origin` `git@github.com:jesdi/api.git` and `/work/y` with `origin`
  `git@gitlab.com:jesdi/api.git`
- **When** the user runs `/test-weigh-in` in each
- **Then** the snapshots land in `~/.test-weigh-in/github.com/jesdi/api/` and
  `~/.test-weigh-in/gitlab.com/jesdi/api/`

#### Scenario: a repo without origin uses its folder name

- **Given** `/work/scratch`, a git repository with no remotes
- **When** the user runs `/test-weigh-in`
- **Then** the snapshot lands in `~/.test-weigh-in/local/scratch/`

#### Scenario: a dirty tree is recorded

- **Given** `board.py` has an uncommitted change
- **When** the user runs `/test-weigh-in`
- **Then** the snapshot records commit `abc1234` and `dirty: true`

#### Scenario: a repeat run is identical except durations

- **Given** the default repo
- **When** the user runs `/test-weigh-in` twice
- **Then** the two snapshots are equal after removing the timestamp and the duration-based
  metrics and scores

### Dashboard and skill output

#### Scenario: the dashboard opens offline

- **Given** a written `dashboard.html` and no network
- **When** the user opens it in a browser
- **Then** the inventory line, five gauges and the details render, and the page makes no network
  request

#### Scenario: Economy shows the Map tab

- **Given** the dashboard
- **When** the user selects the Economy gauge
- **Then** its details show the tabs Metrics, Worst tests, Smells, Overlaps and Map, and other
  pillars show no Map tab

#### Scenario: clicking a dot lists its near-duplicates

- **Given** `test_intent_1` has similarity 1.0 with 3 tests and 0.4 with every other test
- **When** the user clicks its dot on the Map
- **Then** those 3 tests are highlighted and listed with 1.00, and no other test is listed

#### Scenario: worst tests are capped at 10

- **Given** 25 tests have the smell "Mock-heavy"
- **When** the user selects Resistance to refactoring and opens Worst tests
- **Then** 10 tests are listed, each with its reason

#### Scenario: the chat summary

- **Given** a run where Economy scores lowest and 1 test is flaky
- **When** the run ends
- **Then** the chat shows the five scores with partial flags, the inventory line, the flaky
  warning, the 3 worst Economy tests, and the snapshot and dashboard paths, and no advice

#### Scenario: no-open

- **Given** the default repo
- **When** the user runs `/test-weigh-in --no-open`
- **Then** no browser opens and the chat still prints the dashboard path

### Robustness

#### Scenario: an edited rules file is told apart

- **Given** two runs of the same commit, with `rules.toml` version 1 in both but the branch
  coverage bad anchor changed from 50% to 60% before the second
- **When** both snapshots are read
- **Then** both say rules version 1 and their `rules.sha256` values differ

#### Scenario: two runs in the same second keep both snapshots

- **Given** two runs of commit `abc1234` that finish within the same second
- **When** both write their snapshot
- **Then** the home holds `<timestamp>_abc1234.json` and `<timestamp>_abc1234-2.json`

#### Scenario: two first runs race for the overlay

- **Given** no overlay exists for Python 3.12
- **When** runs on `/work/a` and `/work/b`, both Python 3.12, start at the same moment
- **Then** both exit 0 with per-test coverage recorded, and one complete overlay exists for
  Python 3.12

#### Scenario: an interrupted mutation run is cleaned up

- **Given** a `--mutation` run on the default repo killed with SIGKILL while mutmut runs
- **When** the user runs `/test-weigh-in --mutation` again
- **Then** before mutating, `git worktree list` in `/work/demo` shows only the main checkout,
  and the second run exits 0

#### Scenario: Ctrl-C removes the worktree at once

- **Given** a `--mutation` run on the default repo
- **When** the user presses Ctrl-C while mutmut runs
- **Then** the run exits non-zero and `git worktree list` in `/work/demo` shows only the main
  checkout

#### Scenario: the target's git config is never changed

- **Given** the default repo
- **When** the user runs `/test-weigh-in --mutation`
- **Then** `/work/demo/.git/config` has the same bytes before and after the run

#### Scenario: an invalid rules file stops the run

- **Given** `rules.toml` gives Fast feedback the weights .40, .30, .15 and .10
- **When** the user runs `/test-weigh-in`
- **Then** it exits 2, writes no snapshot, and the message names Fast feedback

#### Scenario: equal anchors stop the run

- **Given** `rules.toml` gives branch coverage target 80% and bad 80%
- **When** the user runs `/test-weigh-in`
- **Then** it exits 2 and the message names branch coverage

## Out of scope

Improving tests, interpretation in the chat, non-pytest runners, JavaScript, end-to-end suites,
a portfolio page, the test→module graph, Test Desiderata scoring, embeddings, a per-repo config
file, a before/after view, assertion roulette, poetry/pdm/conda environments without a venv, and
dedicated flakiness reruns. See proposal.md.
