"""Test files on the AST: find a test by id, read its assertions, and compute the delete-only edit.

The edit mirrors what `make prune-gate` point 1 accepts: whole test functions (decorators, the
comment lines directly above and the blank lines before them), a test class left with nothing but
its docstring, and a top-level import whose every name only deleted code reads. Nothing else.
"""
from __future__ import annotations

import ast

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
ASSERTING_CALLS = {"raises", "approx", "warns"}


def split_id(test_id: str) -> tuple[str, tuple[str, ...], str]:
    """`tests/x.py::C::f[p]` → (`tests/x.py`, (`C`,), `f`)."""
    path, *names = test_id.split("[", 1)[0].split("::")
    return path, tuple(names[:-1]), names[-1]


def _span(node: ast.stmt) -> tuple[int, int]:
    return min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))]), node.end_lineno


def find(tree: ast.Module, classes: tuple[str, ...], name: str) -> ast.stmt | None:
    body = tree.body
    for cls in classes:
        node = next((n for n in body if isinstance(n, ast.ClassDef) and n.name == cls), None)
        if node is None:
            return None
        body = node.body
    return next((n for n in body if isinstance(n, FUNCS) and n.name == name), None)


def _callee(func: ast.expr) -> str | None:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def _asserts_by_call(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and _callee(n.func) in ASSERTING_CALLS for n in ast.walk(node))


def _is_assertion(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return any(_asserts_by_call(item.context_expr) for item in node.items)
    return isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign)) and _asserts_by_call(node)


def describe(source: str, test_id: str) -> dict | None:
    """{source, assertions} of the test function behind test_id, or None when the file lacks it."""
    _, classes, name = split_id(test_id)
    node = find(ast.parse(source), classes, name)
    if node is None:
        return None
    start, end = _span(node)
    asserts = sorted((n for n in ast.walk(node) if _is_assertion(n)), key=lambda n: (n.lineno, n.col_offset))
    return {"source": "".join(source.splitlines(keepends=True)[start - 1:end]),
            "assertions": [ast.get_source_segment(source, n) for n in asserts]}


def _uses(tree: ast.Module) -> tuple[dict[str, set[int]], set[str]]:
    """name → lines that read, take or spell it; and the names a parameter or a string carries."""
    uses: dict[str, set[int]] = {}
    pinned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            name, pin = node.id, False
        elif isinstance(node, ast.arg):
            name, pin = node.arg, True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            name, pin = node.value, True
        else:
            continue
        uses.setdefault(name, set()).add(node.lineno)
        if pin:
            pinned.add(name)
    return uses, pinned


def undeletable(source: str, test_id: str) -> str | None:
    """Why the prune gate would refuse deleting this test whole, or None."""
    _, classes, name = split_id(test_id)
    tree = ast.parse(source)
    node = find(tree, classes, name)
    if node is None:
        return "not a test function defined in its file (inherited or generated), cannot be deleted whole"
    uses = _uses(tree)[0]
    if _named_outside(uses, node):
        return f"the file reads or spells the name {name} outside the test"
    named = next((cls for cls in _enclosing(tree, classes) if _named_outside(uses, cls)), None)
    if named is not None:
        return (f"class {named.name} is subclassed or named elsewhere in the file: deleting its test changes "
                "what that code inherits or finds")
    owner = find_class(tree, classes)
    if owner is not None and owner.body[0] is node and node.decorator_list:
        return (f"decorated first member of class {owner.name}: the prune gate reads its decorators as the "
                "class header")
    return None


def _named_outside(uses: dict, node: ast.stmt) -> bool:
    start, end = _span(node)
    return bool(uses.get(node.name, set()) - set(range(start, end + 1)))


def _enclosing(tree: ast.Module, classes: tuple[str, ...]) -> list:
    """The class nodes along classes, outermost first."""
    return [c for c in (find_class(tree, classes[:depth]) for depth in range(1, len(classes) + 1)) if c is not None]


def find_class(tree: ast.Module, classes: tuple[str, ...]) -> ast.ClassDef | None:
    node, body = None, tree.body
    for cls in classes:
        node = next((n for n in body if isinstance(n, ast.ClassDef) and n.name == cls), None)
        if node is None:
            return None
        body = node.body
    return node


