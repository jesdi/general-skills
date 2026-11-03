"""The plain and coverage runs: pytest through the target's interpreter, cases aggregated into tests."""
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from weigh_coverage import contexts_regex
from weigh_env import SetupError
from weigh_inventory import TEST_GLOBS
from weigh_snapshot import git

PYTEST = ["-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider", "-p", "weigh_pytest_plugin"]
# Paths inside the repo that the coverage run leaves out: environments, and the test files.
OMIT = (".venv/**", "venv/**") + TEST_GLOBS
# Environments find_interpreter looks for: the interpreter runs from the repo, so the copy leaves them out.
ENVS = (".venv", "venv")
# pytest exit codes of a run that executed its tests: all passed, some failed.
RAN = (0, 1)


@contextmanager
def _copy(repo):
    """A throwaway copy of the repo's working tree, uncommitted and ignored files included, that a run works in: whatever
    pytest, its config or the tests write relative to where they run lands there, never in the repo."""
    # ponytail: a full copy per run; clone the files (APFS clonefile, reflink) if big trees make it slow
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp).resolve() / repo.name
        shutil.copytree(repo, work, symlinks=True, ignore=lambda d, names: [n for n in names if n in ENVS] if Path(d) == repo else [])
        yield work


def _state(repo):
    """{path: (size, mtime)} of everything under the repo but .git, so a change to an ignored or already-dirty file shows."""
    found = {}
    for folder, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in dirs + files:
            path = os.path.join(folder, name)
            st = os.lstat(path)
            found[os.path.relpath(path, repo)] = "dir" if stat.S_ISDIR(st.st_mode) else (st.st_size, st.st_mtime_ns)
    return found


def _pytest(python, repo, work, overlay, pytest_args, tmp, runner=(), env=None):
    """One pytest run in the copy; a run that still changed the repo itself (an absolute path into it) is refused."""
    out = os.path.join(tmp, "cases.json")
    env = {**os.environ, **(env or {}), "PYTHONPATH": str(overlay), "PYTHONDONTWRITEBYTECODE": "1", "TEST_WEIGH_IN_OUT": out}
    before = git(repo, "status", "--porcelain"), _state(repo)
    proc = subprocess.run([str(python), *runner, *PYTEST, *shlex.split(pytest_args)], cwd=work, env=env, capture_output=True, text=True)
    after = git(repo, "status", "--porcelain"), _state(repo)
    if after != before:
        changed = sorted(p for p in before[1].keys() | after[1].keys() if before[1].get(p) != after[1].get(p))
        raise SetupError("pytest changed the target repo by an absolute path; drop the test, plugin or option that writes there:\n"
                         + "\n".join(changed or [after[0]]))
    return proc, out


def plain_run(python, repo, overlay, pytest_args):
    with tempfile.TemporaryDirectory() as tmp, _copy(repo) as work:
        proc, out = _pytest(python, repo, work, overlay, pytest_args, tmp)
        if proc.returncode not in RAN or not os.path.exists(out):
            raise SetupError(f"pytest exited {proc.returncode} without running the tests:\n" + proc.stdout + proc.stderr)
        with open(out) as f:
            return json.load(f)


def aggregate(cases):
    """Group cases by node id without [params]; durations sum, any failure wins, all-skipped is skipped."""
    tests = {}
    for node, case in cases.items():
        tests.setdefault(node.split("[", 1)[0], []).append(case)
    result = {}
    for node, group in tests.items():
        outcomes = {c["outcome"] for c in group}
        if outcomes & {"failed", "error"}:
            outcome = "failed"
        elif outcomes == {"skipped"}:
            outcome = "skipped"
        else:
            outcome = "passed"
        result[node] = {"outcome": outcome, "duration": sum(c["duration"] for c in group), "cases": len(group)}
    return result


