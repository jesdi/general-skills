"""Which tracked Python files are tests or source, and how many lines they hold."""
import re
import subprocess

TEST_FILE = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|conftest\.py$)")


def count_lines(repo):
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=repo, capture_output=True, text=True, check=True).stdout.split()
    counts = {"test_files": 0, "test_lines": 0, "source_lines": 0}
    for rel in files:
        lines = len((repo / rel).read_text(errors="replace").splitlines())
        if TEST_FILE.search(rel):
            counts["test_files"] += 1
            counts["test_lines"] += lines
        else:
            counts["source_lines"] += lines
    return counts
