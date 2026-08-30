import pytest

from crap_model import Func, ToolError
from crap_python import functions

SRC = '''
def simple(x):
    return x

def branchy(items, flag):
    total = 0
    for i in items:
        if i and flag:
            total += 1
        elif i > 2:
            total += 2
    return total if total else -1

class K:
    def method(self, xs):
        return [x for x in xs if x]

def outer(f):
    def inner(y):
        return y if y else 0
    handler = lambda z: z if z else 1
    return inner(f) + handler(f)
'''


def by_name(funcs):
    return {f.name: f for f in funcs}


def test_named_functions_with_qualified_names_and_ranges():
    got = by_name(functions(SRC, "m.py"))
    assert set(got) == {"simple", "branchy", "K.method", "outer", "outer.inner"}
    assert got["simple"] == Func("m.py", "simple", 2, 3, 1)
    assert (got["branchy"].start, got["branchy"].end) == (5, 12)
    assert (got["outer"].start, got["outer"].end) == (18, 22)
    assert (got["outer.inner"].start, got["outer.inner"].end) == (19, 20)


def test_cyclomatic_complexity_counts_branches_boolops_ternaries_comprehensions():
    got = by_name(functions(SRC, "m.py"))
    assert got["branchy"].cc == 6       # for, if, `and`, elif, ternary
    assert got["K.method"].cc == 3      # comprehension for + if
    assert got["outer"].cc == 2         # lambda's ternary folds into outer
    assert got["outer.inner"].cc == 2   # its own ternary, not counted in outer


def test_try_except_match_and_async():
    src = '''
async def fetch(url):
    try:
        return await get(url)
    except ValueError:
        return None
    except KeyError:
        return {}

def route(cmd):
    match cmd:
        case "a":
            return 1
        case "b":
            return 2
    while True:
        break
'''
    got = by_name(functions(src, "m.py"))
    assert got["fetch"].cc == 3   # two except handlers
    assert got["route"].cc == 4   # two cases + while


def test_defs_under_module_level_if_and_decorators():
    src = '''
import typing
if typing.TYPE_CHECKING:
    def stub(a):
        return a or 1

@decorator
def deco(b):
    return b
'''
    got = by_name(functions(src, "m.py"))
    assert got["stub"].cc == 2
    assert got["deco"].start == 8   # the `def` line, not the decorator line


def test_syntax_error_is_a_tool_error():
    with pytest.raises(ToolError, match="m.py"):
        functions("def (:", "m.py")
