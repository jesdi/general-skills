import math

from crap_model import Func
from crap_score import crap_score, hint, max_cc, required_coverage, score

THRESHOLDS = {"existing": 15, "new": 9}


def test_formula_matches_savoia_definition():
    assert crap_score(22, 0.0) == 506
    assert crap_score(9, 1.0) == 9
    assert crap_score(5, 0.0) == 30
    assert math.isclose(crap_score(41, 0.86), 41 * 41 * 0.14**3 + 41)


def test_required_coverage_is_none_when_cc_alone_exceeds_limit():
    assert required_coverage(10, 9) is None
    assert required_coverage(16, 15) is None


def test_required_coverage_at_the_edge_is_full():
    assert required_coverage(9, 9) == 1.0


def test_required_coverage_scales_with_cc():
    assert required_coverage(2, 9) == 0.0
    assert math.isclose(required_coverage(5, 9), 1 - (4 / 25) ** (1 / 3))


def test_max_cc_is_largest_passing_complexity_at_given_coverage():
    assert max_cc(0.0, 9) == 2      # CC 3 at 0% = 12 > 9
    assert max_cc(1.0, 15) == 15
    assert max_cc(1.0, 9) == 9


def test_hint_offers_coverage_when_reachable_and_split_when_cc_too_high():
    assert hint(5, 0.0, 9) == "cover ≥ 46% or split to CC ≤ 2"
    assert hint(22, 0.0, 15) == "split to CC ≤ 3"
    assert hint(9, 0.5, 9) == "cover ≥ 100% or split to CC ≤ 5"   # 25·0.125+5 = 8.1 ok, 36·0.125+6 = 10.5 not


def test_score_uses_new_limit_for_new_functions():
    f = Func("a.py", "f", 1, 5, 5)
    new = score("backend", f, 0.0, "new", THRESHOLDS)
    old = score("backend", f, 0.0, "existing", THRESHOLDS)
    assert (new.limit, new.status) == (9, "FAIL")
    assert (old.limit, old.status) == (15, "FAIL")
    ok = score("backend", f, 1.0, "new", THRESHOLDS, note="n")
    assert (ok.status, ok.hint, ok.note, ok.crap) == ("ok", "", "n", 5)
