"""CRAP formula and threshold logic. Stdlib only."""
from __future__ import annotations

import math

from crap_model import Func, Scored


def crap_score(cc: int, coverage: float) -> float:
    """CRAP = CC² · (1 − cov)³ + CC  (Savoia/Cunningham)."""
    return cc * cc * (1.0 - coverage) ** 3 + cc


def required_coverage(cc: int, limit: float) -> float | None:
    """Smallest coverage at which crap_score(cc, cov) <= limit; None when
    even 100% coverage cannot bring CC under the limit."""
    if cc > limit:
        return None
    if cc == limit:
        return 1.0
    return max(0.0, 1.0 - ((limit - cc) / (cc * cc)) ** (1.0 / 3.0))


def max_cc(coverage: float, limit: float) -> int:
    """Largest CC that still passes at the given coverage."""
    cc = 1
    while crap_score(cc + 1, coverage) <= limit:
        cc += 1
    return cc


def hint(cc: int, coverage: float, limit: float) -> str:
    """The cheapest ways out for a failing function, as one short phrase."""
    parts: list[str] = []
    need = required_coverage(cc, limit)
    if need is not None and need > coverage:
        parts.append(f"cover ≥ {math.ceil(need * 100)}%")
    ceiling = max_cc(coverage, limit)
    if ceiling < cc:
        parts.append(f"split to CC ≤ {ceiling}")
    return " or ".join(parts)


def score(
    target: str,
    func: Func,
    coverage: float,
    kind: str,
    thresholds: dict[str, float],
    note: str = "",
) -> Scored:
    limit = thresholds["new"] if kind == "new" else thresholds["existing"]
    value = crap_score(func.cc, coverage)
    failing = value > limit
    return Scored(
        target=target,
        func=func,
        coverage=coverage,
        crap=value,
        kind=kind,
        limit=limit,
        status="FAIL" if failing else "ok",
        hint=hint(func.cc, coverage, limit) if failing else "",
        note=note,
    )
