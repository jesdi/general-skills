"""CLI regressions for status retrieval, pending identities and lease writers."""
import subprocess
import sys
import time

from test_rounds_and_checks import Ci, MIN, T, ci_world, run_ci, status_world
from test_babysit import SCRIPT


def wrap_gh(w, code):
    gh = w.home.parent / "bin" / "gh"
    real = gh.with_name("gh-real")
    gh.rename(real)
    gh.write_text("#!/usr/bin/env python3\nimport os, subprocess, sys, time\n"
                  + f"real = {str(real)!r}\n" + code)
    gh.chmod(0o755)


def test_status_api_error_cannot_report_green(tmp_path):
    w = status_world(tmp_path, "success")
    wrap_gh(w, "if any(a.endswith('/statuses') for a in sys.argv):\n"
               "    sys.stderr.write('API unavailable'); sys.exit(1)\n"
               "os.execv(real, [real, *sys.argv[1:]])\n")
    p = w.checks()
    assert p.returncode != 0, p.output
    assert "API unavailable" in p.output
    assert "green" not in p.stdout


def test_pending_status_reaches_stall_deadline(tmp_path):
    w = status_world(tmp_path, "pending")
    assert w.checks().stdout.strip() == "pending"
    w.set_now(T + 60 * MIN)
    assert w.checks().stdout.strip() == "CI stalled"


def test_new_run_with_same_name_gets_its_own_deadline(tmp_path):
    w = ci_world(tmp_path, [dict(run_ci("e2e", "queued"), id=1)])
    assert w.checks().stdout.strip() == "pending"
    w.seed(check_runs={"head1": [dict(run_ci("e2e", "completed", "success"), id=1)]})
    assert w.checks().stdout.strip() == "green"
    w.set_now(T + 120 * MIN)
    w.seed(check_runs={"head1": [dict(run_ci("e2e", "queued"), id=2)]})
    assert w.checks().stdout.strip() == "pending"
    w.set_now(T + 150 * MIN)
    w.seed(check_runs={"head1": [dict(run_ci("e2e", "in_progress", now=T + 150 * MIN), id=2)]})
    assert w.checks().stdout.strip() == "pending"
    w.set_now(T + 180 * MIN)
    assert w.checks().stdout.strip() == "CI stalled"


