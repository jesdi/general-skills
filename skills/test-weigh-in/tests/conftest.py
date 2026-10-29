collect_ignore_glob = ["testdata/*"]

import pytest

from fixture_repo import build_repo, weigh


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
