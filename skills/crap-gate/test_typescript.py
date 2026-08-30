from pathlib import Path

import pytest

from crap_model import Func, ToolError
from crap_typescript import functions

REPO_ROOT = Path(__file__).resolve().parents[2]  # has node_modules/typescript (devDependency)

TSX = """import { memo, useMemo } from 'react';
export function Plain(a: number) { return a; }
export const Widget = ({ items, flag }: { items: number[]; flag?: boolean }) => {
  const tiles = useMemo(() => items.filter((i) => i > 1 && flag), [items, flag]);
  const onKey = (e: { key: string }) => (e.key === 'a' ? 1 : 0);
  return <div>{flag ? tiles.map((t) => <span key={t}>{t}</span>) : null}</div>;
};
export default memo(({ x }: { x?: number }) => x ?? 0);
class Store { get(k: string) { return k || 'x'; } }
export const Wrapped = memo(function Inner({ n }: { n: number }) { return n?.toString() ?? ''; });
button.addEventListener('click', () => { if (ready) go(); });
"""


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def by_name(funcs):
    return {f.name: f for f in funcs}


def test_named_functions_folding_and_cc(tmp_path):
    p = write(tmp_path, "w.tsx", TSX)
    got = by_name(functions([(p, "src/w.tsx")], REPO_ROOT)["src/w.tsx"])
    assert set(got) == {"Plain", "Widget", "Widget.onKey", "default", "Store.get", "Wrapped"}
    assert got["Plain"] == Func("src/w.tsx", "Plain", 2, 2, 1)
    assert (got["Widget"].start, got["Widget"].end, got["Widget"].cc) == (3, 7, 3)  # `&&` in filter cb + ternary in JSX
    assert got["Widget.onKey"].cc == 2
    assert got["default"].cc == 2          # `??`
    assert got["Store.get"].cc == 2        # `||`
    assert got["Wrapped"].cc == 2          # `??`; optional chaining not counted
    # the addEventListener callback has no named ancestor → ignored, not a function


def test_switch_loops_catch_and_logical_assignment(tmp_path):
    src = """export function f(k: string, xs: number[]) {
  let n = 0;
  switch (k) { case 'a': n = 1; break; case 'b': n = 2; break; default: n = 3; }
  for (const x of xs) { while (x > n) { n++; } }
  try { n ||= 4; } catch (e) { n &&= 5; }
  do { n--; } while (n > 0);
  return n;
}
"""
    p = write(tmp_path, "f.ts", src)
    got = by_name(functions([(p, "src/f.ts")], REPO_ROOT)["src/f.ts"])
    assert got["f"].cc == 9  # 2 cases, for-of, while, catch, ||=, &&=, do-while


def test_batch_returns_every_label_even_when_empty(tmp_path):
    a = write(tmp_path, "a.ts", "export const n = 1;\n")
    b = write(tmp_path, "b.ts", "export const g = () => 1;\n")
    got = functions([(a, "a.ts"), (b, "b.ts")], REPO_ROOT)
    assert got["a.ts"] == []
    assert [f.name for f in got["b.ts"]] == ["g"]


def test_missing_typescript_package_is_a_tool_error(tmp_path):
    p = write(tmp_path, "a.ts", "export const g = () => 1;\n")
    with pytest.raises(ToolError, match="typescript"):
        functions([(p, "a.ts")], tmp_path)
