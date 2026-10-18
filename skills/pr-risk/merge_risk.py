#!/usr/bin/env python3
"""Parse and validate the ```merge-risk v1 block of a PR body.

Usage: merge_risk.py parse|validate <body-file>
       merge_risk.py gate --body <file> --base <ref> --head <sha>
"""
import json
import re
import subprocess
import sys

CATEGORIES = ["docs", "tests", "ci", "deps", "config", "schema/migration", "auth/security",
              "public-api", "infra", "money", "data-delete", "ui", "logic"]
ENUMS = {
    "door": ["one-way", "two-way"],
    "blast_radius": ["isolated", "module", "service", "consumers"],
    "rollback": ["revert", "revert+redeploy", "data-fix", "impossible"],
    "verdict": ["auto", "human"],
}
KEYS = [*ENUMS, "touches", "reasons"]
FENCE = re.compile(r"^```merge-risk v1[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)


def strip_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i].rstrip()
    return line.rstrip()


def unquote(v):
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v


def split_inline(value):
    """Split the inside of `[a, b]` on commas outside quotes; keep empty elements."""
    parts, cur, quote = [], "", None
    for ch in value:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            parts.append(cur.strip())
            cur = ""
            continue
        cur += ch
    parts.append(cur.strip())
    return parts


def reason_error(v):
    """Why a reason is not a plain or fully quoted scalar, else None."""
    if not v:
        return "empty reason"
    if v[0] in "\"'":
        if len(v) < 2 or v[-1] != v[0] or v[0] in v[1:-1]:
            return f"malformed quoted reason: {v}"
        return "empty reason" if not v[1:-1].strip() else None
    if v[0] in "[{-" or re.search(r":(\s|$)", v):
        return f"unsupported reason form: {v}"
    return None


def add_reason(fields, errors, v):
    err = reason_error(v)
    if err:
        errors.append(err)
    else:
        fields["reasons"].append(unquote(v))


def parse_block(text):
    """Return (fields, errors) for the inside of one block."""
    fields, errors, in_reasons = {}, [], False
    for raw in text.splitlines():
        line = strip_comment(raw)
        if not line.strip():
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and in_reasons:
            add_reason(fields, errors, item.group(1).strip())
            continue
        m = re.match(r"^([a-z_]+):[ \t]*(.*)$", line)
        if not m:
            errors.append(f"unsupported line: {line.strip()}")
            continue
        key, value = m.group(1), m.group(2).strip()
        in_reasons = key == "reasons"
        if key not in KEYS:
            errors.append(f"unknown key: {key}")
        elif key in fields:
            errors.append(f"duplicate key: {key}")
        elif key == "reasons":
            fields[key] = []
            if value.startswith("[") and value.endswith("]"):
                for v in split_inline(value[1:-1]):
                    add_reason(fields, errors, v)
            elif value:
                errors.append("reasons must be a `- item` list or an inline list")
        elif key == "touches":
            if value.startswith("[") and value.endswith("]"):
                fields[key] = split_inline(value[1:-1]) if value[1:-1].strip() else []
                if "" in fields[key]:
                    errors.append("touches has an empty element")
            else:
                fields[key] = []
                errors.append("touches must be an inline list like [docs, tests]")
        else:
            fields[key] = unquote(value)
    for key in KEYS:
        if key not in fields:
            errors.append(f"missing key: {key}")
    for key, allowed in ENUMS.items():
        if key in fields and fields[key] not in allowed:
            errors.append(f"{key}: {fields[key]!r} is not one of {' | '.join(allowed)}")
    for c in fields.get("touches", []):
        if c and c not in CATEGORIES:
            errors.append(f"unknown category: {c}")
    if "reasons" in fields and not fields["reasons"]:
        errors.append("reasons must have at least one item")
    return fields, errors


