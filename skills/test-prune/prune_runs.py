"""apply (interactive) and prune (unattended): apply the accepted deletions, run the safety check,
restore every culprit, then commit; unattended also branches, gates, pushes and opens the PR."""
from __future__ import annotations

import re

from prune_repo import Fail, fenced, ratchet_lines
from prune_review import CAP, equivalents_section, suggestions_section
from prune_source import delete_tests, families, targets

_CHECK_LINE = re.compile(r"^(  [✓✗•!] |      \S|    ↑)")
_VERDICT = re.compile(r"^Prune gate (PASSED|FAILED|ERROR)")


def _check_block(lines: list) -> list:
    """The last `make mutation-check` block in lines: back from `Mutation ratchet PASSED/FAILED.` over its
    module lines to `Mutation results from…` when printed."""
    ends = [i for i, line in enumerate(lines) if re.match(r"^Mutation ratchet (PASSED|FAILED)", line)]
    if not ends:
        return []
    start = ends[-1]
    while start and _CHECK_LINE.match(lines[start - 1]):
        start -= 1
    if start and lines[start - 1].startswith("Mutation results from"):
        start -= 1
    return lines[start:ends[-1] + 1]


def gate_report(output: str) -> str:
    """The prune gate's own report out of its whole log (the real gate prints its cold mutmut run): the
    `make mutation-check` block it printed and everything from its verdict on. No verdict found: the
    log's last 30 lines."""
    lines = output.splitlines()
    verdicts = [i for i, line in enumerate(lines) if _VERDICT.match(line)]
    if not verdicts:
        return "\n".join(lines[-30:])
    return "\n".join(_check_block(lines[:verdicts[-1]]) + lines[verdicts[-1]:])


class DeletionSet:
    """Writes a deletion set over the base commit's content of every file the whole batch touches."""

    def __init__(self, repo, commit: str, ids):
        self.repo, self.commit, self.files = repo, commit, sorted(targets(ids))

    def write(self, ids) -> None:
        chosen, texts = targets(ids), {}
        for path in self.files:
            source = self.repo.show_test(self.commit, path)
            texts[self.repo.test_path(path)] = delete_tests(source, chosen[path]) if path in chosen else source
        self.repo.write(texts, self.commit)


def _culprit(family: list, failing: dict, passing: dict) -> list:
    name = family[0] if len(family) == 1 else family[0].split("[")[0]
    return [{"test_id": name, "test_ids": family, "module": m, "survivors_with": failing["modules"][m][0],
             "baseline": failing["modules"][m][1], "survivors_after": passing["modules"].get(m, [None])[0]}
            for m in failing["failed"]]


def _flat(units) -> list:
    return [test_id for unit in units for test_id in unit]


def safety_check(repo, base: dict, ids: list) -> tuple[list, list, dict]:
    """Restore every deletion whose absence lets a module exceed its baseline; returns (kept ids, restored,
    ratchet of the kept set) with the kept set written to the tree. Survivors only grow as tests go, so
    the smallest failing prefix of families ends with a culprit; repeat until the whole kept set passes."""
    deletion_set = DeletionSet(repo, base["commit"], ids)
    results = {(): base["ratchet"]}

    def check(units: list) -> dict:
        key = tuple(_flat(units))
        if key not in results:
            deletion_set.write(key)
            results[key] = repo.cold_ratchet()
        return results[key]

    kept, restored, lo = families(ids), [], 0
    while not check(kept)["ok"]:
        hi = len(kept)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if check(kept[:mid])["ok"]:
                lo = mid
            else:
                hi = mid
        restored += _culprit(kept[hi - 1], check(kept[:hi]), check(kept[:hi - 1]))
        del kept[hi - 1]
        lo = hi - 1
    deletion_set.write(_flat(kept))
    return _flat(kept), restored, check(kept)


def _commit(repo, kept: list, by_id: dict) -> str:
    subject = f"test: delete {len(kept)} redundant test{'' if len(kept) == 1 else 's'}"
    body = "Each one is contained by another test and the mutation ratchet passes without it:\n" + "\n".join(
        f"- {t} (contained by {by_id[t]['into']})" for t in kept)
    repo.git("add", "--", *(repo.test_path(path) for path in sorted(targets(kept))))
    repo.git("commit", "-q", "-m", subject, "-m", body)
    return subject


def restored_lines(restored: list) -> list:
    return [f"- `{r['test_id']}`{_cases(r)}: {r['module']} had {r['survivors_with']} survivors with it deleted, baseline "
            f"{r['baseline']}; {r['survivors_after']} after restoring it" for r in restored]


def _cases(restored: dict) -> str:
    return f" ({len(restored['test_ids'])} parametrized cases)" if len(restored["test_ids"]) > 1 else ""


def _deletions_report(heading: str, kept: list, by_id: dict, restored: list, left: list, ratchet: dict) -> list:
    lines = [f"## {heading} ({len(kept)})", ""]
    lines += [f"- `{t}`, contained by `{by_id[t]['into']}`: {by_id[t]['reason']}" for t in kept]
    if restored:
        lines += ["", f"## Restored by the safety check ({len({t for r in restored for t in r['test_ids']})})", ""]
        lines += restored_lines(restored)
    if left:
        lines += ["", f"## Left for next week ({len(left)})", "",
                  f"Accepted, over the cap of {CAP} deletions per run (a parametrized test counts each case):", ""]
        lines += [f"- `{d['test_id']}`, contained by `{d['into']}`" for d in left]
    lines += ["", "## Mutation ratchet (cold run on this branch)", ""]
    lines += [f"- {line}" for line in ratchet_lines(ratchet)]
    return lines


