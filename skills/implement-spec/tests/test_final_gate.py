"""Full integration gating through the driver CLI, after ticket landings."""
import pytest

from test_final import fixer
from test_walk import plan
from test_fixloop import inside  # noqa: F401 (in-process runtime discovery)
from test_tidy import Inside, tools  # noqa: F401 (fake gh and signing config)


@pytest.mark.parametrize("reword", [False, True])
def test_red_repair_parks_once_and_retry_uses_saved_failure_without_reopening_tickets(tmp_path, reword):
    rig = Inside(tmp_path)
    rig.gate_conf(full={'subjects': ['feat(01): Export rows', 'fix(01): first repair']})
    steps = plan(**{'final/fixer': [fixer('first\n', 'first repair', ticket_of='01'),
                                     fixer('second\n', 'second repair', ticket_of='01')]})
    rig.run(steps, '--push')
    assert rig.code == 2, rig.output()
    assert len(rig.final_calls('fixer')) == 1
    assert not rig.final_calls('final-spec') and not rig.pushes()
    assert rig.state()['tickets']['01']['phase'] == 'merged'
    assert rig.final()['gate_allowance'] == 1
    assert rig.state()['run']['questions'][0]['resume']['gate_allowance'] == 2
    gate_count = len(rig.gates('full'))
    if reword:
        import json
        state = rig.state()
        state['run']['questions'][0]['subject'] = 'Revised wording for the integration retry'
        (rig.repo / '.agent/implement/state.json').write_text(json.dumps(state))
    rig.run(steps, '--push', '--answer', 'final-ticket-blocked-1=retry')
    assert rig.code == 0, rig.output()
    assert len(rig.final_calls('fixer')) == 2
    assert len(rig.calls('01', 'implementer')) == 1
    assert len(rig.gates('full')) == gate_count + 1
    assert rig.final()['gate_repairs'] == rig.final()['gate_allowance'] == 2
    assert rig.state()['tickets']['01']['round'] == 0
    assert rig.pushes() == [f'refs/heads/feat/csv {rig.branch()}']


def test_editing_after_red_gate_does_not_grant_another_repair(tmp_path):
    rig = Inside(tmp_path)
    rig.gate_conf(full={'subjects': ['feat(01): Export rows', 'fix(01): first repair']})
    steps = plan(**{'final/fixer': [fixer('first\n', 'first repair', ticket_of='01')]})
    rig.run(steps)
    assert rig.code == 2, rig.output()
    snapshot = rig.snap()
    rig.run(steps, '--answer', 'final-ticket-blocked-1=edit')
    assert rig.code == 1, rig.output()
    assert rig.final()['gate_allowance'] == rig.final()['gate_repairs'] == 1
    assert len(rig.calls()) == snapshot.calls and len(rig.gates()) == snapshot.gates
    assert rig.state()['tickets']['01']['phase'] == 'merged'


def test_interrupted_integration_repair_resumes_from_saved_log(tmp_path, monkeypatch):
    import driver_final
    from driver_run import Halted

    rig = Inside(tmp_path)
    rig.gate_conf(full={'subjects': ['feat(01): Export rows']})
    steps = plan(**{'final/fixer': [fixer('fixed\n', 'repair integration', ticket_of='01')]})
    real = driver_final._run_fixer

    def interrupted(*args, **kwargs):
        raise Halted('interrupted before repair')

    monkeypatch.setattr(driver_final, '_run_fixer', interrupted)
    rig.run(steps)
    assert rig.code == 4 and len(rig.gates('full')) == 1, rig.out
    assert rig.final()['gate_failure']['record'].endswith('.log')
    assert rig.final()['gate_repairs'] == 0
    monkeypatch.setattr(driver_final, '_run_fixer', real)
    rig.run(steps)
    assert rig.code == 0, rig.out
    assert [gate['exit'] for gate in rig.gates('full')] == [1, 0]
    assert len(rig.calls('01', 'implementer')) == 1
    assert rig.final()['gate_repairs'] == 1


