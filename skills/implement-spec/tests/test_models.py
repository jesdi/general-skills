"""Ticket 07: models.json through the driver and in content; README against the help text of run.sh."""
import json
import re
import subprocess

import pytest

from test_cli import RUN, SKILL, drive, env, make_repo, three  # noqa: F401  (env is a fixture)

STANDARD = {
    "implementer": ["claude-sonnet-5@medium", "openai/gpt-sol@medium"],
    "test-writer": ["claude-sonnet-5@medium", "openai/gpt-sol@medium"],
    "reviewer": ["openai/gpt-astra@medium", "claude-opus-5@medium"],
    "final-reviewer": ["openai/gpt-astra@medium", "claude-opus-5@medium"],
}
ENTRY = re.compile(r"^(?:(?:anthropic|openai)/)?[\w.-]+@(?:minimal|low|medium|high|xhigh|max)$")


def models():
    return json.loads((SKILL / "models.json").read_text())


def test_models_json_loads_through_the_driver(env, tmp_path):
    repo = make_repo(tmp_path / "demo", three())
    proc = drive(env, repo)  # no --models: the skill's own file; fake claude and codex on PATH
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "max frontier width" in proc.stdout


def test_every_role_has_an_ordered_list_of_provider_model_effort_entries():
    for role, entries in models().items():
        assert isinstance(entries, list) and entries, role
        assert all(ENTRY.match(entry) for entry in entries), (role, entries)
    assert {"implementer", "test-writer", "reviewer", "final-reviewer"} <= models().keys()


@pytest.mark.parametrize("role", STANDARD)
def test_the_standard_track_in_order(role):
    assert models()[role] == STANDARD[role]


def test_readme_documents_every_flag_of_the_help():
    help_text = subprocess.run(["sh", str(RUN), "--help"], capture_output=True, text=True, timeout=60).stdout
    flags = sorted(set(re.findall(r"--[a-z][a-z-]*", help_text)))
    assert len(flags) >= 11, flags
    readme = (SKILL / "README.md").read_text()
    assert [flag for flag in flags if flag not in readme] == []
