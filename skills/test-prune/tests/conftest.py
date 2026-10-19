import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the prune modules are one folder up

from prune_harness import Target  # noqa: E402

collect_ignore = ["testdata"]


@pytest.fixture
def target(tmp_path):
    return Target(tmp_path)
