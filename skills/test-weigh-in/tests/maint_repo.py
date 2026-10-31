"""Fixture suites for ticket tw/s5-smells/10, built on the default repo with its tests replaced by a controlled set.

Every suite has exactly 40 tests, all passing unless a docstring says otherwise. The filler tests are structurally
distinct from each other (test_fill_NN asserts `board.render(1) == "1"` plus NN empty-string additions), so they never
form a duplicate-body cluster and carry none of the six smells. test_pick covers both arms of demo/pick.py.

smell_suite:    tests/test_maint.py   test_flow_2 (2 act-then-assert cycles), test_flow_3 (3), test_parse_all (for over a
                                      literal list), test_load (try/except Exception: pass)
                tests/test_guest.py   test_a (opens tmp_path / "x.json"), test_b (opens "~/.cache/app.json", a file
                                      committed at that relative path)
                tests/test_sleep.py   test_a (time.sleep(0)), test_b (time.sleep(2))
cluster_suite:  tests/test_intent.py  test_intent_1..4 (differ only in a string literal and a variable name) and
                test_cousin (same shape, calls str instead of board.render: it must not join the cluster)
summary_suite:  tests/test_dups.py    12 clones; tests/test_retry.py test_retry (flaky: passes plain, fails under coverage)
perfect_suite:  fillers and test_pick only: 0% hollow, 100% branch coverage, 0% duplicate clusters
clone_suite:    40 clones: 100% duplicate-body cluster, 100% without unique coverage
"""
import re

from fixture_repo import build_repo, git

PICK = "def pick(x):\n    if x:\n        return 1\n    return 2\n"
PICK_TEST = "def test_pick():\n    assert pick.pick(True) + pick.pick(False) == 3\n"
HEADER = "from demo import board, pick\n\n\n"


def filler(k):
    return f'def test_fill_{k:02d}():\n    assert board.render(1) == "1"' + ' + ""' * k + "\n"


def clone(i):
    return f'def test_clone_{i:02d}():\n    v{i} = board.render(1)\n    assert v{i} in "1 {i}"\n'


def suite(path, files, total=40):
    repo = build_repo(path)
    for old in (repo / "tests").glob("test_*.py"):
        old.unlink()
    (repo / "demo" / "config.py").unlink()
    (repo / "demo" / "pick.py").write_text(PICK)
    own = 0
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
        own += len(re.findall(r"^def test_", text, re.M))
    fillers = [filler(k) for k in range(total - own - 1)]
    (repo / "tests" / "test_fill.py").write_text(HEADER + "\n\n".join(fillers + [PICK_TEST]))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "suite")
    return repo


MAINT = '''from demo import board


def test_flow_2():
    first = board.render(1)
    assert first == "1"
    second = board.render(2)
    assert second == "2"


def test_flow_3():
    first = board.render(1)
    assert first == "1"
    second = board.render(2)
    assert second == "2"
    third = board.render(3)
    assert third == "3"


def test_parse_all():
    for s in ["a", "b"]:
        assert board.render(s) == s


def test_load():
    try:
        board.render(1)
    except Exception:
        pass
'''
GUEST = '''import json


def test_a(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{}")
    with open(path) as handle:
        assert json.load(handle) == {}


def test_b():
    with open("~/.cache/app.json") as handle:
        assert json.load(handle) == {}
'''
SLEEP = '''import time


def test_a():
    time.sleep(0)
    assert time.monotonic() > 0


def test_b():
    time.sleep(2)
    assert time.monotonic() > 0
'''
INTENT = "from demo import board\n\n\n" + "\n\n".join(
    f'def test_intent_{i}():\n    {name} = board.render(1)\n    assert {name} in "1 {lit}"\n'
    for i, name, lit in ((1, "first", "a"), (2, "second", "b"), (3, "third", "c"), (4, "fourth", "d"))
) + '\n\ndef test_cousin():\n    fifth = str(1)\n    assert fifth in "1 e"\n'
RETRY = 'import sys\n\n\ndef test_retry():\n    assert "coverage" not in sys.modules\n'


def smell_suite(path):
    repo = suite(path, {"tests/test_maint.py": MAINT, "tests/test_guest.py": GUEST, "tests/test_sleep.py": SLEEP})
    (repo / "~" / ".cache").mkdir(parents=True)
    (repo / "~" / ".cache" / "app.json").write_text("{}")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "guest file")
    return repo


def cluster_suite(path):
    return suite(path, {"tests/test_intent.py": INTENT})


def summary_suite(path):
    dups = "from demo import board\n\n\n" + "\n\n".join(clone(i) for i in range(12))
    return suite(path, {"tests/test_dups.py": dups, "tests/test_retry.py": RETRY})


def perfect_suite(path):
    return suite(path, {})


def clone_suite(path):
    return suite(path, {"tests/test_clone.py": "from demo import board\n\n\n" + "\n\n".join(clone(i) for i in range(39))})
