"""Exercise the manual runner's skill provisioning without a GitHub session."""
import os
from pathlib import Path
import subprocess
import sys


def test_runner_loads_reviewed_dependency_outside_checkout(tmp_path):
    runner = Path(__file__).resolve().parents[2] / "scripts/e2e-babysit.sh"
    setup = runner.read_text().split('start=HEAD')[0]
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\nexec git init -q "$4"\n')
    gh.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", TMPDIR=str(tmp_path))
    result = subprocess.run(
        ["sh", "-c", setup + '\nprintf "%s\\n" "$tmp"', str(runner), "fix"],
        env=env, capture_output=True, text=True, check=True,
    )
    staging = Path(result.stdout.strip())
    probe = """
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('babysit', '.claude/skills/babysit-pr/babysit.py')
babysit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(babysit)
reviewed = Path('.claude/skills/pr-risk').resolve()
assert not reviewed.is_relative_to(Path.cwd()), reviewed
assert babysit.trusted_pr_risk_dirs()[0] == reviewed
assert Path(babysit.load_merge_risk().__file__).resolve() == reviewed / 'merge_risk.py'
assert Path('.agents/skills/babysit-pr').resolve() == Path('.claude/skills/babysit-pr').resolve()
"""
    subprocess.run([sys.executable, "-c", probe], cwd=staging / "work", check=True)


def test_fix_scenario_keeps_a_feature_after_empty_input_repair(tmp_path):
    runner = Path(__file__).resolve().parents[2] / "scripts/e2e-babysit.sh"
    setup = runner.read_text().split('commit "$title"\ngit push')[0]
    gh = tmp_path / "gh"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", str(seed)], check=True)
    subprocess.run(["git", "-C", str(seed), "-c", "user.name=e2e", "-c",
                    "user.email=e2e@example.invalid", "-c", "commit.gpgsign=false",
                    "commit", "-q", "--allow-empty", "-m", "seed"], check=True)
    # Model a previous successful run merged to the default branch.
    (seed / "feature.txt").write_text("Already merged feature\n")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(["git", "-C", str(seed), "-c", "user.name=e2e", "-c",
                    "user.email=e2e@example.invalid", "-c", "commit.gpgsign=false",
                    "commit", "-qm", "previous run"], check=True)
    gh.write_text('#!/bin/sh\ngit clone -q "$SEED_REPO" "$4"\nmkdir -p "$4/src/demo" "$4/tests"\n')
    gh.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", TMPDIR=str(tmp_path),
               SEED_REPO=str(seed))
    result = subprocess.run(
        ["sh", "-c", setup + '\nprintf "%s\\n" "$tmp"', str(runner), "fix"],
        env=env, capture_output=True, text=True, check=True,
    )
    work = Path(result.stdout.strip()) / "work"
    assert not (work / "feature.txt").exists()  # each run starts from the seed
    source = work / "src/demo/parse.py"
    namespace = {}
    exec(source.read_text(), namespace)
    assert namespace["parse"]("") == [[]]  # the injected failure still exists
    source.write_text(source.read_text().replace(" or [[]]", ""))
    exec(source.read_text(), namespace)
    assert namespace["parse"]("") == []
    env["PYTHONPATH"] = str(work / "src")
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_parse_bom.py"],
        cwd=work, env=env, check=True,
    )
    # The retained test covers behavior absent from the base, not a cosmetic diff.
    exec(source.read_text().replace('text = text.removeprefix("\\ufeff")', 'text = text'), namespace)
    assert namespace["parse"]("\ufeffa, b\nc, d\n") != [["a", "b"], ["c", "d"]]
