"""Acceptance tests for `babysit.py update-plan --base <ref>`.

Interface under test (run with the PR branch checked out, cwd = the clone):
  babysit.py update-plan --base <ref>
Prints one JSON object on stdout, exit 0:
  plan     "rebase" | "rebase-with-conflict" | "merge"
  compare  the ref compared against, e.g. "origin/main"
  push     the push command: rebase plans use
           `git push --force-with-lease=<branch>:<old sha> origin <branch>`;
           the merge plan pushes without any force flag
  files    conflicting files (rebase-with-conflict only)
It never changes the worktree, the index, HEAD or any ref.
"""
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("babysit.py").resolve()
BRANCH = "feat/x"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                               "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                               "HOME": str(cwd)}).stdout.strip()


def commit(cwd, path, text, msg):
    f = Path(cwd) / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    git(cwd, "add", path)
    git(cwd, "commit", "-m", msg)


class Repo:
    """origin (bare) + clone `work`, with main history that already touched src/parse.py twice."""

    def __init__(self, tmp):
        self.origin = tmp / "origin.git"
        self.work = tmp / "work"
        git(tmp, "init", "--bare", "-b", "main", str(self.origin))
        git(tmp, "clone", str(self.origin), str(self.work))
        git(self.work, "checkout", "-b", "main")
        commit(self.work, "src/parse.py", "a\nb\nc\n", "old 1")
        commit(self.work, "src/parse.py", "a\nb\nc\nd\n", "old 2")
        git(self.work, "push", "origin", "main")
        git(self.work, "checkout", "-b", BRANCH)

    def branch_commits(self, n, path="src/parse.py"):
        for i in range(n):
            commit(self.work, path, f"X{i}\nb\nc\nd\n", f"branch {i}")
        git(self.work, "push", "origin", BRANCH)

    def advance(self, branch, n, path="other.txt", text=None):
        git(self.work, "checkout", branch)
        for i in range(n):
            commit(self.work, path, text or f"base {i}\n", f"{branch} {i}")
        git(self.work, "push", "origin", branch)
        git(self.work, "checkout", BRANCH)

    def plan(self, base="main"):
        git(self.work, "fetch", "origin")
        r = subprocess.run(["python3", str(SCRIPT), "update-plan", "--base", base],
                           cwd=self.work, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return json.loads(r.stdout)


def test_new_base_commits_without_conflict_plan_rebase(tmp_path):
    r = Repo(tmp_path)
    r.branch_commits(1)
    r.advance("main", 3)
    old = git(r.work, "rev-parse", "HEAD")
    p = r.plan()
    assert p["plan"] == "rebase"
    assert p["push"] == f"git push --force-with-lease={BRANCH}:{old} origin {BRANCH}"


def test_conflict_in_two_branch_commits_plans_rebase_with_conflict(tmp_path):
    r = Repo(tmp_path)
    r.branch_commits(2)
    r.advance("main", 1, "src/parse.py", "M\nb\nc\nd\n")
    p = r.plan()
    assert p["plan"] == "rebase-with-conflict"
    assert "src/parse.py" in p["files"]
    assert "--force-with-lease=" in p["push"]


def test_conflict_in_three_branch_commits_plans_merge(tmp_path):
    r = Repo(tmp_path)
    r.branch_commits(3)
    r.advance("main", 1, "src/parse.py", "M\nb\nc\nd\n")
    p = r.plan()
    assert p["plan"] == "merge"
    assert p["compare"] == "origin/main"
    assert "force" not in p["push"]
    assert p["push"].endswith(f"origin {BRANCH}")


def test_update_plan_leaves_worktree_index_and_head_unchanged(tmp_path):
    r = Repo(tmp_path)
    r.branch_commits(2)
    r.advance("main", 1, "src/parse.py", "M\nb\nc\nd\n")
    (r.work / "src/parse.py").write_text("dirty\n")
    (r.work / "untracked.txt").write_text("u\n")
    (r.work / "staged.txt").write_text("s\n")
    git(r.work, "add", "staged.txt")
    git(r.work, "fetch", "origin")
    before = (git(r.work, "status", "--porcelain"), git(r.work, "rev-parse", "HEAD"),
              git(r.work, "for-each-ref"), git(r.work, "diff", "--cached"),
              (r.work / "src/parse.py").read_text())
    p = r.plan()
    assert p["plan"] == "rebase-with-conflict"
    after = (git(r.work, "status", "--porcelain"), git(r.work, "rev-parse", "HEAD"),
             git(r.work, "for-each-ref"), git(r.work, "diff", "--cached"),
             (r.work / "src/parse.py").read_text())
    assert after == before
    assert not (r.work / ".git" / "MERGE_HEAD").exists()
    assert not (r.work / ".git" / "rebase-merge").exists()


def test_base_feat_parent_compares_with_origin_feat_parent(tmp_path):
    r = Repo(tmp_path)
    r.branch_commits(1)
    # main would conflict with the branch; the parent does not
    r.advance("main", 1, "src/parse.py", "M\nb\nc\nd\n")
    git(r.work, "checkout", "-b", "feat/parent", "origin/main~1")
    git(r.work, "push", "origin", "feat/parent")
    git(r.work, "checkout", BRANCH)
    r.advance("feat/parent", 1)
    p = r.plan("feat/parent")
    assert p["compare"] == "origin/feat/parent"
    assert p["plan"] == "rebase"


def test_commits_across_two_conflicting_files_are_counted_as_a_union(tmp_path):
    r = Repo(tmp_path)
    git(r.work, "checkout", "main")
    commit(r.work, "a.py", "a\nb\nc\n", "add a")
    commit(r.work, "b.py", "a\nb\nc\n", "add b")
    git(r.work, "push", "origin", "main")
    git(r.work, "checkout", BRANCH)
    git(r.work, "merge", "--ff-only", "main")
    commit(r.work, "a.py", "A1\nb\nc\n", "branch a1")
    commit(r.work, "a.py", "A2\nb\nc\n", "branch a2")
    commit(r.work, "b.py", "B1\nb\nc\n", "branch b1")
    git(r.work, "push", "origin", BRANCH)
    git(r.work, "checkout", "main")
    commit(r.work, "a.py", "M\nb\nc\n", "main a")
    commit(r.work, "b.py", "M\nb\nc\n", "main b")
    git(r.work, "push", "origin", "main")
    git(r.work, "checkout", BRANCH)
    p = r.plan()
    assert p["plan"] == "merge"
