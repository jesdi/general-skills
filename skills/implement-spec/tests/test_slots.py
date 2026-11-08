"""Slots 2..N: created once, prepared once, reused only when clean."""
import subprocess

import driver_slots


def test_a_slot_is_created_and_prepared_once_then_reused_off_any_branch(repo, tmp_path):
    repo()
    log = tmp_path.parent / f"{tmp_path.name}-prepare.log"
    assert driver_slots.setup(3, f"pwd >> {log}") == []
    assert len(log.read_text().splitlines()) == 2 and driver_slots.path(1) == "."
    slot = driver_slots.path(3)
    subprocess.run(["git", "-C", slot, "checkout", "-q", "-b", "ticket/07"], check=True)
    assert driver_slots.setup(3, f"pwd >> {log}") == [] and len(log.read_text().splitlines()) == 2
    assert driver_slots.git(slot, "symbolic-ref", "-q", "HEAD").returncode != 0  # detached: the branch is free


def test_a_slot_with_uncommitted_work_stops_the_run_and_keeps_the_work(repo, tmp_path):
    repo()
    assert driver_slots.setup(2, None) == []
    (tmp_path / ".agent/worktrees/slot-2/wip.txt").write_text("half done\n")
    assert driver_slots.setup(3, None) == [
        "slot 2: .agent/worktrees/slot-2 could not be released: inspect its Git state and uncommitted changes"]
    assert (tmp_path / ".agent/worktrees/slot-2/wip.txt").exists() and not (tmp_path / ".agent/worktrees/slot-3").exists()


def test_a_slot_that_cannot_be_created_is_an_error(repo, tmp_path):
    repo()
    (tmp_path / ".agent/worktrees").mkdir(parents=True)
    (tmp_path / ".agent/worktrees/slot-2").write_text("a file is in the way\n")
    (error,) = driver_slots.setup(2, None)
    assert error.startswith("slot 2: cannot create .agent/worktrees/slot-2: ")


