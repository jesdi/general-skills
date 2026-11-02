"""Mutation score: a pinned mutmut run in a temporary detached worktree of HEAD, so the target repo is never written."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from weigh_env import SetupError, cache_root, mutmut_overlay
from weigh_inventory import is_test, python_files
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


def score(python, repo):
    """Percent of all mutants killed, from the pinned mutmut run on HEAD; the worktree is gone on every exit."""
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
        return 100 * stats["killed"] / stats["total"]
    finally:
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait()
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)