def test_old_final_state_gains_an_unused_repair_allowance_without_rerunning_work(tmp_path):
    import json

    rig = Inside(tmp_path).run(plan())
    assert rig.code == 0, rig.out
    snapshot = rig.snap()
    state = rig.state()
    del state['run']['final']['gate_repairs']
    del state['run']['final']['gate_allowance']
    del state['run']['final']['gate_failure']
    del state['run']['final']['lenses']
    (rig.repo / '.agent/implement/state.json').write_text(json.dumps(state))
    rig.run(plan())
    assert rig.code == 0, rig.out
    assert len(rig.calls()) == snapshot.calls and len(rig.gates()) == snapshot.gates
    assert rig.final()['gate_repairs'] == 0 and rig.final()['gate_failure'] is None
    assert rig.final()['gate_allowance'] == 1
    rig.run(plan(), '--push')
    assert rig.code == 0, rig.out
    body = rig.prs()[0]['body']
    assert f"Full review: {rig.final()['onto']}..{rig.final()['tidied']}" in body
    assert 'Scoped review after the fix batch: not needed' in body


def test_gate_repair_after_review_fix_preserves_findings_for_scoped_review(tmp_path):
    from test_final import final
    from test_review import finding

    rig = Inside(tmp_path)
    rig.gate_conf(full={'subjects': ['fix(01): review fix']})
    steps = plan(**{'final/final-spec': [final(finding('KEEP-THIS-FINDING'))],
                    'final/fixer': [fixer('review\n', 'review fix', ticket_of='01'),
                                    fixer('gate\n', 'gate repair', ticket_of='01')]})
    rig.run(steps, '--push')
    assert rig.code == 0, rig.out
    assert [gate['exit'] for gate in rig.gates('full')] == [0, 1, 0]
    assert len(rig.final_calls('final-spec')) == 1
    assert len(rig.final_calls('final-reviewer')) == 1
    assert 'KEEP-THIS-FINDING' in (rig.repo / '.agent/implement/final/findings-0.json').read_text()
    scoped = rig.final_calls('final-reviewer')[0]
    assert scoped['head'] == rig.branch()
    assert 'findings-0.json' in scoped['prompt']
    assert rig.final()['reviewed'] == rig.branch()
    assert rig.pushes() == [f'refs/heads/feat/csv {rig.branch()}']


@pytest.mark.parametrize("transient", [True, False])
def test_evidenced_noop_repair_checks_the_same_tip_and_keeps_the_bound(tmp_path, monkeypatch, transient):
    import test_final

    monkeypatch.setattr(test_final, "Final", Inside)
    test_final.test_final_gate_repair_can_prove_no_code_change_but_still_spends_its_attempt(tmp_path, transient)


def test_noop_gate_repair_without_failure_evidence_cannot_spend_or_publish(tmp_path):
    rig = Inside(tmp_path)
    rig.gate_conf(full={"subjects": ["feat(01): Export rows"]})
    empty = {"report": dict(status="DONE", commits=[], files=[], green=["passed"], concerns=["no change"])}
    rig.run(plan(**{"final/fixer": [empty, empty]}), "--push")
    assert rig.code == 2, rig.out
    assert rig.final()["gate_repairs"] == 0
    assert len(rig.gates("full")) == 1 and not rig.pushes()
    assert "no concern names the finding the full integration gate is red" in rig.out


def test_gate_repair_preserves_locked_tests_and_escalates_a_needed_correction(tmp_path):
    rig = Inside(tmp_path)
    rig.gate_conf(full={"subjects": ["feat(01): Export rows"]})
    changed = fixer("fixed\n", "alter test", ticket_of="01")
    changed["commits"][0]["files"]["tests/test_01.py"] = "changed = True\n"
    changed["report"]["files"].append("tests/test_01.py")
    retry = {"report": dict(changed["report"], commits=[{"sha": "$HEAD", "ticket": "01"}])}
    rig.run(plan(**{"final/fixer": [changed, retry]}), "--push")
    assert rig.code == 2, rig.out
    assert "locked file changed: tests/test_01.py" in rig.out
    assert rig.final()["gate_repairs"] == 0 and not rig.pushes()
    assert rig.branch() == rig.final()["tidied"]
    assert rig.state()["tickets"]["01"]["phase"] == "merged"
