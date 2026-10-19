"""Acceptance tests for ticket 18: a retried failure record counts once, and the CI stall timer
keeps the earliest pending time it has seen.

Interface under test (same helpers as test_rounds_and_checks.py):
  babysit.py round record-failure --repo R --pr N --session S --host H --check C --error E --op O
      --op is required and names the round the failure belongs to; a retry with the same --op
      records nothing new.
  babysit.py checks --repo R --pr N --wait-minutes M
"""
from test_rounds_and_checks import Ci, MIN, T, ci_world, iso, run_ci

ERR = "test_empty AssertionError"


def record(w, op, error=ERR, session="s1"):
    return w.round("record-failure", session, "--check", "test", "--error", error, "--op", op)


def test_record_failure_without_op_is_refused_naming_op_and_writes_nothing(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    assert w.round("start", "s1", "--op", "a").returncode == 0
    before = w.github()
    p = w.round("record-failure", "s1", "--check", "test", "--error", ERR)
    assert p.returncode != 0, p.output
    assert "--op" in p.output
    assert w.github() == before


def test_a_lost_response_retry_after_the_next_round_start_records_the_failure_once(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    assert w.round("start", "s1", "--op", "f1").returncode == 0
    assert record(w, "f1").returncode == 0  # the response of this call is lost
    assert w.round("start", "s1", "--op", "b").returncode == 0
    body_before = w.comment(1001)["body"]
    retry = record(w, "f1")
    assert retry.returncode == 0, retry.output
    assert w.comment(1001)["body"] == body_before
    assert body_before.count("signature:") == 1
    assert "signature: r1 " in body_before
    p = w.round("start", "s1", "--op", "c")
    assert p.returncode == 0, p.output
    assert p.stdout.strip().split()[-1] == "3"
    assert not any("Needs human" in c["body"] for c in w.github()["comments"])


def test_the_same_failure_recorded_with_two_different_ops_is_same_failure_twice(tmp_path):
    w = Ci(tmp_path)
    assert w.acquire("s1").returncode == 0
    assert w.round("start", "s1", "--op", "f1").returncode == 0
    assert record(w, "f1").returncode == 0
    assert w.round("start", "s1", "--op", "f2").returncode == 0
    p = record(w, "f2")
    assert p.returncode != 0
    assert "same failure twice" in p.output


def test_a_check_that_publishes_pending_again_keeps_its_first_pending_time(tmp_path):
    w = ci_world(tmp_path, [dict(run_ci("e2e", "queued"), id=1, created_at=iso(T))])
    # published pending at minute 0, but `checks` only runs for the first time at minute 30
    w.set_now(T + 30 * MIN)
    assert w.checks(wait=60).stdout.strip() == "pending"
    w.set_now(T + 60 * MIN)
    w.seed(check_runs={"head1": [dict(run_ci("e2e", "queued"), id=1,
                                      created_at=iso(T + 60 * MIN))]})
    assert w.checks(wait=60).stdout.strip() == "CI stalled"


def test_a_run_with_both_timestamps_counts_pending_from_the_earlier_created_at(tmp_path):
    run = dict(run_ci("e2e", "in_progress"), id=1, created_at=iso(T), started_at=iso(T + 20 * MIN))
    w = ci_world(tmp_path, [run])
    w.set_now(T + 30 * MIN)
    assert w.checks(wait=60).stdout.strip() == "pending"
    w.set_now(T + 60 * MIN)
    assert w.checks(wait=60).stdout.strip() == "CI stalled"


def test_a_rerun_after_the_check_completed_starts_a_new_timer(tmp_path):
    w = ci_world(tmp_path, [dict(run_ci("e2e", "queued"), id=1, created_at=iso(T))])
    assert w.checks(wait=60).stdout.strip() == "pending"
    w.set_now(T + 30 * MIN)
    w.seed(check_runs={"head1": [dict(run_ci("e2e", "completed", "success"), id=1,
                                      created_at=iso(T))]})
    assert w.checks(wait=60).stdout.strip() == "green"
    w.set_now(T + 120 * MIN)
    w.seed(check_runs={"head1": [dict(run_ci("e2e", "queued"), id=2,
                                      created_at=iso(T + 120 * MIN))]})
    assert w.checks(wait=60).stdout.strip() == "pending"
    w.set_now(T + 179 * MIN)
    assert w.checks(wait=60).stdout.strip() == "pending"
    w.set_now(T + 180 * MIN)
    assert w.checks(wait=60).stdout.strip() == "CI stalled"


def pending_status(sid, at):
    return {"id": sid, "context": "ci/e2e", "state": "pending", "created_at": iso(at)}


def test_a_commit_status_published_pending_again_keeps_its_first_pending_time(tmp_path):
    w = ci_world(tmp_path, [])
    w.seed(statuses={"head1": [pending_status(1, T)]})
    w.set_now(T + 30 * MIN)
    assert w.checks(wait=60).stdout.strip() == "pending"
    w.set_now(T + 60 * MIN)
    w.seed(statuses={"head1": [pending_status(2, T + 60 * MIN), pending_status(1, T)]})
    assert w.checks(wait=60).stdout.strip() == "CI stalled"


def test_a_commit_status_that_completed_and_goes_pending_again_starts_a_new_timer(tmp_path):
    w = ci_world(tmp_path, [])
    w.seed(statuses={"head1": [pending_status(1, T)]})
    assert w.checks(wait=60).stdout.strip() == "pending"
    w.set_now(T + 30 * MIN)
    w.seed(statuses={"head1": [{"id": 2, "context": "ci/e2e", "state": "success",
                                "created_at": iso(T + 30 * MIN)}, pending_status(1, T)]})
    assert w.checks(wait=60).stdout.strip() == "green"
    w.set_now(T + 120 * MIN)
    w.seed(statuses={"head1": [pending_status(3, T + 120 * MIN)]})
    assert w.checks(wait=60).stdout.strip() == "pending"
    w.set_now(T + 179 * MIN)
    assert w.checks(wait=60).stdout.strip() == "pending"
    w.set_now(T + 180 * MIN)
    assert w.checks(wait=60).stdout.strip() == "CI stalled"


def test_an_earlier_created_at_learned_after_the_timer_was_persisted_moves_it_back(tmp_path):
    w = ci_world(tmp_path, [dict(run_ci("e2e", "queued"), id=1)])  # no created_at yet
    w.set_now(T + 30 * MIN)
    assert w.checks(wait=60).stdout.strip() == "pending"  # persists minute 30
    w.set_now(T + 60 * MIN)
    w.seed(check_runs={"head1": [dict(run_ci("e2e", "queued"), id=1, created_at=iso(T))]})
    assert w.checks(wait=60).stdout.strip() == "CI stalled"


def test_an_earlier_status_created_at_learned_after_the_timer_was_persisted_moves_it_back(tmp_path):
    w = ci_world(tmp_path, [])
    w.seed(statuses={"head1": [pending_status(1, T + 30 * MIN)]})
    w.set_now(T + 30 * MIN)
    assert w.checks(wait=60).stdout.strip() == "pending"  # persists minute 30
    w.set_now(T + 60 * MIN)
    w.seed(statuses={"head1": [pending_status(1, T)]})
    assert w.checks(wait=60).stdout.strip() == "CI stalled"
