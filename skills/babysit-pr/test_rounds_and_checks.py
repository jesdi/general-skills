"""Acceptance tests for `babysit.py checks` and `babysit.py round start|record-failure`.

Interface under test (same lease options as test_babysit.py):
  babysit.py checks --repo R --pr N --wait-minutes M
      prints the CI state of the PR head commit on stdout: "green" (every check passed, required
      or not), "red" (a check failed), "pending", "no checks" (the head commit has none) or
      "CI stalled" (a check pending for at least M minutes of GitHub time).
  babysit.py round start --repo R --pr N --session S --host H
      starts the next fix round of the lease holder and prints "round <n>"; refuses (non-zero)
      with "5 fix rounds" after round 5 and with "same failure twice" after a repeated failure.
  babysit.py round record-failure --repo R --pr N --session S --host H --check C --error E
      records the failure of check C in the current round; non-zero with "same failure twice"
      when the same check failed with the same signature in an earlier round. Signatures ignore
      numbers, hex ids and /tmp paths.
The count and the signatures live in the lease comment, so a new session continues them.
A handoff is an owner comment "## 🧑 Needs human: <reason>"; the fake `gh` is fixtures/gh.
"""
import subprocess
import sys
from datetime import timedelta

import pytest

from test_babysit import SCRIPT, T, MIN, World, iso, lease_comment


class Ci(World):
    def run_cmd(self, *args):
        p = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.project, env=self.env,
                           text=True, capture_output=True)
        p.output = p.stdout + p.stderr
        return p

    def seed(self, **extra):
        st = self.github()
        st.update(extra)
        self.state.write_text(__import__("json").dumps(st))

    def checks(self, wait=60):
        return self.run_cmd("checks", "--repo", "jesdi/demo", "--pr", "58", "--wait-minutes", str(wait))

    def round(self, verb, session, *extra):
        return self.run_cmd("round", verb, "--repo", "jesdi/demo", "--pr", "58",
                            "--session", session, "--host", "mac2", *extra)

    def start(self, session):
        # every call is a new intended round, so it carries its own operation identity
        self.ops = getattr(self, "ops", 0) + 1
        return self.round("start", session, "--op", f"start-{self.ops}")

    def fail(self, session, error, check="test"):
        # a failure belongs to the round of the latest start, so it carries that start's operation
        return self.round("record-failure", session, "--check", check, "--error", error,
                          "--op", f"start-{self.ops}")

    def acquire(self, session):
        return self.run("acquire", session)

    def handoff(self, cid, reason):
        st = self.github()
        st["comments"].append({"id": cid, "user": {"login": "jesdi"}, "created_at": iso(T),
                               "updated_at": iso(T), "body": f"## 🧑 Needs human: {reason}\n"})
        st["next_id"] = max(st["next_id"], cid + 1)
        self.state.write_text(__import__("json").dumps(st))


def run_ci(name, status, conclusion=None, age=0, now=T):
    """A check run as the REST API returns it: no created_at; started_at is null while queued."""
    started = None if status == "queued" else iso(now - age * MIN)
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": started}


def ci_world(tmp_path, runs, old=()):
    w = Ci(tmp_path)
    w.seed(head_sha="head1", required_checks=["test"],
           check_runs={"head1": list(runs), "old0": list(old)})
    return w


def rounds_of(out):
    return out.strip().split()[-1]


# ---- checks ----

def test_a_failed_check_that_is_not_required_makes_ci_red_not_green(tmp_path):
    w = ci_world(tmp_path, [run_ci("test", "completed", "success"),
                            run_ci("lint", "completed", "failure")])
    p = w.checks()
    assert "red" in p.output.lower()
    assert "green" not in p.output.lower()
    assert w.unsupported() == []


def test_every_check_passed_is_green(tmp_path):
    w = ci_world(tmp_path, [run_ci("test", "completed", "success"),
                            run_ci("lint", "completed", "success")])
    p = w.checks()
    assert p.returncode == 0, p.output
    assert "green" in p.output.lower()
    assert w.writes() == []
    assert w.unsupported() == []


