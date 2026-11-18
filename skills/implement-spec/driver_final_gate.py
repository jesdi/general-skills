"""The full integration gate: one automatic repair batch, then an operator retry.

A red record is durable before repair starts. Interrupted repair resumes from it;
only an accepted batch spends the allowance. Ticket fix budgets never change.

A red tip that fails exactly as the base commit (`run.final.onto`) does is not the change's to repair: it passes for
the run, which goes on to review, and the PR opens as a draft that names both records (`run.final.gate_preexisting`):
a match of failure lines is a heuristic, not a proof. The full gate runs on the base once per base commit
(`run.final.base_gate`), in driver_slots.BASE_SLOT, so slot 1 keeps the tip. A failure is a line of the gate output
that names a failure, an error or an exception (`AssertionError`), but a wrapper's line (`make: ***`, `npm ERR!`,
`exit code 2`), with times, `:line:col` and the counts of the tests that passed masked; a count of failures, an
identifier and any other value stay. The tip's failures are old only when both gates judged their commit (a complete
run with an exit under 128, so not a signal's, on a tree clean after it) and the two have the same failure lines, as
often. Anything unproven is new.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import NamedTuple

import driver_gate
import driver_slots
from driver_run import FINAL, FINAL_RESUME, OUT, Blocked, Run, ledger

RETRY_SUBJECT = 'the full integration gate remains red after its repair'
# A line that names a failure; `1) Suite does x:` is the title of a failed test in mocha's list.
_FAILURE = re.compile(r'\b(?:fail(?:ed|ures?|s|ing)?|errors?)\b|(?-i:[a-z]Error)\b|[✗×✖●]|^\d+\) \S', re.I)
# The line a wrapper prints for any red command it ran: it names no failure of its own.
_WRAPPER = re.compile(r'^(?:make(?:\[\d+\])?: \*\*\*|npm (?:ERR!|error)|ERR_PNPM_|ELIFECYCLE)'
                      r'|\bexit(?:ed)? (?:with )?(?:code|status) -?\d+', re.I)
# What differs between two runs of the same failures: a time, a :line:col, the count of the tests that did not
# fail, the place of a failed test in mocha's list. Never a count of failures, nor a number inside an identifier:
# test_limit[1] keeps its 1.
_MASKS = ((re.compile(r'(?<![\w.\[-])\d+(?:\.\d+)?m?s(?![\w\]])'), 'Ns'),
          (re.compile(r'(?<=\S):\d+:\d+(?=[\s:]|$)'), ':N:N'),
          (re.compile(r'(?<![\w.\[-])\d+(?= (?:passed|passing|skipped|pending|tests?)\b)', re.I), 'N'),
          (re.compile(r'^\d+\) '), 'N) '))


class Red(NamedTuple):
    """A red full gate to repair: its record, and the red record of the base, "" when the base is not red with it."""
    record: str
    base: str = ''


def failure(run: Run, tip: str) -> Red | None:
    """The red full gate of `tip` to repair, or None when it passed; at the repair cap it parks instead."""
    final = run.state.read()['run']['final']
    red = _red(run, final, tip)
    if not red or _old(run, tip, red):
        return None
    run.state.update(lambda s: s['run']['final'].update(gate_failure=dict(tip=tip, record=red.record)))
    if final['gate_repairs'] >= final['gate_allowance']:
        subject = f'{RETRY_SUBJECT}: {tip[:7]}; read {red.record}'
        run.ask(FINAL, [('ticket-blocked', subject)], red.record, FINAL_RESUME._replace(
            gate_allowance=final['gate_repairs'] + 1))
        raise Blocked(subject)
    return red


def _red(run: Run, final: dict, tip: str) -> Red | None:
    """The red full gate on `tip`, saved or run now, with the record of the base when the tip's failures need it."""
    if passed(final, tip):
        return None
    pending = final['gate_failure']
    record = (pending['record'] if pending and pending['tip'] == tip
              else run.red_gate(FINAL, '.', tip, driver_gate.FULL))
    if not record:
        return None
    return Red(record, _base_record(run, final['onto']) if _judged(record) else '')


def failures(output: str) -> Counter:
    """The failure lines of a gate's output, but a wrapper's, with what differs between runs masked."""
    lines = (line.strip() for line in output.splitlines())
    return Counter(_mask(line) for line in lines if _FAILURE.search(line) and not _WRAPPER.search(line))


def _mask(line: str) -> str:
    for pattern, mask in _MASKS:
        line = pattern.sub(mask, line)
    return line


