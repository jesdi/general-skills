"""Gate regressions exercised through its CLI in temporary repositories."""
import pytest

from test_merge_risk_gate import block, commit, gate, git, make_repo, policy, write


@pytest.mark.parametrize("pattern", ["src/billing[12]/**", "src/billing[!3]/**"])
def test_unsupported_policy_glob_requires_human(tmp_path, pattern):
    repo, sha = make_repo(
        tmp_path, policy(agentMerge=True, paths={"money": [pattern]}),
        {"src/billing1/invoice.py": "amount = 1\n"})
    out = gate(tmp_path, repo, sha, block(touches="logic"))
    assert out["verdict"] == "human"
    assert "policy: unsupported glob syntax in paths" in out["reasons"]


@pytest.mark.parametrize("was_generated", [False, True])
def test_deleted_file_uses_merge_base_marker_and_base_tip_policy(tmp_path, was_generated):
    repo, _ = make_repo(
        tmp_path,
        {**policy(agentMerge=False),
         "src/app.py": ("# @generated\n" if was_generated else "") + "x\n" * 1501},
        {"README.md": "hi\n"})
    ancestor = git(repo, "rev-parse", "HEAD")
    (repo / "src/app.py").unlink()
    sha = commit(repo, {}, "PR deletes app")
    # Build an independent base commit without moving the PR's checked-out branch.
    write(repo, {**policy(agentMerge=True),
                 "src/app.py": ("" if was_generated else "# @generated\n") + "x\n" * 1501})
    git(repo, "add", "-A")
    tree = git(repo, "write-tree")
    base_tip = git(repo, "commit-tree", tree, "-p", ancestor, "-m", "base changes marker")
    git(repo, "update-ref", "refs/remotes/origin/main", base_tip)
    out = gate(tmp_path, repo, sha, block(touches="logic"))
    assert "agentMerge is off" not in out["reasons"]
    assert out["countedLines"] == (0 if was_generated else 1501)
    assert out["verdict"] == ("auto" if was_generated else "human")
