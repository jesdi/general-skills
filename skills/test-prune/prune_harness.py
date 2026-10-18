"""Test harness for the test-prune black-box tests: a fixture target repo plus agent stand-ins.

`target` builds, per test, a git repo shaped like portfolio_evaluator (backend/tests,
backend/mutation_baseline.json, make targets) whose make targets call testdata/repo/fake_engine.py,
plus a bare `origin` with `main` pushed and a fake `gh` first on PATH. Logs land outside the repo.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRUNE = HERE / "prune.py"
TESTDATA = HERE / "testdata"
DATE = "2026-10-17"
PRUNE_BRANCH = f"chore/test-prune-{DATE}"
STRENGTHEN_BRANCH = f"chore/test-strengthen-{DATE}"
R = "tests/test_rebalancer.py::"
C = "tests/test_concentration.py::"
REFERENCE = C + "test_hhi_reference"


def case(i):
    return f"{C}test_hhi_case_{i:02d}"


def cand(test_id, into=REFERENCE, unprotected=False):
    entry = {"test_id": test_id, "arc_count": 3, "contained_by": [into]}
    if unprotected:
        entry["unprotected"] = True
    return entry


def git_cmd(root, *args, check=True):
    return subprocess.run(["git", *args], cwd=root, check=check, capture_output=True, text=True)


class Target:
    def __init__(self, tmp):
        self.tmp = tmp
        self.root = tmp / "repo"
        self.origin = tmp / "origin.git"
        self.logs = tmp / "logs"
        self.state = tmp / "state"
        self.bin = tmp / "bin"
        self.logs.mkdir()
        self.bin.mkdir()
        shutil.copytree(TESTDATA / "repo", self.root)
        subst = {"@PYTHON@": sys.executable, "@LOGS@": str(self.logs)}
        for path in (self.root / "Makefile", self.bin / "gh"):
            text = (TESTDATA / "gh").read_text() if path.name == "gh" else path.read_text()
            for k, v in subst.items():
                text = text.replace(k, v)
            path.write_text(text)
        (self.bin / "gh").chmod((self.bin / "gh").stat().st_mode | stat.S_IEXEC)
        git_cmd(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git("init", "-q", "-b", "main")
        for k, v in {"user.name": "t", "user.email": "t@example.com", "commit.gpgsign": "false",
                     "core.hooksPath": "/dev/null"}.items():
            self.git("config", k, v)
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "-u", "origin", "main")
        self.base = self.git("rev-parse", "HEAD").strip()

    # --- driving the CLI -----------------------------------------------------------
    def env(self, extra=None):
        env = {k: v for k, v in os.environ.items()
               if k not in ("GITHUB_STEP_SUMMARY", "KEEP_CACHE", "MAKEFLAGS", "MFLAGS", "MAKELEVEL")
               and not k.startswith("FAKE_")}
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env.update(extra or {})
        return env

    def run(self, *args, env=None, cwd=None):
        """python3 prune.py <args> --state <state> --date DATE, from inside the repo."""
        return subprocess.run([sys.executable, str(PRUNE), *args, "--state", str(self.state), "--date", DATE],
                              cwd=cwd or self.root / "backend", env=self.env(env), capture_output=True, text=True)

    def ok(self, *args, env=None):
        proc = self.run(*args, env=env)
        assert proc.returncode == 0, f"prune.py {' '.join(args)} exited {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        return proc

    # --- playing the agents ----------------------------------------------------------
    def set_candidates(self, candidates):
        (self.logs / "candidates.json").write_text(json.dumps(sorted(candidates, key=lambda c: c["test_id"])))

    def proposals(self, deletions=None, strengthen=None, groups=None):
        """Fixer output: every fixer-input candidate and group gets one verdict.

        deletions {test_id: into}, strengthen {test_id: assertion}, groups {group: {verdict, mutant_ids,
        test, reason}}; other candidates are kept; other groups get a strengthen proposal.
        """
        deletions, strengthen, groups = deletions or {}, strengthen or {}, groups or {}
        fixer_input = self.read_state("fixer-input.json")
        cands = []
        for c in fixer_input["candidates"]:
            tid = c["test_id"]
            if tid in deletions:
                cands.append({"test_id": tid, "verdict": "delete", "into": deletions[tid],
                              "reason": f"fixer: covered by {deletions[tid]}"})
            elif tid in strengthen:
                cands.append({"test_id": tid, "verdict": "strengthen", "assertion": strengthen[tid],
                              "reason": "fixer: weak assertion"})
            else:
                cands.append({"test_id": tid, "verdict": "keep", "reason": "fixer: distinct behaviour"})
        spots = []
        for w in fixer_input["weak_spots"]:
            spots.append(dict({"group": w["group"], "verdict": "strengthen", "mutant_ids": w["mutant_ids"],
                               "test": "tests/test_rebalancer.py", "reason": "fixer: add an assertion"},
                              **groups.get(w["group"], {})))
        return {"candidates": cands, "weak_spots": spots}

    def review_input(self, proposals, env=None):
        path = self.tmp / "proposals.json"
        path.write_text(json.dumps(proposals))
        return self.run("review-input", "--proposals", str(path), env=env)

    def reviews(self, accept=(), reasons=None, ids=None):
        """Reviewer output over every review-input proposal (or `ids`): accepted iff in `accept`."""
        reasons = reasons or {}
        ids = ids if ids is not None else [p["id"] for p in self.read_state("review-input.json")["proposals"]]
        path = self.tmp / f"reviews-{len(list(self.tmp.glob('reviews-*')))}.json"
        path.write_text(json.dumps({"reviews": [
            {"id": i, "verdict": "accept" if i in accept else "reject",
             "reason": reasons.get(i, f"reviewer: {'agreed' if i in accept else 'not this run'} for {i}")}
            for i in ids]}))
        return path

    def prepare(self, candidates, unattended=True, accept=(), reasons=None, env=None, before=None, **proposal_kw):
        """collect → review-input → batch (each preceded by `before()`); returns the batch run."""
        before = before or (lambda: None)
        self.set_candidates(candidates)
        before()
        self.ok("collect", *(["--unattended"] if unattended else []), env=env)
        before()
        proc = self.review_input(self.proposals(**proposal_kw), env=env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        before()
        return self.ok("batch", "--reviews", str(self.reviews(accept, reasons)), env=env)

    def commit_test(self, item, test_source, file="backend/tests/test_rebalancer.py"):
        """Strengthen agent: append a test and commit it with the item trailer."""
        path = self.root / file
        path.write_text(path.read_text() + "\n\n" + test_source.strip("\n") + "\n")
        self.git("add", file)
        self.git("commit", "-q", "-m", f"test: strengthen {item}\n\nTest-Prune-Item: {item}")

    # --- observing ---------------------------------------------------------------------
    def git(self, *args):
        return git_cmd(self.root, *args).stdout

    def read_state(self, name):
        return json.loads((self.state / name).read_text())

    def log(self, name):
        path = self.logs / f"{name}.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def pr_creates(self):
        return [c for c in self.log("gh") if c["argv"][:2] == ["pr", "create"]]

    def remote_branches(self):
        out = git_cmd(self.tmp, "--git-dir", str(self.origin), "for-each-ref", "--format=%(refname:short)",
                      "refs/heads").stdout
        return set(out.split())

    def remote_sha(self, branch):
        return git_cmd(self.tmp, "--git-dir", str(self.origin), "rev-parse", f"refs/heads/{branch}").stdout.strip()

    def show(self, ref, path):
        return self.git("show", f"{ref}:{path}")

    def ids_at(self, ref):
        """Test ids collected at ref, by the fake engine's collector (pytest's shape)."""
        sys.path.insert(0, str(self.root))
        try:
            import fake_engine
        finally:
            sys.path.pop(0)
        return set(fake_engine.collect_ref(self.root, ref))

    def junk_mutants(self):
        """A stale cache claiming every mutant killed, as a kept nightly cache would."""
        mutants = self.root / "backend" / "mutants"
        (mutants / "services").mkdir(parents=True, exist_ok=True)
        (mutants / "junk.txt").write_text("stale\n")
        kill_map = json.loads((self.root / "kill_map.json").read_text())
        meta = {"exit_code_by_key": {f"services.rebalancer.{m}": 1 for m in kill_map["services/rebalancer.py"]},
                "hash_by_function_name": {}}
        (mutants / "services" / "rebalancer.py.meta").write_text(json.dumps(meta))


def arg(argv, flag):
    """Value of `flag` in an argv list (None when absent)."""
    return argv[argv.index(flag) + 1] if flag in argv else None


def lines_with(text, needle):
    return "\n".join(line for line in text.splitlines() if needle in line)
