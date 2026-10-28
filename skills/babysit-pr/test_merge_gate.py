"""Acceptance tests: `babysit.py merge` decides from the trusted gate and the owner's comment.

Seam: `babysit.py merge` and `babysit.py judge`, with the fake `gh` and a temporary git checkout
whose `origin/main` holds `.merge-risk.json` (see test_merge_and_handoff.py for the interface).
Spec requirements 7 (the gate) and 23 (the owner's `@babysitter merge <sha>` approval).
"""
import json

from test_merge_and_handoff import BLOCK, handoffs, merge, owner_says, world
from test_trusted_merge_risk import judge_world, run_judge, write_pr_risk

HEAD = "a1b2c3d4e5f6"
APPROVAL_MESSAGE = "merge approval must name a commit: @babysitter merge <sha>"


def merged(w):
    return w.github().get("merges", [])


def test_agent_merge_off_hands_off_with_the_policy_as_the_reason(tmp_path):
    w = world(tmp_path, policy={"agentMerge": False})
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode != 0
    assert merged(w) == []
    [h] = handoffs(w)
    assert "agentMerge" in h.splitlines()[0]
    assert not any("Merged by babysit-pr" in c["body"] for c in w.github()["comments"])


def test_one_way_door_hands_off_although_the_block_says_auto(tmp_path):
    assert "verdict: auto" in BLOCK
    w = world(tmp_path, body=BLOCK.replace("door: two-way", "door: one-way"))
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode != 0
    assert merged(w) == []
    [h] = handoffs(w)
    assert "door one-way" in h.splitlines()[0]


def test_approved_sha_without_an_owner_comment_approves_nothing(tmp_path):
    w = world(tmp_path)
    p = merge(w, tmp_path, "a1b2c3d", "--approved-sha", "a1b2c3d")
    assert p.returncode != 0
    assert merged(w) == []
    [h] = handoffs(w)
    assert h.startswith(f"## 🧑 Needs human: {APPROVAL_MESSAGE}")


def test_owner_comment_with_a_sha_prefix_merges_a_human_verdict_pinned_to_the_head(tmp_path):
    # the policy gives human, so only the owner's comment can merge; the caller passes the full SHA
    w = world(tmp_path, head=HEAD, policy={"agentMerge": False}, approvals=(HEAD[:7],))
    p = merge(w, tmp_path, HEAD, "--approved-sha", HEAD)
    assert p.returncode == 0, p.output
    [m] = merged(w)
    assert m["match"] == HEAD
    assert handoffs(w) == []


def test_non_owner_approval_approves_nothing(tmp_path):
    w = world(tmp_path, head=HEAD)
    owner_says(w, f"@babysitter merge {HEAD}", login="alice")
    p = merge(w, tmp_path, HEAD, "--approved-sha", HEAD)
    assert p.returncode != 0
    assert merged(w) == []
    assert len(handoffs(w)) == 1


def old_install(folder, missing):
    write_pr_risk(folder, folder.parent / "marker")
    with open(folder / "merge_risk.py", "a") as f:
        f.write("".join(f"\ndel {name}\n" for name in missing))


def test_judge_with_a_trusted_pr_risk_lacking_classify_diff_hands_off(tmp_path):
    j, home, script = judge_world(tmp_path)
    old_install(home / ".claude" / "skills" / "pr-risk", ["classify_diff"])
    p = run_judge(j, script, home)
    assert "pr-risk is too old: classify_diff" in p.stdout + p.stderr
    assert "AttributeError" not in p.stdout + p.stderr
    assert '"verdict": "agree"' not in p.stdout


def test_merge_with_a_trusted_pr_risk_lacking_classify_diff_hands_off(tmp_path):
    w = world(tmp_path, pr_risk=False)
    old_install(w.home / ".claude" / "skills" / "pr-risk", ["classify_diff"])
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode != 0
    assert "AttributeError" not in p.output
    assert merged(w) == []
    [h] = handoffs(w)
    assert "pr-risk is too old: classify_diff" in h.splitlines()[0]


def test_gate_reads_the_diff_of_the_sha_it_merges_not_the_checked_out_head(tmp_path):
    # the checkout sits on `work` (= origin/main, an empty diff); only HEAD's own commit adds a counted line
    w = world(tmp_path, policy={"agentMerge": True, "judgeMaxLines": 0})
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode != 0
    assert merged(w) == []
    [h] = handoffs(w)
    assert "counted lines" in h.splitlines()[0]


def live_body_differs(tmp_path, live, *extra):
    # the saved --body file holds the valid auto block; the PR itself says something else
    w = world(tmp_path)
    st = w.github()
    st["body"] = live
    w.state.write_text(json.dumps(st))
    return w, merge(w, tmp_path, "a1b2c3d", *extra)


def test_saved_body_does_not_stand_in_for_a_one_way_live_body(tmp_path):
    w, p = live_body_differs(tmp_path, BLOCK.replace("door: two-way", "door: one-way"), "--judge", "agree")
    assert p.returncode != 0
    assert merged(w) == []
    assert not any("Merged by babysit-pr" in c["body"] for c in w.github()["comments"])


def test_saved_body_does_not_stand_in_for_an_empty_live_body(tmp_path):
    w, p = live_body_differs(tmp_path, "", "--judge", "agree")
    assert p.returncode != 0
    assert merged(w) == []
    assert not any("Merged by babysit-pr" in c["body"] for c in w.github()["comments"])


def test_open_prs_into_the_head_branch_hand_off_and_nothing_merges(tmp_path):
    # the final PR of a feat/<name> cut-over: increments #57 and #58 still target its head branch
    w = world(tmp_path, head_ref="feat/new-cart",
              open_prs=[{"number": 57, "base": "feat/new-cart"}, {"number": 60, "base": "main"},
                        {"number": 61, "base": "feat/new-cart"}])
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode != 0
    assert merged(w) == []
    [h] = handoffs(w)
    assert h.splitlines()[0] == "## 🧑 Needs human: open PRs still target feat/new-cart: #57, #61"


def test_open_prs_into_other_branches_do_not_block_the_merge(tmp_path):
    w = world(tmp_path, head_ref="feat/new-cart", open_prs=[{"number": 60, "base": "main"}])
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode == 0, p.output
    assert len(merged(w)) == 1
