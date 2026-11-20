"""Acceptance tests for `babysit.py exam --repo R --pr N --wait --session S --host H`.

Spec specs/pr-exam, requirement 11 and 13; scenarios "babysit-pr waits for the exam once the PR is
otherwise ready", "a push while the exam is open replaces it", "a push after a pass adds nothing
new", "a push after a pass adds a new concept".

Interface under test:
  --wait   after the request of `exam`, poll GitHub for the head's `exam` commit status, with no
           time limit, and keep the lease of --session/--host alive (`lease renew`) while waiting.
           Real seconds between two polls: $BABYSIT_EXAM_POLL_SECONDS (default 60; tests set 0).
           All time it reads is GitHub's clock (the fake gh `now`), never the host clock.
    exit 0, printing "success"   the head's `exam` status is success, read over every page of the
                                 head's statuses. A host answer never ends the wait.
    exit non-zero, saying why    the PR is closed or merged; another session holds the lease
    head moved                   POST the host again ({"repo", "pr"}) and wait on the new head
    head's `exam` still pending  ask the host again whatever it answered last (a job may fail after
                                 an `open` answer; the contract: "the next request retries"), after
                                 2, 4, 8, then every 16 polls; an `error` answer's `detail` is printed
    never prints "CI stalled" for a pending `exam`.

Controllable clock: the `gh` first on PATH is a wrapper around fixtures/gh. Every gh call advances
GitHub's clock by STEP minutes, and a timeline in the state file changes GitHub (head, statuses,
PR state, lease comment) when the clock reaches a given minute after T. The fake host answers per
current head, read from the same state file.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from exam_host import FakeHost  # noqa: E402
from test_babysit import FAKE_GH, LEASE, MIN, T, iso, lease_comment, parse
from test_exam_request import Exam

STEP = 2  # minutes of GitHub time per gh call
PENDING = [{"context": "exam", "state": "pending"}]
SUCCESS = [{"context": "exam", "state": "success", "description": "passed"}]
GREEN_RUN = [{"name": "test", "status": "completed", "conclusion": "success", "started_at": iso(T)}]

WRAPPER = '''#!/usr/bin/env python3
import json, os, sys
from datetime import datetime, timedelta
STATE, LOG = os.environ["GH_FAKE_STATE"], os.environ["WAIT_LEASE_LOG"]
st = json.load(open(STATE))
fmt = "%Y-%m-%dT%H:%M:%SZ"
now = datetime.strptime(st["now"], fmt) + timedelta(minutes=int(os.environ["WAIT_STEP"]))
st["now"] = now.strftime(fmt)
minutes = (now - datetime.strptime(st["t0"], fmt)).total_seconds() / 60
for step in st.get("timeline", []):
    if not step.get("done") and minutes >= step["at"]:
        step["done"] = True
        st.update(step["set"])
        for c in step.get("comments", []):
            st["comments"] = [x for x in st["comments"] if x["id"] != c["id"]] + [c]
json.dump(st, open(STATE, "w"))
with open(LOG, "a") as f:
    f.write(json.dumps({"minutes": minutes, "now": st["now"], "argv": sys.argv[1:],
                        "comments": [c for c in st["comments"]]}) + "\\n")
os.execv(sys.executable, [sys.executable, os.environ["WAIT_REAL_GH"], *sys.argv[1:]])
'''


class HeadHost(FakeHost):
    """Answers per current head (read from the fake GitHub state): {head: fields of the answer}."""

    def __init__(self, state_file, answers):
        self._state_file, self._answers, self.asked = state_file, answers, []
        super().__init__()

    @property
    def answer(self):  # read once per request: `asked` gets GitHub's minute of each one
        st = json.loads(Path(self._state_file).read_text())
        self.asked.append((parse(st["now"]) - T) / MIN)
        return {**self._base, "head_sha": st["head_sha"], **self._answers.get(st["head_sha"], {})}

    @answer.setter
    def answer(self, value):
        self._base = value


def world(tmp_path, timeline=(), head="abc123", statuses=None, lease=("s1", "mac"), **kw):
    w = Exam(tmp_path, **kw)
    bindir = tmp_path / "bin"
    (bindir / "gh").write_text(WRAPPER)
    (bindir / "gh").chmod(0o755)
    comments = []
    if lease:
        comments = [lease_comment(1001, lease[0], lease[1], T, T + 90 * MIN)]
    w.seed(head_sha=head, t0=iso(T), comments=comments, next_id=1002, labels=["babysitting"],
           check_runs={h: GREEN_RUN for h in ("abc123", "def456")},
           statuses=statuses if statuses is not None else {head: PENDING},
           timeline=[{"at": at, "set": s, **({"comments": c} if c else {})}
                     for at, s, c in ((t + (None,))[:3] if len(t) == 2 else t for t in timeline)])
    w.lease_log = tmp_path / "lease.jsonl"
    w.env.update(WAIT_LEASE_LOG=str(w.lease_log), WAIT_STEP=str(STEP), WAIT_REAL_GH=str(FAKE_GH),
                 BABYSIT_EXAM_POLL_SECONDS="0")
    return w


def wait(w, host, session="s1", host_name="mac"):
    env = {**w.env, "PR_EXAM_HOST": host.url}
    try:
        p = subprocess.run([sys.executable, str(w.script), "exam", "--repo", "jesdi/demo", "--pr",
                            "58", "--wait", "--session", session, "--host", host_name],
                           cwd=w.project, env=env, text=True, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        pytest.fail("--wait never ended: " + str(e.stdout)[-500:])
    p.output = p.stdout + p.stderr
    return p


def minutes(w):
    return (parse(w.github()["now"]) - T) / MIN


def holders(snapshot):
    """(session, expires-at) of the lease comments not ended in a gh-call snapshot."""
    out = []
    for c in snapshot["comments"]:
        m = LEASE.search(c["body"])
        if m and "ended" not in c["body"].lower():
            out.append((m.group(1), parse(m.group(3))))
    return out


def snapshots(w):
    return [json.loads(l) for l in w.lease_log.read_text().splitlines()]


def test_a_pending_exam_is_awaited_past_wait_minutes_and_the_lease_stays_this_sessions(tmp_path):
    w = world(tmp_path, timeline=[(150, {"statuses": {"abc123": SUCCESS}})])
    with HeadHost(w.state, {}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert "success" in p.stdout.lower()
    assert "stalled" not in p.output.lower()
    assert minutes(w) >= 150
    for s in snapshots(w):
        if 95 <= s["minutes"] < 150:  # past the 90 minutes of the lease taken at minute 0
            now = parse(s["now"])
            assert ("s1", ) in [(h[0],) for h in holders(s) if h[1] > now], \
                f"lease no longer s1's at minute {s['minutes']}: {holders(s)}"
    assert any(s["minutes"] >= 120 for s in snapshots(w))


def test_an_exam_already_successful_on_the_head_ends_the_wait_with_success(tmp_path):
    w = world(tmp_path, statuses={"abc123": SUCCESS})
    with HeadHost(w.state, {"abc123": {"state": "passed", "pending": False}}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert "success" in p.stdout.lower()


def test_the_success_status_ends_the_wait_as_soon_as_it_appears(tmp_path):
    w = world(tmp_path, timeline=[(30, {"statuses": {"abc123": SUCCESS}})])
    with HeadHost(w.state, {}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert "success" in p.stdout.lower()
    assert 30 <= minutes(w) < 60, minutes(w)


def test_a_new_head_while_waiting_sends_a_new_request_and_waits_on_the_new_head(tmp_path):
    w = world(tmp_path, timeline=[
        (20, {"head_sha": "def456", "statuses": {"abc123": PENDING, "def456": []}}),
        (60, {"statuses": {"abc123": PENDING, "def456": SUCCESS}})])
    with HeadHost(w.state, {"abc123": {"state": "open"}, "def456": {"state": "open"}}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert len(host.requests) == 2, host.requests
    assert all(r["path"] == "/api/exam" and r["body"] == {"repo": "jesdi/demo", "pr": 58}
               for r in host.requests)
    assert minutes(w) >= 60  # it waited for the exam on def456, not the old pending one


def test_after_a_pass_a_new_head_the_host_answers_passed_for_ends_the_wait(tmp_path):
    # the host posts success on the head it answers `passed` for (route contract, state table)
    w = world(tmp_path, timeline=[(20, {"head_sha": "def456", "statuses": {"def456": SUCCESS}})])
    with HeadHost(w.state, {"abc123": {"state": "open"},
                            "def456": {"state": "passed", "pending": False}}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert "success" in p.stdout.lower()
    assert len(host.requests) == 2, host.requests
    assert minutes(w) < 60, minutes(w)


def test_after_a_pass_a_new_head_the_host_answers_open_for_keeps_waiting(tmp_path):
    w = world(tmp_path, timeline=[
        (20, {"head_sha": "def456", "statuses": {"def456": []}}),
        (60, {"statuses": {"def456": SUCCESS}})])
    with HeadHost(w.state, {"abc123": {"state": "open"},
                            "def456": {"state": "open", "detail": "follow-up: retry budget"}}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert len(host.requests) == 2, host.requests
    assert minutes(w) >= 60, minutes(w)


@pytest.mark.parametrize("pr_state", ["closed", "merged"])
def test_a_closed_or_merged_pr_ends_the_wait_non_zero_saying_so(tmp_path, pr_state):
    w = world(tmp_path, timeline=[(20, {"pr_state": pr_state})])
    with HeadHost(w.state, {}) as host:
        p = wait(w, host)
    assert p.returncode != 0, p.output
    assert pr_state in p.output.lower(), p.output
    assert minutes(w) < 60


def test_another_session_holding_the_lease_at_the_start_ends_the_wait_non_zero(tmp_path):
    w = world(tmp_path, lease=("s2", "mac2"))
    with HeadHost(w.state, {}) as host:
        p = wait(w, host)
    assert p.returncode != 0, p.output
    assert "mac2" in p.output, p.output  # names the session's host that holds the lease
    assert "success" not in p.stdout.lower()


def test_another_session_taking_the_lease_over_during_the_wait_ends_it_non_zero(tmp_path):
    stolen = lease_comment(1001, "s2", "mac2", T + 20 * MIN, T + 110 * MIN)
    w = world(tmp_path, timeline=[(20, {}, [stolen])])
    with HeadHost(w.state, {}) as host:
        p = wait(w, host)
    assert p.returncode != 0, p.output
    assert "mac2" in p.output, p.output  # names the session's host that holds the lease
    assert minutes(w) < 60, minutes(w)


def test_a_host_error_is_printed_with_its_detail_and_the_wait_goes_on(tmp_path):
    w = world(tmp_path, timeline=[(30, {"statuses": {"abc123": SUCCESS}})])
    err = {"state": "error", "detail": "generation session crashed twice"}
    with HeadHost(w.state, {"abc123": err}) as host:
        p = wait(w, host)
    assert "generation session crashed twice" in p.output, p.output
    assert p.returncode == 0, p.output
    assert minutes(w) >= 30


def test_a_passed_answer_for_an_earlier_head_does_not_end_the_wait(tmp_path):
    w = world(tmp_path, timeline=[(150, {"statuses": {"abc123": SUCCESS}})])
    stale = {"state": "passed", "head_sha": "old999", "pending": False}
    with HeadHost(w.state, {"abc123": stale}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert minutes(w) >= 150, minutes(w)  # ended by the status of abc123, not by the answer


def test_a_passed_answer_for_the_head_still_waits_for_its_status(tmp_path):
    w = world(tmp_path, timeline=[(150, {"statuses": {"abc123": SUCCESS}})])
    with HeadHost(w.state, {"abc123": {"state": "passed", "pending": False}}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert minutes(w) >= 150, minutes(w)


def test_success_is_found_behind_more_than_a_page_of_newer_statuses(tmp_path):
    newer = [{"context": f"ci-{i}", "state": "success"} for i in range(35)]
    w = world(tmp_path, statuses={"abc123": newer + SUCCESS})
    with HeadHost(w.state, {}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert "success" in p.stdout.lower()


class FailsOnce(HeadHost):
    """`error` to the first request, `open` to every later one."""

    @property
    def answer(self):
        state = {"state": "open"} if len(self.requests) > 1 else {"state": "error", "detail": "session crashed"}
        return {**HeadHost.answer.fget(self), **state}

    @answer.setter
    def answer(self, value):
        self._base = value


def test_a_pending_exam_is_asked_again_after_2_4_8_then_every_16_polls_whatever_the_answer(tmp_path):
    w = world(tmp_path, timeline=[(1600, {"statuses": {"abc123": SUCCESS}})])  # about 46 polls
    with FailsOnce(w.state, {}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert "session crashed" in p.output
    assert polls_between_requests(w, host) == [2, 4, 8, 16, 16], host.asked  # `open` is asked again too
    assert all(r["body"] == {"repo": "jesdi/demo", "pr": 58} for r in host.requests)


def polls_between_requests(w, host):
    """How many polls of the head's status each request came after the one before it."""
    polls = [s["minutes"] for s in snapshots(w)
             if any(a.endswith("/statuses") for a in s["argv"]) and s["minutes"] > host.asked[0]]
    at = [1 + sum(m < r for m in polls) for r in host.asked[1:]]
    return [b - a for a, b in zip([0] + at, at)]


class OpenThenFails(HeadHost):
    """`open` to the first request; the host's job fails afterwards, so `error` to every later one."""

    @property
    def answer(self):
        state = {"state": "open"} if len(self.requests) == 1 else {"state": "error", "detail": "grading crashed"}
        return {**HeadHost.answer.fget(self), **state}

    @answer.setter
    def answer(self, value):
        self._base = value


def test_a_job_failing_after_an_open_answer_is_asked_again_and_the_wait_ends_on_success(tmp_path):
    w = world(tmp_path, timeline=[(150, {"statuses": {"abc123": SUCCESS}})])
    with OpenThenFails(w.state, {}) as host:
        p = wait(w, host)
    assert p.returncode == 0, p.output
    assert p.stdout.strip().endswith("success"), p.output
    assert minutes(w) >= 150, minutes(w)
    assert len(host.requests) >= 2, host.requests  # the head never moved: the wait itself asked again
    assert "grading crashed" in p.output
    assert all(r["body"] == {"repo": "jesdi/demo", "pr": 58} for r in host.requests)

