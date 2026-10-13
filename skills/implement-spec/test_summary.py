"""The summary: plain sentences, then the measurement table."""
import driver_summary


def test_render_counts_gates_agents_time_rounds_stops_and_cost():
    ticket = lambda phase, rounds: {"phase": phase, "round": rounds}
    agent = lambda role, entry, seconds, cost: {"role": role, "entry": entry, "seconds": seconds, "cost": cost}
    state = {"run": {"pr_branch": "feat/csv", "gates": 4, "stops": 1,
                     "agents": [agent("test-writer", "sonnet@medium", 2.0, 0.25), agent("implementer", "opus@medium", 3.5, 0.5),
                                agent("implementer", "opus@medium", 1.25, 0), agent("implementer", "fable@high", 1, 1.0)]},
             "tickets": {"01": ticket("merged", 1), "02": ticket("pending", 0), "03": ticket("blocked", 0)}}
    assert driver_summary.render(state) == """1 of 3 tickets are merged into feat/csv.
Ticket 02 is not merged: its phase is pending.
Ticket 03 is not merged: its phase is blocked.

| Measure | Value |
|---|---|
| gates run | 4 |
| agents, test-writer on sonnet@medium | 1 |
| agents, implementer on opus@medium | 2 |
| agents, implementer on fable@high | 1 |
| wall time, test-writer | 2.0 s |
| wall time, implementer | 5.8 s |
| fix rounds per ticket | 01: 1, 02: 0, 03: 0 |
| human stops | 1 |
| cost | 1.75 USD |
"""
