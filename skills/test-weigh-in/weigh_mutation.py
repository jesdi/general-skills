"""Mutation score: a pinned mutmut run in a temporary detached worktree of HEAD, so the target repo is never written."""
import ast
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from weigh_env import SetupError, cache_root, mutmut_overlay
from weigh_inventory import is_test, python_files
from weigh_runs import aggregate
from weigh_snapshot import git

ROOT = "worktrees"


def refuse_dirty(repo):
    """A tree with uncommitted changes (untracked files included) is refused, naming them: the score is of the commit."""
    dirty = git(repo, "status", "--porcelain", "-uall")
    if dirty:
        raise SetupError("--mutation needs a clean tree, the score is of the committed code; uncommitted changes:\n" + dirty)


def refuse_red(cases):
    failing = sorted({n.split("[", 1)[0] for n, c in cases.items() if c["outcome"] in ("failed", "error")})
    if failing:
        raise SetupError("--mutation needs a green suite; failing tests:\n" + "\n".join(failing))


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def remove_stale():
    """Remove the worktrees an earlier run left under the cache (named <pid>-<random>, pid no longer running).

    Only these paths are removed, through the repository that owns each: `git worktree prune` would also drop the user's own."""
    for wt in (cache_root() / ROOT).glob("*-*"):
        if _alive(int(wt.name.split("-")[0])):
            continue  # ponytail: a pid reused by another process keeps its stale worktree until that process exits
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=wt, capture_output=True, text=True).stdout.strip()
        if common:
            subprocess.run(["git", f"--git-dir={common}", "worktree", "remove", "--force", str(wt)], capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)


def _configure(wt, files):
    """mutmut's config in the worktree's pyproject.toml, unless the project already has its own."""
    pyproject = wt / "pyproject.toml"
    text = pyproject.read_text() if pyproject.exists() else ""
    if "[tool.mutmut]" in text:
        return
    source = [f for f in files if not is_test(f)]
    tests = sorted({os.path.dirname(f) or "." for f in files if is_test(f)})
    pyproject.write_text(text + "\n[tool.mutmut]\npaths_to_mutate = %s\ntests_dir = %s\n" % (json.dumps(source), json.dumps([t + "/" for t in tests])))


