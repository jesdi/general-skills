import sys
from pathlib import Path

# The engine's modules sit one folder up. Set the path here, before any test module is collected, so a test file
# needs no sys.path line of its own and collection order cannot hide a missing one.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

collect_ignore_glob = ["testdata/*"]

import pytest  # noqa: E402

from fixture_repo import build_repo, weigh


@pytest.fixture(autouse=True, scope="session")
def no_browser():
    """The engine opens the dashboard unless --no-open: a no-op BROWSER keeps every test from opening a real tab.

    Session scope sets it before any module fixture runs the engine."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("BROWSER", "true")
        yield


@pytest.fixture(scope="session")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("overlay-cache")


@pytest.fixture
def home(tmp_path):
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture
def run(tmp_path, home, cache):
    """Build a repo (kwargs for build_repo, `name` picks the folder) and weigh it; returns (repo, result)."""
    def _run(*flags, name="demo", now=None, **repo_kwargs):
        repo = build_repo(tmp_path / "work" / name, **repo_kwargs)
        return repo, weigh(repo, home, *flags, cache=cache, now=now)
    return _run
