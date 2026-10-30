"""Read `coverage json --show-contexts` into per-test coverage, and derive unique coverage and overlaps."""
import os
import re
from itertools import combinations

from weigh_inventory import TEST_FILE

OVERLAP_JACCARD = 0.9


def contexts_regex(cases, eligible=None):
    """Matches the call context of every passing case, of the eligible tests only when given."""
    passing = [re.escape(f"{node}|call") for node, c in cases.items()
               if c["outcome"] == "passed" and (eligible is None or node.split("[", 1)[0] in eligible)]
    return "^(" + "|".join(passing) + ")$" if passing else "(?!)"


def per_test(report, repo):
    """{test node id: {repo-relative path: sorted lines}} from the call contexts; setup, teardown and collection are not the test's."""
    coverage = {}
    for path, info in report["files"].items():
        rel = os.path.relpath(path, repo) if os.path.isabs(path) else path
        if TEST_FILE.search(rel):
            continue
        for line, contexts in info.get("contexts", {}).items():
            for ctx in contexts:
                if ctx.endswith("|call"):
                    node = ctx[: -len("|call")].split("[", 1)[0]
                    coverage.setdefault(node, {}).setdefault(rel, set()).add(int(line))
    return {node: {rel: sorted(lines) for rel, lines in files.items()} for node, files in coverage.items()}


def branch_percent(report):
    """Branch arcs only: percent_covered mixes statements in. No branches: the metric is not measured."""
    totals = report["totals"]
    return 100 * totals["covered_branches"] / totals["num_branches"] if totals["num_branches"] else None


def _lines(coverage):
    return {(rel, n) for rel, lines in coverage.items() for n in lines}


def unique_coverage(coverage):
    """{node: lines covered by that test and no other}, over the tests given."""
    sets = {node: _lines(c) for node, c in coverage.items()}
    seen = {}
    for lines in sets.values():
        for line in lines:
            seen[line] = seen.get(line, 0) + 1
    return {node: sum(seen[line] == 1 for line in lines) for node, lines in sets.items()}


def overlaps(coverage):
    # ponytail: all pairs, quadratic in the passing tests; bucket by file if suites of thousands get slow
    sets = {node: _lines(c) for node, c in coverage.items() if c}
    found = []
    for a, b in combinations(sets, 2):
        jaccard = len(sets[a] & sets[b]) / len(sets[a] | sets[b])
        if jaccard >= OVERLAP_JACCARD:
            found.append({"tests": [a, b], "jaccard": jaccard})
    return found