def test_preparing_a_slot_does_not_prune_another_tasks_invisible_checkout(repo, tmp_path):
    repo()
    other = tmp_path.parent / f"{tmp_path.name}-other"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(other)], check=True)
    hidden = other.with_name(other.name + "-hidden")
    other.rename(hidden)  # as in a session whose mounts hide the other task's checkout
    try:
        assert driver_slots.setup(2, None) == []
    finally:
        hidden.rename(other)
    result = subprocess.run(["git", "-C", str(other), "status", "--porcelain"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_only_a_missing_slots_registration_is_recovered(repo, tmp_path):
    import shutil
    repo()
    log = tmp_path.parent / f"{tmp_path.name}-prepare.log"
    assert driver_slots.setup(2, f"pwd >> {log}") == []
    shutil.rmtree(tmp_path / driver_slots.path(2))
    assert driver_slots.setup(2, f"pwd >> {log}") == []
    assert driver_slots.git(driver_slots.path(2), "status", "--porcelain").returncode == 0
    assert len(log.read_text().splitlines()) == 2


def test_a_missing_locked_slot_is_not_recreated(repo, tmp_path):
    import shutil
    repo()
    assert driver_slots.setup(2, None) == []
    slot = driver_slots.path(2)
    subprocess.run(["git", "worktree", "lock", slot], check=True)
    shutil.rmtree(tmp_path / slot)
    assert driver_slots.setup(2, None)
    assert not (tmp_path / slot).exists()
    assert "locked" in driver_slots.git(".", "worktree", "list", "--porcelain").stdout


def test_releasing_a_broken_checkout_fails_without_reporting_it_reusable(repo, tmp_path):
    repo()
    assert driver_slots.setup(2, None) == []
    slot = tmp_path / driver_slots.path(2)
    (slot / '.git').write_text('gitdir: /missing/diagnosis-metadata\n')
    assert not driver_slots.release(str(slot))


def test_slot_reuse_preserves_installed_dependencies_and_worktree_identity(repo, tmp_path):
    repo()
    log = tmp_path.parent / f"{tmp_path.name}-prepare.log"
    prepare = f"mkdir -p .agent/dependencies; pwd >> {log}"
    assert driver_slots.setup(3, prepare) == []
    slot = driver_slots.path(2)
    metadata = (tmp_path / slot / '.git').read_text()
    dependencies = tmp_path / slot / '.agent/dependencies'
    inode = dependencies.stat().st_ino
    for ticket in ('ticket/01', 'ticket/02', 'ticket/03'):
        subprocess.run(['git', '-C', slot, 'checkout', '-qb', ticket], check=True)
        assert driver_slots.release(slot)
    assert driver_slots.setup(3, prepare) == []
    assert len(log.read_text().splitlines()) == 2
    assert (tmp_path / slot / '.git').read_text() == metadata
    assert dependencies.stat().st_ino == inode
    assert len(driver_slots.git('.', 'worktree', 'list', '--porcelain').stdout.split('worktree ')) - 1 == 3


def test_a_foreign_repository_in_a_slot_is_left_untouched_even_when_kept(repo, tmp_path):
    repo()
    slot = tmp_path / driver_slots.path(2)
    slot.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', '-b', 'foreign', str(slot)], check=True)
    subprocess.run(['git', '-C', str(slot), 'commit', '--allow-empty', '-qm', 'foreign'], check=True)
    assert driver_slots.setup(2, None, {2})
    assert driver_slots.on_branch(str(slot)) == 'foreign'


def test_a_locked_index_does_not_report_a_slot_released(repo, tmp_path):
    repo()
    assert driver_slots.setup(2, None) == []
    slot = driver_slots.path(2)
    subprocess.run(['git', '-C', slot, 'checkout', '-qb', 'ticket/held'], check=True)
    index = driver_slots.git(slot, 'rev-parse', '--path-format=absolute', '--git-path', 'index.lock').stdout.strip()
    from pathlib import Path
    Path(index).touch()
    assert not driver_slots.release(slot)
    assert driver_slots.on_branch(slot) == 'ticket/held'


def test_registry_operations_coordinate_across_processes_and_linked_checkouts(repo, tmp_path):
    import sys
    from pathlib import Path
    import pytest

    repo()
    other = tmp_path.parent / f"{tmp_path.name}-linked"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(other)], check=True)
    unrelated = tmp_path.parent / f"{tmp_path.name}-independent"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(tmp_path), str(unrelated)], check=True)
    program = r'''
import subprocess, sys
sys.path.insert(0, sys.argv[1])
import driver_slots
real_run = subprocess.run
mode = sys.argv[2]
def pause_add(command, *args, **kwargs):
    if "worktree" in command and "add" in command:
        print("holding registration", flush=True)
        sys.stdin.readline()
    return real_run(command, *args, **kwargs)
if mode == "add":
    driver_slots.subprocess.run = pause_add
    result = driver_slots.git(".", "worktree", "add", "-q", "--detach", ".agent/worktrees/new")
else:
    print("requesting checkout", flush=True)
    result = driver_slots.git(".", "checkout", "-q", "--detach")
print(result.stderr, flush=True)
sys.exit(result.returncode)
'''
    command = [sys.executable, "-u", "-c", program, str(Path(driver_slots.__file__).parent)]
    add = subprocess.Popen([*command, "add"], cwd=tmp_path, stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    checkout = None
    try:
        assert add.stdout.readline().strip() == "holding registration"
        checkout = subprocess.Popen([*command, "checkout"], cwd=other, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
        assert checkout.stdout.readline().strip() == "requesting checkout"
        with pytest.raises(subprocess.TimeoutExpired):
            checkout.communicate(timeout=0.2)
        # Read-only commands do not wait on worktree registration.
        probe = "import sys; sys.path.insert(0, sys.argv[1]); import driver_slots; print(driver_slots.head('.'))"
        read = subprocess.run([sys.executable, "-c", probe, str(Path(driver_slots.__file__).parent)],
                              cwd=tmp_path, capture_output=True, text=True, timeout=5)
        assert read.returncode == 0 and read.stdout.strip(), read.stderr
        independent = subprocess.run([*command, "checkout"], cwd=unrelated, capture_output=True, text=True, timeout=5)
        assert independent.returncode == 0, independent.stderr
        add.stdin.write("continue\n")
        add.stdin.flush()
        out, err = add.communicate(timeout=5)
        assert add.returncode == 0, out + err
        out, err = checkout.communicate(timeout=5)
        assert checkout.returncode == 0, out + err
    finally:
        for process in (add, checkout):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()


def test_git_without_a_command_still_returns_gits_usage_error(repo):
    repo()
    result = driver_slots.git(".")
    assert result.returncode != 0 and "usage: git" in result.stdout + result.stderr
