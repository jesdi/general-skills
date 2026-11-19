"""Acceptance tests for the exam gate of `babysit.py merge` (spec specs/pr-exam, requirement 12).

Interface under test (extends `merge` of test_merge_and_handoff.py):
  In a repo with exams enabled (`.pr-exam.json` on origin/<base> absent or enabled), `merge` goes
  ahead only when the `exam` commit status of --sha is success, on every approval path. Otherwise it
  exits non-zero printing "exam not passed", runs no `gh pr merge`, posts no handoff comment and
  keeps the lease and the `babysitting` label. Success with any description ("bypassed", "deferred")
  is a pass. A pass changes nothing else. With `{"enabled": false}` the status is not read.
  After a successful merge it POSTs {"repo", "pr"} once to $PR_EXAM_HOST/api/exam; a failing host
  never fails the merge.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from exam_host import FakeHost  # noqa: E402
from test_babysit_judge import commit as git_commit, git
from test_merge_and_handoff import EXAM_OK, commit, handoffs, merge, world

JUDGE = ("--judge", "agree", "--judge-risk", "2")
PATHS = {
    "session": ("--session-approved",),
    "judge": JUDGE,
    "approved-sha": ("--approved-sha", "a1b2c3d"),
}
PENDING = {"context": "exam", "state": "pending", "description": "attempt 1"}


def set_exam(w, sha, *statuses):
    st = w.github()
    st["statuses"] = {sha: list(statuses)}
    w.state.write_text(json.dumps(st))


def run(w, tmp_path, path, sha="a1b2c3d"):
    assert w.run("acquire", "s1").returncode == 0
    return merge(w, tmp_path, sha, *PATHS[path], "--session", "s1")


def refused(w, p, host=None):
    assert p.returncode != 0
    assert "exam not passed" in p.output
    assert not w.github().get("merges")
    assert handoffs(w) == []
    assert "needs-human" not in w.github()["labels"] and "babysitting" in w.github()["labels"]
    assert [l[1] for l in w.active_leases()] == ["s1"]
    if host is not None:
        assert host.requests == []


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("statuses", [[PENDING], []], ids=["pending", "no-status"])
def test_merge_is_refused_without_a_passed_exam(tmp_path, path, statuses):
    w = world(tmp_path, approvals=("a1b2c3d",), statuses={"a1b2c3d": statuses})
    with FakeHost() as host:
        w.env["PR_EXAM_HOST"] = host.url
        refused(w, run(w, tmp_path, path), host)


def test_a_failed_exam_does_not_pass_either(tmp_path):
    w = world(tmp_path, statuses={"a1b2c3d": [{"context": "exam", "state": "failure"}]})
    refused(w, run(w, tmp_path, "session"))


def test_the_status_read_is_the_one_of_the_sha_not_of_an_earlier_commit(tmp_path):
    w = world(tmp_path, head="a1b2c3d", commits=[commit("c1"), commit("a1b2c3d")],
              statuses={"c1": [EXAM_OK]})
    refused(w, run(w, tmp_path, "judge"))


def test_the_newest_exam_status_of_the_sha_counts(tmp_path):
    w = world(tmp_path, statuses={"a1b2c3d": [PENDING, EXAM_OK]})  # newest first
    refused(w, run(w, tmp_path, "judge"))


def test_a_pass_merges_the_pinned_head_after_a_refusal(tmp_path):
    w = world(tmp_path, statuses={"a1b2c3d": [PENDING]})
    refused(w, run(w, tmp_path, "judge"))
    set_exam(w, "a1b2c3d", EXAM_OK)
    p = merge(w, tmp_path, "a1b2c3d", *JUDGE, "--session", "s1")
    assert p.returncode == 0, p.output
    [m] = w.github()["merges"]
    assert m["match"] == "a1b2c3d" and m["auto"] is False


@pytest.mark.parametrize("description", ["bypassed", "deferred"])
def test_a_bypassed_or_deferred_exam_merges_like_a_pass(tmp_path, description):
    w = world(tmp_path, statuses={"a1b2c3d": [PENDING]})
    refused(w, run(w, tmp_path, "judge"))
    set_exam(w, "a1b2c3d", {"context": "exam", "state": "success", "description": description})
    p = merge(w, tmp_path, "a1b2c3d", *JUDGE, "--session", "s1")
    assert p.returncode == 0, p.output
    assert len(w.github()["merges"]) == 1


def test_a_pass_is_not_merge_approval(tmp_path):
    w = world(tmp_path, policy={"maxAutoRisk": 0}, statuses={"a1b2c3d": [PENDING]})
    refused(w, run(w, tmp_path, "judge"))  # the exam is checked first, with no handoff
    set_exam(w, "a1b2c3d", EXAM_OK)
    p = merge(w, tmp_path, "a1b2c3d", *JUDGE, "--session", "s1")
    assert p.returncode != 0
    assert not w.github().get("merges")
    [h] = handoffs(w)
    assert "exam" not in h.lower()
    assert "needs-human" in w.github()["labels"]


def test_exams_turned_off_do_not_look_at_the_exam_status(tmp_path):
    w = world(tmp_path, statuses={"a1b2c3d": [PENDING]})
    refused(w, run(w, tmp_path, "judge"))  # the same world with exams on
    cfg = git_commit(w.project, {".pr-exam.json": '{"enabled": false}'}, "exams off")
    git(w.project, "update-ref", "refs/remotes/origin/main", cfg)
    p = merge(w, tmp_path, "a1b2c3d", *JUDGE, "--session", "s1")
    assert p.returncode == 0, p.output
    assert len(w.github()["merges"]) == 1


def test_after_a_merge_the_host_gets_one_request_for_the_pr(tmp_path):
    w = world(tmp_path)
    with FakeHost() as host:
        w.env["PR_EXAM_HOST"] = host.url
        p = merge(w, tmp_path, "a1b2c3d", *JUDGE)
    assert p.returncode == 0, p.output
    assert host.requests == [{"path": "/api/exam", "body": {"repo": "jesdi/demo", "pr": 58}}]


def test_a_failing_host_does_not_fail_the_merge(tmp_path):
    seen = []

    class Broken(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.path)
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Broken)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        w = world(tmp_path)
        w.env["PR_EXAM_HOST"] = f"http://127.0.0.1:{server.server_port}"
        p = merge(w, tmp_path, "a1b2c3d", *JUDGE)
    finally:
        server.shutdown()
        server.server_close()
    assert seen == ["/api/exam"]
    assert p.returncode == 0, p.output
    assert len(w.github()["merges"]) == 1
