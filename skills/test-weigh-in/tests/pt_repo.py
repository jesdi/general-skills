"""Fixture suite for ticket tw/s5-smells/11: the default repo's tests replaced by a controlled set of exactly 40 tests.

pt_suite: tests/test_pt.py   test_raises          pytest.raises(Exception) without match (PT011): the only smell
                             test_board           uses the fixture board_rows, declared `@pytest.fixture()` (PT001, style-only)
                             test_uses_helper     calls expect_failure(), a helper outside any test that holds a PT011
          ruff.toml          the target repo's own ruff configuration: selects PT001 and ignores PT011. It must change
                             nothing: PT011 still counts and PT001 still does not.
Every other test is a filler with no smell.
"""
from fixture_repo import git
from maint_repo import suite

PT = '''import pytest

from demo import board


@pytest.fixture()
def board_rows():
    return [1]


def expect_failure():
    with pytest.raises(Exception):
        int("x")


def test_raises():
    with pytest.raises(Exception):
        board.render(int("x"))


def test_board(board_rows):
    assert board.render(board_rows[0]) == "1"


def test_uses_helper():
    expect_failure()
    assert board.render(2) == "2"
'''
RUFF = '[lint]\nselect = ["PT001"]\nignore = ["PT011"]\n'


def pt_suite(path):
    repo = suite(path, {"tests/test_pt.py": PT, "ruff.toml": RUFF})
    return repo
