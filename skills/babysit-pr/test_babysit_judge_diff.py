"""Judge evidence must contain raw changes despite repository diff drivers."""
import sys

import pytest

from test_babysit_judge import Judge, commit, git


@pytest.mark.parametrize("driver", ["textconv", "command"])
def test_diff_driver_cannot_hide_counted_changes(tmp_path, driver):
    j = Judge(tmp_path)
    git(j.repo, "update-ref", "refs/remotes/origin/main", j.head)
    j.head = commit(j.repo, {"src/app.py": "CHANGED-LOGIC-MARKER\n"}, "change logic")
    (j.repo / ".git/info/attributes").write_text("src/app.py diff=hide\n")
    converter = tmp_path / "hide-diff"
    converter.write_text(f"#!{sys.executable}\nprint('CONVERTED-CONTENT')\n")
    converter.chmod(0o755)
    git(j.repo, "config", f"diff.hide.{driver}", str(converter))

    j.run()

    prompt = j.calls()[0]["stdin"]
    assert "+CHANGED-LOGIC-MARKER" in prompt
    assert "-LOGIC-BODY-MARKER 0" in prompt
    assert "CONVERTED-CONTENT" not in prompt
