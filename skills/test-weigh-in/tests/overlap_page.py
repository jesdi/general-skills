"""Ticket 02 fixture, no repo run: `overlap_page.py <out-dir> [reversed]` writes <out-dir>/dashboard.html through
weigh_dashboard.write_dashboard and <out-dir>/pairs.json (the pairs as fed, same order unless "reversed").

6,757 overlap pairs over 2,172 tests with 200-character ids, Jaccard values on a 0.90..1.00 grid (many ties).
One pair holds ids that occur nowhere else (UNIQUE_LEFT / UNIQUE_RIGHT), one pair holds markup in its ids (HOSTILE).
Interface the acceptance tests pin (the spec leaves the markup open), inside the Overlaps tab of the Economy pillar:
  [data-testid=overlap-search]  the search input (an `input` event applies the query)
  [data-testid=overlap-count]   the result count: contains the number of matching pairs
  [data-testid=overlap-pair]    one row per pair on the current page, at most 100 rows; holds two
                                [data-testid=overlap-test] identities and a label naming "Jaccard" with the value
  [data-testid=overlap-empty]   shown only when no pair matches, with an explanation
  [data-testid=overlap-next]    pages forward; disabled on the last page
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weigh_dashboard import write_dashboard
from weigh_rules import load
from weigh_score import feedback_values, score

N_TESTS, N_PAIRS = 2172, 6757
UNIQUE_LEFT = "tests/unique/test_alpha_only.py::test_only_left_xylophone"
UNIQUE_RIGHT = "tests/unique/test_omega_only.py::test_only_right_zeppelin"
HOSTILE = ['tests/<b id="pwn">x<img src=x onerror="pwned=1">/test_</script><script>window.pwned=1</script>.py::test_hostile',
           "tests/test_ok_partner.py::test_partner"]


def name(i):
    return f"tests/{'sub_package_' * 12}{i // 50:03d}/test_module_{i:04d}.py::TestClass{i % 7}::test_case_{i:04d}_{'x' * 40}"


def pairs():
    out = []
    for k in range(N_PAIRS):
        a = k % N_TESTS
        b = (a + 1 + k // N_TESTS) % N_TESTS
        out.append({"tests": [name(a), name(b)], "jaccard": round(0.90 + (k * 7919 % 11) / 100, 2)})
    out[1234] = {"tests": [UNIQUE_LEFT, UNIQUE_RIGHT], "jaccard": 0.93}
    out[4321] = {"tests": HOSTILE, "jaccard": 0.91}
    return out


def build(out, order="forward"):
    out.mkdir(parents=True, exist_ok=True)
    rules = load()
    tests = {name(i): {"outcome": "passed", "duration": 0.01, "coverage": {}, "unique_coverage": 0, "flaky": False, "smells": [], "lines": 2}
             for i in range(N_TESTS)}
    metrics, pillars = score(rules, {**feedback_values(tests, 1.0), "no_unique_coverage": 100.0})
    fed = pairs()[::-1] if order == "reversed" else pairs()
    data = {"commit": "abc1234", "dirty": False, "inventory": {"tests": N_TESTS, "cases": 3196, "test_files": 44, "test_lines": 9000, "source_lines": 9000, "runtime_seconds": 64.2},
            "metrics": metrics, "pillars": pillars, "tests": tests, "overlaps": fed}
    (out / "pairs.json").write_text(json.dumps(fed))
    return write_dashboard(out, data, rules)


if __name__ == "__main__":
    print(build(Path(sys.argv[1]), *sys.argv[2:3]))
