"""Fixture repos for ticket tw/s6-brittleness/12: the default repo with its tests replaced and a scripted git history.

Snapshot interface the tests pin (the spec leaves the JSON keys open):
  test_files {<repo-relative test file>: {sources: [repo-relative paths of its mapped source files, sorted],
                                          brittle: true | false | null (unmeasured)}}
  metrics.brittle_test_files {value: % of mapped test files that are brittle, score}
  commit: null or "" when the target is not a git repository.
Every body below is straight-line statements inside one function, so the test (not the import) covers each line.
"""
import shutil
import subprocess

from fixture_repo import GIT, build_repo, git


def body(name, n):
    return f"def run():\n" + "".join(f"    {name}{i} = {i}\n" for i in range(n))


def commit(repo, message, touch=(), extra=()):
    """Append a comment to each file in `touch` (never a covered line) and commit; `extra` files are new."""
    for rel in touch:
        with (repo / rel).open("a") as out:
            out.write(f"# {message}\n")
    for rel in extra:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(message)
    subprocess.run([*GIT, "add", "-A", "--", *touch, *extra], cwd=repo, check=True, capture_output=True)
    subprocess.run([*GIT, "commit", "-q", "-m", message], cwd=repo, check=True, capture_output=True)


def suite(path, modules, test_file, test_source):
    """Default repo with its demo modules and tests replaced by `modules` {rel: text} and one test file."""
    repo = build_repo(path)
    for old in [*(repo / "tests").glob("test_*.py"), *(repo / "demo").glob("[!_]*.py")]:
        old.unlink()
    for rel, text in {**modules, test_file: test_source}.items():
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "suite")
    return repo


CLAIMS = "demo/claims.py"
TEST = "tests/test_claims.py"


def claims_repo(path):
    """test_claims covers ~82 lines of claims.py and ~18 of other.py (other.py is under 20%, so not mapped)."""
    return suite(
        path,
        {CLAIMS: body("c", 82), "demo/other.py": body("o", 18)},
        TEST,
        "from demo import claims, other\n\n\ndef test_claims():\n    claims.run()\n    other.run()\n",
    )


def history(repo, both, claims_only):
    """After the suite commit (which changed both files): `both` more commits changing claims.py and the test, then `claims_only` changing claims.py alone."""
    for i in range(both):
        commit(repo, f"both {i}", [CLAIMS, TEST])
    for i in range(claims_only):
        commit(repo, f"claims {i}", [CLAIMS])
    return repo


def board_repo(path):
    """test_board covers 300 lines: 260 in board.py and 40 in config.py (13%)."""
    return suite(
        path,
        {"demo/board.py": body("b", 260), "demo/config.py": body("g", 40)},
        "tests/test_board.py",
        "from demo import board, config\n\n\ndef test_board():\n    board.run()\n    config.run()\n",
    )


def without_git(repo):
    shutil.rmtree(repo / ".git")
    return repo
