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
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

SKILL = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Lens:
    """A lens of the full final review: a role with no model list, on an entry of the `final-reviewer` list. Its lines
    of the prompt are `prompts/lens-<source>.md`."""
    source: str  # the `source` of its findings, and its name in the prompt
    skill: str  # the skill that holds its method
    method: str  # the file of that skill it follows
    inline: bool  # its lines hold the method text, not its path
    other_provider: bool  # it runs on another provider than the implementer's first entry


# In the order a tie of the merge keeps.
LENSES = {
    "final-spec": Lens("spec", "review-diff", "spec.md", False, False),
    # Inlined: this lens reads from git, and a project-local review-diff sits in an ignored folder (#120).
    "final-correctness": Lens("correctness", "review-diff", "correctness.md", True, True),
    "final-structure": Lens("structure", "deep-quality-review", "SKILL.md", False, False)}
# Minutes per role.
TIMEOUTS = {"test-writer": 30, "implementer": 90, "reviewer": 45, "fixer": 60, "final-reviewer": 60,
            **dict.fromkeys(LENSES, 60), "conflict": 30, "pr-writer": 20}
PROVIDERS = {"anthropic": ("claude", ("low", "medium", "high", "xhigh", "max")),
             "openai": ("codex", ("minimal", "low", "medium", "high", "xhigh"))}
# USD per million tokens at the API list price; the cached part of the input costs `cached_input`. A
# codex model that is not here keeps its tokens, and the ledger says it is not priced. Update when a price changes.
PRICES = {"gpt-6-astra": {"input": 10.0, "cached_input": 1.0, "output": 50.0},
          "gpt-6.1-sol": {"input": 2.0, "cached_input": 0.10, "output": 10.0}}
# The name of each token count in the agent record, and its key in the `usage` of a codex turn.
_USAGE = {"input": "input_tokens", "cached_input": "cached_input_tokens", "output": "output_tokens"}
READ_ONLY = ("reviewer", "final-reviewer", *LENSES, "pr-writer")
PREFLIGHT = "implement-spec preflight: reply with the word OK. Read no file and run no command.\n"
# The time limit of one preflight call: a CLI that gives no answer in this time fails the preflight.
PREFLIGHT_MINUTES = 5
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


# Roles of older drivers: a --models file that still names one is told to delete the key.
REMOVED = ("correctness", "explorer")


def _role_errors(given: dict, known: dict, label: str) -> list[str]:
    """One error per role of the config file `label` that the skill's models.json does not define."""
    return [f"{role}: the role was removed; delete its key from {label}" if role in REMOVED else
            f"{role}: unknown role (known: {', '.join(known)}); delete its key from {label}"
            for role in given if role not in known]


# The provider config files, in the order they apply; the --models file applies after them. The repo file is read
# from the worktree the driver runs in.
REPO_CONFIG = Path(".implement-spec/models.json")


def config_files() -> list[Path]:
    """The provider config files that exist: the home file, then the repo file, which wins."""
    home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "implement-spec/models.json"
    return [path for path in (home, REPO_CONFIG) if path.is_file()]


def load_models(path: str | None) -> tuple[dict[str, list[str]], list[str]]:
    """The skill's default lists, then the roles of each config file and of the --models file `path` on top, each
    replacing whole lists, and every role and entry error."""
    models = json.loads((SKILL / "models.json").read_text())
    # (the file, how a role error names it, the prefix of its other errors)
    layers = [(file, str(file), str(file)) for file in config_files()]
    layers += [(Path(path), "the --models file", f"--models {path}")] if path else []
    errors = [error for layer in layers for error in _layer(models, *layer)]
    return ({}, errors) if errors else (models, _entry_errors(models))


def _layer(models: dict[str, list[str]], file: Path, label: str, prefix: str) -> list[str]:
    """Put the roles of one config file on top of `models`; its errors, or []."""
    try:
        given = json.loads(file.read_text())
    except (OSError, ValueError) as exc:
        return [f"{prefix}: {exc!r}"]
    if not isinstance(given, dict):
        return [f"{prefix}: a JSON object of role names is required"]
    if unknown := _role_errors(given, models, label):
        return unknown
    if wrong := [role for role, entries in given.items()
                 if not isinstance(entries, list) or not entries or not all(isinstance(e, str) for e in entries)]:
        return [f"{prefix}: {', '.join(wrong)}: a role needs a non-empty list of model entries"]
    models.update(given)
    return []


