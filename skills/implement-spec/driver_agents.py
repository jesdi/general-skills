"""Model entries, the command line per provider, prompt rendering and the blocking launch.

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
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent
# Minutes per role.
TIMEOUTS = {"explorer": 30, "test-writer": 30, "implementer": 90, "reviewer": 45, "correctness": 30, "fixer": 60,
            "final-reviewer": 60, "conflict": 30, "pr-writer": 20}
PROVIDERS = {"anthropic": ("claude", ("low", "medium", "high", "xhigh", "max")),
             "openai": ("codex", ("minimal", "low", "medium", "high", "xhigh"))}
READ_ONLY = ("reviewer", "correctness", "final-reviewer", "pr-writer")
# A process group is here only while its leader is not reaped: then no other process can have its id.
LIVE: set[int] = set()
STOPPED = False
_LIVE = threading.Lock()


def split(entry: str) -> tuple[str, str, str]:
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
    if first and not shutil.which(cli):
        return f"{role}: {cli} is not on PATH (model entry {entry})"
    return None


def correctness_entry(models: dict[str, list[str]]) -> str:
    """The first correctness entry on another provider than the implementer's first entry; "" when there is none."""
    own = split(models["implementer"][0])[0]
    return next((entry for entry in models["correctness"] if split(entry)[0] != own), "")


def load_models(path: str | None) -> tuple[dict[str, list[str]], list[str]]:
    """The skill's default lists with the roles of `path` on top, and every entry error."""
    models = json.loads((SKILL / "models.json").read_text())
    try:
        models = {**models, **(json.loads(Path(path).read_text()) if path else {})}  # a list is a TypeError
        # The entry of each role whose CLI must be on PATH; entries[0]: an empty list is an error.
        first = {**{role: entries[0] for role, entries in models.items()}, "correctness": correctness_entry(models)}
        found = [_entry_error(role, entry, entry == first[role]) for role, entries in models.items() for entry in entries]
        if not first["correctness"]:
            found.append(f"correctness: no entry of its list ({', '.join(models['correctness'])}) is on another "
                         f"provider than the implementer's first entry {models['implementer'][0]}; the correctness "
                         "pass never runs on the implementer's provider")
    except (OSError, ValueError, AttributeError, LookupError, TypeError) as exc:
        return {}, [f"--models {path}: {exc!r}"]
    return models, [error for error in found if error]


def command(role: str, entry: str, report: str) -> list[str]:
    """The CLI call for one agent; the prompt goes to its stdin."""
    provider, model, effort = split(entry)
    if provider == "openai":
        # No --ephemeral: the rollout on disk is the trace. A read-only agent cannot write its report, so -o saves it.
        tail = ["--sandbox", "read-only", "-o", report] if role in READ_ONLY else ["--sandbox", "workspace-write"]
        return ["codex", "exec", "--model", model, "-c", f'model_reasoning_effort="{effort}"', *tail]
    # stream-json keeps tool output in the log and ends with a result that has total_cost_usd.
    return ["claude", "-p", "--model", model, "--effort", effort, "--permission-mode", "auto",
            "--output-format", "stream-json", "--verbose"]


def render(name: str, values: dict[str, str]) -> str:
    return re.sub(r"\{([a-z_]+)\}", lambda m: values[m.group(1)], (SKILL / "prompts" / f"{name}.md").read_text())


def tail(prompt: str, report: str) -> str:
    """The end of a rendered prompt, from the line that names the report path."""
    return prompt[prompt.rfind("\n", 0, prompt.rfind(report)):]


def launch(argv: list[str], prompt: Path, log: Path, seconds: float, slot: str = ".") -> int | None:
    """Run one agent in `slot` to its end, output to `log`. Returns the exit code, None after a timeout."""
    with prompt.open() as stdin, log.open("w") as out:
        with _LIVE:
            if STOPPED:
                raise InterruptedError("the run is stopped")
            # Its own process group, so the children that edit the tree stop with it.
            proc = subprocess.Popen(argv, cwd=slot, stdin=stdin, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            LIVE.add(proc.pid)
        try:
            ended = _ended(proc.pid, seconds)
        finally:
            # Kill before the reap, under the lock of stop(): until then no other process can have the group id.
            with _LIVE:
                _kill(proc.pid)
                LIVE.discard(proc.pid)
            code = proc.wait()
        return code if ended else None


def _ended(pid: int, seconds: float) -> bool:
    """True when the child `pid` ends within `seconds`; it is not reaped."""
    # ponytail: waitid has no timeout, so this polls as `Popen.wait(timeout)` does, at most each 50 ms.
    end, pause = time.monotonic() + seconds, 0.0005
    while not os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG):
        left = end - time.monotonic()
        if left <= 0:
            return False
        pause = min(pause * 2, left, 0.05)
        time.sleep(pause)
    return True


def _kill(group: int) -> None:
    """SIGKILL a process group. No such group means all gone; macOS answers EPERM for a group of zombies."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGKILL)


def stop() -> None:
    """Kill every running agent and let no other start."""
    global STOPPED
    with _LIVE:
        STOPPED = True
        for group in list(LIVE):
            _kill(group)


def cost(log: Path) -> str | None:
    """The dollar cost the CLI reported; None for codex, which reports tokens only."""
    found = re.findall(r'"total_cost_usd":\s*([0-9.]+)', log.read_text(errors="replace"))
    return found[-1] if found else None
