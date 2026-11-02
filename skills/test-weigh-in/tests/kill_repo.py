"""Fixture repos for ticket tw/s7-mutation/14, the default repo with its sources and tests replaced.

Every mutated function has exactly the mutants named here (mutmut 3.8.1: one for a `return x <op> y`, two per `a = b <op> c`).

sample_repo   demo/parser.py   parse(t, u): six `name = prev + u` lines, 12 mutants on 6 lines; noop(): 1 mutant
              tests            test_parse_ok and test_parse_edge each kill every mutant of parse; test_noop kills noop's
matrix_repo   demo/ops.py      seven one-mutant functions: add, sub, mul (x * y: with y == 1 the mutant x / y survives),
                               pack, unpack, wrap, unwrap
              tests            test_a: asserts add, sub and mul: kills the add and sub mutants, not mul's
                               test_b: asserts add and sub: kills the same two
                               test_session_roundtrip: calls pack, unpack, wrap, unwrap and asserts `is not None`: kills none
shift_repo    demo/shift.py    shift(x, y): 1 mutant
              tests            test_parse_invalid and test_shift_other both kill it
"""
from fixture_repo import build_repo, git

PARSER = "def parse(t, u):\n" + "".join(f"    {n} = {p} + u\n" for n, p in zip("abcdef", "tabcde")) + "    return f\n\n\ndef noop():\n    return 7\n"
SAMPLE_TESTS = '''from demo import parser


def test_parse_ok():
    assert parser.parse(1, 2) == 13


def test_parse_edge():
    assert parser.parse(0, 1) == 6


def test_noop():
    assert parser.noop() == 7
'''
OPS = "".join(f"def {n}(x, y):\n    return x {op} y\n\n\n" for n, op in
              (("add", "+"), ("sub", "-"), ("mul", "*"), ("pack", "+"), ("unpack", "-"), ("wrap", "*"), ("unwrap", "/"))).rstrip() + "\n"
MATRIX_TESTS = '''from demo import ops


def test_a():
    assert ops.add(1, 2) == 3
    assert ops.sub(3, 1) == 2
    assert ops.mul(3, 1) == 3


def test_b():
    assert ops.add(2, 2) == 4
    assert ops.sub(5, 1) == 4


def test_session_roundtrip():
    assert ops.pack(1, 2) is not None
    assert ops.unpack(1, 2) is not None
    assert ops.wrap(1, 2) is not None
    assert ops.unwrap(1, 2) is not None
'''
SHIFT = "def shift(x, y):\n    return x + y\n"
SHIFT_TESTS = '''from demo import shift


def test_parse_invalid():
    assert shift.shift(1, 2) == 3


def test_shift_other():
    assert shift.shift(2, 2) == 4
'''


def replaced(path, source, tests):
    repo = build_repo(path)
    for old in [*(repo / "tests").glob("test_*.py"), *(repo / "demo").glob("[!_]*.py")]:
        old.unlink()
    (repo / "demo" / source[0]).write_text(source[1])
    (repo / "tests" / tests[0]).write_text(tests[1])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    return repo


def sample_repo(path):
    return replaced(path, ("parser.py", PARSER), ("test_parser.py", SAMPLE_TESTS))


def matrix_repo(path):
    return replaced(path, ("ops.py", OPS), ("test_ops.py", MATRIX_TESTS))


def shift_repo(path):
    return replaced(path, ("shift.py", SHIFT), ("test_shift.py", SHIFT_TESTS))