def parse(body):
    blocks = FENCE.findall(body)
    if not blocks:
        fields, errors = {}, ["no merge-risk block"]
    elif len(blocks) > 1:
        fields, errors = {}, ["more than one block"]
    else:
        fields, errors = parse_block(blocks[0])
    out = {k: fields.get(k) for k in KEYS}
    out["errors"] = errors
    return out


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
        if key not in POLICY_TYPES:
            errors.append(f"policy: unknown key {key}")
        elif not POLICY_TYPES[key](value):
            errors.append(f"policy: wrong type for {key}")
        elif key == "paths" and any(
                "[" in g or "]" in g for globs in value.values() for g in globs):
            errors.append("policy: unsupported glob syntax in paths")
        else:
            policy[key] = value
    return policy, errors


def glob_re(glob):
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        else:
            c = glob[i]
            out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
            i += 1
    return re.compile(out + r"\Z")


def file_categories(path, paths):
    cats = {c for c, globs in paths.items() if any(glob_re(g).match(path) for g in globs)}
    if "logic" in cats:
        cats.discard("docs")
    return cats or {"logic"}


def is_generated(path, base, head):
    if path.endswith(".snap") or any(glob_re(g).match(path) for g in DEFAULT_PATHS["deps"]):
        return True
    # the head version decides; only a deleted file falls back to its base version
    blob = subprocess.run(["git", "show", f"{head}:{path}"], capture_output=True)
    if blob.returncode:
        blob = subprocess.run(["git", "show", f"{base}:{path}"], capture_output=True)
    return b"@generated" in blob.stdout


def diff_stats(base, head, policy):
    """Return (categories, counted_lines, unknown_paths, policy_changed); base is a commit sha."""
    diff_base = git("merge-base", base, head).stdout.strip()
    paths = {c: list(g) for c, g in DEFAULT_PATHS.items()}
    for c, globs in policy["paths"].items():
        paths[c] = paths.get(c, []) + globs
    out = git("diff", "-z", "--numstat", "--no-renames", diff_base, head).stdout
    cats, counted, unknown, policy_changed = set(), 0, [], False
    for line in filter(None, out.split("\0")):
        added, removed, path = line.split("\t", 2)
        fc = file_categories(path, paths)
        cats |= fc
        policy_changed |= path == POLICY_FILE
        if "docs" in fc or is_generated(path, diff_base, head):
            continue
        if added == "-":  # binary, or hidden by a -diff attribute: the size is unknown
            unknown.append(path)
            continue
        counted += int(added) + int(removed)
    return cats, counted, unknown, policy_changed


def gate(body, base, head):
    block = parse(body)
    base = git("rev-parse", "--verify", f"origin/{base}^{{commit}}").stdout.strip()
    policy, reasons = load_policy(base)
    cats, counted, unknown, policy_changed = diff_stats(base, head, policy)
    touches = set()
    if "no merge-risk block" in block["errors"]:
        reasons.append("no merge-risk block")
    elif block["errors"]:
        reasons += [f"invalid block: {e}" for e in block["errors"]]
    else:
        touches = set(block["touches"])
        if block["door"] == "one-way":
            reasons.append("door one-way")
        if block["rollback"] != "revert":
            reasons.append(f"rollback {block['rollback']}")
        reasons += [f"diff touches {c}, not in touches" for c in sorted(cats - touches)]
    if not policy["agentMerge"]:
        reasons.append("agentMerge is off")
    reasons += [f"{c} is always human" for c in sorted(touches | cats)
                if c in policy["alwaysHuman"]]
    if policy_changed:
        reasons.append(f"config: {POLICY_FILE} changed")
    reasons += [f"unknown line count: {p}" for p in unknown]
    if counted > policy["judgeMaxLines"]:
        reasons.append(f"{counted} counted lines > {policy['judgeMaxLines']}")
    return {"verdict": "human" if reasons else "auto", "reasons": reasons,
            "categories": sorted(cats), "countedLines": counted}


def main(argv):
    if len(argv) == 8 and argv[1] == "gate" and argv[2::2] == ["--body", "--base", "--head"]:
        with open(argv[3]) as f:
            print(json.dumps(gate(f.read(), argv[5], argv[7])))
        return 0
    if len(argv) != 3 or argv[1] not in ("parse", "validate"):
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[2]) as f:
        result = parse(f.read())
    if argv[1] == "parse":
        print(json.dumps(result))
        return 0
    for e in result["errors"]:
        print(e)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
