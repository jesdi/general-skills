#!/usr/bin/env python3
"""Parse and validate the ```merge-risk v1 block of a PR body.

Usage: merge_risk.py parse|validate <body-file>
"""
import json
import re
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


def main(argv):
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
