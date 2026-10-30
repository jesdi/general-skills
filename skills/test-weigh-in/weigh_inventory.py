"""Which tracked Python files are tests or source, and how many lines they hold."""
import re
import subprocess

TEST_FILE = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|conftest\.py$)")


def count_lines(repo):
    # -z: names with spaces or non-ASCII characters come through unquoted
    out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=repo, capture_output=True, text=True, check=True).stdout
    files = [rel for rel in out.split("\0") if rel]
    counts = {"test_files": 0, "test_lines": 0, "source_lines": 0}
    for rel in files:
        lines = len((repo / rel).read_text(errors="replace").splitlines())
        if TEST_FILE.search(rel):
            counts["test_files"] += 1
            counts["test_lines"] += lines
        else:
            counts["source_lines"] += lines
    return counts
