"""Load, validate and hash rules.toml (version 1)."""
import hashlib
import json
import os
import re
from pathlib import Path

from weigh_env import SetupError

DEFAULT = Path(__file__).resolve().parent / "rules.toml"
TABLE = re.compile(r"^\[([A-Za-z0-9_.]+)\]$")
PILLARS = ("regression", "refactoring", "feedback", "maintainability", "economy")


class RulesError(SetupError):
    pass


def parse(text):
    # ponytail: the subset rules.toml uses (dotted [tables], string/number/bool values); tomllib needs Python 3.11 and the system python3 may be older.
    root, table = {}, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip() if '"' not in raw else raw.strip()
        if not line:
            continue
        m = TABLE.match(line)
        if m:
            table = root
            for part in m[1].split("."):
                table = table.setdefault(part, {})
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise RulesError(f"rules.toml line {n}: cannot read {raw!r}")
        value = value.strip()
        try:
            parsed = json.loads(value)
        except ValueError:
            raise RulesError(f"rules.toml line {n}: cannot read value {value!r}")
        (table if table is not None else root)[key.strip()] = parsed
    return root


def load():
    path = Path(os.environ.get("TEST_WEIGH_IN_RULES") or DEFAULT)
    raw = path.read_bytes()
    rules = parse(raw.decode())
    rules["sha256"] = hashlib.sha256(raw).hexdigest()
    validate(rules)
    return rules


def validate(rules):
    if rules.get("version") != 1:
        raise RulesError(f"rules.toml: unsupported version {rules.get('version')!r}")
    for key in PILLARS:
        pillar = rules.get("pillars", {}).get(key)
        if not pillar or not pillar.get("metrics"):
            raise RulesError(f"rules.toml: pillar {key} is missing or has no metrics")
        for mid, m in pillar["metrics"].items():
            if m["target"] == m["bad"]:
                raise RulesError(f"rules.toml: {pillar['name']}: {m['name']} has target and bad anchors both {m['target']}")
        total = sum(m["weight"] for m in pillar["metrics"].values())
        if abs(total - 1) > 1e-9:
            raise RulesError(f"rules.toml: {pillar['name']} weights sum to {total:g}, not 1")
