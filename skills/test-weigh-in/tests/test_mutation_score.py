"""`--mutation` against fixture repos: refusals, a clean run in a temporary worktree, interruption, and the target left untouched."""
import os
import signal
import subprocess
import sys
import time

import pytest

from fixture_repo import WEIGH, build_repo, git, snapshots, uv

KEY = "github.com/jesdi/demo"
SLOW = "import time\ndef test_slow():\n    time.sleep(3)\n"


def marker_conftest(marker):
    return f"def pytest_sessionstart(session):\n    open({str(marker)!r}, 'a').write('x')\n"


def commit(repo, files):
    for rel, text in files.items():
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "more")


def env_for(home, cache):
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH")}
    env.update(HOME=str(home), TEST_WEIGH_IN_CACHE_DIR=str(cache), TEST_WEIGH_IN_UV=uv(), BROWSER="true")
    return env


def mutate(repo, home, cache):
    return subprocess.run([sys.executable, str(WEIGH), str(repo), "--mutation", "--no-open"],
                          env=env_for(home, cache), capture_output=True, text=True, timeout=900)


def worktrees(repo):
    return [l for l in git(repo, "worktree", "list", "--porcelain").splitlines() if l.startswith("worktree ")]


def start(repo, home, cache, log):
    """weigh.py --mutation in its own process group, stderr to the file log (where `finish` reads its stack dump)."""
    with open(log, "w") as err:
        return subprocess.Popen([sys.executable, str(WEIGH), str(repo), "--mutation", "--no-open"], env=env_for(home, cache),
                                stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)


def finish(proc, log, timeout):
    """proc's exit code. A run still going after timeout fails the test with weigh.py's Python stacks (faulthandler, on
    SIGUSR1) and its process group, so a hang shows where it waits."""
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.kill(proc.pid, signal.SIGUSR1)
        time.sleep(2)
        ps = subprocess.run(["ps", "-A", "-o", "pid,ppid,pgid,stat,etime,args"], capture_output=True, text=True).stdout.splitlines()
        group = [l for l in ps[1:] if l.split()[2] == str(proc.pid)]
        pytest.fail(f"weigh.py still running {timeout} s on\n--- its stderr ---\n{open(log).read()}\n--- its process group ---\n"
                    + "\n".join(ps[:1] + group))


def wait_for_mutmut(repo, proc, ignore=()):
    """Poll until a temporary worktree of the repo holds a mutants/ directory, i.e. mutmut is running."""
    deadline = time.time() + 300
    while time.time() < deadline:
        assert proc.poll() is None, "the run ended before mutmut started"
        for line in worktrees(repo)[1:]:
            path = line.removeprefix("worktree ")
            if path not in ignore and os.path.isdir(os.path.join(path, "mutants")):
                return path
        time.sleep(0.1)
    pytest.fail("mutmut never started")


def reap(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def refused(result, name):
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert name in out, out


def test_red_suite_is_refused_naming_the_failing_test(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    commit(repo, {"tests/test_claim.py": "def test_claim_flow():\n    assert False\n"})
    refused(mutate(repo, home, cache), "test_claim_flow")
    assert snapshots(home, KEY) == []


@pytest.mark.parametrize("dirty, name", [
    ({"demo/board.py": "def render(n):\n    return str(n) + ''\n"}, "board.py"),
    ({"tests/test_new.py": "def test_new():\n    assert True\n"}, "tests/test_new.py"),
])
def test_dirty_tree_is_refused_before_any_test_runs(tmp_path, home, cache, dirty, name):
    marker = tmp_path / "ran"
    repo = build_repo(tmp_path / "demo")
    commit(repo, {"tests/conftest.py": marker_conftest(marker)})
    for rel, text in dirty.items():
        (repo / rel).write_text(text)
    refused(mutate(repo, home, cache), name)
    assert not marker.exists()
    assert snapshots(home, KEY) == []


def test_clean_run_leaves_the_target_untouched(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    config = (repo / ".git" / "config").read_bytes()
    result = mutate(repo, home, cache)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / "mutants").exists()
    assert len(worktrees(repo)) == 1
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / ".git" / "config").read_bytes() == config


def test_killed_run_is_cleaned_up_by_the_next_run(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    commit(repo, {"tests/test_slow.py": SLOW})
    proc = start(repo, home, cache, tmp_path / "first.err")
    try:
        wait_for_mutmut(repo, proc)
        proc.kill()
    finally:
        reap(proc)
    stale = [l.removeprefix("worktree ") for l in worktrees(repo)[1:]]
    assert len(stale) == 1
    second = start(repo, home, cache, tmp_path / "second.err")
    try:
        fresh = wait_for_mutmut(repo, second, ignore=stale)
        # the second mutation is under way: the interrupted run's worktree is already gone, registration and directory
        assert [l.removeprefix("worktree ") for l in worktrees(repo)[1:]] == [fresh]
        assert not os.path.exists(stale[0])
        assert finish(second, tmp_path / "second.err", 900) == 0
    finally:
        reap(second)
    assert len(worktrees(repo)) == 1


def test_ctrl_c_removes_the_worktree(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    commit(repo, {"tests/test_slow.py": SLOW})
    proc = start(repo, home, cache, tmp_path / "weigh.err")
    try:
        wait_for_mutmut(repo, proc)
        os.killpg(proc.pid, signal.SIGINT)
        assert finish(proc, tmp_path / "weigh.err", 120) != 0
    finally:
        reap(proc)
    assert len(worktrees(repo)) == 1
