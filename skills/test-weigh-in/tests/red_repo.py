"""Fixture suites that are not green, built on the default repo (40 tests), for the coverage-run tests.

red_suite: 3 of 40 tests fail (test_board_3, test_board_4, test_pick_false). test_pick_false is the only test that
takes the false arm of demo/pick.py, so a suite coverage that counts failing tests would read 100 and not 50.
skipped_suite: 4 of 40 tests are skipped.
"""
from fixture_repo import build_repo, git

FAILING = ("tests/test_board.py::test_board_3", "tests/test_board.py::test_board_4", "tests/test_render.py::test_pick_false")
SKIPPED = ("tests/test_config.py::test_config_2", "tests/test_config.py::test_config_3",
           "tests/test_config.py::test_config_4", "tests/test_render.py::test_render_2")


def _commit(repo):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "suite")
    return repo


def _edit(repo, rel, old, new):
    path = repo / rel
    path.write_text(path.read_text().replace(old, new))


def red_suite(path):
    repo = build_repo(path)
    (repo / "demo" / "pick.py").write_text("def pick(x):\n    if x:\n        return 1\n    return 2\n")
    render = (repo / "tests" / "test_render.py").read_text()
    kept = render[render.index("def test_render_2"):]
    (repo / "tests" / "test_render.py").write_text(
        "import pytest\nfrom demo import board, config, pick\n"
        "def test_pick_true():\n    assert pick.pick(True) == 1\n"
        "def test_pick_false():\n    assert pick.pick(False) == 1\n" + kept)
    for n in (3, 4):
        _edit(repo, "tests/test_board.py", f'def test_board_{n}():\n    assert board.render({n}) == "{n}"',
              f"def test_board_{n}():\n    assert board.render({n}) == 'x'")
    return _commit(repo)


def skipped_suite(path):
    repo = build_repo(path)
    for rel, n in (("test_config", 2), ("test_config", 3), ("test_config", 4), ("test_render", 2)):
        _edit(repo, f"tests/{rel}.py", f"def {rel}_{n}():", f"@pytest.mark.skip\ndef {rel}_{n}():")
    return _commit(repo)
