"""Model entries, the command line per provider, prompt rendering and the blocking launch. Stdlib only.

A model entry is `provider/model@effort`; a bare `model@effort` is anthropic.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
from pathlib import Path

SKILL = Path(__file__).resolve().parent
# Minutes per role (requirement 12).
TIMEOUTS = {"explorer": 30, "test-writer": 30, "implementer": 90, "reviewer": 45, "correctness": 30, "fixer": 60,
            "final-reviewer": 60, "conflict": 30}
# provider -> (CLI, efforts the CLI accepts).
PROVIDERS = {"anthropic": ("claude", ("low", "medium", "high", "xhigh", "max")),
             "openai": ("codex", ("minimal", "low", "medium", "high", "xhigh"))}
READ_ONLY = ("reviewer", "correctness")
LIVE: set[int] = set()  # the process groups of the agents that run now
STOPPED = False  # set by stop(): no agent starts after it
_LIVE = threading.Lock()  # an agent starts or the run stops, never both at once


def split(entry: str) -> tuple[str, str, str]:
    """provider, model, effort."""
    head, _, effort = str(entry).partition("@")
    provider, _, model = head.partition("/") if "/" in head else ("anthropic", "", head)
    return provider, model, effort


def _entry_error(role: str, entry: str, first: bool) -> str | None:
    provider, model, effort = split(entry)
    if provider not in PROVIDERS:
        return f"{role}: model entry {entry} names an unknown provider (known: {', '.join(PROVIDERS)})"
    cli, efforts = PROVIDERS[provider]
    if not model or effort not in efforts:
        return f"{role}: model entry {entry} needs a model and an effort from {', '.join(efforts)}"
    # The correctness pass folds into the reviewer when its CLI is absent (requirement 13).
    if first and role != "correctness" and not shutil.which(cli):
        return f"{role}: {cli} is not on PATH (first model entry {entry})"
    return None


def load_models(path: str | None) -> tuple[dict[str, list[str]], list[str]]:
    """The skill's default lists with the roles of `path` on top, and every entry error."""
    models = json.loads((SKILL / "models.json").read_text())
    try:
        models = {**models, **(json.loads(Path(path).read_text()) if path else {})}  # a list is a TypeError
        found = [_entry_error(role, entry, i == 0) for role, entries in models.items()
                 for i, entry in enumerate([entries[0], *entries[1:]])]  # entries[0]: an empty list is an error
    except (OSError, ValueError, AttributeError, LookupError, TypeError) as exc:
        return {}, [f"--models {path}: {exc!r}"]
    return models, [error for error in found if error]


def command(role: str, entry: str, report: str) -> list[str]:
    """The CLI call for one agent; the prompt goes to its stdin."""
    provider, model, effort = split(entry)
    if provider == "openai":
        # No --ephemeral: the rollout stays on disk as the trace. A read-only agent cannot write its
        # report, so its last message is saved there.
        tail = ["--sandbox", "read-only", "-o", report] if role in READ_ONLY else ["--sandbox", "workspace-write"]
        return ["codex", "exec", "--model", model, "-c", f'model_reasoning_effort="{effort}"', *tail]
    # stream-json keeps tool output in the log (the red lines) and ends with a result that has total_cost_usd.
    return ["claude", "-p", "--model", model, "--effort", effort, "--permission-mode", "auto",
            "--output-format", "stream-json", "--verbose"]


def render(name: str, values: dict[str, str]) -> str:
    """A prompt template with its {pointers} filled; the driver composes no prose."""
    return re.sub(r"\{([a-z_]+)\}", lambda m: values[m.group(1)], (SKILL / "prompts" / f"{name}.md").read_text())


def tail(prompt: str, report: str) -> str:
    """The end of a rendered prompt, from the line that names the report path: report path, schema, rule."""
    return prompt[prompt.rfind("\n", 0, prompt.rfind(report)):]


def launch(argv: list[str], prompt: Path, log: Path, seconds: float, slot: str = ".") -> int | None:
    """Run one agent in `slot` to its end; stdout and stderr go to `log`. Returns the exit code, None after a timeout."""
    with prompt.open() as stdin, log.open("w") as out:
        with _LIVE:
            if STOPPED:
                raise InterruptedError("the run is stopped")
            # Its own process group, so the children that edit the tree stop with it.
            proc = subprocess.Popen(argv, cwd=slot, stdin=stdin, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            LIVE.add(proc.pid)
        try:
            return proc.wait(seconds)
        except subprocess.TimeoutExpired:
            return None
        finally:
            # Every exit path, Ctrl-C too: nothing of the agent may write to the tree or the log after this.
            # No such group = all gone; macOS answers EPERM for a group of zombies.
            _kill(proc.pid)
            proc.wait()
            LIVE.discard(proc.pid)


def _kill(group: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGKILL)


def stop() -> None:
    """Kill every running agent and let no other start. For an interrupted run: its ticket threads would let the
    agents finish, and would start the next ones."""
    global STOPPED
    with _LIVE:
        STOPPED = True
        for group in list(LIVE):
            _kill(group)


def cost(log: Path) -> str | None:
    """The dollar cost the CLI reported: claude's result has total_cost_usd; codex reports tokens only."""
    found = re.findall(r'"total_cost_usd":\s*([0-9.]+)', log.read_text(errors="replace"))
    return found[-1] if found else None
