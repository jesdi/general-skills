"""Acceptance tests for `merge_risk.py gate --body <file> --base <ref> --head <sha>`.

Each test builds a temp git repo: `origin/<base>` is a remote-tracking ref that holds the
policy, the head commit is the PR. The gate prints one JSON object on stdout:
`verdict` ("auto" | "human"), `reasons` (every reason that made it human), `risk` (1-10),
`maxAutoRisk` (the limit that applied), `categories` (categories of the diff) and `countedLines`.
`gh` is a fake on PATH that answers `gh repo view` with $FAKE_PRIVATE ("true" | "false"); unset,
it fails, and the repo counts as public.
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


def block(door="two-way", touches="docs", rollback="revert", risk=1, blast="isolated"):
    return (
        "## Summary\n\nText.\n\n## Merge Danger\n\n```merge-risk v1\n"
        f"door: {door}\nblast_radius: {blast}\ntouches: [{touches}]\n"
        f"rollback: {rollback}\nrisk: {risk}\n"
        'reasons:\n  - "Small and reversible"\n```\n'
    )


FAKE_GH = """#!/bin/sh
[ "$1 $2" = "repo view" ] && [ -n "$FAKE_PRIVATE" ] && echo "$FAKE_PRIVATE" && exit 0
exit 1
"""


def gate(tmp_path, repo, sha, body, base="main", private=None):
    f = tmp_path / "body.md"
    f.write_text(body)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    env = {**ENV, "PATH": f"{bin_dir}{os.pathsep}{ENV['PATH']}"}
    if private is not None:
        env["FAKE_PRIVATE"] = "true" if private else "false"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "gate", "--body", str(f), "--base", base, "--head", sha],
        cwd=repo, env=env, text=True, capture_output=True, timeout=60)
    assert proc.returncode == 0, f"gate exited {proc.returncode}: {proc.stderr or proc.stdout}"
    return json.loads(proc.stdout)


def test_no_block_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=3), {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, "## Summary\n\nNo block here.\n")
    assert out["verdict"] == "human"
    assert "no merge-risk block" in out["reasons"]


def test_repo_paths_add_to_defaults(tmp_path):
    repo, sha = make_repo(
        tmp_path, policy(maxAutoRisk=3, paths={"money": ["src/billing/**"]}),
        {"src/billing/invoice.py": "x\n", "db/migrations/0042_add.sql": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="money, schema/migration"))
    assert sorted(out["categories"]) == ["money", "schema/migration"]


def test_repo_paths_extend_default_globs_of_existing_category(tmp_path):
    repo, sha = make_repo(
        tmp_path, policy(maxAutoRisk=3, paths={"docs": ["notes/**"]}),
        {"docs/a.md": "x\n", "notes/b.txt": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="docs"))
    assert out["categories"] == ["docs"]
    assert out["verdict"] == "auto"


def test_repo_category_risk_overrides_one_default(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=8, categoryRisk={"infra": 5}),
                          {"Dockerfile": "FROM x\n"})
    out = gate(tmp_path, repo, sha, block(touches="infra"), private=True)
    assert out["risk"] == 5
    assert out["verdict"] == "auto"
    assert out["reasons"] == []


def test_old_policy_keys_name_their_replacement(tmp_path):
    repo, sha = make_repo(tmp_path, policy(agentMerge=True, alwaysHuman=["money"]),
                          {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, block())
    assert out["verdict"] == "human"
    assert "policy: agentMerge was replaced by maxAutoRisk" in out["reasons"]
    assert "policy: alwaysHuman was replaced by categoryRisk" in out["reasons"]


def test_pr_cannot_loosen_its_own_policy(tmp_path):
    repo, sha = make_repo(
        tmp_path, policy(maxAutoRisk=3),
        {"db/migrations/0042_add.sql": "x\n", **policy(maxAutoRisk=10)})
    out = gate(tmp_path, repo, sha, block(touches="schema/migration, config"), private=True)
    assert out["verdict"] == "human"
    assert "risk 9 > max auto risk 3" in out["reasons"]
    assert "config: .merge-risk.json changed" in out["reasons"]


def test_first_policy_file_is_not_used_by_its_own_pr(tmp_path):
    repo, sha = make_repo(tmp_path, {"README.md": "hi\n"}, policy(maxAutoRisk=3))
    out = gate(tmp_path, repo, sha, block(touches="config"))
    assert out["verdict"] == "human"
    assert "policy: no .merge-risk.json on the base" in out["reasons"]


def test_unknown_policy_key_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=3, autoMerge=True),
                          {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, block())
    assert out["verdict"] == "human"
    assert "policy: unknown key autoMerge" in out["reasons"]


def test_skill_md_is_logic_not_docs(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=3),
                          {"skills/pr-risk/SKILL.md": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="docs"))
    assert out["verdict"] == "human"
    assert "diff touches logic, not in touches" in out["reasons"]


def test_instruction_file_under_docs_counts_as_logic_with_its_lines(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=3, judgeMaxLines=3),
                          {"docs/SKILL.md": "a\nb\nc\nd\ne\n"})
    out = gate(tmp_path, repo, sha, block(touches="docs, logic"))
    assert out["categories"] == ["logic"]
    assert out["countedLines"] == 5
    assert out["verdict"] == "human"


def test_one_way_door_is_human_at_any_risk_and_limit(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=10), {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, block(door="one-way", risk=1), private=True)
    assert out["verdict"] == "human"
    assert out["reasons"] == ["door one-way"]


def test_rollback_that_cannot_revert_is_one_way(tmp_path):
    for rollback in ("data-fix", "impossible"):
        sub = tmp_path / rollback
        sub.mkdir()
        repo, sha = make_repo(sub, policy(maxAutoRisk=10), {"docs/a.md": "x\n"})
        out = gate(sub, repo, sha, block(rollback=rollback), private=True)
        assert out["reasons"] == [f"rollback {rollback} is one-way"]


def test_risk_floor_adds_blast_radius_and_redeploy(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=10), {"src/app.py": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="logic", blast="service",
                                          rollback="revert+redeploy"), private=True)
    assert out["risk"] == 4 + 2 + 1
    assert out["verdict"] == "auto"


def test_block_risk_raises_the_floor_but_never_lowers_it(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=5), {"src/app.py": "x\n"})
    assert gate(tmp_path, repo, sha, block(touches="logic", risk=6), private=True)["risk"] == 6
    out = gate(tmp_path, repo, sha, block(touches="logic", risk=1), private=True)
    assert out["risk"] == 4
    assert out["verdict"] == "auto"


def test_risk_above_the_repo_limit_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=3), {"src/app.py": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="logic"), private=True)
    assert out["verdict"] == "human"
    assert out["reasons"] == ["risk 4 > max auto risk 3"]


def test_public_repo_caps_the_limit_at_3(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=8), {"src/app.py": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="logic"), private=False)
    assert out["maxAutoRisk"] == 3
    assert out["reasons"] == ["risk 4 > max auto risk 3 (public repo)"]


def test_unknown_visibility_counts_as_public(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=8), {"src/app.py": "x\n"})
    assert gate(tmp_path, repo, sha, block(touches="logic"))["maxAutoRisk"] == 3


def test_limit_defaults_to_3_when_the_policy_sets_none(tmp_path):
    repo, sha = make_repo(tmp_path, policy(), {"src/app.py": "x\n"})
    assert gate(tmp_path, repo, sha, block(touches="logic"), private=True)["maxAutoRisk"] == 3


def test_invalid_risk_is_an_invalid_block(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=3), {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, block(risk=11))
    assert "invalid block: risk: '11' is not a whole number from 1 to 10" in out["reasons"]
    assert out["risk"] == 10


def test_undeclared_migration_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=3),
                          {"src/app.py": "x\n", "tests/test_app.py": "x\n",
                           "db/migrations/0042_add.sql": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="logic, tests"))
    assert out["verdict"] == "human"
    assert "diff touches schema/migration, not in touches" in out["reasons"]


def test_category_declared_only_in_body_counts_toward_risk(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=9), {"src/app.py": "x\n"})
    out = gate(tmp_path, repo, sha, block(touches="logic, auth/security"), private=True)
    assert out["risk"] == 10
    assert out["reasons"] == ["risk 10 > max auto risk 9"]


def test_max_auto_risk_0_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=0), {"docs/a.md": "x\n"})
    out = gate(tmp_path, repo, sha, block())
    assert out["verdict"] == "human"
    assert "risk 1 > max auto risk 0" in out["reasons"]


def counted_diff(n):
    return {"src/app.py": "x\n" * n, "pnpm-lock.yaml": "l\n" * 4000,
            "docs/guide.md": "d\n" * 900}


def test_counted_lines_at_the_limit_is_auto(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=5), counted_diff(1500))
    out = gate(tmp_path, repo, sha, block(touches="deps, docs, logic"), private=True)
    assert out["countedLines"] == 1500
    assert out["verdict"] == "auto"


def test_counted_lines_over_the_limit_is_human(tmp_path):
    repo, sha = make_repo(tmp_path, policy(maxAutoRisk=3), counted_diff(1501))
    out = gate(tmp_path, repo, sha, block(touches="deps, docs, logic"))
    assert out["verdict"] == "human"
    assert "1501 counted lines > 1500" in out["reasons"]


def test_stacked_base_uses_origin_parent_never_main(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "work")
    main_sha = commit(repo, {"README.md": "hi\n"}, "main")  # main: no policy
    git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    parent_sha = commit(repo, {**policy(maxAutoRisk=3), "db/migrations/0001.sql": "x\n"},
                        "parent")
    git(repo, "update-ref", "refs/remotes/origin/feat/parent", parent_sha)
    sha = commit(repo, {"docs/a.md": "x\n"}, "head")
    out = gate(tmp_path, repo, sha, block(), base="feat/parent")
    assert out["categories"] == ["docs"]
    assert out["verdict"] == "auto"
