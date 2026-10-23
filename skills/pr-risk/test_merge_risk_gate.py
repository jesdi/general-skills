"""Acceptance tests for `merge_risk.py gate --body <file> --base <ref> --head <sha>`.

Each test builds a temp git repo: `origin/<base>` is a remote-tracking ref that holds the
policy, the head commit is the PR. The gate prints one JSON object on stdout:
`verdict` ("auto" | "human"), `reasons` (every reason that made it human),
`categories` (categories of the diff) and `countedLines`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("merge_risk.py").resolve()
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

POLICY = ".merge-risk.json"


def git(repo, *args):
    p = subprocess.run(["git", *args], cwd=repo, env=ENV, text=True,
                       capture_output=True, check=True)
    return p.stdout.strip()


def write(repo, files):
    for name, content in files.items():
        f = Path(repo) / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)


def commit(repo, files, msg="c"):
    write(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path, base_files, head_files, base="main"):
    """Base commit published as origin/<base>; head commit on top of it. Returns (repo, sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "work")
    base_sha = commit(repo, base_files or {"README.md": "hi\n"}, "base")
    git(repo, "update-ref", f"refs/remotes/origin/{base}", base_sha)
    return repo, commit(repo, head_files, "head")


def policy(**kw):
    return {POLICY: json.dumps(kw)}


def block(door="two-way", touches="docs", rollback="revert", verdict="auto"):
    return (
        "## Summary\n\nText.\n\n## Merge Danger\n\n```merge-risk v1\n"
        f"door: {door}\nblast_radius: isolated\ntouches: [{touches}]\n"
        f"rollback: {rollback}\nverdict: {verdict}\n"
        'reasons:\n  - "Small and reversible"\n```\n'
    )


def gate(tmp_path, repo, sha, body, base="main"):
    f = tmp_path / "body.md"
    f.write_text(body)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "gate", "--body", str(f), "--base", base, "--head", sha],
        cwd=repo, env=ENV, text=True, capture_output=True, timeout=60)
    assert proc.returncode == 0, f"gate exited {proc.returncode}: {proc.stderr or proc.stdout}"
    return json.loads(proc.stdout)


def test_no_block_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True), {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, "## Summary\n\nNo block here.\n")
    assert out["verdict"] == "human"
    assert "no merge-risk block" in out["reasons"]


def test_repo_paths_add_to_defaults(tmp_path):
    repo, sha = make_repo(
        tmp_path, policy(agentMerge=True, paths={"money": ["src/billing/**"]}),
        {"src/billing/invoice.py": "x\n", "db/migrations/0042_add.sql": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="money, schema/migration"))
    assert sorted(out["categories"]) == ["money", "schema/migration"]


def test_repo_paths_extend_default_globs_of_existing_category(tmp_path):
    repo, sha = make_repo(
        tmp_path, policy(agentMerge=True, paths={"docs": ["notes/**"]}),
        {"docs/a.md": "x\n", "notes/b.txt": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="docs"))
    assert out["categories"] == ["docs"]
    assert out["verdict"] == "auto"


def test_repo_always_human_replaces_defaults(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True, alwaysHuman=["money"]),
                          {"Dockerfile": "FROM x\n"})
    out = gate(tmp_path, repo, sha, block(touches="infra"))
    assert out["verdict"] == "auto"
    assert out["reasons"] == []


def test_pr_cannot_loosen_its_own_policy(tmp_path):
    repo, sha = make_repo(
        tmp_path, policy(agentMerge=True),
        {"db/migrations/0042_add.sql": "x\n", **policy(agentMerge=True, alwaysHuman=[])})
    out = gate(tmp_path, repo, sha, block(touches="schema/migration, config"))
    assert out["verdict"] == "human"
    assert "schema/migration is always human" in out["reasons"]
    assert "config: .merge-risk.json changed" in out["reasons"]


def test_first_policy_file_is_not_used_by_its_own_pr(tmp_path):
    repo, sha = make_repo(tmp_path, {"README.md": "hi\n"}, policy(agentMerge=True))
    out = gate(tmp_path, repo, sha, block(touches="config"))
    assert out["verdict"] == "human"
    assert "agentMerge is off" in out["reasons"]


def test_unknown_policy_key_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True, autoMerge=True),
                          {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, block())
    assert out["verdict"] == "human"
    assert "policy: unknown key autoMerge" in out["reasons"]


def test_skill_md_is_logic_not_docs(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True),
                          {"skills/pr-risk/SKILL.md": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="docs"))
    assert out["verdict"] == "human"
    assert "diff touches logic, not in touches" in out["reasons"]


def test_instruction_file_under_docs_counts_as_logic_with_its_lines(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True, judgeMaxLines=3),
                          {"docs/SKILL.md": "a\nb\nc\nd\ne\n"})
    out = gate(tmp_path, repo, sha, block(touches="docs, logic"))
    assert out["categories"] == ["logic"]
    assert out["countedLines"] == 5
    assert out["verdict"] == "human"


def test_one_way_door_is_human_whatever_verdict_says(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True), {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, block(door="one-way", verdict="auto"))
    assert out["verdict"] == "human"
    assert "door one-way" in out["reasons"]


def test_rollback_other_than_revert_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True), {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, block(rollback="revert+redeploy"))
    assert out["verdict"] == "human"


def test_undeclared_migration_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True),
                          {"src/app.py": "x\n", "tests/test_app.py": "x\n",
                           "db/migrations/0042_add.sql": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="logic, tests"))
    assert out["verdict"] == "human"
    assert "diff touches schema/migration, not in touches" in out["reasons"]
    assert "schema/migration is always human" in out["reasons"]


def test_always_human_category_declared_only_in_body_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True), {"src/app.py": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="logic, auth/security"))
    assert out["verdict"] == "human"
    assert "auth/security is always human" in out["reasons"]


def test_agent_merge_false_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=False), {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, block())
    assert out["verdict"] == "human"
    assert "agentMerge is off" in out["reasons"]


def counted_diff(n):
    return {"src/app.py": "x\n" * n, "pnpm-lock.yaml": "l\n" * 4000,
            "docs/guide.md": "d\n" * 900}


def test_counted_lines_at_the_limit_is_auto(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True), counted_diff(1500))
    out = gate(tmp_path, repo, sha, block(touches="deps, docs, logic"))
    assert out["countedLines"] == 1500
    assert out["verdict"] == "auto"


def test_counted_lines_over_the_limit_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True), counted_diff(1501))
    out = gate(tmp_path, repo, sha, block(touches="deps, docs, logic"))
    assert out["verdict"] == "human"
    assert "1501 counted lines > 1500" in out["reasons"]


def test_stacked_base_uses_origin_parent_never_main(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "work")
    main_sha = commit(repo, {"README.md": "hi\n"}, "main")  # main: no policy
    git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    parent_sha = commit(repo, {**policy(agentMerge=True), "db/migrations/0001.sql": "x\n"},
                        "parent")
    git(repo, "update-ref", "refs/remotes/origin/feat/parent", parent_sha)
    sha = commit(repo, {"docs/a.md": "x\n"}, "head")
    out = gate(tmp_path, repo, sha, block(), base="feat/parent")
    assert out["categories"] == ["docs"]
    assert out["verdict"] == "auto"
