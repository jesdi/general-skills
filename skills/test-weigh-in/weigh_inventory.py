"""Which tracked Python files are tests or source, and how many lines they hold."""
import subprocess
from fnmatch import fnmatchcase
from posixpath import split

# The one definition of a test file: any file under a folder of TEST_DIRS, or a file whose name matches TEST_NAMES.
TEST_DIRS = ("tests", "test")
TEST_NAMES = ("test_*.py", "*_test.py", "conftest.py")
# The same, as glob patterns relative to any folder, for coverage's --omit.
TEST_GLOBS = tuple(f"{d}/**" for d in TEST_DIRS) + TEST_NAMES


def is_test(rel):
    """A repo-relative posix path is a test file."""
    folder, name = split(rel)
    return any(d in TEST_DIRS for d in folder.split("/")) or any(fnmatchcase(name, n) for n in TEST_NAMES)


def count_lines(repo):
    # -z: names with spaces or non-ASCII characters come through unquoted
    out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=repo, capture_output=True, text=True, check=True).stdout
    files = [rel for rel in out.split("\0") if rel]
    counts = {"test_files": 0, "test_lines": 0, "source_lines": 0}
    for rel in files:
        lines = len((repo / rel).read_text(errors="replace").splitlines())
        if is_test(rel):
            counts["test_files"] += 1
            counts["test_lines"] += lines
        else:
            counts["source_lines"] += lines
    return counts