def coverage_run(python, repo, overlay, pytest_args, eligible):
    """Pytest under branch coverage with a context per test phase, as a dict: cases; report, the coverage json report of
    the passing cases; value_report, the same of the eligible tests that also passed under coverage; arcs, the arcs of
    each passing case's call context. A coverage report that fails is an error, so no metric comes from missing data."""
    with tempfile.TemporaryDirectory() as tmp, _copy(repo) as work:
        rc = os.path.join(tmp, "rc")
        open(rc, "w").close()
        data, raw = os.path.join(tmp, "data"), os.path.join(tmp, "raw")
        # the copy, and the repo for code imported from it (an editable install points there). Test files are the cost
        # being weighed, not the code under test; anchored in each root, so a folder named tests above it excludes nothing
        omit = ",".join(f"{root}/**/{p}" for root in (work, repo) for p in OMIT)
        run, out = _pytest(python, repo, work, overlay, pytest_args, tmp, env={"COVERAGE_RCFILE": rc},
                           runner=["-m", "coverage", "run", "--branch", f"--data-file={raw}", f"--source={work},{repo}", f"--omit={omit}"])
        if run.returncode not in RAN or not os.path.exists(out):
            raise SetupError(f"pytest under coverage exited {run.returncode} without running the tests:\n" + run.stdout + run.stderr)
        with open(out) as f:
            cases = json.load(f)["cases"]
        eligible = eligible & {n for n, t in aggregate(cases).items() if t["outcome"] == "passed"}
        env = {**os.environ, "PYTHONPATH": str(overlay), "PYTHONDONTWRITEBYTECODE": "1", "COVERAGE_RCFILE": rc}
        coverage = lambda *a: subprocess.run([str(python), *a], cwd=repo, env=env, capture_output=True, text=True)
        _combine(coverage, rc, raw, data, repo, work)
        passing = contexts_regex(cases)
        return {"cases": cases,
                "report": _report(coverage, rc, data, os.path.join(tmp, "report.json"), passing),
                "value_report": _report(coverage, rc, data, os.path.join(tmp, "eligible.json"), contexts_regex(cases, eligible)),
                "arcs": _arcs(coverage, data, os.path.join(tmp, "arcs.json"), passing)}


def _combine(coverage, rc, raw, data, repo, work):
    """The run's data with each path in the copy named as the repo's, merged with the same file's entry in the repo (listed
    unexecuted by --source, or run through an editable install): one file per source file, read where the repo holds it."""
    with open(rc, "w") as f:
        f.write(f"[paths]\nsource =\n    {repo}\n    {work}\n")
    proc = coverage("-m", "coverage", "combine", f"--data-file={data}", raw)
    if proc.returncode or not os.path.exists(data):
        raise SetupError(f"coverage combine exited {proc.returncode}:\n" + proc.stdout + proc.stderr)


def _report(coverage, rc, data, out, regex):
    # coverage splits a config list at each comma and expands $NAME in it: write both as regex escapes that
    # match the same characters (re.escape writes a literal $ as \$)
    with open(rc, "w") as f:
        f.write("[report]\ncontexts = " + regex.replace(",", r"\x2c").replace(r"\$", r"\x24") + "\n")
    proc = coverage("-m", "coverage", "json", "--show-contexts", f"--data-file={data}", "-o", out, "-q")
    if "No data to report" in proc.stdout + proc.stderr:  # no source file ran at all: measured, and empty
        return {"files": {}, "totals": {"covered_branches": 0, "num_branches": 0}}
    if proc.returncode or not os.path.exists(out):
        raise SetupError(f"coverage json exited {proc.returncode}:\n" + proc.stdout + proc.stderr)
    with open(out) as f:
        report = json.load(f)
    # combined data that holds no arc has no branch mode: no source file ran there either
    return report if "num_branches" in report["totals"] else {"files": {}, "totals": {"covered_branches": 0, "num_branches": 0}}


# Runs in the target's interpreter: coverage json has per-context lines but not per-context arcs.
# ponytail: one query per context and file; read the data file's arc table in one query if suites of thousands get slow
ARCS = """import json, re, sys
from coverage import Coverage, CoverageData
from coverage.python import PythonFileReporter
data, regex, out = sys.argv[1:]
cov = CoverageData(basename=data)
cov.read()
# raw arcs use the physical lines the tracer saw; the report maps them to each statement's first line, so
# map them the same way or a branch of a multi-line statement matches no reported branch
loaded = Coverage(data_file=data)
loaded.load()
reporters = {}
reporter = lambda path: reporters.setdefault(path, PythonFileReporter(path, loaded))
arcs = {}
for ctx in sorted(c for c in cov.measured_contexts() if re.search(regex, c)):
    cov.set_query_contexts(["^" + re.escape(ctx) + "$"])
    arcs[ctx] = {path: sorted(reporter(path).translate_arcs(a)) for path in cov.measured_files() if (a := cov.arcs(path))}
with open(out, "w") as f:
    json.dump(arcs, f)
"""


def _arcs(coverage, data, out, regex):
    """{context: {measured path: [[from, to]]}} of the contexts the regex matches."""
    proc = coverage("-c", ARCS, data, regex, out)
    if proc.returncode or not os.path.exists(out):
        raise SetupError(f"reading the coverage arcs exited {proc.returncode}:\n" + proc.stdout + proc.stderr)
    with open(out) as f:
        return json.load(f)
