"""Acceptance tests for `pr_exam.py setup <owner/repo>` (spec specs/pr-exam, requirement 14;
scenarios "setup on a public repo", "setup on a private repo of a free account").

Interface under test:
  pr_exam.py setup OWNER/REPO
      Asks the host for its GitHub App: `GET $PR_EXAM_HOST/api/exam/host` answers 200 with
      {"app_id": <int>}. Then protects the repo's default branch through `gh api`:
      - adds the required status check `exam`, bound to that App id, keeping every existing required
        check (with its app) and every other protection setting;
      - an unprotected default branch gets protection holding only `exam`, enforced for admins;
      - GitHub answering "Upgrade to GitHub Pro or make this repository public": no write, exit 0,
        the output says the gate is agent-enforced only;
      - any other GitHub error, or a host that cannot report the App id: non-zero, no write.
`gh` is the fake in ../babysit-pr/fixtures/gh (branch protection state, see its docstring).
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "pr_exam.py"
FAKE_GH = Path(__file__).parent.parent / "babysit-pr" / "fixtures" / "gh"
APP_ID = 424242
UPGRADE = {"status": 403, "message": "Upgrade to GitHub Pro or make this repository public."}

PROTECTION = {
    "required_status_checks": {"strict": True, "contexts": ["test", "lint"],
                               "checks": [{"context": "test", "app_id": 15368},
                                          {"context": "lint", "app_id": 15368}]},
    "enforce_admins": {"enabled": False},
    "required_pull_request_reviews": {"required_approving_review_count": 2,
                                      "dismiss_stale_reviews": True},
    "restrictions": None,
    "required_linear_history": {"enabled": True},
    "allow_force_pushes": {"enabled": False},
    "allow_deletions": {"enabled": False},
    "block_creations": {"enabled": True},
    "required_conversation_resolution": {"enabled": True},
    "lock_branch": {"enabled": False},
    "allow_fork_syncing": {"enabled": True},
}


class Host:
    """GET /api/exam/host -> `status`, `answer`; records the request paths."""

    def __init__(self, answer=None, status=200):
        self.answer = {"app_id": APP_ID} if answer is None else answer
        self.status = status
        self.paths = []
        host = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                host.paths.append(self.path)
                data = json.dumps(host.answer).encode()
                self.send_response(host.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def host():
    h = Host()
    yield h
    h.close()


class Repo:
    def __init__(self, tmp_path, **state):
        bindir = tmp_path / "bin"
        bindir.mkdir()
        shutil.copy(FAKE_GH, bindir / "gh")
        (bindir / "gh").chmod(0o755)
        self.state, self.log = tmp_path / "state.json", tmp_path / "log.jsonl"
        self.log.write_text("")
        self.state.write_text(json.dumps({"now": "2026-01-01T00:00:00Z", "user": "jesdi",
                                          "comments": [], "labels": [], "next_id": 1, **state}))
        self.env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                    "GH_FAKE_STATE": str(self.state), "GH_FAKE_LOG": str(self.log)}
        self.env.pop("PR_EXAM_HOST", None)

    def setup(self, repo, host_url):
        env = {**self.env, **({"PR_EXAM_HOST": host_url} if host_url else {})}
        p = subprocess.run([sys.executable, str(SCRIPT), "setup", repo], env=env, text=True,
                           capture_output=True, cwd=self.state.parent)
        p.output = p.stdout + p.stderr
        return p

    def github(self):
        return json.loads(self.state.read_text())

    def set(self, **kw):
        st = self.github()
        st.update(kw)
        self.state.write_text(json.dumps(st))

    def writes(self):
        return [e["argv"] for e in map(json.loads, self.log.read_text().splitlines()) if e["write"]]


def exam_check(app_id=APP_ID):
    return {"context": "exam", "app_id": app_id}


def test_setup_on_a_public_repo_adds_exam_bound_to_the_app_and_keeps_the_rest(tmp_path, host):
    r = Repo(tmp_path, private=False, default_branch="trunk", protection=copy.deepcopy(PROTECTION))
    p = r.setup("jesdi/agent-ops", host.url)
    assert p.returncode == 0, p.output
    assert "/api/exam/host" in host.paths
    after = r.github()["protection"]
    rsc = after["required_status_checks"]
    assert rsc["contexts"] == ["test", "lint", "exam"]
    assert rsc["checks"] == PROTECTION["required_status_checks"]["checks"] + [exam_check()]
    assert rsc["strict"] is True
    assert {k: v for k, v in after.items() if k != "required_status_checks"} == \
        {k: v for k, v in PROTECTION.items() if k != "required_status_checks"}
    assert any("branches/trunk/protection" in " ".join(a) for a in r.writes())


def test_running_setup_a_second_time_changes_nothing(tmp_path, host):
    r = Repo(tmp_path, private=False, protection=copy.deepcopy(PROTECTION))
    assert r.setup("jesdi/agent-ops", host.url).returncode == 0
    first = r.github()
    assert first["protection"]["required_status_checks"]["contexts"] == ["test", "lint", "exam"]
    p = r.setup("jesdi/agent-ops", host.url)
    assert p.returncode == 0, p.output
    assert r.github()["protection"] == first["protection"]
    assert r.github()["protection"]["required_status_checks"]["checks"].count(exam_check()) == 1


def test_an_unprotected_default_branch_gets_exam_only_enforced_for_admins(tmp_path, host):
    r = Repo(tmp_path, private=False, default_branch="main")
    p = r.setup("jesdi/agent-ops", host.url)
    assert p.returncode == 0, p.output
    after = r.github()["protection"]
    assert after["required_status_checks"]["checks"] == [exam_check()]
    assert after["required_status_checks"]["contexts"] == ["exam"]
    assert after["enforce_admins"] == {"enabled": True}
    assert after["required_pull_request_reviews"] is None
    assert after["restrictions"] is None


def test_a_private_repo_on_a_free_account_is_left_alone_and_said_agent_enforced(tmp_path, host):
    r = Repo(tmp_path, private=True, protection_error=UPGRADE)
    p = r.setup("jesdi/claude-config", host.url)
    assert p.returncode == 0, p.output
    assert "agent-enforced" in p.output.lower(), p.output
    assert r.writes() == []
    assert "protection" not in r.github()


@pytest.mark.parametrize("error", [
    {"status": 500, "message": "Server Error"},
    {"status": 403, "message": "Resource not accessible by integration"},
], ids=["server-error", "forbidden"])
def test_any_other_github_error_fails_and_changes_nothing(tmp_path, host, error):
    r = Repo(tmp_path, private=False, protection=copy.deepcopy(PROTECTION), protection_error=error)
    p = r.setup("jesdi/agent-ops", host.url)
    assert p.returncode != 0, p.output
    assert "agent-enforced" not in p.output.lower()
    assert r.writes() == []
    # control: the failure was the refusal, not a missing command
    r.set(protection_error=None)
    ok = r.setup("jesdi/agent-ops", host.url)
    assert ok.returncode == 0, ok.output
    assert "exam" in r.github()["protection"]["required_status_checks"]["contexts"]


def test_a_failed_protection_write_fails_and_leaves_the_protection_as_it_was(tmp_path, host):
    r = Repo(tmp_path, private=False, protection=copy.deepcopy(PROTECTION),
             put_error={"status": 422, "message": "Validation Failed"})
    p = r.setup("jesdi/agent-ops", host.url)
    assert p.returncode != 0, p.output
    assert r.github()["protection"] == PROTECTION
    r.set(put_error=None)
    assert r.setup("jesdi/agent-ops", host.url).returncode == 0
    assert "exam" in r.github()["protection"]["required_status_checks"]["contexts"]


@pytest.mark.parametrize("answer, status", [({}, 200), ({"app_id": "abc"}, 200), ({"app_id": None}, 200),
                                            ({"error": "boom"}, 500), (None, "unreachable")],
                         ids=["no-app-id", "bad-app-id", "null-app-id", "host-error", "unreachable"])
def test_a_host_that_cannot_report_the_app_id_fails_and_changes_nothing(tmp_path, answer, status):
    r = Repo(tmp_path, private=False, protection=copy.deepcopy(PROTECTION))
    bad = Host(answer, status if status != "unreachable" else 200)
    url = bad.url
    if status == "unreachable":
        bad.close()
    try:
        p = r.setup("jesdi/agent-ops", url)
    finally:
        if status != "unreachable":
            bad.close()
    assert p.returncode != 0, p.output
    assert r.writes() == []
    assert r.github()["protection"] == PROTECTION
    # control: with a healthy host the same repo is protected
    good = Host()
    try:
        ok = r.setup("jesdi/agent-ops", good.url)
    finally:
        good.close()
    assert ok.returncode == 0, ok.output
    assert exam_check() in r.github()["protection"]["required_status_checks"]["checks"]


def test_setup_without_a_host_configured_fails_and_changes_nothing(tmp_path, host):
    r = Repo(tmp_path, private=False, protection=copy.deepcopy(PROTECTION))
    p = r.setup("jesdi/agent-ops", None)
    assert p.returncode != 0, p.output
    assert r.writes() == []
    assert r.setup("jesdi/agent-ops", host.url).returncode == 0


def test_review_allowances_and_push_restrictions_go_back_as_names_without_urls(tmp_path, host):
    people = {"url": "https://api.github.com/x", "users": [{"login": "ana", "url": "u"}],
              "teams": [{"slug": "core", "url": "u"}], "apps": [{"slug": "bot", "url": "u"}]}
    protection = copy.deepcopy(PROTECTION)
    protection["restrictions"] = copy.deepcopy(people)
    protection["required_pull_request_reviews"] = {
        "url": "https://api.github.com/r", "required_approving_review_count": 1,
        "dismissal_restrictions": copy.deepcopy(people), "bypass_pull_request_allowances": {"users": [], "teams": []}}
    r = Repo(tmp_path, private=False, protection=protection)
    p = r.setup("jesdi/agent-ops", host.url)
    assert p.returncode == 0, p.output
    after = r.github()["protection"]
    names = {"users": ["ana"], "teams": ["core"], "apps": ["bot"]}
    assert after["restrictions"] == names
    assert after["required_pull_request_reviews"] == {
        "required_approving_review_count": 1, "dismissal_restrictions": names,
        "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []}}


def test_a_protected_branch_without_required_reviews_keeps_none(tmp_path, host):
    r = Repo(tmp_path, private=False, protection={**copy.deepcopy(PROTECTION), "required_pull_request_reviews": None})
    p = r.setup("jesdi/agent-ops", host.url)
    assert p.returncode == 0, p.output
    assert r.github()["protection"]["required_pull_request_reviews"] is None
    assert exam_check() in r.github()["protection"]["required_status_checks"]["checks"]


def test_a_repo_github_cannot_read_fails_and_changes_nothing(tmp_path, host):
    r = Repo(tmp_path, private=False, protection=copy.deepcopy(PROTECTION))
    p = r.setup("agent-ops", host.url)  # no owner: `gh api repos/agent-ops` fails
    assert p.returncode != 0, p.output
    assert "fake gh: unsupported api GET repos/agent-ops" in p.stderr, p.stderr
    assert r.writes() == []
    assert r.github()["protection"] == PROTECTION
