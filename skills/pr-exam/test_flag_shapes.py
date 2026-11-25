"""`score --flags` refuses flag collections that are not lists of question ids (design.md, flags.json)."""
import pytest

from test_scoring import flagged


@pytest.mark.parametrize("flags, field", [
    ({"bypassed": {"q4": False}}, "bypassed"),
    ({"assisted": {"q2": False}}, "assisted"),
    ({"assisted": None}, "assisted"),
    ({"bypassed": "q6"}, "bypassed"),
    ({"assisted": [2]}, "assisted"),
    (["q6"], "object"),
])
def test_malformed_flags_are_refused_without_a_result(tmp_path, flags, field):
    r = flagged(tmp_path, flags, grades={"q4": "fail"})
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert field in r.stderr and r.stdout == "", (r.stdout, r.stderr)
