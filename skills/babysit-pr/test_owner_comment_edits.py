"""Regression coverage for handoffs whose mutable heading was removed."""
import json

import pytest

from test_babysit import MIN, T, World, iso
from test_owner_comments import HANDOFF_BODY, comment, read


@pytest.mark.parametrize("editor", ["alice", "jesdi"])
def test_removing_handoff_heading_does_not_reactivate_approval(tmp_path, editor):
    w = World(tmp_path, comments=[
        comment(1001, "jesdi", "@babysitter merge a1b2c3d", at=T - 20 * MIN),
        comment(1002, "jesdi", HANDOFF_BODY, at=T - 10 * MIN)])
    state = w.github()
    state["commits"] = [{"sha": "a1b2c3d", "date": iso(T - 30 * MIN)}]
    w.state.write_text(json.dumps(state))
    assert read(w) == {"handoff": None, "instructions": []}

    # The final editor can also be the owner after another account's earlier edit.
    state["comments"][1] = comment(1002, "jesdi", "please continue",
                                   at=T - 10 * MIN, editor=editor)
    w.state.write_text(json.dumps(state))
    out = read(w)
    assert out["handoff"] == "handoff comment edited, boundary unverifiable"
    assert out["instructions"] == []
