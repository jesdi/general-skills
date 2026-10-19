"""Deletion-shape edges the fixture repo does not reach, checked in-process (prune_source)."""
from prune_source import class_refusals, delete_tests, describe, undeletable
from prune_source import test_sources as sources

EDGES = '''"""Edges."""
import os
import json as j
from typing import Any
from helpers import make_thing, other


class TestFirst:
    def test_first(self):
        assert os.sep

    def test_second(self):
        assert True


def test_uses_param(other):
    assert other


def test_anno(x: "Any"):
    assert make_thing()
    assert j.dumps({}) == "{}"


def test_kept():
    with pytest.raises(ValueError):
        int("x")
'''


def test_first_class_member_takes_the_blank_lines_after_it_and_pinned_imports_stay():
    out = delete_tests(EDGES, {(("TestFirst",), "test_first"), ((), "test_uses_param"), ((), "test_anno")})

    assert "class TestFirst:\n    def test_second(self):" in out
    assert "import os\n" not in out and "import json as j\n" not in out  # read only by deleted tests
    assert "from typing import Any\n" in out  # a string carries Any
    assert "from helpers import make_thing, other\n" in out  # a parameter carries other
    assert out.endswith('        int("x")\n')


def test_undeletable_names_what_the_prune_gate_would_refuse():
    decorated = "import pytest\n\n\nclass TestX:\n    @pytest.mark.slow\n    def test_a(self):\n        assert 1\n"
    named = "def test_a():\n    assert 1\n\n\nALIAS = test_a\n"

    assert "decorated first member" in undeletable(decorated, "tests/t.py::TestX::test_a")
    assert "reads or spells the name test_a" in undeletable(named, "tests/t.py::test_a")
    assert "cannot be deleted whole" in undeletable(named, "tests/t.py::test_missing")
    assert undeletable(EDGES, "tests/t.py::TestFirst::test_second") is None


def test_describe_lists_raises_blocks_and_misses_unknown_tests():
    found = describe(EDGES, "tests/t.py::test_kept")

    assert found["assertions"] == ['with pytest.raises(ValueError):\n        int("x")']
    assert describe(EDGES, "tests/t.py::TestNope::test_kept") is None


def test_a_test_in_a_class_named_elsewhere_is_undeletable():
    subclassed = "class TestBase:\n    def test_a(self):\n        assert 1\n\n    def test_b(self):\n        assert 2\n\n\nclass TestChild(TestBase):\n    def test_c(self):\n        assert 3\n"
    spelled = "class TestA:\n    def test_a(self):\n        assert 1\n\n\ndef test_b():\n    assert 'TestA'\n"

    assert "class TestBase is subclassed" in undeletable(subclassed, "tests/t.py::TestBase::test_a")  # partial
    assert "class TestA is subclassed or named" in undeletable(spelled, "tests/t.py::TestA::test_a")
    assert undeletable(subclassed, "tests/t.py::TestChild::test_c") is None


def test_class_refusals_keep_an_enclosing_class_non_empty():
    nested = "class TestOuter:\n    class TestInner:\n        def test_a(self):\n            assert 1\n"
    documented = 'class TestOuter:\n    """Doc."""\n\n    class TestInner:\n        def test_a(self):\n            assert 1\n'
    inner = "tests/t.py::TestOuter::TestInner::test_a"

    assert "leaves class TestOuter empty" in class_refusals(nested, [inner])[inner]
    assert class_refusals(documented, [inner]) == {}


def test_a_new_parametrize_case_changes_the_test_source():
    before = "@pytest.mark.parametrize('x', [1])\ndef test_x(x):\n    assert x\n"
    after = before.replace("[1]", "[1, 2]")

    assert sources(before)["test_x"] != sources(after)["test_x"]
