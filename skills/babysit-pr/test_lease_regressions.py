"""Lease election, durable history, and deterministic CLI race regressions."""
import json
import subprocess
import sys
import time

import pytest

from test_babysit import MIN, SCRIPT, T, World, lease_comment


def test_renew_keeps_lowest_id_when_a_losing_claim_is_pending(tmp_path):
    w = World(tmp_path, comments=[
        lease_comment(1001, "s1", "mac", T, T + 85 * MIN),
        lease_comment(1002, "s2", "mac2", T, T + 85 * MIN)])
    p = w.run("renew", "s1")
    assert p.returncode == 0, p.output
    assert w.run("check", "s1").returncode == 0
    assert w.run("check", "s2").returncode != 0


@pytest.mark.parametrize("already_ended", [False, True])
def test_release_handoff_keeps_rounds_and_failure_signatures(tmp_path, already_ended):
    history = "rounds: 3\nsignature: test_empty AssertionError"
    w = World(tmp_path, comments=[
        lease_comment(1001, "s1", "mac", T, T + 85 * MIN,
                      rest="ended\n" + history if already_ended else history)])
    if not already_ended:
        assert w.run("release", "s1").returncode == 0
    p = w.run("acquire", "s2")
    assert p.returncode == 0, p.output
    assert history in w.comment(1002)["body"]
    assert "ended" not in w.comment(1002)["body"]


def install_pause_wrapper(w):
    """Pause selected gh calls before dispatching to the unchanged fixture."""
    gh = w.home.parent / "bin" / "gh"
    real = gh.with_name("gh-real")
    gh.rename(real)
    gh.write_text("""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time
match = json.loads(os.environ.get("GH_PAUSE_MATCH", "[]"))
if match and all(part in " ".join(sys.argv[1:]) for part in match):
    prefix = Path(os.environ["GH_PAUSE_PREFIX"])
    prefix.with_suffix(".ready").touch()
    deadline = time.monotonic() + 20
    while not prefix.with_suffix(".resume").exists():
        if time.monotonic() > deadline:
            sys.exit("pause timed out")
        time.sleep(0.01)
os.execv(str(Path(__file__).with_name("gh-real")), sys.argv)
""")
    gh.chmod(0o755)