def test_a_head_commit_with_no_checks_reports_no_checks(tmp_path):
    w = ci_world(tmp_path, [], old=[run_ci("test", "completed", "success")])
    assert "no checks" in w.checks().output.lower()


def test_a_check_queued_for_wait_minutes_reports_ci_stalled(tmp_path):
    w = ci_world(tmp_path, [run_ci("test", "completed", "success"), run_ci("e2e", "queued")])
    assert "pending" in w.checks(wait=60).output.lower()  # first sight of the queued check
    w.set_now(T + 60 * MIN)
    out = w.checks(wait=60).output
    assert "CI stalled" in out


def test_a_check_queued_for_less_than_wait_minutes_is_not_stalled(tmp_path):
    w = ci_world(tmp_path, [run_ci("test", "completed", "success"), run_ci("e2e", "queued")])
    assert w.checks(wait=60).returncode == 0
    w.set_now(T + 10 * MIN)
    p = w.checks(wait=60)
    assert p.returncode == 0, p.output
    assert "pending" in p.output.lower()
    assert "CI stalled" not in p.output


def test_a_check_in_progress_since_wait_minutes_reports_ci_stalled(tmp_path):
    w = ci_world(tmp_path, [run_ci("test", "completed", "success"),
                            run_ci("e2e", "in_progress", None, age=60)])
    assert "CI stalled" in w.checks(wait=60).output


def test_a_check_in_progress_for_less_than_wait_minutes_is_not_stalled(tmp_path):
    w = ci_world(tmp_path, [run_ci("test", "completed", "success"),
                            run_ci("e2e", "in_progress", None, age=10)])
    p = w.checks(wait=60)
    assert p.returncode == 0, p.output
    assert "pending" in p.output.lower()
    assert "CI stalled" not in p.output



def status_world(tmp_path, state):
    """CI world with one commit status on the head commit, seeded as st["statuses"]."""
    w = ci_world(tmp_path, [run_ci("test", "completed", "success")])
    w.seed(statuses={"head1": [{"context": "ci/lint", "state": state}]})
    return w


def test_a_failing_commit_status_makes_ci_red_not_green(tmp_path):
    for state in ("failure", "error"):
        (tmp_path / state).mkdir()
        w = status_world(tmp_path / state, state)
        p = w.checks()
        assert "red" in p.output.lower(), p.output
        assert "green" not in p.output.lower()
        assert w.unsupported() == []


def test_a_pending_commit_status_is_pending_not_green(tmp_path):
    w = status_world(tmp_path, "pending")
    p = w.checks()
    assert "pending" in p.output.lower(), p.output
    assert "green" not in p.output.lower()


def test_a_successful_commit_status_with_successful_checks_is_green(tmp_path):
    w = status_world(tmp_path, "success")
    p = w.checks()
    assert p.returncode == 0, p.output
    assert "green" in p.output.lower()
    assert w.unsupported() == []


# ---- rounds ----

