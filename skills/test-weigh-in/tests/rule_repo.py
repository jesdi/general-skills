"""Fixture suite for the boundaries of the smell rules, built on the default repo (40 tests, no smells).

rules_suite adds tests/conftest.py, demo/_queue.py and tests/test_rules.py. Expected smells by test of test_rules.py:
  No assertion:     TestA::test_board (TestB::test_board, same name, asserts)
  Interaction-only: test_call_count, test_call_args
  Mock-heavy:       test_setattr_4 (4 monkeypatch.setattr; test_setattr_3 has 3)
  Private access:   test_module_private (demo._queue._requeue through a module alias)
No smell: test_check (check_equal from conftest), test_expect (expect_equal from conftest),
test_conftest_private (conftest._conftest_private through `from tests import conftest`).
"""
from fixture_repo import build_repo, git

CONFTEST = '''def check_equal(a, b):
    assert a == b


def expect_equal(a, b):
    assert a == b


def _conftest_private():
    return 1
'''
RULES = '''import os
from unittest.mock import MagicMock, call

import demo._queue as queue
from demo import board
from tests import conftest
from tests.conftest import check_equal, expect_equal


def test_check():
    check_equal(board.render(1), "1")


def test_expect():
    expect_equal(board.render(1), "1")


def test_call_count():
    sender = MagicMock()
    sender.send("hi")
    assert sender.send.call_count == 1


def test_call_args():
    sender = MagicMock()
    sender.send("hi")
    assert sender.send.call_args == call("hi")


def test_setattr_3(monkeypatch):
    monkeypatch.setattr(os, "getcwd", lambda: "/")
    monkeypatch.setattr(os, "getpid", lambda: 1)
    monkeypatch.setattr(os, "getppid", lambda: 1)
    assert os.getpid() == 1


def test_setattr_4(monkeypatch):
    monkeypatch.setattr(os, "getcwd", lambda: "/")
    monkeypatch.setattr(os, "getpid", lambda: 1)
    monkeypatch.setattr(os, "getppid", lambda: 1)
    monkeypatch.setattr(os, "getuid", lambda: 1)
    assert os.getpid() == 1


def test_conftest_private():
    assert conftest._conftest_private() == 1


def test_module_private():
    assert queue._requeue() == 1


class TestA:
    def test_board(self):
        board.render(1)


class TestB:
    def test_board(self):
        assert board.render(1) == "1"
'''


def rules_suite(path):
    repo = build_repo(path)
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "conftest.py").write_text(CONFTEST)
    (repo / "demo" / "_queue.py").write_text("def _requeue():\n    return 1\n")
    (repo / "tests" / "test_rules.py").write_text(RULES)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "suite")
    return repo
