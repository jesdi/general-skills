"""In-session merge instructions reach the same pinned, green merge CLI."""
import pytest

from test_merge_and_handoff import commit, comments, handoffs, merge, world


def session(w):
    assert w.run("acquire", "s1").returncode == 0
    return ("--session", "s1", "--session-approved")


def test_session_instruction_merges_human_risk_without_a_github_approval(tmp_path):
    w = world(tmp_path, policy={"maxAutoRisk": 0})
    p = merge(w, tmp_path, "a1b2c3d", *session(w))
    assert p.returncode == 0, p.output
    [merged] = w.github()["merges"]
    assert merged["match"] == "a1b2c3d"
    assert handoffs(w) == []
    assert any("session s1 authorized merge" in c for c in comments(w))
    assert w.active_leases() == []


def test_session_instruction_cannot_merge_a_head_that_moved(tmp_path):
    w = world(tmp_path, head="f0e1d2c", commits=[commit("a1b2c3d"), commit("f0e1d2c")])
    p = merge(w, tmp_path, "a1b2c3d", *session(w))
    assert p.returncode != 0
    assert "head moved" in p.output
    assert not w.github().get("merges")


@pytest.mark.parametrize("checks", [[], [{"name": "ci", "status": "completed", "conclusion": "failure"}]])
def test_session_instruction_still_requires_green_checks(tmp_path, checks):
    w = world(tmp_path, check_runs={"a1b2c3d": checks})
    p = merge(w, tmp_path, "a1b2c3d", *session(w))
    assert p.returncode != 0
    assert not w.github().get("merges")
    assert len(handoffs(w)) == 1


def test_session_instruction_requires_the_lease_holder(tmp_path):
    w = world(tmp_path)
    assert w.run("acquire", "s2").returncode == 0
    p = merge(w, tmp_path, "a1b2c3d", "--session", "s1", "--session-approved")
    assert p.returncode != 0
    assert not w.github().get("merges")
    assert w.active_leases()[0][1] == "s2"


def test_session_instruction_still_hands_off_for_an_unresolved_review(tmp_path):
    w = world(tmp_path, threads=[{"user": {"login": "alice"}, "resolved": False}])
    p = merge(w, tmp_path, "a1b2c3d", *session(w))
    assert p.returncode != 0
    assert not w.github().get("merges")
    assert "review comment from alice" in p.output


def test_session_instruction_cannot_override_branch_protection(tmp_path):
    w = world(tmp_path, merge_error="At least 1 approving review is required")
    p = merge(w, tmp_path, "a1b2c3d", *session(w))
    assert p.returncode != 0
    assert not w.github().get("merges")
    assert "merge blocked: approval required" in p.output


def test_session_approval_without_a_session_is_invalid_input(tmp_path):
    w = world(tmp_path)
    before = w.github()
    p = merge(w, tmp_path, "a1b2c3d", "--session-approved")
    assert p.returncode != 0
    assert "--session-approved needs --session" in p.output
    assert w.github() == before


@pytest.mark.parametrize("other", [("--approved-sha", "a1b2c3d"), ("--judge", "agree"), ("--judge-risk", "1")])
def test_session_approval_cannot_be_mixed_with_other_approval_paths(tmp_path, other):
    w = world(tmp_path)
    args = session(w)
    before = w.github()
    p = merge(w, tmp_path, "a1b2c3d", *args, *other)
    assert p.returncode != 0
    assert "cannot be combined" in p.output
    assert w.github() == before
