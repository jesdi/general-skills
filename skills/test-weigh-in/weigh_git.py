"""Brittle test files from git history: a test file that changed in over half the commits that changed its mapped sources."""
import re
import subprocess
from typing import NamedTuple

from weigh_inventory import is_test

# Spec requirement 20: the commit window and the big-commit cutoff (is_brittle holds the "more than half")
MAX_COMMITS = 200
MAX_FILES = 20
SINCE = "12 months ago"

RENAME = re.compile(r"R\d*")  # git's rename status, with its similarity score


class Commit(NamedTuple):
    sha: str
    date: int
    parents: list
    files: set
    renames: dict  # {old path: new path}, for a commit with one parent


def brittleness(repo, sources_by_test_file):
    """{test file: {"sources": [...], "brittle": True | False | None}}, None when unmeasured (no git, no mapped source)."""
    commits = source_commits(repo) if is_repo(repo) else None

    def brittle(test_file, sources):
        return is_brittle(commits, test_file, sources) if commits is not None and sources else None

    return {test_file: {"sources": sources, "brittle": brittle(test_file, sources)}
            for test_file, sources in sorted(sources_by_test_file.items())}


def brittle_percent(test_files):
    """% of the measured test files that are brittle, or None when none is measured."""
    measured = [info["brittle"] for info in test_files.values() if info["brittle"] is not None]
    return 100 * sum(measured) / len(measured) if measured else None


def is_brittle(commits, test_file, sources):
    """A test file is brittle when it changed in more than half of the commits that changed its sources: most edits to the
    code under test also needed an edit to the test, the sign of a test tied to how the code works rather than what it does."""
    touching = [files for files in commits if files & set(sources)]
    return 2 * sum(test_file in files for files in touching) > len(touching)


def is_repo(repo):
    return subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=repo, capture_output=True).returncode == 0


def source_commits(repo):
    """The files changed by each of the newest 200 commits, by date, of the last 12 months that change a source file and
    at most 20 files. Each file carries the name HEAD gives it, so a renamed source keeps its history."""
    log = _read_log(repo)
    merge_parents = [(commit.sha, parent) for commit in log if len(commit.parents) > 1 for parent in commit.parents]
    commits = [c for c in _with_head_names(log, _merge_renames(repo, merge_parents)) if _changes_source(c.files)]
    commits.sort(key=lambda commit: commit.date, reverse=True)  # stable: commits of the same second keep the log's order
    return [commit.files for commit in commits[:MAX_COMMITS]]


def _changes_source(files):
    return len(files) <= MAX_FILES and any(f.endswith(".py") and not is_test(f) for f in files)


def _read_log(repo):
    """The commits of the last 12 months, every child before its parents (--topo-order), with renames detected (-M).

    A merge lists only the files its resolution changed, those that differ from every parent (--cc): the changes it brings
    in from a branch are counted in that branch's own commits."""
    out = subprocess.run(["git", "log", "-M", "--cc", "--topo-order", f"--since={SINCE}", "--name-status", "-z",
                          "--format=%x01%H %ct %P"], cwd=repo, capture_output=True, text=True, check=True).stdout
    log = []
    for block in out.split("\x01")[1:]:
        header, *tokens = [t.strip() for t in block.split("\0") if t.strip()]
        sha, date, *parents = header.split()
        files, renames, tokens = set(), {}, iter(tokens)
        for status in tokens:
            path = next(tokens)
            if RENAME.fullmatch(status):  # only a one-parent diff has a rename; a merge's status has a letter per parent
                old, path = path, next(tokens)
                renames[old] = path
            files.add(path)
        log.append(Commit(sha, int(date), parents, files, renames))
    return log


def _with_head_names(log, merge_renames):
    """The commits of the log with each file under the name HEAD gives it.

    Each commit hands its parents a map {path at the parent: name at HEAD}, adding the renames between the two, so a
    rename is followed on every branch a merge brought past it, and a later file reusing the old name keeps its own."""
    handed_down = {}
    for commit in log:  # every child before its parents
        to_head = handed_down.pop(commit.sha, {})
        for parent in commit.parents:
            renames = commit.renames if len(commit.parents) == 1 else merge_renames[commit.sha, parent]
            parent_map = handed_down.setdefault(parent, {})
            for path, name in {**to_head, **{old: to_head.get(new, new) for old, new in renames.items()}}.items():
                parent_map.setdefault(path, name)
        yield commit._replace(files={to_head.get(f, f) for f in commit.files})


def _merge_renames(repo, merge_parents):
    """{(merge, parent): {old path: new path}}: the files renamed between each parent of a merge and the merge."""
    if not merge_parents:
        return {}
    # --always prints each pair's merge id, even with no rename, so the output follows the pairs in order
    out = subprocess.run(["git", "diff-tree", "--stdin", "--always", "-M", "-r", "-z", "--name-status", "--diff-filter=R"],
                         cwd=repo, input="".join(f"{merge} {parent}\n" for merge, parent in merge_parents),
                         capture_output=True, text=True, check=True).stdout
    renames, pairs, tokens = {}, iter(merge_parents), iter(out.split("\0")[:-1])
    for token in tokens:
        if RENAME.fullmatch(token):
            old, new = next(tokens), next(tokens)
            current[old] = new
        else:  # the merge id that opens the next pair
            current = renames[next(pairs)] = {}
    return renames
