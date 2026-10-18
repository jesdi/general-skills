"""The target repo: git, make targets, mutmut results, run state and the run summary."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# mutmut's status_by_exit_code, as check_mutation.py reads it.
SURVIVING = {0, 5, 33}
KILLED = {1, 3}
PUSHABLE = re.compile(r"^chore/test-(prune|strengthen)-\d{4}-\d{2}-\d{2}$")
DROPPED_ENV = {"KEEP_CACHE", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "PYTEST_ADDOPTS"}
_FAILED = re.compile(r"✗ (\S+): (\d+) survivors > baseline (\d+)")
_PASSED = re.compile(r"✓ (\S+): (\d+) survivors, baseline (\d+)")


class Fail(Exception):
    """A make target, a gate or a guard failed: exit 1."""


class BadInput(Exception):
    """Invalid input or an unmet precondition: exit 2."""


SUMMARY: list = []
FULL_DIR: list = [None]  # where a summary over LIMIT keeps its full text (the state dir)
LIMIT = 60_000  # characters per PR body or summary block; GitHub refuses a PR body over 65,536


def capped(text: str, full: Path, limit: int = LIMIT) -> str:
    """text, or when it exceeds limit, its start with a visible truncation line (the full text saved to full)."""
    if len(text) <= limit:
        return text
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(text)
    note = f"\n\n… truncated, full text in {full}\n"
    cut = text[:limit - len(note) - 4]
    return cut + ("\n```" if cut.count("```") % 2 else "") + note


def emit(text: str) -> None:
    """Print a run summary; also append it to the GitHub job summary when there is one. Every summary a
    command emits is kept in SUMMARY, in order; one over LIMIT is cut, its full text kept in FULL_DIR."""
    if FULL_DIR[0] is not None:
        text = capped(text, FULL_DIR[0] / f"summary-{len(SUMMARY) + 1}.md")
    SUMMARY.append(text)
    print(text, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(text.rstrip("\n") + "\n\n")


def tail(text: str, lines: int = 30) -> str:
    return "\n".join(text.splitlines()[-lines:])


def fenced(text: str) -> str:
    return f"```\n{text.strip()}\n```"


def parse_ratchet(output: str, returncode: int) -> dict:
    """`make mutation-check` → {ok, output, modules: {module: [survivors, baseline]}, failed: [modules]}."""
    modules, failed = {}, []
    for line in output.splitlines():
        match = _FAILED.search(line) or _PASSED.search(line)
        if match:
            modules[match.group(1)] = [int(match.group(2)), int(match.group(3))]
            if match.re is _FAILED:
                failed.append(match.group(1))
    if returncode and not failed:
        raise Fail(f"make mutation-check failed without a survivor count (exit {returncode})\n\n{fenced(tail(output))}")
    return {"ok": not returncode, "output": output, "modules": modules, "failed": failed}


def ratchet_lines(ratchet: dict) -> list[str]:
    return [line.strip() for line in ratchet["output"].splitlines() if _FAILED.search(line) or _PASSED.search(line)]


class Repo:
    def __init__(self, cwd: Path):
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True)
        if proc.returncode:
            raise BadInput(f"not inside a git repository: {cwd}")
        self.root = Path(proc.stdout.strip())
        self.backend = self.root / "backend"
        self.written: dict = {}  # path → the bytes this run last wrote there (None: the run deleted it)
        self.stranded = ""  # why leave_on stayed off the base branch, when it did

    # --- git -------------------------------------------------------------------------
    def run_git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=self.root,
                              capture_output=True, text=True)

    def git(self, *args: str) -> str:
        proc = self.run_git(*args)
        if proc.returncode:
            raise Fail(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    def git_dir(self) -> Path:
        return Path(self.git("rev-parse", "--absolute-git-dir").strip())

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def ref(self, branch: str) -> str:
        return self.git("rev-parse", f"refs/heads/{branch}").strip()

    def branch(self) -> str:
        proc = self.run_git("symbolic-ref", "--short", "-q", "HEAD")
        if proc.returncode:
            raise BadInput("HEAD is detached: check out the base branch first")
        return proc.stdout.strip()

    def dirty(self) -> list[str]:
        """Paths with tracked changes, from `git status --porcelain -z` (unquoted; a rename gives its new path)."""
        fields, paths = self.git("status", "--porcelain", "-z", "--untracked-files=no").split("\0"), []
        while fields and fields[0]:
            entry = fields.pop(0)
            paths.append(entry[3:])
            if entry[0] in "RC":
                fields.pop(0)
        return paths

    def require_clean(self) -> None:
        dirty = self.dirty()
        if dirty:
            raise BadInput("tracked changes in the working tree, commit or discard them first:\n"
                           + "\n".join(dirty))

    def require_new_branch(self, name: str) -> None:
        local = self.run_git("rev-parse", "--verify", "-q", f"refs/heads/{name}").returncode == 0
        remote = bool(self.git("ls-remote", "--heads", "origin", name).strip())
        if local or remote:
            raise BadInput(f"branch {name} already exists {'locally' if local else 'on origin'}")

    def show(self, commit: str, path: str) -> str | None:
        proc = self.run_git("show", f"{commit}:{path}")
        return proc.stdout if proc.returncode == 0 else None

    def push(self, branch: str) -> None:
        if not PUSHABLE.match(branch):
            raise Fail(f"refusing to push {branch}: only chore/test-prune-<date> and chore/test-strengthen-<date>")
        self.git("push", "-q", "origin", f"refs/heads/{branch}:refs/heads/{branch}")

    def open_pr(self, base: str, branch: str, title: str, body_file: Path) -> str:
        proc = subprocess.run(["gh", "pr", "create", "--base", base, "--head", branch, "--title", title,
                               "--body-file", str(body_file)], cwd=self.root, capture_output=True, text=True)
        if proc.returncode:
            raise Fail(f"gh pr create failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def read(self, path: str):
        """The bytes at path (relative to the repo root), None when there is no file."""
        try:
            return (self.root / path).read_bytes()
        except FileNotFoundError:
            return None

    def unchanged(self, path: str, commit: str) -> bool:
        """Nobody else changed path: it holds what the run last wrote there, or its content at commit."""
        return (path in self.written and self.read(path) == self.written[path]
                or self.run_git("diff", "--quiet", commit, "--", path).returncode == 0)

    def write(self, paths: dict, commit: str) -> None:
        """Write {path: text} for the run, but only over files nobody else changed since (see unchanged):
        otherwise Fail naming them, writing nothing. A run needs a checkout of its own."""
        changed = [path for path in paths if not self.unchanged(path, commit)]
        if changed:
            raise Fail(f"{', '.join(changed)} changed during the run, not by test-prune; left as it is "
                       "(a run needs a checkout of its own)")
        for path, text in paths.items():
            (self.root / path).write_text(text)
            self.own(path)

    def own(self, path: str) -> None:
        """Remember the current content of path (None when missing) as the run's own: leave_on may restore it."""
        self.written[path] = self.read(path)

    def leave_on(self, branch: str, commit: str) -> None:
        """Back on the base branch, undoing only the run's own edits (files still holding what the run
        wrote). Any other tracked change is never discarded: HEAD then stays where it is and `stranded`
        says why (the command then fails). The base ref must not move."""
        dirty = self.dirty()
        foreign = [path for path in dirty if path not in self.written or not self.unchanged(path, commit)]
        if foreign:
            self.stranded = (f"test-prune: staying at {self.head()[:12]}, not on {branch}: tracked changes "
                             f"test-prune did not make: {', '.join(foreign)}; keep or discard them, then "
                             f"`git checkout {branch}`")
        else:
            if dirty:
                self.git("checkout", "-q", "HEAD", "--", *dirty)
            self.git("checkout", "-q", branch)
        if self.ref(branch) != commit:
            raise Fail(f"guard: {branch} moved from {commit} during the run")

    # --- make ------------------------------------------------------------------------
    def make(self, target: str, *assigns: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
        print(f"make {target} {' '.join(assigns)}".rstrip(), flush=True)
        return subprocess.run(["make", "-s", "--no-print-directory", "-C", str(self.root), target, *assigns],
                              env=env, capture_output=True, text=True)

    def must_make(self, target: str, *assigns: str) -> str:
        proc = self.make(target, *assigns)
        if proc.returncode:
            raise Fail(f"make {target} failed (exit {proc.returncode})\n\n{fenced(tail(proc.stdout + proc.stderr))}")
        return proc.stdout

    def cold_mutation(self) -> None:
        """rm -rf backend/mutants, then make mutation: no verdict survives from an earlier run."""
        shutil.rmtree(self.backend / "mutants", ignore_errors=True)
        self.must_make("mutation")

    def cold_ratchet(self) -> dict:
        """A cold mutation run, then make mutation-check."""
        self.cold_mutation()
        proc = self.make("mutation-check")
        return parse_ratchet(proc.stdout, proc.returncode)

    def mutant_codes(self) -> dict[str, dict[str, int]]:
        """{module: {mutant id: exit code}} from backend/mutants/**/*.meta."""
        mutants = self.backend / "mutants"
        codes = {}
        for meta in sorted(mutants.rglob("*.meta")):
            keys = json.loads(meta.read_text()).get("exit_code_by_key", {})
            codes[meta.relative_to(mutants).as_posix()[:-len(".meta")]] = {
                key.rpartition(".")[2]: code for key, code in keys.items()}
        return codes

    def collected_tests(self) -> list[str]:
        text = (self.backend / ".mutation" / "tests.txt").read_text()
        return [line.strip() for line in text.splitlines() if "::" in line]


class State:
    def __init__(self, path: Path):
        self.path = path

    def reset(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(parents=True)

    def load(self, name: str) -> dict:
        path = self.path / name
        if not path.exists():
            raise BadInput(f"{path} is missing: run the earlier test-prune commands first")
        return json.loads(path.read_text())

    def save(self, name: str, data: dict) -> Path:
        return self.save_text(name, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def save_body(self, name: str, text: str) -> tuple[Path, str]:
        """A PR body, capped to fit GitHub with room for the summary around it; the full text, when cut,
        goes to <name>-full.md."""
        body = capped(text, self.path / name.replace(".md", "-full.md"), LIMIT - 1000)
        return self.save_text(name, body), body

    def save_text(self, name: str, text: str) -> Path:
        path = self.path / name
        path.write_text(text)
        return path


def read_agent_json(path: str, key: str) -> list:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise BadInput(f"cannot read {path}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise BadInput(f"{path}: expected an object with a list under {key!r}")
    if not all(isinstance(entry, dict) for entry in data[key]):
        raise BadInput(f"{path}: every entry of {key!r} must be an object")
    return data[key]
