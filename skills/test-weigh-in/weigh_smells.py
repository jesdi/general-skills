"""Per-test smells from each test function's syntax tree: No assertion, Interaction-only, Mock-heavy, Private access."""
import ast

from weigh_inventory import is_test

MOCK_ASSERTS = ("assert_called", "assert_any_call", "assert_has_calls", "assert_not_called", "assert_awaited", "assert_not_awaited")
MOCK_MAKERS = ("MagicMock", "Mock", "AsyncMock", "NonCallableMock", "create_autospec", "patch")
MOCK_HEAVY_ABOVE = 3
# Worst tests tab: the smells each pillar lists
PILLAR_SMELLS = {
    "regression": ("No assertion",),
    "refactoring": ("Mock-heavy", "Interaction-only", "Private access"),
}


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
    return [s for s, has in (("No assertion", not plain and not mock), ("Interaction-only", mock and not plain),
                             ("Mock-heavy", mocks > MOCK_HEAVY_ABOVE), ("Private access", private)) if has]


def find_smells(repo, tests):
    """{node: [smell]} for every test; a test whose source cannot be read or parsed has none."""
    trees, result = {}, {}
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
        result[node] = smells_of(found, trees[path][1]) if found else []
    return result


def smell_values(tests):
    n = len(tests) or 1
    share = lambda smell: 100 * sum(smell in t["smells"] for t in tests.values()) / n
    return {"hollow_tests": share("No assertion"), "mock_heavy": share("Mock-heavy"),
            "interaction_only": share("Interaction-only"), "private_access": share("Private access")}


def worst_tests(tests, smells, limit=10):
    """The tests with most of the given smells first, each with its reasons."""
    ranked = sorted(((n, [s for s in t["smells"] if s in smells]) for n, t in tests.items()), key=lambda r: (-len(r[1]), r[0]))
    return [{"test": n, "reason": ", ".join(r)} for n, r in ranked if r][:limit]
