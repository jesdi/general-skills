"""Merge-risk policy: categories, default paths, the repo policy file and its validation."""
import json
import subprocess

CATEGORIES = ["docs", "tests", "ci", "deps", "config", "schema/migration", "auth/security",
              "public-api", "infra", "money", "data-delete", "ui", "logic"]
POLICY_FILE = ".merge-risk.json"
LOCKFILES = ["package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "poetry.lock",
             "uv.lock", "Pipfile.lock", "Cargo.lock", "Gemfile.lock", "go.sum", "composer.lock"]
DEFAULT_PATHS = {
    "docs": ["docs/**", "README*", "*.md"],
    "tests": ["tests/**", "**/test_*.py", "**/*_test.py", "**/*.test.*", "**/*.spec.*"],
    "ci": [".github/workflows/**"],
    "deps": [f"**/{n}" for n in LOCKFILES],
    "config": [POLICY_FILE],
    "schema/migration": ["**/migrations/**"],
    "infra": ["**/Dockerfile*"],
    "logic": ["**/SKILL.md", "**/AGENTS.md", "**/CLAUDE.md", "**/prompts/**"],
}
DEFAULT_POLICY = {"agentMerge": False, "paths": {}, "mergeMethod": None,
                  "judgeMaxLines": 1500, "waitMinutes": 60,
                  "alwaysHuman": ["schema/migration", "auth/security", "money", "data-delete",
                                  "infra"]}


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


POLICY_TYPES = {
    "agentMerge": lambda v: isinstance(v, bool),
    "alwaysHuman": lambda v: is_str_list(v) and all(c in CATEGORIES for c in v),
    "paths": lambda v: isinstance(v, dict) and all(
        c in CATEGORIES and is_str_list(g) for c, g in v.items()),
    "mergeMethod": lambda v: v in ("rebase", "merge", "squash"),
    "judgeMaxLines": is_int,
    "waitMinutes": is_int,
}


def git(*args, check=True):
    p = subprocess.run(["git", *args], text=True, capture_output=True)
    if check and p.returncode:
        raise SystemExit(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p


def policy_error(key, value):
    """Why one policy entry is rejected, else None."""
    if key not in POLICY_TYPES:
        return f"policy: unknown key {key}"
    if not POLICY_TYPES[key](value):
        return f"policy: wrong type for {key}"
    if key == "paths" and any("[" in g or "]" in g for globs in value.values() for g in globs):
        return "policy: unsupported glob syntax in paths"
    return None


def load_policy(base):
    """Policy from the base commit, never the PR head. Returns (policy, errors)."""
    p = git("show", f"{base}:{POLICY_FILE}", check=False)
    if p.returncode:
        return dict(DEFAULT_POLICY), []
    try:
        raw = json.loads(p.stdout)
    except ValueError:
        return dict(DEFAULT_POLICY), ["policy: invalid JSON"]
    if not isinstance(raw, dict):
        return dict(DEFAULT_POLICY), ["policy: not an object"]
    policy, errors = dict(DEFAULT_POLICY), []
    for key, value in raw.items():
        err = policy_error(key, value)
        if err:
            errors.append(err)
        else:
            policy[key] = value
    return policy, errors
