"""Acceptance tests for `babysit.py lease acquire|renew|check|release`.

Interface under test:
  babysit.py lease <verb> --repo <owner/name> --pr <number> --session <id> --host <name>
Exit 0 means success (acquire: the session holds the lease; check: it still holds it).
Any failure exits non-zero and prints its reason on stdout or stderr.

The lease is a PR comment `<!-- babysit-pr lease: <session> <host> <expires-at> -->` (ISO UTC).
An ended lease comment keeps its id and its body says "ended".

`gh` is the fake in fixtures/gh, first on PATH. It holds GitHub state and its own clock (`now`),
and records every call. The fake GitHub clock is set in 2020 or 2099, never near the host
clock, so a babysitter that reads the host clock decides wrongly.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("babysit.py").resolve()
FAKE_GH = Path(__file__).parent / "fixtures" / "gh"
LEASE = re.compile(r"<!-- babysit-pr lease: (\S+) (\S+) (\S+) -->")
INSTALL = "npx @jesdi/skills-cli install pr-risk"
T = datetime(2020, 1, 1, 10, 5, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def lease_comment(cid, session, host, updated_at, expires_at, login="jesdi", rest="rounds: 0"):
    return {"id": cid, "user": {"login": login}, "created_at": iso(updated_at),
            "updated_at": iso(updated_at),
            "body": f"<!-- babysit-pr lease: {session} {host} {iso(expires_at)} -->\n{rest}"}


class World:
    def __init__(self, tmp_path, now=T, comments=(), labels=(), hidden=(), pr_risk=True):
        self.project = tmp_path / "project"
        self.home = tmp_path / "home"
        bindir = tmp_path / "bin"
        for d in (self.project, self.home, bindir):
            d.mkdir()
        shutil.copy(FAKE_GH, bindir / "gh")
        (bindir / "gh").chmod(0o755)
        if pr_risk:
            skill = self.project / ".agents" / "skills" / "pr-risk"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: pr-risk\n---\n")
        self.state = tmp_path / "state.json"
        self.log = tmp_path / "log.jsonl"
        self.log.write_text("")
        self.state.write_text(json.dumps({
            "now": iso(now), "user": "jesdi", "comments": list(comments),
            "labels": list(labels), "hidden_until_post": list(hidden), "posted": False,
            "next_id": max([c["id"] for c in comments] + [1000]) + 1}))
        self.env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                    "HOME": str(self.home), "GH_FAKE_STATE": str(self.state),
                    "GH_FAKE_LOG": str(self.log)}

    def run(self, verb, session, repo="jesdi/demo", pr="58"):
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "lease", verb, "--repo", repo, "--pr", pr,
             "--session", session, "--host", "mac2"],
            cwd=self.project, env=self.env, text=True, capture_output=True)
        p.output = p.stdout + p.stderr
        return p

    def set_now(self, dt):
        st = self.github()
        st["now"] = iso(dt)
        self.state.write_text(json.dumps(st))

    def github(self):
        return json.loads(self.state.read_text())

    def comment(self, cid):
        return next(c for c in self.github()["comments"] if c["id"] == cid)

    def writes(self):
        return [e for e in map(json.loads, self.log.read_text().splitlines()) if e["write"]]

    def calls(self):
        return [e["argv"] for e in map(json.loads, self.log.read_text().splitlines())]

    def unsupported(self):
        return [e["argv"] for e in map(json.loads, self.log.read_text().splitlines())
                if e["unsupported"]]

    def active_leases(self):
        """(id, session, host, expires-at, updated_at) of lease comments not ended."""
        return [(c["id"], *LEASE.search(c["body"]).groups(), c["updated_at"])
                for c in self.github()["comments"]
                if LEASE.search(c["body"]) and "ended" not in c["body"].lower()]


@pytest.mark.parametrize("verb", ["acquire", "renew", "check", "release"])
def test_without_pr_risk_nothing_runs_and_nothing_is_written(tmp_path, verb):
    w = World(tmp_path, pr_risk=False)
    p = w.run(verb, "s2")
    assert p.returncode != 0
    assert INSTALL in p.output
    assert w.writes() == []


def test_valid_lease_of_another_session_blocks_acquire_without_writes(tmp_path):
    w = World(tmp_path, comments=[lease_comment(1001, "s1", "mac", T - 50 * MIN, T + 40 * MIN)],
              labels=["babysitting"])
    p = w.run("acquire", "s2")
    assert p.returncode != 0
    assert "already babysat by mac" in p.output
    assert w.unsupported() == []
    assert w.writes() == []


def test_expired_lease_is_taken_over_with_expiry_90_minutes_after_github_updated_at(tmp_path):
    now = datetime(2099, 1, 1, 10, 5, tzinfo=timezone.utc)
    w = World(tmp_path, now=now,
              comments=[lease_comment(1001, "s1", "mac", now - 91 * MIN, now - 1 * MIN)])
    p = w.run("acquire", "s2")
    assert p.returncode == 0, p.output
    mine = [l for l in w.active_leases() if l[1] == "s2"]
    assert len(mine) == 1
    _, _, host, expires_at, updated_at = mine[0]
    assert host == "mac2"
    assert parse(updated_at) == now
    assert parse(expires_at) == parse(updated_at) + 90 * MIN
    assert [l for l in w.active_leases() if l[1] == "s1"] == []
    assert w.run("check", "s2").returncode == 0


def test_two_claims_within_one_second_lowest_comment_id_wins(tmp_path):
    t = datetime(2020, 1, 1, 10, 0, 1, tzinfo=timezone.utc)
    # s1's comment 1001 is not visible to s2's first read: s1 posts right after it.
    s1 = lease_comment(1001, "s1", "mac", t, t + 90 * MIN)
    w = World(tmp_path, now=t, comments=[s1], hidden=[1001], labels=["babysitting"])
    p = w.run("acquire", "s2")
    assert p.returncode != 0
    assert w.unsupported() == []
    mine = w.comment(1002)
    assert LEASE.search(mine["body"]) and LEASE.search(mine["body"]).group(1) == "s2"
    assert "ended" in mine["body"].lower()
    assert w.comment(1001) == s1
    assert "babysitting" in w.github()["labels"]


def test_expiry_uses_github_time_not_the_host_clock(tmp_path):
    t10 = datetime(2020, 1, 1, 10, 0, tzinfo=timezone.utc)
    w = World(tmp_path, now=T,
              comments=[lease_comment(1001, "s1", "mac", t10, t10 + 90 * MIN)])
    p = w.run("acquire", "s2")
    assert p.returncode != 0
    assert "already babysat by mac" in p.output
    assert w.writes() == []


def test_check_fails_for_a_session_that_does_not_hold_the_lease(tmp_path):
    w = World(tmp_path, comments=[
        lease_comment(1001, "s2", "mac", T - 5 * MIN, T + 85 * MIN),
        lease_comment(1002, "s1", "mac2", T - 4 * MIN, T + 86 * MIN)])
    p = w.run("check", "s1")
    assert p.returncode != 0
    assert w.writes() == []
    assert w.run("check", "s2").returncode == 0


def test_release_removes_the_label_and_marks_the_lease_ended(tmp_path):
    w = World(tmp_path, comments=[lease_comment(1001, "s1", "mac2", T - 5 * MIN, T + 85 * MIN)],
              labels=["babysitting"])
    p = w.run("release", "s1")
    assert p.returncode == 0, p.output
    assert "babysitting" not in w.github()["labels"]
    assert "ended" in w.comment(1001)["body"].lower()
    assert w.active_leases() == []
    assert w.unsupported() == []


def test_acquire_adds_the_babysitting_label_and_removes_needs_human(tmp_path):
    w = World(tmp_path, labels=["needs-human", "bug"])
    p = w.run("acquire", "s2")
    assert p.returncode == 0, p.output
    assert sorted(w.github()["labels"]) == ["babysitting", "bug"]
    assert [l[1] for l in w.active_leases()] == ["s2"]
    assert w.unsupported() == []


def test_renew_extends_expiry_from_github_time_and_keeps_persisted_state(tmp_path):
    rest = "rounds: 3\nsignature: test_empty AssertionError"
    w = World(tmp_path, comments=[lease_comment(1001, "s1", "mac2", T - 5 * MIN, T + 85 * MIN, rest=rest)],
              labels=["babysitting"])
    w.set_now(T + 60 * MIN)
    p = w.run("renew", "s1")
    assert p.returncode == 0, p.output
    [(cid, session, _, expires_at, updated_at)] = w.active_leases()
    assert (cid, session) == (1001, "s1")
    assert parse(updated_at) == T + 60 * MIN
    assert parse(expires_at) == T + 150 * MIN
    assert rest in w.comment(1001)["body"]
    assert w.unsupported() == []
    w.set_now(T + 120 * MIN)  # past the original expiry, within the renewed one
    assert w.run("check", "s1").returncode == 0


def test_renew_by_a_session_that_does_not_hold_the_lease_fails_without_writes(tmp_path):
    w = World(tmp_path, comments=[lease_comment(1001, "s1", "mac", T - 5 * MIN, T + 85 * MIN)])
    p = w.run("renew", "s2")
    assert p.returncode != 0
    assert w.writes() == []
    assert w.comment(1001)["body"] == lease_comment(1001, "s1", "mac", T, T + 85 * MIN)["body"]


@pytest.mark.parametrize("repo,pr", [("jesdi/demo", "58"), ("acme/widgets", "7"), ("other-org/x.y", "1234")])
def test_every_call_targets_the_requested_repository_and_pr(tmp_path, repo, pr):
    w = World(tmp_path, labels=["needs-human"])
    for verb in ("acquire", "renew", "check", "release"):
        p = w.run(verb, "s2", repo=repo, pr=pr)
        assert p.returncode == 0, p.output
    assert w.unsupported() == []
    targets = [a for argv in w.calls() for a in argv
               if re.search(r"\b(repos|issues|pulls)/", a) or a in ("--repo", "-R")]
    assert targets
    pattern = rf"^/?repos/{re.escape(repo)}/issues/({pr}(/|$)|comments/\d+$)"
    for t in targets:
        assert re.match(pattern, t), t