def _apply_and_commit(ctx, base: dict, deletions: list) -> tuple[list, list, dict]:
    """Safety check, make test and the commit; any failure puts every touched test file back."""
    repo = ctx.repo
    by_id = {d["test_id"]: d for d in deletions}
    ids = [d["test_id"] for d in deletions]
    try:
        kept, restored, ratchet = safety_check(repo, base, ids)
        if kept:
            repo.must_make("test")
            _commit(repo, kept, by_id)
    except BaseException:
        try:
            DeletionSet(repo, base["commit"], ids).write([])
        except Fail as kept_edit:  # a file someone else changed stays as it is
            ctx.state.emit(f"test-prune: {kept_edit}")
        raise
    return kept, restored, ratchet


def apply(ctx) -> int:
    repo, base = ctx.repo, ctx.base("interactive")
    repo.require_at(base, "apply")
    deletions = ctx.state.load("batch.json")["deletions"]
    if not deletions:
        ctx.state.emit("no accepted deletions to apply")
        return 0
    kept, restored, ratchet = _apply_and_commit(ctx, base, deletions)
    head = (f"Committed on {base['branch']}: {repo.git('log', '-1', '--format=%s').strip()}" if kept
            else "Every deletion was restored: nothing committed")
    by_id = {d["test_id"]: d for d in deletions}
    ctx.state.emit("\n".join([head, ""] + _deletions_report("Deletions committed", kept, by_id, restored, [], ratchet)))
    return 0


def _nothing_to_prune(ctx, base: dict, out: dict) -> str:
    if not ctx.state.load("fixer-input.json")["candidates"]:
        reason = "no protected candidates" if base["protected"] else "no candidates"
    else:
        reason = f"no accepted deletions ({len(out['rejected'])} rejected, {len(out['skipped'])} skipped)"
    return "\n".join([f"test-prune: {reason}; no branch, no PR"] + suggestions_section(out)
                     + equivalents_section(out["equivalents"]))


def prune(ctx) -> int:
    repo, base = ctx.repo, ctx.base("unattended")
    out = ctx.state.load("batch.json")
    if not out["deletions"]:
        ctx.state.emit(_nothing_to_prune(ctx, base, out))
        return 0
    branch = f"chore/test-prune-{ctx.date}"
    repo.detach_at(base, branch, "prune")
    try:
        return _prune_detached(ctx, base, out, branch)
    finally:
        repo.leave_on(base["branch"], base["commit"])


def _prune_detached(ctx, base: dict, out: dict, branch: str) -> int:
    repo = ctx.repo
    by_id = {d["test_id"]: d for d in out["deletions"]}
    left = out["left"]
    kept, restored, ratchet = safety_check(repo, base, [d["test_id"] for d in out["deletions"]])
    if not kept:
        ctx.state.emit("\n".join(["test-prune: the safety check restored every accepted deletion; no branch, no PR", ""]
                       + restored_lines(restored)))
        return 0
    def report(heading: str) -> list:
        return _deletions_report(heading, kept, by_id, restored, left, ratchet)

    try:
        return _publish(ctx, base, out, branch, kept, report)
    except Fail as exc:  # the job summary keeps what the safety check found
        raise Fail("\n".join([f"{exc}", ""] + report(_where(repo, branch)))) from None


def _where(repo, branch: str) -> str:
    """Where the deletions are after a failure past the safety check."""
    if repo.run_git("rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode:
        return "Deletions that passed the safety check, not committed"
    if repo.run_git("ls-remote", "--heads", "origin", branch).stdout.strip():
        return f"Deletions on `{branch}`, pushed without a PR"
    return f"Deletions on the unpushed branch `{branch}`"


def _publish(ctx, base: dict, out: dict, branch: str, kept: list, report) -> int:
    """make test, the commit, the branch, make prune-gate, then the push and the PR."""
    repo = ctx.repo
    repo.must_make("test")
    subject = _commit(repo, kept, {d["test_id"]: d for d in out["deletions"]})
    repo.git("branch", branch)
    gate = repo.make("prune-gate", f"BASE={base['commit']}", f"HEAD={branch}")
    log = ctx.state.save_text("prune-gate.log", gate.stdout + gate.stderr)
    gate_output = f"{fenced(gate_report(gate.stdout + gate.stderr))}\n\nThe gate's report; its full log is in `{log}`."
    if gate.returncode:
        ctx.state.emit("\n".join([f"test-prune: make prune-gate failed on {branch}; nothing pushed, no PR", "", gate_output, ""]
                       + report(f"Deletions on the unpushed branch `{branch}`")))
        return 1
    body = "\n".join([f"Weekly test prune from `{base['branch']}` at {base['commit'][:12]}. "
                      "Delete-only; merge it to accept the batch.", ""] + report("Deleted tests")
                     + ["", "## Prune gate", "", gate_output]
                     + suggestions_section(out) + equivalents_section(out["equivalents"])) + "\n"
    repo.push(branch)
    path, body = ctx.state.save_body("prune-pr.md", body)
    url = repo.open_pr(base["branch"], branch, subject, path)
    ctx.state.emit(f"Opened {url}\n\n{body}")
    return 0
