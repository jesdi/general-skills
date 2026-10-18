import pytest

from prune_harness import Target

collect_ignore = ["testdata"]


@pytest.fixture
def target(tmp_path):
    return Target(tmp_path)
