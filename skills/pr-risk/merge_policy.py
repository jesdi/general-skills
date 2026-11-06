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
# Risk of each category, 1 (harmless) to 10 (never merged by an agent). A repo overrides
# single entries with `categoryRisk`.
CATEGORY_RISK = {"docs": 1, "tests": 2, "ui": 3, "logic": 4, "deps": 5, "config": 6, "ci": 7,
                 "public-api": 7, "infra": 8, "schema/migration": 9, "auth/security": 10,
                 "money": 10, "data-delete": 10}
# A public repo never merges above this risk without a human; a repo that sets no
# `maxAutoRisk` gets it too.
PUBLIC_MAX_RISK = 3
DEFAULT_POLICY = {"maxAutoRisk": PUBLIC_MAX_RISK, "categoryRisk": {}, "paths": {},
                  "mergeMethod": None, "judgeMaxLines": 1500, "waitMinutes": 60}
REPLACED_KEYS = {"agentMerge": "maxAutoRisk", "alwaysHuman": "categoryRisk"}


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_risk(v, low=1):
    return is_int(v) and low <= v <= 10


def is_str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


POLICY_TYPES = {
    "maxAutoRisk": lambda v: is_risk(v, low=0),
    "categoryRisk": lambda v: isinstance(v, dict) and all(
        c in CATEGORIES and is_risk(r) for c, r in v.items()),
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


def has_bracket_glob(paths):
    return any("[" in g or "]" in g for globs in paths.values() for g in globs)


def policy_error(key, value):
    """Why one policy entry is rejected, else None."""
    if key in REPLACED_KEYS:
        return f"policy: {key} was replaced by {REPLACED_KEYS[key]}"
    if key not in POLICY_TYPES:
        return f"policy: unknown key {key}"
    if not POLICY_TYPES[key](value):
        return f"policy: wrong type for {key}"
    if key == "paths" and has_bracket_glob(value):
        return "policy: unsupported glob syntax in paths"
    return None


def load_policy(base):
    """Policy from the base commit, never the PR head. Returns (policy, errors).

    No policy file on the base is an error: a PR must not merge itself under a policy it adds."""
    p = git("show", f"{base}:{POLICY_FILE}", check=False)
    if p.returncode:
        return dict(DEFAULT_POLICY), [f"policy: no {POLICY_FILE} on the base"]
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
