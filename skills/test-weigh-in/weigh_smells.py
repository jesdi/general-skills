"""Per-test smells from each test function's syntax tree, one test at a time."""
import ast
import copy
import hashlib

from weigh_inventory import is_test

MOCK_ASSERTS = ("assert_called", "assert_any_call", "assert_has_calls", "assert_not_called", "assert_awaited", "assert_not_awaited")
# what an assertion on a mock reads
MOCK_ATTRS = ("called", "call_count", "call_args", "call_args_list", "mock_calls", "await_count", "await_args", "await_args_list")
MOCK_MAKERS = ("MagicMock", "Mock", "AsyncMock", "NonCallableMock", "create_autospec", "patch")
HELPER_ASSERTS = ("assert", "check_", "expect_")
RAISES = ("pytest.raises", "pytest.warns", "pytest.deprecated_call")
MOCK_HEAVY_ABOVE = 3
# Smells and Worst tests tabs: the smells of each pillar
PILLAR_SMELLS = {
    "regression": ("No assertion",),
    "refactoring": ("Mock-heavy", "Interaction-only", "Private access"),
    "feedback": ("Real sleep",),
    "maintainability": ("Eager test", "Conditional test logic", "Mystery guest", "Swallowed exception", "PT"),
    "economy": ("Duplicate body",),
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


def _scope_nodes(nodes):
    """The nodes of a scope's statements in source order, not those inside the functions and classes it defines."""
    for n in nodes:
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from _scope_nodes(ast.iter_child_nodes(n))


def _module_privates(tree):
    """Private names the module imports, at module scope, from a non-test module."""
    found = set()
    for n in _scope_nodes(tree.body):
        if isinstance(n, ast.ImportFrom) and not _from_test_module(n):
            found |= {a.asname or a.name for a in n.names if _private(a.name)}
    return found


def _aliases(nodes):
    """{name bound by an import among nodes: the dotted module or object it names}; a later import wins."""
    found = {}
    for n in nodes:
        if isinstance(n, ast.Import):
            for a in n.names:
                found[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom):
            found.update({a.asname or a.name: f"{n.module}.{a.name}" if n.module else a.name for a in n.names})
    return found


def _test_owned(owner, aliases):
    """The object an attribute is read from is the test's own: self, cls, or a name imported from a test module."""
    dotted = _dotted(owner)
    if dotted in ("self", "cls"):
        return True
    head, _, rest = dotted.partition(".")
    return head in aliases and is_test(".".join(filter(None, (aliases[head], rest))).replace(".", "/") + ".py")


def _conjuncts(test):
    """The checks of an assert: each side of an `and`, each pair of an equality of two tuples or lists."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return [c for v in test.values for c in _conjuncts(v)]
    sides = [test.left, *test.comparators] if isinstance(test, ast.Compare) else []
    if (len(sides) == 2 and isinstance(test.ops[0], ast.Eq) and all(isinstance(e, (ast.Tuple, ast.List)) for e in sides)
            and len(sides[0].elts) == len(sides[1].elts) > 0):
        return [c for a, b in zip(sides[0].elts, sides[1].elts) for c in _conjuncts(ast.Compare(a, [ast.Eq()], [b]))]
    return [test]


def _reads_mock(expr):
    return any(isinstance(a, ast.Attribute) and a.attr in MOCK_ATTRS for a in ast.walk(expr))


def _assertion(n):
    """"plain", "mock" or None: the one classification of an assertion, shared by every rule that looks for one."""
    if isinstance(n, ast.Expr):
        return _assertion(n.value)
    if isinstance(n, ast.Assert):
        # `assert result == x and sender.call_count == 1` checks a result too
        return "plain" if not all(_reads_mock(c) for c in _conjuncts(n.test)) else "mock"
    if isinstance(n, (ast.With, ast.AsyncWith)):
        return "plain" if any(isinstance(i.context_expr, ast.Call) and _dotted(i.context_expr.func) in RAISES for i in n.items) else None
    if isinstance(n, ast.Call):
        name = _dotted(n.func)
        last = name.split(".")[-1]
        if last.startswith(MOCK_ASSERTS):
            return "mock"
        if last.lower().startswith(HELPER_ASSERTS) or name == "pytest.fail":
            return "plain"
    return None


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _safe_names(func):
    """Names obtained from tmp_path or monkeypatch: the fixtures and what is assigned from them."""
    safe = {a.arg for a in func.args.args if a.arg in SAFE_ROOTS}
    for n in ast.walk(func):
        if isinstance(n, ast.Assign) and _names(n.value) & safe:
            safe |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return safe


def _literal_path(node, safe, guests):
    """A path built from a string literal or from a name holding one, and not from tmp_path or monkeypatch."""
    names = _names(node)
    return bool(any(isinstance(c, ast.Constant) and isinstance(c.value, str) for c in ast.walk(node)) or names & guests) and not names & safe


def _guest_names(func, safe):
    """Names assigned a literal path, in source order, so a name assigned from another one carries its path."""
    guests = set()
    for n in sorted((n for n in ast.walk(func) if isinstance(n, ast.Assign)), key=lambda n: (n.lineno, n.col_offset)):
        if _literal_path(n.value, safe, guests):
            guests |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return guests


def _reads_guest(n, safe, guests):
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
    return _literal_path(target, safe, guests)


def _eager(func):
    """Act-then-assert cycles: a statement that is not an assertion, followed by one that is."""
    cycles, acted = 0, False

    def walk(body):
        nonlocal cycles, acted
        for st in body:
            if _assertion(st):
                cycles, acted = cycles + acted, False
            elif isinstance(st, (ast.With, ast.AsyncWith)):
                walk(st.body)
            else:
                acted = True

    walk(func.body)
    return cycles


def _swallows(func):
    return any(isinstance(h, ast.ExceptHandler) and not any(isinstance(x, ast.Raise) or _assertion(x) for x in ast.walk(h))
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


def smells_of(func, module_privates, module_aliases):
    aliases = {**module_aliases, **_aliases(ast.walk(func))}
    kinds = {_assertion(n) for n in ast.walk(func)}
    plain, mock, private = "plain" in kinds, "mock" in kinds, False
    mocks = 0
    for n in ast.walk(func):
        if isinstance(n, ast.Call):
            name = _dotted(n.func)
            if name.split(".")[-1] in MOCK_MAKERS or name.startswith(("patch.", "mock.patch.")) or name == "monkeypatch.setattr":
                mocks += 1
        elif isinstance(n, ast.ImportFrom):
            private |= not _from_test_module(n) and any(_private(a.name) for a in n.names)
        elif isinstance(n, ast.Name):
            private |= n.id in module_privates
        elif isinstance(n, ast.Attribute):
            private |= _private(n.attr) and not _test_owned(n.value, aliases)
    safe = _safe_names(func)
    guests = _guest_names(func, safe)
    return [s for s, has in (("No assertion", not plain and not mock), ("Interaction-only", mock and not plain),
                             ("Mock-heavy", mocks > MOCK_HEAVY_ABOVE), ("Private access", private),
                             ("Eager test", _eager(func) >= EAGER_CYCLES),
                             ("Conditional test logic", any(isinstance(n, (ast.If, ast.For, ast.While)) for n in ast.walk(func))),
                             ("Mystery guest", any(_reads_guest(n, safe, guests) for n in ast.walk(func))),
                             ("Swallowed exception", _swallows(func)), ("Real sleep", _sleeps(func))) if has]


def _definitions(tree):
    """({"Class::name" or "name": function}, {"Class": (enclosing prefix, class)}), by the qualification pytest gives;
    a later definition wins, as in Python."""
    functions, classes = {}, {}

    def visit(body, prefix):
        for st in body:
            if isinstance(st, ast.ClassDef):
                classes[prefix + st.name] = (prefix, st)
                visit(st.body, f"{prefix}{st.name}::")
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[prefix + st.name] = st

    visit(tree.body, "")
    return functions, classes


def _mro(classes, cls, seen=frozenset()):
    """Python's method resolution order (C3) of a class of the module; a base defined elsewhere ends its line."""
    if cls not in classes or cls in seen:
        return [cls]
    prefix, node = classes[cls]
    bases = [next((q for q in (prefix + d, d) if q in classes), d)
             for d in (_dotted(b).replace(".", "::") for b in node.bases)]
    lines, order = [_mro(classes, b, seen | {cls}) for b in bases] + [bases], [cls]
    while lines := [line for line in lines if line]:
        head = next((line[0] for line in lines if not any(line[0] in other[1:] for other in lines)), None)
        if head is None:  # no consistent order: Python refuses to create such a class
            return order + [c for line in lines for c in line if c not in order]
        order.append(head)
        lines = [line[1:] if line[0] == head else line for line in lines]
    return order


def _resolve(module, qualified):
    """The function of a test: by its full qualification, else the method its class inherits, found in Python's
    resolution order among the classes of the module (None when the method lives in another module)."""
    functions, classes = module["functions"], module["classes"]
    if qualified in functions:
        return functions[qualified]
    cls, _, name = qualified.rpartition("::")
    return next((functions[f"{c}::{name}"] for c in _mro(classes, cls)[1:] if f"{c}::{name}" in functions), None)


def find_smells(repo, tests, ruff=()):
    """({node: {smells, lines}}, clusters) for every test; a test whose source cannot be read or parsed has none.

    ruff holds (path, row, code) violations: each is a smell of the test whose lines contain the row.

    A cluster is the sorted node ids of 2 or more tests with one normalized body; each gets the smell Duplicate body."""
    modules, result, bodies = {}, {}, {}
    for node in tests:
        path, _, qualified = node.partition("::")
        if path not in modules:
            try:
                tree = ast.parse((repo / path).read_text(errors="replace"))
                functions, classes = _definitions(tree)
                modules[path] = {"functions": functions, "classes": classes, "privates": _module_privates(tree),
                                 "aliases": _aliases(_scope_nodes(tree.body))}
            except (OSError, SyntaxError, ValueError):
                modules[path] = None
        module = modules[path]
        found = module and _resolve(module, qualified)
        result[node] = {"smells": smells_of(found, module["privates"], module["aliases"]) if found else [],
                        "lines": found.end_lineno - found.lineno + 1 if found else 0}
        if found:
            result[node]["smells"] += sorted({c for p, row, c in ruff if p == path and found.lineno <= row <= found.end_lineno})
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
            "smelly_tests": 100 * sum(any(s.startswith(MAINTAINABILITY_SMELLS) for s in t["smells"]) for t in tests.values()) / n,
            "long_tests": 100 * sum(t["lines"] > LONG_TEST_LINES for t in tests.values()) / n, "mock_heavy": share("Mock-heavy"),
            "interaction_only": share("Interaction-only"), "private_access": share("Private access")}