def test_start_requires_stable_operation_id_before_writing(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    before = w.github()
    p = w.round("start", "s1")
    assert p.returncode != 0, p.output
    assert "--op" in p.output
    assert w.github() == before


def test_reacquire_cannot_overwrite_concurrent_round(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    ready, release = tmp_path / "ready", tmp_path / "release"
    wrap_gh(w, f"ready, release = {str(ready)!r}, {str(release)!r}\n"
               "if any(a.endswith('/comments') for a in sys.argv) and '-X' not in sys.argv and not os.path.exists(ready):\n"
               "    p = subprocess.run([real, *sys.argv[1:]], capture_output=True)\n"
               "    open(ready, 'w').close()\n"
               "    deadline = time.monotonic() + 20\n"
               "    while not os.path.exists(release):\n"
               "        if time.monotonic() > deadline: sys.exit(2)\n"
               "        time.sleep(.01)\n"
               "    sys.stdout.buffer.write(p.stdout); sys.exit(p.returncode)\n"
               "os.execv(real, [real, *sys.argv[1:]])\n")
    base = [sys.executable, str(SCRIPT)]
    opts = ["--repo", "jesdi/demo", "--pr", "58", "--session", "s1", "--host", "mac2"]
    acquire = subprocess.Popen(base + ["lease", "acquire", *opts], cwd=w.project, env=w.env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    start = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(.01)
        start = subprocess.Popen(base + ["round", "start", *opts, "--op", "a"], cwd=w.project,
                                 env=w.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            start.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # serialized behind acquire
        release.touch()
        assert acquire.communicate(timeout=10)[1] == ""
        assert acquire.returncode == 0
        assert start.communicate(timeout=10)[1] == ""
        assert start.returncode == 0
        assert "op: 1 s1 a" in w.comment(1001)["body"]
        retry = w.round("start", "s1", "--op", "a")
        assert retry.stdout.strip() == "round 1"
        assert "op: 1 s1 a" in w.comment(1001)["body"]
    finally:
        release.touch()
        for p in (acquire, start):
            if p is not None and p.poll() is None:
                p.kill()
                p.communicate()


def test_pending_status_publications_share_a_continuous_deadline(tmp_path):
    from test_babysit import iso
    w = status_world(tmp_path, "pending")
    for minute in (0, 30, 60, 90):
        w.set_now(T + minute * MIN)
        w.seed(statuses={"head1": [{"context": "ci/lint", "state": "pending",
                                    "id": minute + 1, "created_at": iso(T + minute * MIN)}]})
        expected = "pending" if minute < 60 else "CI stalled"
        assert w.checks().stdout.strip() == expected


def test_terminal_status_resets_the_pending_context_deadline(tmp_path):
    w = status_world(tmp_path, "pending")
    assert w.checks().stdout.strip() == "pending"
    w.set_now(T + 30 * MIN)
    w.seed(statuses={"head1": [{"context": "ci/lint", "state": "success"}]})
    assert w.checks().stdout.strip() == "green"
    w.set_now(T + 60 * MIN)
    w.seed(statuses={"head1": [{"context": "ci/lint", "state": "pending"}]})
    assert w.checks().stdout.strip() == "pending"
    w.set_now(T + 120 * MIN)
    assert w.checks().stdout.strip() == "CI stalled"


def test_failure_retry_uses_its_original_round_operation(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    assert w.round("start", "s1", "--op", "a").returncode == 0
    args = ("--check", "test", "--error", "test_empty AssertionError", "--op", "a")
    assert w.round("record-failure", "s1", *args).returncode == 0
    assert w.round("start", "s1", "--op", "b").returncode == 0
    retry = w.round("record-failure", "s1", *args)
    assert retry.returncode == 0, retry.output
    assert w.round("start", "s1", "--op", "c").returncode == 0
    repeated = w.round("record-failure", "s1", *args[:-1], "c")
    assert repeated.returncode != 0
    assert "same failure twice" in repeated.output


def test_failure_write_crossing_expiry_cannot_report_success(tmp_path):
    from test_lease_regressions import install_pause_wrapper, start, wait_paused, resume
    import json
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    assert w.start("s1").returncode == 0
    install_pause_wrapper(w)
    record_pause, post_pause = tmp_path / "record", tmp_path / "post"
    record = subprocess.Popen(
        [sys.executable, str(SCRIPT), "round", "record-failure", "--repo", "jesdi/demo",
         "--pr", "58", "--session", "s1", "--host", "mac", "--check", "test",
         "--error", "test_empty AssertionError", "--op", "start-1"], cwd=w.project,
        env={**w.env, "GH_PAUSE_PREFIX": str(record_pause),
             "GH_PAUSE_MATCH": json.dumps(["PATCH", "issues/comments/1001", "signature:"])},
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    acquire = None
    try:
        wait_paused(record, record_pause)
        w.set_now(T + 91 * MIN)
        acquire = start(w, "acquire", "s2", post_pause, ["POST", "issues/58/comments"])
        wait_paused(acquire, post_pause)
        status, output = resume(record, record_pause)
        assert status != 0, output  # expired writers must retry under the new holder
        status, output = resume(acquire, post_pause)
        assert status == 0, output
        assert "signature: r1 test: test_empty AssertionError" in w.comment(1002)["body"]
    finally:
        for process in (record, acquire):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()


def test_failure_operation_must_belong_to_a_started_round(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    assert w.start("s1").returncode == 0
    before = w.github()
    p = w.round("record-failure", "s1", "--check", "test", "--error", "AssertionError",
                "--op", "not-started")
    assert p.returncode != 0
    assert "originating round" in p.output
    assert w.github() == before


def test_late_first_failure_still_detects_two_distinct_rounds(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    assert w.round("start", "s1", "--op", "a").returncode == 0
    assert w.round("start", "s1", "--op", "b").returncode == 0
    args = ("--check", "test", "--error", "test_empty AssertionError", "--op")
    assert w.round("record-failure", "s1", *args, "b").returncode == 0
    p = w.round("record-failure", "s1", *args, "a")
    assert p.returncode != 0
    assert "same failure twice" in p.output
