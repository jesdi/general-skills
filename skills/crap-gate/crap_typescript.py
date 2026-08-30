"""Run crap_typescript.mjs and map its output to Func. Stdlib only."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from crap_model import Func, ToolError

SCRIPT = Path(__file__).with_name("crap_typescript.mjs")


def functions(files: list[tuple[Path, str]], cwd: Path) -> dict[str, list[Func]]:
    """Analyse (path_on_disk, label) pairs in one node process; result keyed by label."""
    if shutil.which("node") is None:
        raise ToolError("node is required for the typescript analyzer but is not on PATH")
    payload = json.dumps({"files": [{"path": str(p), "label": label} for p, label in files]})
    proc = subprocess.run(
        ["node", str(SCRIPT), "--cwd", str(cwd)],
        input=payload,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ToolError(f"typescript analyzer failed (cwd {cwd}): {proc.stderr.strip()}")
    result: dict[str, list[Func]] = {label: [] for _, label in files}
    for item in json.loads(proc.stdout):
        result[item["file"]].append(Func(item["file"], item["name"], item["start"], item["end"], item["cc"]))
    return result