def _whole_class(node: ast.ClassDef, scope: tuple[str, ...], targets: set) -> bool:
    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    return bool(body) and all(isinstance(m, FUNCS) and (scope, m.name) in targets for m in body)


def _marked(body: list[ast.stmt], scope: tuple[str, ...], floor: int, targets: set):
    """(node, floor) for every statement to delete; floor = last line of the enclosing class header."""
    for node in body:
        if isinstance(node, FUNCS) and (scope, node.name) in targets:
            yield node, floor
        elif isinstance(node, ast.ClassDef) and _whole_class(node, scope + (node.name,), targets):
            yield node, floor
        elif isinstance(node, ast.ClassDef):
            yield from _marked(node.body, scope + (node.name,), node.body[0].lineno - 1, targets)


def _removed_classes(body: list[ast.stmt], scope: tuple[str, ...], parent, targets: set):
    """(class, its scope, enclosing class or None) for every class the deletion removes whole."""
    for node in body:
        if isinstance(node, ast.ClassDef) and _whole_class(node, scope + (node.name,), targets):
            yield node, scope, parent
        elif isinstance(node, ast.ClassDef):
            yield from _removed_classes(node.body, scope + (node.name,), node, targets)


def _leaves_empty(parent: ast.ClassDef, scope: tuple[str, ...], targets: set) -> bool:
    """Whether deleting targets leaves the class with no statement at all."""
    def removed(m: ast.stmt) -> bool:
        if isinstance(m, FUNCS):
            return (scope, m.name) in targets
        return isinstance(m, ast.ClassDef) and _whole_class(m, scope + (m.name,), targets)
    return all(removed(m) for m in parent.body)


def class_refusals(source: str, test_ids: list) -> dict:
    """{test id: reason} for tests whose deletion together removes a nested class and leaves its
    enclosing class empty (a class named outside itself is refused per test, by undeletable)."""
    tree = ast.parse(source)
    targets = {split_id(t)[1:] for t in test_ids}
    out: dict = {}
    for node, scope, parent in _removed_classes(tree.body, (), None, targets):
        if parent is not None and _leaves_empty(parent, scope, targets):
            path = scope + (node.name,)
            reason = f"deleting it removes class {node.name} and leaves class {parent.name} empty"
            out.update({t: reason for t in test_ids if split_id(t)[1][:len(path)] == path})
    return out


def _with_surroundings(lines: list[str], start: int, end: int, floor: int) -> set[int]:
    """The statement's lines, the comment lines directly above, the blank lines before those; when
    that reaches the class header or the file start, the blank lines after instead."""
    first = start
    while first - 1 > floor and lines[first - 2].strip().startswith("#"):
        first -= 1
    while first - 1 > floor and not lines[first - 2].strip():
        first -= 1
    last = end
    if first - 1 == floor:
        while last < len(lines) and not lines[last].strip():
            last += 1
    return set(range(first, last + 1))


def _dead_imports(tree: ast.Module, removed: set) -> set:
    """Lines of top-level imports whose every name is read only on removed lines (never a name a
    parameter or a string carries: a fixture, a usefixtures marker)."""
    uses, pinned = _uses(tree)
    dead: set = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {(a.asname or a.name).split(".")[0] for a in node.names}
            if not names & pinned and all(uses.get(n) and uses[n] <= removed for n in names):
                dead |= set(range(node.lineno, node.end_lineno + 1))
    return dead


def delete_tests(source: str, targets: set[tuple[tuple[str, ...], str]]) -> str:
    """source without the test functions in targets ({(classes, name)}), shaped for the prune gate."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    removed: set[int] = set()
    for node, floor in list(_marked(tree.body, (), 0, targets)):
        removed |= _with_surroundings(lines, *_span(node), floor)
    removed |= _dead_imports(tree, removed)
    return "".join(line for number, line in enumerate(lines, 1) if number not in removed)


def test_sources(source: str) -> dict[str, str]:
    """`[Class::]name` → source of every test function in the file."""
    out: dict[str, str] = {}

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, FUNCS) and node.name.startswith("test"):
                out[prefix + node.name] = ast.get_source_segment(source, node) or ""
            elif isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}::")

    walk(ast.parse(source).body, "")
    return out
