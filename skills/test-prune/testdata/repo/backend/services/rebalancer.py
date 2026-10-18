def compute_trades(current, targets):
    return {k: targets.get(k, 0) - current.get(k, 0) for k in set(current) | set(targets)}


def apply_drift(drift):
    return min(max(drift, 0), 1)


def rebalance(current, targets, band=1e-6):
    trades = compute_trades(current, targets)
    return {k: v for k, v in trades.items() if abs(v) > band}
