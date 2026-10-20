#!/usr/bin/env python3
"""Parse and validate the ```merge-risk v1 block of a PR body.

Usage: merge_risk.py parse|validate <body-file>
       merge_risk.py gate --body <file> --base <ref> --head <sha>
"""
import json
import re
import subprocess
import sys

from merge_policy import CATEGORIES, DEFAULT_PATHS, POLICY_FILE, git, load_policy  # noqa: F401

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


def quoted_reason_error(v):
    """Why a quoted reason is malformed or empty, else None."""
    if len(v) < 2 or v[-1] != v[0] or v[0] in v[1:-1]:
        return f"malformed quoted reason: {v}"
    return None if v[1:-1].strip() else "empty reason"


def reason_error(v):
    """Why a reason is not a plain or fully quoted scalar, else None."""
    if not v:
        return "empty reason"
    if v[0] in "\"'":
        return quoted_reason_error(v)
    if v[0] in "[{-" or re.search(r":(\s|$)", v):
        return f"unsupported reason form: {v}"
    return None


def enum_errors(fields):
    """One error per enum field whose value is not allowed."""
    return [f"{k}: {fields[k]!r} is not one of {' | '.join(allowed)}"
            for k, allowed in ENUMS.items() if k in fields and fields[k] not in allowed]


class Block:
    """Fields and errors collected while reading one merge-risk block."""

    def __init__(self):
        self.fields, self.errors, self.in_reasons = {}, [], False

    def add_reason(self, v):
        """Add one reason, or record why it is not a plain or quoted scalar."""
        err = reason_error(v)
        if err:
            self.errors.append(err)
        else:
            self.fields["reasons"].append(unquote(v))

    def add_reasons_key(self, value):
        """Handle `reasons:`: an inline list, or empty before `- item` lines."""
        self.fields["reasons"] = []
        if value.startswith("[") and value.endswith("]"):
            for v in split_inline(value[1:-1]):
                self.add_reason(v)
        elif value:
            self.errors.append("reasons must be a `- item` list or an inline list")

    def add_touches_key(self, value):
        """Handle `touches:`: it must be an inline list without empty elements."""
        if not (value.startswith("[") and value.endswith("]")):
            self.fields["touches"] = []
            self.errors.append("touches must be an inline list like [docs, tests]")
            return
        items = split_inline(value[1:-1]) if value[1:-1].strip() else []
        self.fields["touches"] = items
        if "" in items:
            self.errors.append("touches has an empty element")

    def add_key(self, key, value):
        """Store one `key: value` pair, or record an unknown or duplicate key."""
        self.in_reasons = key == "reasons"
        if key not in KEYS:
            self.errors.append(f"unknown key: {key}")
        elif key in self.fields:
            self.errors.append(f"duplicate key: {key}")
        elif key == "reasons":
            self.add_reasons_key(value)
        elif key == "touches":
            self.add_touches_key(value)
        else:
            self.fields[key] = unquote(value)

    def add_line(self, raw):
        """Route one source line to a `- item` reason, a `key: value` pair, or an error."""
        line = strip_comment(raw)
        if not line.strip():
            return
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and self.in_reasons:
            return self.add_reason(item.group(1).strip())
        m = re.match(r"^([a-z_]+):[ \t]*(.*)$", line)
        if m:
            self.add_key(m.group(1), m.group(2).strip())
        else:
            self.errors.append(f"unsupported line: {line.strip()}")

    def validate(self):
        """Append errors for missing keys, bad enum values, unknown categories, empty reasons."""
        fields = self.fields
        self.errors += [f"missing key: {k}" for k in KEYS if k not in fields]
        self.errors += enum_errors(fields)
        self.errors += [f"unknown category: {c}" for c in fields.get("touches", [])
                        if c and c not in CATEGORIES]
        if "reasons" in fields and not fields["reasons"]:
            self.errors.append("reasons must have at least one item")


