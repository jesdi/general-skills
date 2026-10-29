"""The target repo's interpreter and the overlay that carries the measurement plugin."""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = "weigh_pytest_plugin.py"


class SetupError(Exception):
    pass


def find_interpreter(repo):
    for rel in (".venv/bin/python", "venv/bin/python"):
        if (repo / rel).is_file():
            return repo / rel
    raise SetupError(f"no interpreter found in {repo}: pass --python PATH")


def build_overlay():
    """Plugin-only directory (no pytest, so the repo's pytest is the one used); built aside, then renamed into place.

    Keyed by the plugin's content, so an overlay left by an older plugin is never reused."""
    root = Path(os.environ.get("TEST_WEIGH_IN_CACHE_DIR") or Path.home() / ".cache" / "test-weigh-in")
    overlay = root / f"overlay-{hashlib.sha256((HERE / PLUGIN).read_bytes()).hexdigest()[:16]}"
    if not overlay.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=root))
        shutil.copy(HERE / PLUGIN, tmp)
        try:
            tmp.rename(overlay)
        except OSError:  # another run won the race
            shutil.rmtree(tmp)
    return overlay
