"""Ticket 07: models.json through the driver and in content; README against the help text of run.sh.

Fewer roles, ticket 01: the driver has no `correctness` or `explorer` role, and a --models file that names a role the
skill's models.json does not define stops the run before the preflight and before any agent."""
import json
import re
import subprocess
import sys

import pytest

from test_cli import RUN, SKILL, drive, env, make_repo, three  # noqa: F401  (env is a fixture)

RUNS = ("--tickets", ".agent/tickets", "--gate", "true", "--fast-gate", "true", "--spec", "spec.md")

STANDARD = {
    "implementer": ["claude-sonnet-5-5@medium", "claude-opus-5-5@medium"],
    "test-writer": ["claude-sonnet-5-5@medium", "openai/gpt-6.1-sol@medium"],
    "reviewer": ["openai/gpt-6-astra@medium", "claude-opus-5-5@medium"],
    "final-reviewer": ["openai/gpt-6-astra@medium", "claude-opus-5-5@medium"],
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


def _started(env, tmp_path, models, *extra):
    """A run that is not a dry run: without an input error, the preflight launches the fake claude and codex."""
    repo = make_repo(tmp_path / "demo", three())
    path = tmp_path / "models.json"
    path.write_text(json.dumps(models))
    return drive(env, repo, *RUNS, "--models", str(path), *extra)


@pytest.mark.parametrize("role", ["correctness", "explorer"])
def test_a_models_file_with_a_removed_role_exits_3_before_the_preflight(env, tmp_path, role):
    proc = _started(env, tmp_path, {role: ["openai/gpt-6-astra@medium"]})
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert f"{role}: the role was removed; delete its key from the --models file" in proc.stdout.splitlines()
    assert not (tmp_path / "launched").exists(), "no preflight and no agent"


def test_a_models_file_with_an_unknown_role_exits_3_naming_it(env, tmp_path):
    proc = _started(env, tmp_path, {"planner": ["claude-opus-5-5@medium"]})
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert [line for line in proc.stdout.splitlines() if line.startswith("planner: unknown role (known: ")]
    assert not (tmp_path / "launched").exists()


def test_a_models_file_that_overrides_only_the_implementer_is_accepted(env, tmp_path):
    repo = make_repo(tmp_path / "demo", three())
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"implementer": ["claude-opus-5-5@medium", "openai/gpt-6.1-sol@medium"]}))
    proc = drive(env, repo, *RUNS, "--models", str(path), "--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "max frontier width" in proc.stdout


def test_only_correctness_exits_3_as_an_unknown_role(env, tmp_path):
    proc = _started(env, tmp_path, {}, "--only", "correctness")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "argument --only: invalid choice: 'correctness'" in proc.stdout
    assert not (tmp_path / "launched").exists()


def test_the_skill_has_no_correctness_or_explorer_role():
    assert not {"correctness", "explorer"} & models().keys()
    assert not (SKILL / "prompts/correctness.md").exists()


# ---------------------------------------------------------------- the provider config files

OPUS, ASTRA, SOL = "claude-opus-5-5@medium", "openai/gpt-6-astra@medium", "openai/gpt-6.1-sol@medium"


@pytest.fixture
def layers(tmp_path, monkeypatch):
    """A home config folder and a worktree; returns a writer of (the home file, the repo file)."""
    sys.path.insert(0, str(SKILL))
    import driver_agents
    monkeypatch.setattr(driver_agents.shutil, "which", lambda cli: cli)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    home, repo = tmp_path / "config/implement-spec/models.json", tmp_path / ".implement-spec/models.json"

    def write(path, roles):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(roles))
        return path
    return driver_agents, lambda roles: write(home, roles), lambda roles: write(repo, roles), tmp_path


def test_the_home_file_replaces_the_lists_it_names_and_keeps_the_rest(layers):
    driver_agents, home, _, _ = layers
    home({"reviewer": [OPUS]})
    loaded, errors = driver_agents.load_models(None)
    assert errors == [] and loaded["reviewer"] == [OPUS] and loaded["implementer"] == models()["implementer"]


def test_the_repo_file_wins_over_the_home_file_and_the_models_flag_wins_over_both(layers):
    driver_agents, home, repo, tmp = layers
    home({"reviewer": [OPUS], "fixer": [OPUS]})
    repo({"reviewer": [ASTRA]})
    loaded, errors = driver_agents.load_models(None)
    assert errors == [] and loaded["reviewer"] == [ASTRA] and loaded["fixer"] == [OPUS]
    flag = tmp / "flag.json"
    flag.write_text(json.dumps({"reviewer": [SOL]}))
    loaded, errors = driver_agents.load_models(str(flag))
    assert errors == [] and loaded["reviewer"] == [SOL] and loaded["fixer"] == [OPUS]


def test_an_error_in_a_config_file_names_that_file(layers):
    driver_agents, home, repo, tmp = layers
    home({"correctness": [OPUS]})
    repo({"planner": [OPUS]})
    _, errors = driver_agents.load_models(None)
    assert errors[0] == f"correctness: the role was removed; delete its key from {tmp}/config/implement-spec/models.json"
    assert errors[1].startswith("planner: unknown role (known: ") and errors[1].endswith(
        "); delete its key from .implement-spec/models.json"), errors
    (tmp / ".implement-spec/models.json").write_text("{not json")
    _, errors = driver_agents.load_models(None)
    assert errors[1].startswith(".implement-spec/models.json: JSONDecodeError("), errors