def parse_block(text):
    """Return (fields, errors) for the inside of one block."""
    block = Block()
    for raw in text.splitlines():
        block.add_line(raw)
    block.validate()
    return block.fields, block.errors


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


def base_policy(base):
    """(sha of origin/<base>, the policy committed there, policy errors as reasons)."""
    sha = git("rev-parse", "--verify", f"origin/{base}^{{commit}}").stdout.strip()
    return (sha, *load_policy(sha))


def classify_diff(base, head, policy):
    """The one classification of a PR's files that the gate and the judge both read.

    Returns (diff_base, files) for head against its merge-base with base (a commit sha).
    Each file is (path, categories, lines, counted): lines is added plus removed, None when
    unknown (binary, or hidden by a -diff attribute); docs and generated files are not counted.
    Diff drivers of the repo never apply: they could hide counted lines.
    """
    diff_base = git("merge-base", base, head).stdout.strip()
    paths = {c: list(g) for c, g in DEFAULT_PATHS.items()}
    for c, globs in policy["paths"].items():
        paths[c] = paths.get(c, []) + globs
    out = git("diff", "-z", "--numstat", "--no-renames", "--no-textconv", "--no-ext-diff",
              diff_base, head).stdout
    files = []
    for line in filter(None, out.split("\0")):
        added, removed, path = line.split("\t", 2)
        cats = file_categories(path, paths)
        lines = None if added == "-" else int(added) + int(removed)
        files.append((path, cats, lines,
                      "docs" not in cats and not is_generated(path, diff_base, head)))
    return diff_base, files


def diff_stats(files):
    """Return (categories, counted_lines, unknown_paths, policy_changed) of classified files."""
    cats, counted, unknown = set(), 0, []
    for path, file_cats, lines, is_counted in files:
        cats |= file_cats
        if not is_counted:
            continue
        if lines is None:
            unknown.append(path)
        else:
            counted += lines
    return cats, counted, unknown, any(path == POLICY_FILE for path, *_ in files)


def block_reasons(block, cats):
    """Reasons from the parsed merge-risk block, given the categories the diff touches."""
    if "no merge-risk block" in block["errors"]:
        return ["no merge-risk block"]
    if block["errors"]:
        return [f"invalid block: {e}" for e in block["errors"]]
    reasons = []
    if block["door"] == "one-way":
        reasons.append("door one-way")
    if block["rollback"] != "revert":
        reasons.append(f"rollback {block['rollback']}")
    return reasons + [f"diff touches {c}, not in touches"
                      for c in sorted(cats - set(block["touches"]))]


def diff_reasons(policy, touches, stats):
    """Reasons from the policy and the diff stats."""
    cats, counted, unknown, policy_changed = stats
    reasons = [] if policy["agentMerge"] else ["agentMerge is off"]
    reasons += [f"{c} is always human" for c in sorted(touches | cats)
                if c in policy["alwaysHuman"]]
    if policy_changed:
        reasons.append(f"config: {POLICY_FILE} changed")
    reasons += [f"unknown line count: {p}" for p in unknown]
    if counted > policy["judgeMaxLines"]:
        reasons.append(f"{counted} counted lines > {policy['judgeMaxLines']}")
    return reasons


def gate(body, base, head):
    block = parse(body)
    base, policy, reasons = base_policy(base)
    stats = diff_stats(classify_diff(base, head, policy)[1])
    cats, counted = stats[0], stats[1]
    reasons += block_reasons(block, cats)
    touches = set(block["touches"]) if not block["errors"] else set()
    reasons += diff_reasons(policy, touches, stats)
    return {"verdict": "human" if reasons else "auto", "reasons": reasons,
            "categories": sorted(cats), "countedLines": counted}


def is_gate_call(argv):
    return len(argv) == 8 and argv[1] == "gate" and argv[2::2] == ["--body", "--base", "--head"]


def main(argv):
    if is_gate_call(argv):
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
    print(*result["errors"], sep="\n", end="\n" if result["errors"] else "")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