def other_provider_entries(models: dict[str, list[str]]) -> list[str]:
    """The entries of the `final-reviewer` list on another provider than the implementer's first entry: the correctness
    lens and the scoped review run and rerun on these when there is one."""
    own = split(models["implementer"][0])[0]
    return [entry for entry in models["final-reviewer"] if split(entry)[0] != own]


def other_provider_entry(models: dict[str, list[str]]) -> str:
    """The other-provider entry of the `final-reviewer` list, its first one; "" when there is none."""
    return next(iter(other_provider_entries(models)), "")


def correctness_entries(models: dict[str, list[str]]) -> list[str]:
    """The entries of the correctness lens and the scoped review: the other-provider ones, or, when the config has none
    or none passed the preflight, the whole `final-reviewer` list (the ledger and the PR body say so)."""
    return other_provider_entries(models) or models["final-reviewer"]


def lens_entries(models: dict[str, list[str]], role: str) -> list[str]:
    """The entries a lens of the final review runs on, the first one first, its rerun on the next one."""
    return correctness_entries(models) if LENSES[role].other_provider else models["final-reviewer"]


def _entry_errors(models: dict[str, list[str]]) -> list[str]:
    """Every entry error of the merged lists."""
    # The entries whose CLI must be on PATH; `_layer` gave each role a non-empty list.
    first = {(role, entries[0]) for role, entries in models.items()} | {
        ("final-reviewer", other_provider_entry(models))}
    found = [_entry_error(role, entry, (role, entry) in first) for role, entries in models.items() for entry in entries]
    if len(models["implementer"]) < 2:
        found.append("implementer: a second implementer model entry is required for fix round 2")
    return [error for error in found if error]


