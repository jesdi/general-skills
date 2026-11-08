# Recover an older questionless implementer stop

Use this only when an older run stopped after two implementer attempts and left ticket `phase: blocked` with no question. Repair the reported environment problem first. New runs ask `ticket-blocked` after exhausted retries, including timeouts and moved branches.

Stop the driver and prevent the dispatcher from resuming it during recovery. In the PR worktree, preserve any dirty files with a binary patch and exact file copies outside tracked paths. Do not reset the checkout or overwrite locked tests. Keep the branch and driver records; recovery adds a question, not a passing gate.

Read the ticket's ledger and reports. Confirm that the implementer stopped before any accepted implementation and that its recorded tip is still the test-writer commit. The command below accepts only that narrow checkpoint: schema 5, round zero, no gate results, no prior question for this ticket, two recorded implementer attempts, and an intact test-writer commit on the ticket branch. Other phases, uncertain evidence, or prior operator decisions require investigation; do not guess a phase or delete state to start over.

Run the following from the PR worktree, replacing the skill path, ticket number and full verified test-writer SHA. It takes the driver's locks, backs up state, records a question through the existing helper and leaves the ticket BLOCKED. No checkout, branch or gate is changed.

```sh
python3 - /path/to/implement-spec 01 VERIFIED_TEST_WRITER_SHA <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import driver
import driver_decisions
import driver_names
import driver_slots
import driver_state

number, tip = sys.argv[2:]
path = Path('.agent/implement/state.json')
state = driver_state.State(path)
pr = state.read()['run']['pr_branch']
common = Path(driver_slots.git('.', 'rev-parse', '--git-common-dir').stdout.strip()).resolve()
held, error = driver._hold(common, pr)
if not held:
    raise SystemExit(error)
with held:
    before = path.read_bytes()
    data = json.loads(before)
    entry = data['tickets'][number]
    attempts = [a['role'] for a in data['run']['agents'] if a['ticket'] == number]
    branch = driver_names.Names(pr).ticket(number)
    valid = (data['run']['schema'] == 5 and entry['phase'] == 'blocked'
             and entry['round'] == 0 and entry['tip'] == entry['test_writer_sha'] == tip
             and entry['locked'] and not any(entry['green_gates'].values())
             and not any(q['ticket'] == number for q in data['run']['questions'])
             and attempts[-2:] == ['implementer', 'implementer']
             and driver_slots.git('.', 'rev-parse', '--verify', branch).stdout.strip() == tip)
    if not valid:
        raise SystemExit('Checkpoint not verified; leave state unchanged and investigate.')
    backup = path.with_name(f'state-before-recovery-{number}.json')
    with backup.open('xb') as out:
        out.write(before)
    def recover(current):
        driver_decisions.stop(current['run'], 'ticket-blocked', number,
            ['Operator verified the repaired environment and pre-implementation checkpoint.'],
            ['.agent/implement/ledger.md'], driver_decisions.Resume('implement', tip))
    updated = state.update(recover)
    question = updated['run']['questions'][-1]['id']
    with path.with_name('ledger.md').open('a') as out:
        out.write(f'Operator recovery: backup {backup}; added {question} at verified tip {tip}.\n')
    print(f'Rerun the original driver command with --answer {question}=retry')
PY
```

The normal driver invocation publishes the question and consumes that answer. Keep all original gate, spec and push flags. It reruns the implementer, then the normal gates and reviews; locked-test ownership stays unchanged. An unanswered invocation stays parked and creates no second question.

If the driver refuses dirty files, keep their verified backup and determine which slot owns them before restoring anything. Inspect preserved implementation as input to the resumed implementer; do not apply test changes over its locked baseline. Keep the recovery backup until the ticket is safely completed.
