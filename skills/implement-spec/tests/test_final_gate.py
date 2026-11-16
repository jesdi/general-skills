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


# ---------------------------------------------------------------- the base gate: only new failures count

OLD, NEW = 'FAILED tests/test_old.py::test_y - AssertionError', 'FAILED tests/test_01.py::test_z - KeyError'


def test_a_red_gate_with_only_the_failures_of_the_base_goes_on_to_review_and_a_draft_pr(tmp_path, monkeypatch):
    import driver_final_gate
    import driver_slots
    from test_final import final
    from test_review import finding

    rig = Inside(tmp_path)
    tips = ['feat(01): Export rows', 'fix(01): review fix']
    rig.gate_conf(full={'subjects': ['spec', *tips]}, print={subject: [OLD] for subject in ['spec', *tips]})
    steps = plan(**{'final/final-spec': [final(finding('A REVIEW FINDING'))],
                    'final/fixer': [fixer('review\n', 'review fix', ticket_of='01')]})
    slot_1 = []
    base_gate = driver_final_gate._run_base_gate
    seen = lambda: (driver_slots.head('.'), driver_slots.on_branch('.'), driver_slots.git('.', 'status', '-s').stdout)

    def watched(*args):
        before = seen()
        kept = base_gate(*args)
        slot_1.append(before == seen())
        return kept

    monkeypatch.setattr(driver_final_gate, '_run_base_gate', watched)
    rig.run(steps, '--push')
    assert rig.code == 0, rig.output()
    assert slot_1 == [True], 'the base gate leaves slot 1 on its branch, at its tip, with its status'
    onto = rig.final()['onto']
    base = [gate for gate in rig.gates('full') if gate['commit'] == onto]
    assert len(base) == 1, 'the base gate runs once for both red tips'
    assert base[0]['cwd'].endswith('.agent/worktrees/slot-base')
    assert len(rig.final_calls('fixer')) == 1, 'only the review fix: no gate repair'
    assert "not this change's to fix" in rig.final_calls('final-spec')[0]['prompt']
    assert rig.final()['reviewed'] == rig.branch()
    assert rig.pushes() == [f'refs/heads/feat/csv {rig.branch()}']
    (pr,) = rig.prs()
    assert '--draft' in pr['argv'], 'a match of failure lines is no proof: the operator readies the PR'
    old = f"final/base/gate-full-{onto[:7]}.log"
    assert f'only with failures that its base {onto[:7]} is red with too' in pr['body'] and old in pr['body']
    assert rig.led(f'red on {rig.branch()[:7]} only with failures that its base {onto[:7]} is red with too', old,
                   'the PR opens as a draft')
    assert rig.led('is open as a draft: the full gate is red on')


def test_a_failure_the_base_lacks_goes_to_the_repair_with_the_base_record(tmp_path):
    rig = Inside(tmp_path)
    rig.gate_conf(full={'subjects': ['spec', 'feat(01): Export rows']},
                  print={'spec': [OLD], 'feat(01): Export rows': [OLD, NEW]})
    rig.run(plan(**{'final/fixer': [fixer('fixed\n', 'repair integration', ticket_of='01')]}), '--push')
    assert rig.code == 0, rig.output()
    (repair,) = rig.final_calls('fixer')
    assert f"final/base/gate-full-{rig.final()['onto'][:7]}.log" in repair['prompt']
    assert '--draft' not in rig.prs()[0]['argv']


def test_a_red_tip_on_a_green_base_goes_to_the_repair(tmp_path):
    rig = Inside(tmp_path)
    rig.gate_conf(full={'subjects': ['feat(01): Export rows']}, print={'feat(01): Export rows': [OLD]})
    rig.run(plan(**{'final/fixer': [fixer('fixed\n', 'repair integration', ticket_of='01')]}), '--push')
    assert rig.code == 0, rig.output()
    (repair,) = rig.final_calls('fixer')
    assert 'is red too' not in repair['prompt']
    assert rig.led(f"the full gate is green on the base {rig.final()['onto'][:7]}")
    assert not rig.final()['gate_preexisting'] and '--draft' not in rig.prs()[0]['argv']


def test_a_base_gate_that_cannot_run_counts_each_failure_as_new(tmp_path):
    rig = Inside(tmp_path)
    (rig.repo / '.agent/worktrees').mkdir(parents=True, exist_ok=True)
    (rig.repo / '.agent/worktrees/slot-base').write_text('not a worktree\n')
    rig.gate_conf(full={'subjects': ['spec', 'feat(01): Export rows']},
                  print={'spec': [OLD], 'feat(01): Export rows': [OLD]})
    rig.run(plan(**{'final/fixer': [fixer('fixed\n', 'repair integration', ticket_of='01')]}), '--push')
    assert rig.code == 0, rig.output()
    assert len(rig.final_calls('fixer')) == 1 and not rig.final()['gate_preexisting']
    assert rig.led('the full gate cannot run on the base', 'so each failure counts as new')


