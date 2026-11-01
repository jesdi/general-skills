"""Fixture suite for the boundaries of the smell rules, built on the default repo (40 tests, no smells).

rules_suite adds tests/conftest.py, demo/_queue.py, data/app.json, tests/test_rules.py and tests/test_maint_rules.py.
Expected smells by test of test_rules.py:
  No assertion:     TestA::test_board (TestB::test_board, same name, asserts),
                    TestOther::test_inherited (TestChild::test_inherited, from Base, asserts)
  Interaction-only: test_call_count, test_call_args (test_mixed also asserts on a result)
  Mock-heavy:       test_setattr_4 (4 monkeypatch.setattr; test_setattr_3 has 3)
  Private access:   test_module_private (demo._queue._requeue through a module alias),
                    test_local_private (the same through a function-local alias `module`)
No smell: test_check (check_equal from conftest), test_expect (expect_equal from conftest),
test_conftest_private (conftest._conftest_private through `from tests import conftest`),
test_local_conftest (the same through a function-local alias `module`, defined after test_local_private).
Expected smells by test of test_maint_rules.py:
  Mystery guest:    test_guest_name (open(path), path a literal), test_guest_path (Path from a literal, then read_text)
  Eager test:       test_eager_helpers (3 act-then-check_equal cycles)
No smell: test_swallow_checked (its handler calls check_equal), test_long (54 lines of asserts, a long test).
Every call takes 1.125 s (fakeclock), so every test is slow.
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


def test_local_private():
    import demo._queue as module
    assert module._requeue() == 1


def test_local_conftest():
    from tests import conftest as module
    assert module._conftest_private() == 1


def test_mixed():
    sender = MagicMock()
    result = board.render(1)
    sender.send(result)
    assert result == "1" and sender.send.call_count == 1


class TestA:
    def test_board(self):
        board.render(1)


class TestB:
    def test_board(self):
        assert board.render(1) == "1"


class Base:
    def test_inherited(self):
        assert board.render(1) == "1"


class TestOther:
    def test_inherited(self):
        board.render(1)


class TestChild(Base):
    pass
'''

MAINT = """from pathlib import Path

from demo import board
from tests.conftest import check_equal


def test_guest_name():
    path = "data/app.json"
    with open(path) as handle:
        assert handle.read() == "{}"


def test_guest_path():
    source = Path("data/app.json")
    copied = source
    assert copied.read_text() == "{}"


def test_eager_helpers():
    first = board.render(1)
    check_equal(first, "1")
    second = board.render(2)
    check_equal(second, "2")
    third = board.render(3)
    check_equal(third, "3")


def test_swallow_checked():
    try:
        board.render(1)
    except Exception as error:
        check_equal(str(error), "")


def test_long():
""" + "".join(f"    assert board.render({i}) == '{i}'\n" for i in range(53))


def rules_suite(path):
    repo = build_repo(path)
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "conftest.py").write_text(CONFTEST)
    (repo / "demo" / "_queue.py").write_text("def _requeue():\n    return 1\n")
    (repo / "tests" / "test_rules.py").write_text(RULES)
    (repo / "tests" / "test_maint_rules.py").write_text(MAINT)
    (repo / "data").mkdir()
    (repo / "data" / "app.json").write_text("{}")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "suite")
    return repo