def pillar_smells(tests, smells):
    """{smell: sorted node ids that have it}, for the smells given (a prefix such as "PT" matches every PT code)."""
    found = {}
    for node, t in sorted(tests.items()):
        for s in t["smells"]:
            if s.startswith(smells):
                found.setdefault(s, []).append(node)
    return dict(sorted(found.items()))


def _measured(test, pillar, slow):
    """(reasons, weight) a test ranks low by in a pillar beyond its smells: its length, duration or redundant coverage."""
    if pillar == "maintainability" and test["lines"] > LONG_TEST_LINES:
        return [f"{test['lines']} lines"], test["lines"]
    if pillar == "feedback" and test["duration"] > slow:
        return [f"{test['duration']:.2f} s"], test["duration"]
    # unique coverage is measured only for tests that passed unflaky
    if pillar == "economy" and test["outcome"] == "passed" and not test["flaky"] and not test.get("unique_coverage", 0):
        return ["No unique coverage"], 0
    return [], 0


def worst_tests(tests, pillar, slow, limit=10):
    """The lowest-ranked tests of a pillar, each with its reasons: most reasons first, then the longest or slowest.
    A test is slow above `slow` seconds."""
    ranked = []
    for node, t in tests.items():
        extra, weight = _measured(t, pillar, slow)
        reasons = [s for s in t["smells"] if s.startswith(PILLAR_SMELLS.get(pillar, ()))] + extra
        if reasons:
            ranked.append((-len(reasons), -weight, node, reasons))
    return [{"test": n, "reason": ", ".join(r)} for _, _, n, r in sorted(ranked)[:limit]]
