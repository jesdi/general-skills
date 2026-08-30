"""Named functions and cyclomatic complexity for Python via `ast`. Stdlib only."""
from __future__ import annotations

import ast

from crap_model import Func, ToolError

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_DEFS, ast.ClassDef)
_BRANCHES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.match_case,
)


def functions(source: str, file: str) -> list[Func]:
    try:
        tree = ast.parse(source, filename=file)
    except SyntaxError as exc:
        raise ToolError(f"{file}: cannot parse: {exc.msg} (line {exc.lineno})") from exc
    out: list[Func] = []
    for node in tree.body:
        _visit(node, "", out, file)
    return out


def _visit(node: ast.AST, prefix: str, out: list[Func], file: str) -> None:
    if isinstance(node, _DEFS):
        name = prefix + node.name
        cc, nested = _complexity(node)
        out.append(Func(file, name, node.lineno, node.end_lineno or node.lineno, cc))
        for child in nested:
            _visit(child, name + ".", out, file)
    elif isinstance(node, ast.ClassDef):
        for child in node.body:
            _visit(child, prefix + node.name + ".", out, file)
    else:
        # Compound statements outside any function (if TYPE_CHECKING:, try:, …)
        # may still hold defs; keep the same prefix.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.stmt, ast.ExceptHandler)):
                _visit(child, prefix, out, file)


def _complexity(fn: ast.AST) -> tuple[int, list[ast.AST]]:
    """CC of `fn` counting everything except nested named scopes, which are
    returned (in source order) for separate scoring. Lambdas fold in."""
    cc = 1
    nested: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPES):
            nested.append(node)
            continue
        if isinstance(node, _BRANCHES):
            cc += 1
        elif isinstance(node, ast.BoolOp):
            cc += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            cc += 1 + len(node.ifs)
        stack.extend(ast.iter_child_nodes(node))
    nested.sort(key=lambda n: n.lineno)
    return cc, nested
