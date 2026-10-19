"""Structure and behavior-preservation tests for the merge_risk refactor (ticket 17).

Golden outputs were recorded from merge_risk.py before the refactor (commit 74ccb2c).
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "merge_risk.py"
BRANCHES = (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.IfExp,
            ast.comprehension)

CASES = {'valid': 'door: two-way\nblast_radius: isolated\ntouches: [docs, tests]\nrollback: revert\nverdict: auto\nreasons:\n  - "Only prose"\n', 'inline': 'door: two-way\nblast_radius: isolated\ntouches: [docs, tests]\nrollback: revert\nverdict: auto\nreasons: [a, "b, c"]\n', 'bad': 'door: sideways\ntouches: [docs, styles, ]\nrollback: revert\nrollback: x\nfoo: 1\nreasons: nope\n  junk\n', 'emptyreasons': 'door: two-way\nblast_radius: isolated\ntouches: [docs, tests]\nrollback: revert\nverdict: auto\nreasons:\n', 'badreasons': 'door: two-way\nblast_radius: isolated\ntouches: [docs, tests]\nrollback: revert\nverdict: auto\nreasons:\n  - a: b\n  - ""\n  - [x]\n  - \'q\'x\'\n'}
GOLDEN = {'valid:parse': [0, '{"door": "two-way", "blast_radius": "isolated", "rollback": "revert", "verdict": "auto", "touches": ["docs", "tests"], "reasons": ["Only prose"], "errors": []}\n'], 'valid:validate': [0, ''], 'inline:parse': [0, '{"door": "two-way", "blast_radius": "isolated", "rollback": "revert", "verdict": "auto", "touches": ["docs", "tests"], "reasons": ["a", "b, c"], "errors": []}\n'], 'inline:validate': [0, ''], 'bad:parse': [0, '{"door": "sideways", "blast_radius": null, "rollback": "revert", "verdict": null, "touches": ["docs", "styles", ""], "reasons": [], "errors": ["touches has an empty element", "duplicate key: rollback", "unknown key: foo", "reasons must be a `- item` list or an inline list", "unsupported line: junk", "missing key: blast_radius", "missing key: verdict", "door: \'sideways\' is not one of one-way | two-way", "unknown category: styles", "reasons must have at least one item"]}\n'], 'bad:validate': [1, "touches has an empty element\nduplicate key: rollback\nunknown key: foo\nreasons must be a `- item` list or an inline list\nunsupported line: junk\nmissing key: blast_radius\nmissing key: verdict\ndoor: 'sideways' is not one of one-way | two-way\nunknown category: styles\nreasons must have at least one item\n"], 'emptyreasons:parse': [0, '{"door": "two-way", "blast_radius": "isolated", "rollback": "revert", "verdict": "auto", "touches": ["docs", "tests"], "reasons": [], "errors": ["reasons must have at least one item"]}\n'], 'emptyreasons:validate': [1, 'reasons must have at least one item\n'], 'badreasons:parse': [0, '{"door": "two-way", "blast_radius": "isolated", "rollback": "revert", "verdict": "auto", "touches": ["docs", "tests"], "reasons": [], "errors": ["unsupported reason form: a: b", "empty reason", "unsupported reason form: [x]", "malformed quoted reason: \'q\'x\'", "reasons must have at least one item"]}\n'], 'badreasons:validate': [1, "unsupported reason form: a: b\nempty reason\nunsupported reason form: [x]\nmalformed quoted reason: 'q'x'\nreasons must have at least one item\n"]}

GATE_GOLDEN = '{"verdict": "human", "reasons": ["diff touches logic, not in touches", "agentMerge is off"], "categories": ["docs", "logic"], "countedLines": 1}\n'


def complexity(fn):
    """McCabe complexity of one function, nested functions counted separately."""
    n, stack = 1, list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        n += isinstance(node, BRANCHES)
        n += len(node.ifs) if isinstance(node, ast.comprehension) else 0
        n += len(node.values) - 1 if isinstance(node, ast.BoolOp) else 0
        stack.extend(ast.iter_child_nodes(node))
    return n


def functions(path):
    tree = ast.parse(path.read_text())
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_no_function_is_more_complex_than_8():
    too_complex = {f"{p.name}:{f.name}": complexity(f)
                   for p in (SCRIPT, HERE / "merge_policy.py") for f in functions(p)
                   if complexity(f) > 8}
    assert too_complex == {}


def test_parse_block_is_gone_or_under_15_lines():
    for f in functions(SCRIPT):
        if f.name == "parse_block":
            assert f.end_lineno - f.lineno + 1 < 15


def test_policy_lives_in_merge_policy_and_merge_risk_still_exposes_it():
    code = ("import merge_risk, merge_policy;"
            "[getattr(merge_risk, n) for n in "
            "('git','load_policy','DEFAULT_PATHS','POLICY_FILE','file_categories')];"
            "assert merge_risk.load_policy is merge_policy.load_policy;"
            "assert merge_policy.POLICY_FILE and merge_policy.LOCKFILES and merge_policy.DEFAULT_POLICY")
    proc = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_parse_and_validate_output_is_byte_identical_to_before_the_refactor(tmp_path):
    for key, (code, out) in GOLDEN.items():
        name, cmd = key.split(":")
        f = tmp_path / "body.md"
        f.write_text("x\n```merge-risk v1\n" + CASES[name] + "```\n")
        proc = subprocess.run([sys.executable, str(SCRIPT), cmd, str(f)],
                              capture_output=True, text=True, cwd=tmp_path)
        assert (proc.returncode, proc.stdout) == (code, out), key


def test_gate_output_is_byte_identical_to_before_the_refactor(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, env=env, text=True,
                              capture_output=True, check=True).stdout.strip()

    git("init", "-q", "-b", "work")
    (repo / "README.md").write_text("hi\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("update-ref", "refs/remotes/origin/main", git("rev-parse", "HEAD"))
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / "docs.md").write_text("doc\n")
    git("add", "-A")
    git("commit", "-qm", "head")
    body = tmp_path / "body.md"
    body.write_text("x\n```merge-risk v1\n" + CASES["valid"] + "```\n")
    proc = subprocess.run([sys.executable, str(SCRIPT), "gate", "--body", str(body),
                           "--base", "main", "--head", git("rev-parse", "HEAD")],
                          capture_output=True, text=True, cwd=repo, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == GATE_GOLDEN