def _judged(record: str) -> Counter:
    """The failures of a red gate record that judged its commit: a complete run, not ended by a signal, on a tree
    clean after it. No failure for any other record, which proves nothing. An exit from 128 up may be a signal's, as
    a shell reports a crashed child (139 for SIGSEGV), so it judged nothing either."""
    output, code, after = driver_gate.read(record)
    if not (isinstance(code, int) and 0 < code < 128 and after == 'clean'):
        return Counter()
    return failures(output)


def only_old(record: str, base: str) -> bool:
    """The red gate `record` fails exactly as the red gate `base`: both judged their commit, with the same failure
    lines, as often."""
    found = _judged(record)
    return bool(found) and found == _judged(base)


def _old(run: Run, tip: str, red: Red) -> bool:
    """True, recorded and in the ledger, when the red gate of `tip` fails exactly as the gate of its base."""
    if not (red.base and only_old(red.record, red.base)):
        return False
    final = run.state.update(lambda s: s['run']['final'].update(
        gate_failure=None, gate_preexisting=dict(tip=tip, record=red.record, base=red.base)))['run']['final']
    ledger(f"End of run: {preexisting(final, tip)}, so the run goes on and the PR opens as a draft.")
    return True


def preexisting(final: dict, tip: str) -> str:
    """The sentence of the full gate on `tip` that is red only as its base is; "" when it is not."""
    old = final['gate_preexisting']
    if not (old and old['tip'] == tip):
        return ''
    return (f"the full gate is red on {tip[:7]} only with failures that its base {final['onto'][:7]} is red with "
            f"too: read {old['record']} and {old['base']}")


def passed(final: dict, tip: str) -> bool:
    """The full gate on `tip` is green, or red only as its base is."""
    return tip in final['green_gates'][driver_gate.FULL] or bool(preexisting(final, tip))


def evidence(run: Run, tip: str) -> str:
    """The full gate evidence of `tip` for an agent: its record, and the sentence of its failures the base has too."""
    record = str(driver_gate.record_path(OUT / FINAL, driver_gate.FULL, tip))
    old = preexisting(run.state.read()['run']['final'], tip)
    return f"{record} ({old}: they are not this change's to fix)" if old else record


def _base_record(run: Run, onto: str) -> str:
    """The record of the full gate on the base commit `onto` when it is red, "" when it is green or cannot run. It
    runs once per base commit."""
    cached = run.state.read()['run']['final']['base_gate']
    if cached and cached['commit'] == onto:
        return cached['record']
    slot = driver_slots.BASE_SLOT
    if error := (driver_slots.make(slot, 'the base slot', run.args.prepare, start=onto)
                 or driver_slots.git(slot, 'checkout', '-q', '--detach', onto).stderr.strip()):
        ledger(f'End of run: the full gate cannot run on the base {onto[:7]}, so each failure counts as new: {error}.')
        return ''
    return _run_base_gate(run, slot, onto)


def _run_base_gate(run: Run, slot: str, onto: str) -> str:
    """Run the full gate on `onto` in `slot` and keep its result. Returns its record when it is red."""
    green, record, killed = driver_gate.run(run.args.gate, driver_gate.FULL, slot, OUT / FINAL / 'base', run.pr, [])
    for sentence in killed:
        ledger(f'End of run: {sentence}.')
    kept = '' if green else str(record)

    def keep(state: dict) -> None:
        state['run']['gates'][driver_gate.FULL] += 1
        state['run']['final']['base_gate'] = dict(commit=onto, record=kept)

    run.state.update(keep)
    ledger(f"End of run: the full gate is {'green' if green else 'red'} on the base {onto[:7]}, record {record}.")
    return kept


def pointers(final: dict, red: Red, tip: str) -> dict[str, str]:
    """Keep gate-repair evidence separate from review findings and reports. When the base is red too, the evidence
    names its record: a failure in it is not this change's to repair."""
    also = f"; the base {final['onto'][:7]} is red too, read {red.base}: its failures are not this change's" * bool(
        red.base)
    finding = dict(severity='critical', kind='code', source='gate', file=red.record, line=0,
                   title=f'the full integration gate is red on {tip[:7]}',
                   evidence=f'Read the exact failure log: {red.record}{also}')
    path = OUT / FINAL / f"gate-findings-{final['gate_repairs']}.json"
    path.write_text(json.dumps([finding], indent=1) + '\n')
    return dict(gate_title=finding["title"], findings=str(path),
                findings_list=f"- critical: {finding['title']}; read {red.record}{also}")
