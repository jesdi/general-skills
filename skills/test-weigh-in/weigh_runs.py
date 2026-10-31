"""The plain and coverage runs: pytest through the target's interpreter, cases aggregated into tests."""
import json
import os
import shlex
import subprocess
import tempfile

from weigh_coverage import contexts_regex
from weigh_env import SetupError
from weigh_inventory import TEST_GLOBS
from weigh_snapshot import git

PYTEST = ["-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider", "-p", "weigh_pytest_plugin"]
# Paths inside the repo that the coverage run leaves out: environments, and the test files.
OMIT = (".venv/**", "venv/**") + TEST_GLOBS
# pytest exit codes of a run that executed its tests: all passed, some failed.
RAN = (0, 1)


def _pytest(python, repo, overlay, pytest_args, tmp, runner=(), env=None):
    """One pytest run; the plugin sends pytest's file writers to tmp, and a run that still changed the repo is refused."""
    args = shlex.split(pytest_args)
    # --debug opens its log before any plugin is configured, so it cannot be redirected.
    if any(a.split("=", 1)[0] == "--debug" for a in args + shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))):
        raise SetupError("--pytest-args: --debug writes files and would change the target repo")
    out = os.path.join(tmp, "cases.json")
    env = {**os.environ, **(env or {}), "PYTHONPATH": str(overlay), "PYTHONDONTWRITEBYTECODE": "1",
           "TEST_WEIGH_IN_OUT": out, "TEST_WEIGH_IN_ARTIFACTS": tmp}
    before = git(repo, "status", "--porcelain")
    proc = subprocess.run([str(python), *runner, *PYTEST, *args], cwd=repo, env=env, capture_output=True, text=True)
    if git(repo, "status", "--porcelain") != before:
        raise SetupError("pytest changed the target repo; drop the --pytest-args or configuration that writes files:\n"
                         + git(repo, "status", "--porcelain"))
    return proc, out


def plain_run(python, repo, overlay, pytest_args):
    with tempfile.TemporaryDirectory() as tmp:
        proc, out = _pytest(python, repo, overlay, pytest_args, tmp)
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
    with tempfile.TemporaryDirectory() as tmp:
        rc = os.path.join(tmp, "rc")
        open(rc, "w").close()
        data = os.path.join(tmp, "data")
        # test files are the cost being weighed, not the code under test; anchored in the repo, so a folder
        # named tests above it excludes nothing
        omit = ",".join(f"{repo}/**/{p}" for p in OMIT)
        run, out = _pytest(python, repo, overlay, pytest_args, tmp, env={"COVERAGE_RCFILE": rc},
                           runner=["-m", "coverage", "run", "--branch", f"--data-file={data}", f"--source={repo}", f"--omit={omit}"])
        if run.returncode not in RAN or not os.path.exists(out):
            raise SetupError(f"pytest under coverage exited {run.returncode} without running the tests:\n" + run.stdout + run.stderr)
        with open(out) as f:
            cases = json.load(f)["cases"]
        eligible = eligible & {n for n, t in aggregate(cases).items() if t["outcome"] == "passed"}
        env = {**os.environ, "PYTHONPATH": str(overlay), "PYTHONDONTWRITEBYTECODE": "1", "COVERAGE_RCFILE": rc}
        coverage = lambda *a: subprocess.run([str(python), *a], cwd=repo, env=env, capture_output=True, text=True)
        passing = contexts_regex(cases)
        return {"cases": cases,
                "report": _report(coverage, rc, data, os.path.join(tmp, "report.json"), passing),
                "value_report": _report(coverage, rc, data, os.path.join(tmp, "eligible.json"), contexts_regex(cases, eligible)),
                "arcs": _arcs(coverage, data, os.path.join(tmp, "arcs.json"), passing)}


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
        return json.load(f)


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