def test_the_same_failure_in_two_rounds_is_refused_and_round_3_does_not_start(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    assert rounds_of(w.start("s1").output) == "1"
    assert w.fail("s1", "test_empty AssertionError").returncode == 0
    assert rounds_of(w.start("s1").output) == "2"
    p = w.fail("s1", "test_empty AssertionError")
    assert p.returncode != 0
    assert "same failure twice" in p.output
    p = w.start("s1")
    assert p.returncode != 0
    assert "same failure twice" in p.output


def test_five_rounds_with_different_failures_then_round_start_is_refused(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    for n in range(1, 6):
        assert rounds_of(w.start("s1").output) == str(n)
        assert w.fail("s1", f"test_case{chr(96 + n)} AssertionError").returncode == 0
    p = w.start("s1")
    assert p.returncode != 0
    assert "5 fix rounds" in p.output


def test_a_new_session_continues_the_round_count_of_a_crashed_one(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    for n in range(1, 4):
        assert rounds_of(w.start("s1").output) == str(n)
    w.set_now(T + 91 * MIN)  # s1 crashed; its lease expired
    assert w.acquire("s2").returncode == 0
    assert rounds_of(w.start("s2").output) == "4"
    assert rounds_of(w.start("s2").output) == "5"
    p = w.start("s2")
    assert p.returncode != 0
    assert "5 fix rounds" in p.output


def test_a_new_session_remembers_the_failure_signatures_of_a_crashed_one(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    w.start("s1")
    assert w.fail("s1", "test_empty AssertionError").returncode == 0
    w.set_now(T + 91 * MIN)
    assert w.acquire("s2").returncode == 0
    w.start("s2")
    p = w.fail("s2", "test_empty AssertionError")
    assert p.returncode != 0
    assert "same failure twice" in p.output


def test_a_retried_round_start_with_the_same_op_does_not_spend_another_round(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    assert rounds_of(w.round("start", "s1", "--op", "a").output) == "1"
    retry = w.round("start", "s1", "--op", "a")
    assert retry.returncode == 0, retry.output
    assert rounds_of(retry.output) == "1"
    assert rounds_of(w.round("start", "s1", "--op", "b").output) == "2"
    assert rounds_of(w.round("start", "s1", "--op", "b").output) == "2"
    assert rounds_of(w.round("start", "s1", "--op", "c").output) == "3"


def five_rounds_then_handoff(tmp_path, reason, rounds):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    for n in range(1, rounds + 1):
        assert w.start("s1").returncode == 0
        assert w.fail("s1", f"test_case{chr(96 + n)} AssertionError").returncode == 0
    assert w.run("release", "s1").returncode == 0
    w.handoff(2001, reason)
    return w


def test_the_owner_resume_after_5_fix_rounds_starts_a_new_budget_with_no_signatures(tmp_path):
    w = five_rounds_then_handoff(tmp_path, "5 fix rounds", 5)
    assert w.acquire("s2").returncode == 0
    assert rounds_of(w.start("s2").output) == "1"
    # the failure of old round 5 and of old round 1 is not remembered
    assert w.fail("s2", "test_casee AssertionError").returncode == 0
    assert w.fail("s2", "test_casea AssertionError").returncode == 0


def test_a_resume_after_a_handoff_for_another_reason_keeps_the_round_count(tmp_path):
    w = five_rounds_then_handoff(tmp_path, "door one-way", 2)
    assert w.acquire("s2").returncode == 0
    assert rounds_of(w.start("s2").output) == "3"


# ---- signatures ----

@pytest.mark.parametrize("first,second", [
    ("test_empty AssertionError at line 42", "test_empty AssertionError at line 97"),
    ("test_empty AssertionError id 0xdeadbeef", "test_empty AssertionError id 0xcafe1234"),
    ("test_empty AssertionError id 3f9a1c2b7d", "test_empty AssertionError id 8e4b0a6c51"),
    ("test_empty AssertionError /tmp/pytest-of-ci/pytest-1/test_empty0/out.txt",
     "test_empty AssertionError /tmp/pytest-of-ci/pytest-2/test_empty0/out.txt"),
    # differ in words, so only the tmp-path normalization (not the number one) can equate them
    ("test_empty AssertionError /tmp/run-alpha/workdir/out.txt",
     "test_empty AssertionError /tmp/run-omega/scratch/out.txt"),
    ("test_empty AssertionError after 12 retries", "test_empty AssertionError after 7 retries"),
])
def test_failures_that_differ_only_in_a_number_hex_id_or_tmp_path_have_the_same_signature(
        tmp_path, first, second):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    w.start("s1")
    assert w.fail("s1", first).returncode == 0
    w.start("s1")
    p = w.fail("s1", second)
    assert p.returncode != 0
    assert "same failure twice" in p.output


@pytest.mark.parametrize("check,error", [
    ("test", "test_full AssertionError"),
    ("test", "test_empty KeyError"),
    ("lint", "test_empty AssertionError"),
])
def test_a_different_test_error_or_check_is_not_the_same_failure(tmp_path, check, error):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    w.start("s1")
    assert w.fail("s1", "test_empty AssertionError").returncode == 0
    w.start("s1")
    p = w.fail("s1", error, check=check)
    assert p.returncode == 0, p.output