def _function(tree, mangled):
    """The def node of a mutmut name such as x_parse or xǁClassǁmethod, found in a parsed module."""
    node = tree
    for part in mangled.split("ǁ")[1:] if "ǁ" in mangled else [mangled[2:]]:
        node = next((n for n in ast.walk(node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part), None)
        if node is None:
            return None
    return node


def _mutant_line(wt, mutant):
    """(source file, source line) of the first line a mutant changes, found by comparing mutmut's mutated copy with its original."""
    module, name = mutant.rsplit(".", 1)
    mangled = name.split("__mutmut_")[0]
    base = module.replace(".", "/")
    rel = base + ".py" if (wt / (base + ".py")).exists() else base + "/__init__.py"
    text = (wt / "mutants" / rel).read_text()
    lines = text.splitlines()
    defs = {n.name: lines[n.lineno - 1:n.end_lineno] for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    orig, mutated = defs[mangled + "__mutmut_orig"], defs[name]
    offset = next((i for i, (a, b) in enumerate(zip(orig, mutated)) if i and a != b), 0)  # line 0 is the def, whose name differs
    original = _function(ast.parse((wt / rel).read_text()), mangled)
    return rel, (original.lineno if original else 1) + offset


def _sample(seed, by_function, per_function):
    """Up to per_function mutants of each function on different lines (all of them for None), in the order of a hash of the commit, function and mutant."""
    sampled = []
    for func, mutants in by_function.items():
        ranked = sorted(mutants, key=lambda m: (hashlib.sha256(f"{seed}:{func}:{m[0]}".encode()).hexdigest(), m[0]))
        lines = set()
        for name, line in ranked:
            if per_function is None or (len(lines) < per_function and line not in lines):
                lines.add(line)
                sampled.append(name)
    return sorted(sampled)


def _failing(python, wt, env, mutant, tests, timeout):
    """The tests (params folded) that fail with the mutant active, every one of them run (pytest without -x); None when the run hangs."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "cases.json")
        run_env = {**env, "MUTANT_UNDER_TEST": mutant, "TEST_WEIGH_IN_OUT": out, "TEST_WEIGH_IN_ARTIFACTS": tmp}
        try:
            subprocess.run([str(python), "-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider", "--maxfail=1000000", "-p", "weigh_pytest_plugin", *tests],
                           cwd=wt / "mutants", env=run_env, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        if not os.path.exists(out):
            raise SetupError(f"pytest did not run for the mutant {mutant}")
        with open(out) as f:
            tested = aggregate(json.load(f)["cases"])
        return {n for n, t in tested.items() if t["outcome"] == "failed"}


def _kills(python, wt, env, mutant, tests, timeout):
    """The tests that kill the mutant. A hung run says nothing of the tests it never reached: each test then runs alone,
    and kills it when it fails or hangs."""
    killed = _failing(python, wt, env, mutant, tests, timeout)
    if killed is None:
        killed = {t for t in tests if _failing(python, wt, env, mutant, [t], timeout) != set()}
    return sorted(killed)


def _matrix(python, repo, wt, env, per_function):
    """Pseudo-tested share and the sampled kill matrix, from mutmut's results in the worktree. Every generated mutant is a
    sampling candidate; one in a function no test covers has an empty row."""
    data = json.loads((wt / "mutants" / "mutmut-stats.json").read_text())
    covering = {f: sorted({t.split("[", 1)[0] for t in tests}) for f, tests in data["tests_by_mangled_function_name"].items() if tests}
    exit_codes = {}
    for meta in (wt / "mutants").rglob("*.meta"):
        exit_codes.update(json.loads(meta.read_text())["exit_code_by_key"])
    by_function, where, survived = {}, {}, {}
    for mutant, code in sorted(exit_codes.items()):
        func = mutant.rsplit("__mutmut_", 1)[0]
        where[mutant] = _mutant_line(wt, mutant)
        by_function.setdefault(func, []).append((mutant, where[mutant][1]))
        if func in covering:
            survived[func] = survived.get(func, True) and code == 0
    sampled = _sample(git(repo, "rev-parse", "HEAD"), by_function, per_function)
    timeout = 60 + 10 * sum(data.get("duration_by_test", {}).values())  # ponytail: a hung mutant waits this long
    tests_of = lambda m: covering.get(m.rsplit("__mutmut_", 1)[0], [])
    return {
        "pseudo_tested": 100 * sum(survived.values()) / len(survived) if survived else 0.0,
        "sampled": sampled,
        "mutants": {m: {"file": where[m][0], "line": where[m][1]} for m in sampled},
        "matrix": {m: _kills(python, wt, env, m, tests_of(m), timeout) if tests_of(m) else [] for m in sampled},
    }


def score(python, repo, overlay, per_function=3):
    """The pinned mutmut run on HEAD and its kill matrix, as a dict: score (percent of all mutants killed), pseudo_tested
    (percent of the covered functions whose mutants all survived), sampled (mutant ids: per_function per function on
    different lines, every one for None), mutants {id: {file, line}} and matrix {id: ids of the killing tests}.
    The worktree is gone on every exit."""
    root = cache_root() / ROOT
    root.mkdir(parents=True, exist_ok=True)
    wt = Path(tempfile.mkdtemp(prefix=f"{os.getpid()}-", dir=root))
    wt.rmdir()
    proc = None
    try:
        added = subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt), "HEAD"], cwd=repo, capture_output=True, text=True)
        if added.returncode:
            raise SetupError("cannot create the temporary worktree:\n" + added.stderr)
        _configure(wt, python_files(wt))
        env = {**os.environ, "PYTHONPATH": str(mutmut_overlay(python)), "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.Popen([str(python), "-m", "mutmut", "run"], cwd=wt, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        out = proc.communicate()[0]
        if proc.returncode:
            raise SetupError(f"mutmut run exited {proc.returncode}:\n" + out)
        exported = subprocess.run([str(python), "-m", "mutmut", "export-cicd-stats"], cwd=wt, env=env, capture_output=True, text=True)
        stats = wt / "mutants" / "mutmut-cicd-stats.json"
        if exported.returncode or not stats.exists():
            raise SetupError("mutmut export-cicd-stats failed:\n" + exported.stdout + exported.stderr)
        stats = json.loads(stats.read_text())
        if not stats["total"]:
            raise SetupError("mutmut found no mutants: no source code to mutate")
        env["PYTHONPATH"] = f"{overlay}{os.pathsep}{env['PYTHONPATH']}"
        return {"score": 100 * stats["killed"] / stats["total"], **_matrix(python, repo, wt, env, per_function)}
    finally:
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait()
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)
