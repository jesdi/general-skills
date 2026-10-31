"""Per-test smells from each test function's syntax tree: No assertion, Interaction-only, Mock-heavy, Private access."""
import ast
import copy
import hashlib

from weigh_inventory import is_test

MOCK_ASSERTS = ("assert_called", "assert_any_call", "assert_has_calls", "assert_not_called", "assert_awaited", "assert_not_awaited")
MOCK_MAKERS = ("MagicMock", "Mock", "AsyncMock", "NonCallableMock", "create_autospec", "patch")
MOCK_HEAVY_ABOVE = 3
# Worst tests tab: the smells each pillar lists
PILLAR_SMELLS = {
    "regression": ("No assertion",),
    "refactoring": ("Mock-heavy", "Interaction-only", "Private access"),
    "feedback": ("Real sleep",),
    "maintainability": ("Eager test", "Conditional test logic", "Mystery guest", "Swallowed exception"),
    "economy": ("Duplicate body", "No unique coverage"),
}
MAINTAINABILITY_SMELLS = PILLAR_SMELLS["maintainability"]
LONG_TEST_LINES = 50
EAGER_CYCLES = 3
SAFE_ROOTS = ("tmp_path", "tmp_path_factory", "monkeypatch")
FILE_READS = ("read_text", "read_bytes", "open")
SLEEPS = ("time.sleep", "asyncio.sleep")
HOME_ENV = ("os.environ", "os.getenv", "os.path.expanduser", "Path.home", "pathlib.Path.home")


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _private(name):
    return name.startswith("_") and not name.endswith("__")


def _from_test_module(node):
    return is_test((node.module or "").replace(".", "/") + ".py")


def _module_privates(tree):
    """Private names the module imports from a non-test module."""
    found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and not _from_test_module(n):
            found |= {a.asname or a.name for a in n.names if _private(a.name)}
    return found


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _safe_names(func):
    """Names obtained from tmp_path or monkeypatch: the fixtures and what is assigned from them."""
    safe = {a.arg for a in func.args.args if a.arg in SAFE_ROOTS}
    for n in ast.walk(func):
        if isinstance(n, ast.Assign) and _names(n.value) & safe:
            safe |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return safe


def _reads_guest(n, safe):
    """A file read or home/environment access whose path is not from tmp_path or monkeypatch."""
    if isinstance(n, ast.Attribute) and _dotted(n) in HOME_ENV:
        return "monkeypatch" not in safe
    if not isinstance(n, ast.Call):
        return False
    if isinstance(n.func, ast.Name) and n.func.id == "open" and n.args:
        target = n.args[0]
    elif isinstance(n.func, ast.Attribute) and n.func.attr in FILE_READS:
        target = n.func.value
    else:
        return False
    return any(isinstance(c, ast.Constant) and isinstance(c.value, str) for c in ast.walk(target)) and not _names(target) & safe


def _eager(func):
    """Act-then-assert cycles: a statement that is not an assertion, followed by one that is."""
    cycles, acted = 0, False

    def walk(body):
        nonlocal cycles, acted
        for st in body:
            raises = isinstance(st, ast.With) and any(isinstance(i.context_expr, ast.Call) and _dotted(i.context_expr.func) == "pytest.raises" for i in st.items)
            if isinstance(st, ast.With) and not raises:
                walk(st.body)
            elif isinstance(st, ast.Assert) or raises:
                cycles, acted = cycles + acted, False
            else:
                acted = True

    walk(func.body)
    return cycles


def _swallows(func):
    return any(isinstance(h, ast.ExceptHandler) and not any(isinstance(x, (ast.Raise, ast.Assert)) for x in ast.walk(h))
               for n in ast.walk(func) if isinstance(n, ast.Try) for h in n.handlers)


def _sleeps(func):
    return any(isinstance(n, ast.Call) and _dotted(n.func) in SLEEPS and n.args
               and not (isinstance(n.args[0], ast.Constant) and n.args[0].value == 0) for n in ast.walk(func))


class _Normalize(ast.NodeTransformer):
    """Names and literals become placeholders; call and attribute structure stays."""

    def visit_Name(self, node):
        return ast.Name(id="_", ctx=node.ctx)

    def visit_arg(self, node):
        return ast.arg(arg="_")

    def visit_Constant(self, node):
        return ast.Constant(value=type(node.value).__name__)


