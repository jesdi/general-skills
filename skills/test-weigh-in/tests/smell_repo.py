"""Fixture suites for the smell tests (ticket tw/s5-smells/09), built on the default repo (40 tests, no smells).

smelly_suite adds tests/test_smells.py and tests/conftest.py. Expected smells by test (all other tests have none):
  No assertion:     test_board_renders, test_fail_hollow (fails), test_error_hollow (errors in setup), test_skipped_hollow (skipped)
  Interaction-only: test_notify
  Mock-heavy:       test_b_heavy (3 @patch + MagicMock())
  Private access:   test_b_private (imports _requeue_locked from dispatcher.queue_ops)
No smell, each on a boundary: test_board (helper assert_board_equal from conftest), test_invalid (pytest.raises),
test_warns (pytest.warns), test_nested_helper (assert inside a nested def), test_a_patched (3 @patch),
test_a_dunder (obj.__dict__), test_conftest_private (a _name from conftest), test_mixed_mock (mock assert plus a plain assert).
mock_heavy_suite adds 25 mock-heavy tests that pass.
"""
from fixture_repo import build_repo, git

SMELLS = '''import pytest
from unittest.mock import MagicMock, patch

from demo import board
from tests.conftest import _conftest_private, assert_board_equal


def test_board_renders():
    board.render(1)


def test_fail_hollow():
    raise RuntimeError("boom")


@pytest.fixture
def broken():
    raise RuntimeError("setup")


def test_error_hollow(broken):
    board.render(1)


@pytest.mark.skip
def test_skipped_hollow():
    board.render(1)


def test_board():
    assert_board_equal(board.render(1), "1")


def test_invalid():
    with pytest.raises(ValueError):
        int("x")


def test_warns():
    import warnings
    with pytest.warns(UserWarning):
        warnings.warn("w", UserWarning)


def test_nested_helper():
    def check(value):
        assert value == "1"
    check(board.render(1))


@patch("os.getcwd")
@patch("os.getpid")
@patch("os.getppid")
def test_a_patched(p1, p2, p3):
    assert board.render(1) == "1"


@patch("os.getcwd")
@patch("os.getpid")
@patch("os.getppid")
def test_b_heavy(p1, p2, p3):
    spare = MagicMock()
    assert spare is not None


def test_notify():
    sender = MagicMock()
    sender.send("hi")
    sender.send.assert_called_once_with("hi")


def test_mixed_mock():
    sender = MagicMock()
    sender.send("hi")
    sender.send.assert_called_once_with("hi")
    assert board.render(1) == "1"


def test_a_dunder():
    class Obj:
        pass
    assert Obj().__dict__ == {}


def test_b_private():
    from dispatcher.queue_ops import _requeue_locked
    assert _requeue_locked() == 1


def test_conftest_private():
    assert _conftest_private() == 1
'''
CONFTEST = '''def assert_board_equal(a, b):
    assert a == b


def _conftest_private():
    return 1
'''
MOCK_HEAVY = "from unittest.mock import patch\n\n\n" + "\n\n".join(
    f'@patch("os.getcwd")\n@patch("os.getpid")\n@patch("os.getppid")\n@patch("os.getuid")\n'
    f"def test_heavy_{i:02d}(a, b, c, d):\n    assert a is not None\n" for i in range(25))


def _commit(repo):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "suite")
    return repo


def smelly_suite(path):
    repo = build_repo(path)
    (repo / "dispatcher").mkdir()
    (repo / "dispatcher" / "__init__.py").write_text("")
    (repo / "dispatcher" / "queue_ops.py").write_text("def _requeue_locked():\n    return 1\n")
    (repo / "tests" / "conftest.py").write_text(CONFTEST)
    (repo / "tests" / "test_smells.py").write_text(SMELLS)
    return _commit(repo)


def mock_heavy_suite(path):
    repo = build_repo(path)
    (repo / "tests" / "test_heavy.py").write_text(MOCK_HEAVY)
    return _commit(repo)