def test_a_red_gate_with_no_failure_line_counts_as_new_and_runs_no_base_gate(tmp_path):
    rig = Inside(tmp_path)
    rig.gate_conf(full={'subjects': ['spec', 'feat(01): Export rows']})
    rig.run(plan(**{'final/fixer': [fixer('fixed\n', 'repair integration', ticket_of='01')]}), '--push')
    assert rig.code == 0, rig.output()
    assert len(rig.final_calls('fixer')) == 1
    assert not [gate for gate in rig.gates('full') if gate['commit'] == rig.final()['onto']]


def test_failures_are_the_failure_lines_of_the_output_with_times_line_numbers_and_counts_masked(tmp_path):
    import driver_final_gate

    def record(name, output, after='clean', end='exit: 1\nafter: {after}\nend: y\n'):
        path = tmp_path / name
        path.write_text(f'start: x\ntier: full\ncommit: abc\nstatus: clean\ncommand: make check\n\n{output}\n'
                        + end.format(after=after))
        return str(path)

    base = record('base', 'FAILED tests/a.py::test_1 - x\n== 1 failed, 40 passed in 2.31s ==\n'
                          'src/a.ts:12:5 error no-x\nmake: *** [check] Error 1')
    same = record('same', 'ok\nFAILED tests/a.py::test_1 - x\n== 1 failed, 52 passed in 3.1s ==\n'
                          'src/a.ts:14:5 error no-x')
    assert driver_final_gate.only_old(same, base), 'a wrapper line names no failure'
    assert not driver_final_gate.only_old(record('new', 'FAILED tests/a.py::test_2 - x'), base)
    assert not driver_final_gate.only_old(record('twice', 'src/a.ts:1:1 error no-x\nsrc/a.ts:2:1 error no-x'), base)
    assert not driver_final_gate.only_old(record('none', 'Segmentation fault'), base)
    assert not driver_final_gate.only_old(record('dirty', 'FAILED tests/a.py::test_1 - x', 'not clean'), base)
    one = record('one', 'FAILED tests/a.py::test_limit[1] - x')
    assert not driver_final_gate.only_old(record('two', 'FAILED tests/a.py::test_limit[2] - x'), one), \
        'a number in a test identifier is part of its name'
    assert not driver_final_gate.only_old(record('fewer', 'FAILED tests/a.py::test_1 - x'), base), \
        'a new failure that no line names can hide behind the same lines: only the same failures are old'
    wrapped = record('wrapped', 'make: *** [check] Error 1')
    assert not driver_final_gate.only_old(wrapped, wrapped), 'a wrapper line alone proves nothing'
    mocha = record('mocha', '  2 failing\n  1) Suite does x:\n     AssertionError: expected 1 to equal 2 (12ms)')
    assert driver_final_gate.only_old(
        record('mocha-2', '  2 failing\n  1) Suite does x:\n     AssertionError: expected 1 to equal 2 (40ms)'), mocha)
    assert not driver_final_gate.only_old(
        record('mocha-3', '  2 failing\n  1) Suite does x:\n     AssertionError: expected 1 to equal 3 (40ms)'), mocha)
    assert not driver_final_gate.only_old(record('3-failing', '  3 failing'), record('2-failing', '  2 failing')), \
        'a count of failures stays'
    titled = record('titled', '  1 failing\n  1) Suite does x:\n     AssertionError: expected 1 to equal 2')
    assert not driver_final_gate.only_old(
        record('retitled', '  1 failing\n  1) Suite does y:\n     AssertionError: expected 1 to equal 2'), titled), \
        'the title of a failed test names it'
    for name, end in (('killed', 'exit: 137\nafter: clean\nend: y\n'), ('signal', 'exit: -9\nafter: clean\nend: y\n'),
                      ('segfault', 'exit: 139\nafter: clean\nend: y\n'), ('abort', 'exit: 134\nafter: clean\nend: y\n'),
                      ('cut', '')):
        stopped = record(name, 'FAILED tests/a.py::test_1 - x', end=end)
        assert not driver_final_gate.only_old(stopped, stopped), f'a {name} run judged nothing'
        assert not driver_final_gate.only_old(same, stopped), f'a {name} base run judged nothing'


def test_read_gives_the_output_exit_and_after_of_a_gate_record(tmp_path):
    import driver_gate

    record = tmp_path / 'r.log'
    record.write_text('start: x\ncommand: make\n\nline 1\n\nline 2\nexit: 2\nafter: not clean\n    M a.py\nend: y\n')
    assert driver_gate.read(record) == ('line 1\n\nline 2', 2, 'not clean\n    M a.py')
    record.write_text('start: x\ncommand: make\n\nline 1\n')
    assert driver_gate.read(record) == ('line 1\n', '', '')