def _body_hash(func):
    body = ast.Module(body=[_Normalize().visit(copy.deepcopy(st)) for st in func.body], type_ignores=[])
    return hashlib.sha1(ast.dump(body).encode()).hexdigest()


def smells_of(func, module_privates):
    plain = mock = private = False
    mocks = 0
    for n in ast.walk(func):
        if isinstance(n, ast.Assert):
            plain = True
        elif isinstance(n, ast.With):
            plain |= any(isinstance(i.context_expr, ast.Call) and _dotted(i.context_expr.func) in ("pytest.raises", "pytest.warns", "pytest.deprecated_call")
                         for i in n.items)
        elif isinstance(n, ast.Call):
            name = _dotted(n.func)
            last = name.split(".")[-1]
            if last.startswith(MOCK_ASSERTS):
                mock = True
            elif last.lower().startswith("assert") or name == "pytest.fail":
                plain = True
            if last in MOCK_MAKERS or name.startswith(("patch.", "mock.patch.")):
                mocks += 1
        elif isinstance(n, ast.ImportFrom):
            private |= not _from_test_module(n) and any(_private(a.name) for a in n.names)
        elif isinstance(n, ast.Name):
            private |= n.id in module_privates
        elif isinstance(n, ast.Attribute):
            private |= _private(n.attr) and not (isinstance(n.value, ast.Name) and n.value.id in ("self", "cls"))
    safe = _safe_names(func)
    return [s for s, has in (("No assertion", not plain and not mock), ("Interaction-only", mock and not plain),
                             ("Mock-heavy", mocks > MOCK_HEAVY_ABOVE), ("Private access", private),
                             ("Eager test", _eager(func) >= EAGER_CYCLES),
                             ("Conditional test logic", any(isinstance(n, (ast.If, ast.For, ast.While)) for n in ast.walk(func))),
                             ("Mystery guest", any(_reads_guest(n, safe) for n in ast.walk(func))),
                             ("Swallowed exception", _swallows(func)), ("Real sleep", _sleeps(func))) if has]


def find_smells(repo, tests):
    """({node: {smells, lines}}, clusters) for every test; a test whose source cannot be read or parsed has none.

    A cluster is the sorted node ids of 2 or more tests with one normalized body; each gets the smell Duplicate body."""
    trees, result, bodies = {}, {}, {}
    for node in tests:
        path, *_, name = node.split("::")
        if path not in trees:
            try:
                tree = ast.parse((repo / path).read_text(errors="replace"))
                trees[path] = (tree, _module_privates(tree))
            except (OSError, SyntaxError, ValueError):
                trees[path] = None
        found = trees[path] and next((f for f in ast.walk(trees[path][0])
                                      if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == name), None)
        result[node] = {"smells": smells_of(found, trees[path][1]) if found else [], "lines": found.end_lineno - found.lineno + 1 if found else 0}
        if found:
            bodies.setdefault(_body_hash(found), []).append(node)
    clusters = sorted(sorted(g) for g in bodies.values() if len(g) > 1)
    for node in (n for c in clusters for n in c):
        result[node]["smells"].append("Duplicate body")
    return result, clusters


def smell_values(tests):
    n = len(tests) or 1
    share = lambda smell: 100 * sum(smell in t["smells"] for t in tests.values()) / n
    return {"hollow_tests": share("No assertion"), "duplicate_clusters": share("Duplicate body"),
            "smelly_tests": 100 * sum(any(s in t["smells"] for s in MAINTAINABILITY_SMELLS) for t in tests.values()) / n,
            "long_tests": 100 * sum(t["lines"] > LONG_TEST_LINES for t in tests.values()) / n, "mock_heavy": share("Mock-heavy"),
            "interaction_only": share("Interaction-only"), "private_access": share("Private access")}


def worst_tests(tests, smells, limit=10):
    """The tests with most of the given smells first, each with its reasons."""
    # "No unique coverage" is not an AST smell: it is a measured value, only for tests that passed unflaky
    reasons = lambda t: [*t["smells"], *(["No unique coverage"] if t["outcome"] == "passed" and not t["flaky"] and not t.get("unique_coverage", 0) else [])]
    ranked = sorted(((n, [s for s in reasons(t) if s in smells]) for n, t in tests.items()), key=lambda r: (-len(r[1]), r[0]))
    return [{"test": n, "reason": ", ".join(r)} for n, r in ranked if r][:limit]
