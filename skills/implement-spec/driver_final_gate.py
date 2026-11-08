"""The full integration gate: one automatic repair batch, then an operator retry.

A red record is durable before repair starts. Interrupted repair resumes from it;
only an accepted batch spends the allowance. Ticket fix budgets never change.
"""
from __future__ import annotations

import json

import driver_gate
from driver_run import FINAL, FINAL_RESUME, OUT, Blocked, Run

RETRY_SUBJECT = 'the full integration gate remains red after its repair'


def _allowance(state: dict) -> int:
    return 1 + sum(q['ticket'] == FINAL and q['subject'].startswith(RETRY_SUBJECT)
                   and q['answer'] == 'retry' for q in state['run']['questions'])


def failure(run: Run, tip: str) -> str:
    """Return a repairable red record, empty for green, or park at the repair cap."""
    state = run.state.read()
    final = state['run']['final']
    pending = final['gate_failure']
    record = (pending['record'] if pending and pending['tip'] == tip
              else run.red_gate(FINAL, '.', tip, driver_gate.FULL))
    if not record:
        return ''
    run.state.update(lambda s: s['run']['final'].update(gate_failure=dict(tip=tip, record=record)))
    if final['gate_repairs'] >= _allowance(state):
        subject = f'{RETRY_SUBJECT}: {tip[:7]}; read {record}'
        run.ask(FINAL, 'ticket-blocked', [subject], record, FINAL_RESUME)
        raise Blocked(subject)
    return record


def pointers(final: dict, record: str, tip: str) -> dict[str, str]:
    """Keep gate-repair evidence separate from review findings and reports."""
    finding = dict(severity='critical', kind='code', source='gate', file=record, line=0,
                   title=f'the full integration gate is red on {tip[:7]}',
                   evidence=f'Read the exact failure log: {record}')
    path = OUT / FINAL / f"gate-findings-{final['gate_repairs']}.json"
    path.write_text(json.dumps([finding], indent=1) + '\n')
    return dict(gate_title=finding["title"], findings=str(path), findings_list=f"- critical: {finding['title']}; read {record}")
