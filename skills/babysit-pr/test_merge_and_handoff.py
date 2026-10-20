"""Acceptance tests for `babysit.py merge-method|merge|handoff`.

Interface under test (defined here):
  babysit.py merge-method --repo R --pr N [--policy FILE]
      stdout: {"method": "rebase" | "merge" | "squash"}. FILE is the policy `.merge-risk.json`.
  babysit.py merge --repo R --pr N --sha SHA --body FILE [--policy FILE]
                   [--judge "agree" | "same-model agree"] [--approved-sha PREFIX]
      SHA is the head the gate (and judge) saw. BODY is the PR body (summary + merge-risk block).
      Merges with `gh pr merge N --<method> --match-head-commit SHA`, never `--auto`, then posts
      `🤖 Merged by babysit-pr: gate passed, judge <label>` and exits 0.
      --approved-sha is the owner's `@babysitter merge <sha>` approval (instead of --judge): it
      is honoured only while SHA starts with it, else nothing merges and it hands off with
      "approval was for <approved-sha>".
      Exit non-zero when it does not merge. A moved head prints "gate again" and merges nothing.
      Any other `gh pr merge` failure hands off with "merge blocked: <reason>".
  babysit.py handoff --repo R --pr N --reason TEXT --ci TEXT --rounds N --gate TEXT --judge TEXT
                     --fix-log FILE
      One comment `## 🧑 Needs human: <reason>`, label `needs-human` added, `babysitting` removed.

`gh` is the fake in fixtures/gh, which records `gh pr merge` calls in the state `merges`.
"""
import json
import subprocess
import sys

import pytest

from test_babysit import SCRIPT, T, World, iso

BLOCK = """## Summary

Parse rows that are empty.

## Merge Danger

```merge-risk v1
door: two-way
blast_radius: isolated
touches: [logic, tests]
rollback: revert
verdict: auto
reasons:
  - "small and reversible"
```
"""
URL = "https://github.com/jesdi/demo/pull/59"


def commit(sha, message="feat: x", author="jesdi", parents=("p0",)):
    return {"sha": sha, "date": iso(T), "message": message, "author": author, "parents": list(parents)}


def world(tmp_path, head="a1b2c3d", commits=None, **state):
    w = World(tmp_path, labels=["babysitting"])
    st = w.github()
    green = [{"name": "ci", "status": "completed", "conclusion": "success",
              "created_at": iso(T), "started_at": iso(T)}]
    st.update(head_sha=head, title="feat: parse empty rows",
              commits=commits or [commit(head)], **{"check_runs": {head: green}, **state})
    w.state.write_text(json.dumps(st))
    (tmp_path / "body.md").write_text(BLOCK)
    return w


def cli(w, *args):
    p = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=w.project, env=w.env,
                       text=True, capture_output=True)
    p.output = p.stdout + p.stderr
    return p


def merge(w, tmp_path, sha="a1b2c3d", *extra, pr="58", policy=None):
    args = ["merge", "--repo", "jesdi/demo", "--pr", pr, "--sha", sha,
            "--body", str(tmp_path / "body.md"), *extra]
    if policy is not None:
        f = tmp_path / "policy.json"
        f.write_text(json.dumps(policy))
        args += ["--policy", str(f)]
    return cli(w, *args)


def comments(w):
    return [c["body"] for c in w.github()["comments"]]


def handoffs(w):
    return [b for b in comments(w) if b.startswith("## 🧑 Needs human")]


def test_stale_gate_sha_fails_and_nothing_merges(tmp_path):
    w = world(tmp_path, head="f0e1d2c", commits=[commit("a1b2c3d"), commit("f0e1d2c", author="alice")])
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode != 0
    assert "gate again" in p.output
    assert w.github().get("merges", []) == []
    assert not any("Merged by babysit-pr" in b for b in comments(w))


@pytest.mark.parametrize("extra", [("--judge", "agree"), ("--approved-sha", "a1b2c3d")])
def test_head_with_no_checks_hands_off_and_nothing_merges(tmp_path, extra):
    w = world(tmp_path, check_runs={})
    p = merge(w, tmp_path, "a1b2c3d", *extra)
    assert p.returncode != 0
    assert w.github().get("merges", []) == []
    [h] = handoffs(w)
    assert h.startswith("## 🧑 Needs human: CI is no checks at merge time")


def test_default_method_is_rebase_pinned_to_the_gated_sha_and_commented(tmp_path):
    w = world(tmp_path)
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode == 0, p.output
    [m] = w.github()["merges"]
    assert (m["number"], m["method"], m["match"], m["auto"]) == ("58", "rebase", "a1b2c3d", False)
    assert "🤖 Merged by babysit-pr: gate passed, judge agree" in comments(w)


def test_same_model_judge_shows_in_the_merge_comment(tmp_path):
    w = world(tmp_path)
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "same-model agree")
    assert p.returncode == 0, p.output
    assert "🤖 Merged by babysit-pr: gate passed, judge same-model agree" in comments(w)


def test_merge_method_is_merge_when_the_branch_has_a_merge_commit(tmp_path):
    w = world(tmp_path, commits=[commit("c1"), commit("a1b2c3d", parents=("c1", "m1"))])
    p = cli(w, "merge-method", "--repo", "jesdi/demo", "--pr", "58")
    assert p.returncode == 0, p.output
    assert json.loads(p.stdout) == {"method": "merge"}
    assert merge(w, tmp_path, "a1b2c3d", "--judge", "agree").returncode == 0
    assert w.github()["merges"][0]["method"] == "merge"