def start(w, verb, session, prefix, match):
    return subprocess.Popen(
        [sys.executable, str(SCRIPT), "lease", verb, "--repo", "jesdi/demo",
         "--pr", "58", "--session", session, "--host", "mac2"],
        cwd=w.project, env={**w.env, "GH_PAUSE_PREFIX": str(prefix),
                            "GH_PAUSE_MATCH": json.dumps(match)},
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def wait_paused(process, prefix):
    deadline = time.monotonic() + 10
    while not prefix.with_suffix(".ready").exists():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "CLI never reached the requested pause"
        time.sleep(0.01)


def resume(process, prefix):
    prefix.with_suffix(".resume").touch()
    output = process.communicate(timeout=10)
    return process.returncode, output


def test_takeover_cleanup_cannot_displace_a_successful_renewal(tmp_path):
    w = World(tmp_path, comments=[
        lease_comment(1001, "s1", "mac", T - 89 * MIN, T + MIN)])
    install_pause_wrapper(w)
    renew_pause, cleanup_pause = tmp_path / "renew", tmp_path / "cleanup"
    renew = start(w, "renew", "s1", renew_pause, ["PATCH", "issues/comments/1001"])
    acquire = None
    try:
        wait_paused(renew, renew_pause)
        w.set_now(T + 2 * MIN)
        acquire = start(w, "acquire", "s2", cleanup_pause,
                        ["PATCH", "issues/comments/1001", "ended"])
        wait_paused(acquire, cleanup_pause)
        renewal_status, output = resume(renew, renew_pause)
        acquisition_status, acquisition_output = resume(acquire, cleanup_pause)
        assert acquisition_status == 0, acquisition_output
        assert renewal_status != 0, output
        assert w.run("check", "s2").returncode == 0
        assert w.run("check", "s1").returncode != 0
    finally:
        for process in (renew, acquire):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()


def test_renewal_completed_before_takeover_post_wins(tmp_path):
    w = World(tmp_path, comments=[
        lease_comment(1001, "s1", "mac", T - 89 * MIN, T + MIN)])
    install_pause_wrapper(w)
    renew_pause, post_pause = tmp_path / "renew", tmp_path / "post"
    renew = start(w, "renew", "s1", renew_pause, ["PATCH", "issues/comments/1001"])
    acquire = None
    try:
        wait_paused(renew, renew_pause)
        w.set_now(T + 2 * MIN)
        acquire = start(w, "acquire", "s2", post_pause, ["POST", "issues/58/comments"])
        wait_paused(acquire, post_pause)
        status, output = resume(renew, renew_pause)
        assert status == 0, output
        renewed = w.comment(1001)
        status, output = resume(acquire, post_pause)
        assert status != 0, output
        assert w.comment(1001) == renewed
        assert w.run("check", "s1").returncode == 0
    finally:
        for process in (renew, acquire):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()


def test_check_reads_github_time_after_comments_during_takeover(tmp_path):
    w = World(tmp_path, comments=[
        lease_comment(1001, "s1", "mac", T - 89 * MIN, T + MIN)])
    install_pause_wrapper(w)
    check_pause, cleanup_pause = tmp_path / "check", tmp_path / "cleanup"
    check = start(w, "check", "s1", check_pause, ["issues/58/comments"])
    acquire = None
    try:
        wait_paused(check, check_pause)
        w.set_now(T + 2 * MIN)
        acquire = start(w, "acquire", "s2", cleanup_pause,
                        ["PATCH", "issues/comments/1001", "ended"])
        wait_paused(acquire, cleanup_pause)
        status, output = resume(check, check_pause)
        assert status != 0, output
        status, output = resume(acquire, cleanup_pause)
        assert status == 0, output
        assert w.run("check", "s2").returncode == 0
    finally:
        for process in (check, acquire):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()


def test_losing_claim_cannot_replace_previous_holders_durable_history(tmp_path):
    w = World(tmp_path)
    assert w.run("acquire", "s1").returncode == 0
    # Hide s1 from s2's first read, as if s1 posted just after that read.
    state = w.github()
    state.update(posted=False, hidden_until_post=[1001])
    w.state.write_text(json.dumps(state))
    assert w.run("acquire", "s2").returncode != 0
    history = "rounds: 3\nsignature: test_empty AssertionError"
    state = w.github()
    state["comments"][0]["body"] = state["comments"][0]["body"].replace("rounds: 0", history)
    w.state.write_text(json.dumps(state))
    w.set_now(T + 91 * MIN)
    p = w.run("acquire", "s3")
    assert p.returncode == 0, p.output
    assert history in w.comment(1003)["body"]


@pytest.mark.parametrize("resume_verb", [None, "acquire", "renew"])
def test_pending_claim_history_requires_successful_ownership(tmp_path, resume_verb):
    history = "rounds: 3\nsignature: test_empty AssertionError"
    pending = "<!-- babysit-pr claim: pending -->\n"
    w = World(tmp_path, comments=[
        lease_comment(1001, "s1", "mac", T - 91 * MIN, T - MIN, rest=history),
        lease_comment(1002, "s2", "mac2", T, T + 90 * MIN,
                      rest=pending + "rounds: 4\nsignature: newer failure")])
    if resume_verb:
        p = w.run(resume_verb, "s2")
        assert p.returncode == 0, p.output
        history = "rounds: 4\nsignature: newer failure"
    w.set_now(T + 91 * MIN)
    p = w.run("acquire", "s3")
    assert p.returncode == 0, p.output
    assert history in w.comment(1003)["body"]
