"""The frontier: blocking edges and Touches holds."""
from driver_graph import frontier, overlap, touches
from driver_tickets import Ticket


def make(number, touched, *blocked_by):
    return Ticket(number, f"{number}-t.md", "t", None, "`f()`", touched, None, None, [], {}, list(blocked_by))


def test_touches_reads_each_backticked_entry_and_ignores_the_prose_around_it():
    ticket = make("01", "`a.py` (new: frontier), `b.py: main`, tests beside each")
    assert touches(ticket) == {("a.py", ""), ("b.py", "main")}
    assert touches(make("02", None)) == set()


def test_overlap_names_the_shared_function_and_a_whole_file_overlaps_its_functions():
    assert overlap(make("01", "`a.py: f`, `b.py: g`"), make("02", "`b.py: g`")) == "b.py: g"
    assert overlap(make("01", "`a.py`"), make("02", "`a.py: f`")) == "a.py: f"
    assert overlap(make("01", "`a.py`"), make("02", "`a.py`")) == "a.py"
    assert overlap(make("01", "`a.py: f`"), make("02", "`a.py: g`, `c.py: f`")) == ""


def test_frontier_waits_for_blockers_and_holds_on_a_running_overlap():
    tickets = [make("01", "`a.py: f`"), make("02", "`a.py: f`", "01"), make("03", "`a.py: f`"), make("04", "`b.py: g`")]
    phases = dict.fromkeys(("01", "02", "03", "04"), "pending")
    ready, holds = frontier(tickets, phases, set())
    assert [t.number for t in ready] == ["01", "03", "04"] and holds == {}
    ready, holds = frontier(tickets, phases, {"01"})
    assert [t.number for t in ready] == ["04"] and holds == {"03": "held while 01 runs: both touch a.py: f"}
    ready, holds = frontier(tickets, {**phases, "01": "merged", "04": "blocked"}, set())
    assert [t.number for t in ready] == ["02", "03"]
    assert frontier(tickets, {**phases, "01": "blocked", "03": "merged", "04": "merged"}, set()) == ([], {})