def preflight(models: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Give one short prompt to every entry of every list, all at the same time. Returns (the lists without the
    entries whose CLI failed: a model the account cannot use or is out of credits for, a CLI that is not signed in;
    one error per role with no entry left; one sentence per skipped entry, and one more when no entry on another
    provider than the implementer's first one is left)."""
    failed = _failures(sorted({entry for role in models.values() for entry in role}))
    kept = {role: [entry for entry in entries if entry not in failed] for role, entries in models.items()}
    errors = [_no_entry_left(role, models[role], failed) for role, entries in kept.items() if not entries]
    return kept, errors, [] if errors else _skipped(models, kept, failed)


def _no_entry_left(role: str, entries: list[str], failed: dict[str, str]) -> str:
    """The error of a role whose every entry failed the preflight."""
    return f"{role}: every model entry failed the preflight: " + "; ".join(
        f"model entry {entry}: {failed[entry]}" for entry in entries)


def _failures(entries: list[str]) -> dict[str, str]:
    """{entry: why} for each entry whose CLI fails the preflight prompt."""
    with tempfile.TemporaryDirectory(prefix="implement-spec-preflight-") as folder:
        def check(index: int, entry: str) -> str:
            cli = PROVIDERS[split(entry)[0]][0]
            if not shutil.which(cli):
                return f"{cli} is not on PATH"
            prompt, log = (Path(folder) / f"{index}{suffix}" for suffix in (".prompt.md", ".log"))
            prompt.write_text(PREFLIGHT)
            # A read-only command: the agent can change nothing, and codex writes its answer to the -o file.
            code = launch(command("reviewer", entry, str(Path(folder) / f"{index}.json")), prompt, log,
                          PREFLIGHT_MINUTES * 60)
            if code == 0:
                return ""
            why = f"no answer in {PREFLIGHT_MINUTES} minutes" if code is None else f"exit {code}"
            return f"the {cli} CLI failed the preflight ({why}): {last_words(log)}"

        with ThreadPoolExecutor(len(entries)) as pool:
            return {entry: why for entry, why in zip(entries, pool.map(check, range(len(entries)), entries)) if why}


def _skipped(models: dict[str, list[str]], kept: dict[str, list[str]], failed: dict[str, str]) -> list[str]:
    """One ledger sentence per skipped entry, and one when the correctness lens lost its other provider."""
    skipped = [f"Model entry {entry} is skipped for this invocation: {why.rstrip('.')}; each role that lists it "
               f"goes on with its next entry ({', '.join(role for role in models if entry in models[role])})."
               for entry, why in failed.items()]
    if not other_provider_entry(kept):
        # Another provider for correctness is a preference: a config with none runs, and says so.
        other = f"on another provider than {kept['implementer'][0]}"
        why = f"{other} passed the preflight" if other_provider_entry(models) else f"is {other}"
        skipped.append(f"No final-reviewer entry {why}: the correctness lens and the scoped review run on "
                       f"{', '.join(kept['final-reviewer'])}, the implementer's provider.")
    return skipped


def last_words(log: Path) -> str:
    """The last line of an agent log, or the `result` of the last claude stream-json line or the error `message` of
    the last codex --json line: the error a failed CLI gave."""
    lines = [line for line in log.read_text(errors="replace").splitlines() if line.strip()] if log.exists() else []
    if not lines:
        return "no output"
    with contextlib.suppress(ValueError, AttributeError):
        event = json.loads(lines[-1])
        error = event.get("error")
        return str(event.get("result") or event.get("message") or (isinstance(error, dict) and error.get("message"))
                   or lines[-1])[-500:]
    return lines[-1][-500:]


def command(role: str, entry: str, report: str) -> list[str]:
    """The CLI call for one agent; the prompt goes to its stdin."""
    provider, model, effort = split(entry)
    if provider == "openai":
        # No --ephemeral: the rollout on disk is the trace. A read-only agent may not write its report, so -o saves
        # the last message next to it; settle_report picks one. -o on the report itself overwrites a written report.
        # A role that commits needs full access: workspace-write keeps .git read-only, even under --add-dir.
        tail = ["--sandbox", "read-only", "-o", last_message(report)] if role in READ_ONLY else [
            "--sandbox", "danger-full-access"]
        # --json: the log carries the token usage of each turn, which prices the run.
        return ["codex", "exec", "--json", "--model", model, "-c", f'model_reasoning_effort="{effort}"', *tail]
    # stream-json keeps tool output in the log and ends with a result that has total_cost_usd.
    return ["claude", "-p", "--model", model, "--effort", effort, "--permission-mode", "auto",
            "--output-format", "stream-json", "--verbose"]


def last_message(report: str | Path) -> str:
    """The file where codex saves the last message of a read-only agent."""
    return f"{report}.last"


def settle_report(report: Path) -> None:
    """The report is the one the agent wrote when it parses, else the saved last message, when there is one."""
    last = Path(last_message(report))
    if not last.exists():
        return
    with contextlib.suppress(OSError, ValueError):
        json.loads(report.read_text())
        return
    report.write_text(last.read_text())


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


def tokens(log: Path) -> dict[str, int]:
    """The tokens a codex agent used, summed over the turns of its --json log; {} for a log with no turn."""
    used: dict[str, int] = {}
    for line in log.read_text(errors="replace").splitlines():
        with contextlib.suppress(ValueError, AttributeError, TypeError):
            event = json.loads(line)
            if event.get("type") == "turn.completed":
                for name, key in _USAGE.items():
                    used[name] = used.get(name, 0) + int(event["usage"].get(key, 0))
    return used


def cost(log: Path, entry: str, used: dict[str, int]) -> float | None:
    """The dollar cost of an agent: the one the claude CLI reported, or the tokens `used` of a codex agent at the list
    price of its model; None when there is neither."""
    provider, model, _ = split(entry)
    if provider == "openai":
        if not used or not (price := PRICES.get(model)):
            return None
        uncached = used["input"] - used["cached_input"]
        return round((uncached * price["input"] + used["cached_input"] * price["cached_input"]
                      + used["output"] * price["output"]) / 1e6, 4)
    found = re.findall(r'"total_cost_usd":\s*([0-9.]+)', log.read_text(errors="replace"))
    return float(found[-1]) if found else None