def test_merge_method_is_rebase_for_a_linear_branch(tmp_path):
    w = world(tmp_path)
    p = cli(w, "merge-method", "--repo", "jesdi/demo", "--pr", "58")
    assert json.loads(p.stdout) == {"method": "rebase"}


def test_squash_policy_keeps_the_title_summary_and_block_digest(tmp_path):
    w = world(tmp_path)
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree",
              policy={"agentMerge": True, "mergeMethod": "squash"})
    assert p.returncode == 0, p.output
    [m] = w.github()["merges"]
    assert m["method"] == "squash"
    assert m["match"] == "a1b2c3d"
    assert m["subject"] == "feat: parse empty rows (#58)"
    assert "Parse rows that are empty." in m["body"]
    assert "merge-risk v1: two-way, isolated, touches [logic, tests], rollback revert" in m["body"]


def test_required_approval_failure_hands_off(tmp_path):
    w = world(tmp_path, merge_error="At least 1 approving review is required by reviewers "
                                    "with write access.")
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode != 0
    [h] = handoffs(w)
    assert h.startswith("## 🧑 Needs human: merge blocked: approval required")
    assert "needs-human" in w.github()["labels"]
    assert w.github().get("merges", []) == []


def test_handoff_comment_label_and_resume_command(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting"])  # needs-human does not exist yet
    (tmp_path / "fixlog.md").write_text("- round 1: fixed test_empty\n- round 2: fixed lint\n")
    p = cli(w, "handoff", "--repo", "jesdi/demo", "--pr", "59", "--reason", "door one-way",
            "--ci", "green", "--rounds", "2", "--gate", "gate: human (door one-way)",
            "--judge", "judge: not run", "--fix-log", str(tmp_path / "fixlog.md"))
    assert p.returncode == 0, p.output
    [h] = handoffs(w)
    assert h.startswith("## 🧑 Needs human: door one-way")
    for part in ("CI green · 2 fix rounds", "gate: human (door one-way)", "judge: not run",
                 "- round 1: fixed test_empty", "- round 2: fixed lint", f"/babysit-pr {URL}"):
        assert part in h
    labels = w.github()["labels"]
    assert "needs-human" in labels and "babysitting" not in labels
    assert "needs-human" in w.github()["repo_labels"]
    assert w.unsupported() == []


def test_owner_approval_of_the_head_merges_without_the_judge(tmp_path):
    w = world(tmp_path)
    p = merge(w, tmp_path, "a1b2c3d", "--approved-sha", "a1b2c3d", pr="59")
    assert p.returncode == 0, p.output
    [m] = w.github()["merges"]
    assert (m["number"], m["match"], m["auto"]) == ("59", "a1b2c3d", False)
    assert handoffs(w) == []


def test_owner_approval_of_a_sha_prefix_merges(tmp_path):
    w = world(tmp_path, head="a1b2c3d4e5f6")
    p = merge(w, tmp_path, "a1b2c3d4e5f6", "--approved-sha", "a1b2c3d", pr="59")
    assert p.returncode == 0, p.output
    assert w.github()["merges"][0]["match"] == "a1b2c3d4e5f6"


def test_approval_voided_by_a_new_commit_hands_off_with_the_new_commits(tmp_path):
    w = world(tmp_path, head="e4f5a6b", commits=[
        commit("a1b2c3d", "feat: parse empty rows"),
        commit("e4f5a6b", "fix(ci): repair test_empty (babysit-pr)", author="babysitter-bot")])
    p = merge(w, tmp_path, "e4f5a6b", "--approved-sha", "a1b2c3d", pr="59")
    assert p.returncode != 0
    assert w.github().get("merges", []) == []
    [h] = handoffs(w)
    assert h.startswith("## 🧑 Needs human: approval was for a1b2c3d")
    assert "e4f5a6b" in h and "fix(ci): repair test_empty (babysit-pr)" in h
    assert "babysitter-bot" in h
    assert "https://github.com/jesdi/demo/compare/a1b2c3d...e4f5a6b" in h
    assert "needs-human" in w.github()["labels"]


def test_approval_voided_by_a_rebase_hands_off(tmp_path):
    w = world(tmp_path, head="b7c8d9e", commits=[commit("b7c8d9e")])
    p = merge(w, tmp_path, "b7c8d9e", "--approved-sha", "a1b2c3d", pr="59")
    assert p.returncode != 0
    assert w.github().get("merges", []) == []
    [h] = handoffs(w)
    assert h.startswith("## 🧑 Needs human: approval was for a1b2c3d")
    assert "https://github.com/jesdi/demo/compare/a1b2c3d...b7c8d9e" in h


@pytest.mark.parametrize("method", [None, "squash", "merge", "rebase"])
@pytest.mark.parametrize("extra", [("--judge", "agree"), ("--approved-sha", "a1b2c3d")])
def test_auto_merge_is_never_requested(tmp_path, extra, method):
    w = world(tmp_path)
    policy = {"agentMerge": True, "mergeMethod": method} if method else None
    assert merge(w, tmp_path, "a1b2c3d", *extra, policy=policy).returncode == 0
    [m] = w.github()["merges"]
    assert m["auto"] is False
    assert method is None or m["method"] == method
    assert not any("--auto" in argv for argv in w.calls())
