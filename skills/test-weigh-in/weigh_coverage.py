"""Read `coverage json --show-contexts` into per-test coverage, and derive unique coverage and overlaps."""
import os
import re
from itertools import combinations

from weigh_inventory import TEST_FILE

OVERLAP_JACCARD = 0.9


# Case outcomes that pass: an expected failure aggregates into a passing test, so its coverage counts like a pass.
PASSING = ("passed", "xfail")


def contexts_regex(cases, eligible=None):
    """Matches the call context of every passing case, of the eligible tests only when given."""
    passing = [re.escape(f"{node}|call") for node, c in cases.items()
               if c["outcome"] in PASSING and (eligible is None or node.split("[", 1)[0] in eligible)]
    return "^(" + "|".join(passing) + ")$" if passing else "(?!)"


def _relative(path, repo):
    rel = os.path.relpath(path, repo) if os.path.isabs(path) else path
    return None if TEST_FILE.search(rel) else rel


def per_test(report, arcs, repo):
    """{test node id: {"coverage": {path: sorted lines}, "branches": {path: sorted [from, to]}}}, paths repo-relative,
    from the call contexts; setup, teardown and collection are not the test's."""
    found = {}

    def add(ctx, kind, rel, item):
        if ctx.endswith("|call"):
            node = ctx[: -len("|call")].split("[", 1)[0]
            found.setdefault(node, {"coverage": {}, "branches": {}})[kind].setdefault(rel, set()).add(item)

    branches = {}
    for path, info in report["files"].items():
        if rel := _relative(path, repo):
            branches[rel] = {tuple(a) for a in info.get("executed_branches", [])}
            for line, contexts in info.get("contexts", {}).items():
                for ctx in contexts:
                    add(ctx, "coverage", rel, int(line))
    for ctx, files in arcs.items():
        for path, file_arcs in files.items():
            rel = _relative(path, repo)
            # ponytail: raw arcs that coverage's report translates (multi-line statements) do not match and are dropped
            for arc in map(tuple, file_arcs):
                if arc in branches.get(rel, ()):
                    add(ctx, "branches", rel, arc)
    return {node: {kind: {rel: sorted(items) for rel, items in files.items()} for kind, files in test.items()}
            for node, test in found.items()}


def branch_percent(report):
    """Branch arcs only: percent_covered mixes statements in. No branches: the metric is not measured."""
    totals = report["totals"]
    return 100 * totals["covered_branches"] / totals["num_branches"] if totals["num_branches"] else None


def valued(tests):
    """The tests that give value: passed in the plain run and the same under coverage, so not flaky."""
    return {node: test for node, test in tests.items() if test["outcome"] == "passed" and not test["flaky"]}


def _items(test):
    """The lines and branches a test covers."""
    return ({(rel, n) for rel, lines in test.get("coverage", {}).items() for n in lines}
            | {(rel, tuple(arc)) for rel, arcs in test.get("branches", {}).items() for arc in arcs})


def unique_coverage(tests):
    """{node: lines and branches covered by that test and no other}, over the tests given."""
    sets = {node: _items(t) for node, t in tests.items()}
    seen = {}
    for items in sets.values():
        for item in items:
            seen[item] = seen.get(item, 0) + 1
    return {node: sum(seen[item] == 1 for item in items) for node, items in sets.items()}


def overlaps(tests):
    # ponytail: all pairs, quadratic in the passing tests; bucket by file if suites of thousands get slow
    sets = {node: items for node, t in tests.items() if (items := _items(t))}
    found = []
    for a, b in combinations(sets, 2):
        jaccard = len(sets[a] & sets[b]) / len(sets[a] | sets[b])
        if jaccard >= OVERLAP_JACCARD:
            found.append({"tests": [a, b], "jaccard": jaccard})
    return found


MAP_SIMILAR = 0.6


def _axis(b, others, n):
    """Top eigenvector of the double-centred matrix b by power iteration from a fixed start, orthogonal to `others`."""
    v = [1.0 + i % 7 for i in range(n)]
    for _ in range(100):
        for o in others:
            d = sum(x * y for x, y in zip(v, o))
            v = [x - d * y for x, y in zip(v, o)]
        w = [sum(b[i][j] * v[j] for j in range(n)) for i in range(n)]
        norm = sum(x * x for x in w) ** 0.5
        if not norm:
            return [0.0] * n
        v = [x / norm for x in w]
    lam = sum(sum(b[i][j] * v[j] for j in range(n)) * v[i] for i in range(n))
    return [x * max(lam, 0) ** 0.5 for x in v]


def similarity_map(coverage):
    """Dots for the map: [{test, x, y, similar: [[other, jaccard >= 0.6]]}], x and y in 0..1.
    Placement is classical MDS of 1 - similarity from a fixed start, so it is the same on every load."""
    # ponytail: dense n x n, fine for hundreds of tests; sample or bucket if suites of thousands get slow
    nodes = list(coverage)
    sets = [_items({"coverage": coverage[n]}) for n in nodes]
    n = len(nodes)
    if not n:
        return []
    sim = [[(len(a & b) / len(a | b) if a | b else 0.0) if i != j else 1.0 for j, b in enumerate(sets)] for i, a in enumerate(sets)]
    sq = [[(1 - s) ** 2 for s in row] for row in sim]
    row_mean = [sum(r) / n for r in sq]
    total = sum(row_mean) / n
    b = [[-0.5 * (sq[i][j] - row_mean[i] - row_mean[j] + total) for j in range(n)] for i in range(n)]
    x = _axis(b, [], n)
    y = _axis(b, [[v / (sum(u * u for u in x) ** 0.5 or 1) for v in x]], n)

    def unit(axis):
        lo, hi = min(axis, default=0), max(axis, default=0)
        return [(v - lo) / (hi - lo) if hi > lo else 0.5 for v in axis]

    xs, ys = unit(x), unit(y)
    return [{"test": node, "x": xs[i], "y": ys[i],
             "similar": [[nodes[j], sim[i][j]] for j in range(n) if j != i and sim[i][j] >= MAP_SIMILAR]}
            for i, node in enumerate(nodes)]
