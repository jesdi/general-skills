"""The Map tab draws the tests that give value: a test that passed only in the plain run (flaky) is not a dot."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import map_fixture
from weigh_dashboard import write_dashboard
from weigh_rules import load


def test_a_flaky_test_is_not_drawn(tmp_path):
    map_fixture.build(tmp_path)
    data = json.loads((tmp_path / "snapshot.json").read_text())
    data["tests"]["tests/test_twin.py::test_twin_1"]["flaky"] = True
    html = write_dashboard(tmp_path, data, load()).read_text()
    page = json.loads(html.split("const data = ", 1)[1].split(";\n", 1)[0])
    drawn = {d["test"] for d in page["map"]}
    assert drawn == set(map_fixture.COVERAGE) - {"tests/test_twin.py::test_twin_1"}
