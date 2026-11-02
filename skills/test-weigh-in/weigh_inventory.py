"""Which tracked Python files are tests or source, and how many lines they hold."""
import os
import re
import subprocess
from fnmatch import fnmatchcase
from posixpath import split

# The one definition of a test file: any file under a folder of TEST_DIRS, or a file whose name matches TEST_NAMES.
TEST_DIRS = ("tests", "test")
TEST_NAMES = ("test_*.py", "*_test.py", "conftest.py")
# The same, as glob patterns relative to any folder, for coverage's --omit.
TEST_GLOBS = tuple(f"{d}/**" for d in TEST_DIRS) + TEST_NAMES


# git's message for a folder with no repository above it; a broken .git file says "not a git repository: <path>"
NO_REPOSITORY = re.compile(r"not a git repository \(or any (of the )?parent")


def is_test(rel):
    """A repo-relative posix path is a test file."""
    folder, name = split(rel)
    return any(d in TEST_DIRS for d in folder.split("/")) or any(fnmatchcase(name, n) for n in TEST_NAMES)


def python_files(repo):
    """Tracked Python files of a git work tree; in a folder git finds no repository above, every one outside hidden folders.

    Any other git failure (an unreadable index, an unsafe directory) raises: it never falls back to the untracked files."""
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo, capture_output=True, text=True,
                           env={**os.environ, "LC_ALL": "C"})
    if probe.returncode and NO_REPOSITORY.search(probe.stderr):
        rels = (p.relative_to(repo).as_posix() for p in repo.rglob("*.py"))
        return sorted(r for r in rels if not any(x.startswith(".") or x in ("site-packages", "__pycache__") for x in r.split("/")))
    # -z: names with spaces or non-ASCII characters come through unquoted
    out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=repo, capture_output=True, text=True, check=True).stdout
    return [rel for rel in out.split("\0") if rel]


def count_lines(repo):
    files = python_files(repo)
    counts = {"test_files": 0, "test_lines": 0, "source_lines": 0}
    for rel in files:
        lines = len((repo / rel).read_text(errors="replace").splitlines())
        if is_test(rel):
            counts["test_files"] += 1
            counts["test_lines"] += lines
        else:
            counts["source_lines"] += lines
    return counts
