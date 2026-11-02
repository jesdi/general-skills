"""The pinned mutmut still exposes what the kill matrix relies on: the mutant-selection variable and the covering-tests data.

The pin is `weigh_env.MUTMUT`, a requirement string such as "mutmut==3.8.1" (the closure is resolved by uv from it).
"""
import json
import os
import subprocess
import sys

from fixture_repo import HERE, uv

sys.path.insert(0, str(HERE.parent))

SELECTION_VARIABLE = "MUTANT_UNDER_TEST"
COVERING_DATA = "tests_by_mangled_function_name"


def test_pinned_mutmut_exposes_what_the_kill_matrix_relies_on(tmp_path):
    import weigh_env

    pin = getattr(weigh_env, "MUTMUT", None)
    assert pin and pin.startswith("mutmut=="), f"weigh_env.MUTMUT must pin mutmut exactly, got {pin!r}"
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "__init__.py").write_text("")
    (proj / "pkg" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (proj / "tests").mkdir()
    (proj / "tests" / "test_calc.py").write_text("from pkg.calc import add\ndef test_add():\n    assert add(1, 2) == 3\n")
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0"\n[tool.mutmut]\npaths_to_mutate = ["pkg/"]\ntests_dir = ["tests/"]\n'
        '[tool.pytest.ini_options]\npythonpath = ["."]\n')
    py = proj / ".venv" / "bin" / "python"
    subprocess.run([uv(), "venv", "-q", "--python", "3.12", str(proj / ".venv")], check=True)
    subprocess.run([uv(), "pip", "install", "-q", "--python", str(py), pin, "pytest"], check=True)
    run = subprocess.run([str(proj / ".venv" / "bin" / "mutmut"), "run"], cwd=proj, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    stats = json.loads((proj / "mutants" / "mutmut-stats.json").read_text())
    assert COVERING_DATA in stats, f"pinned mutmut no longer exposes {COVERING_DATA}"
    (func,) = stats[COVERING_DATA]
    env = {**os.environ, SELECTION_VARIABLE: func + "__mutmut_1"}
    picked = subprocess.run([str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=proj / "mutants", env=env,
                            capture_output=True, text=True)
    assert picked.returncode != 0, f"pinned mutmut no longer exposes {SELECTION_VARIABLE}: selecting a mutant did not change behavior"
