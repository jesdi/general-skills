"""Acceptance tests for `babysit.py judge`.

Interface under test (defined here):
  babysit.py judge --body <file> --base <ref> --head <sha> --agent claude-code|codex
Run inside the PR checkout: `origin/<base>` holds the policy, <sha> is the PR head. The body
file is the PR body (summary + merge-risk block). Exit 0, one JSON object on stdout:
  verdict    "agree" | "disagree"   (disagree also stands for bad JSON, timeout, any doubt)
  reason     the handoff reason when disagree: "judge: <judge reason>", or "judge timeout"
  sameModel  true when the other vendor's CLI was missing and a fresh agent of the same vendor judged
  label      what the merge comment shows after "judge ": "agree" | "same-model agree" (agree only)
Env BABYSIT_JUDGE_TIMEOUT overrides the 10 minute limit, in seconds.

The judge CLIs are fakes on PATH (`codex`, `claude`). A fake logs {name, argv, stdin} to
$FAKE_LOG and prints the text in $FAKE_ANSWER (also writes it to a `-o <file>` argument).
`FAKE_SLEEP` makes it hang that many seconds first. `FAKE_ANSWER_<NAME>` and `FAKE_EXIT_<NAME>`
(NAME = CODEX or CLAUDE) override the answer and the exit code of one fake.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("babysit.py").resolve()
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull}

FAKE = """#!{py}
import json, os, sys, time
a = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({{"name": "{name}", "argv": a, "stdin": sys.stdin.read()}}) + "\\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
ans = open(os.environ.get("FAKE_ANSWER_{NAME}", os.environ["FAKE_ANSWER"])).read()
for i, x in enumerate(a):
    if x in ("-o", "--output-last-message") and i + 1 < len(a):
        open(a[i + 1], "w").write(ans)
print(ans)
sys.exit(int(os.environ.get("FAKE_EXIT_{NAME}", "0")))
"""

AGREE = '{"verdict": "agree", "reason": "small and reversible"}'


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env={**os.environ, **GIT_ENV}, text=True,
                          capture_output=True, check=True).stdout.strip()


def commit(repo, files, msg):
    for name, content in files.items():
        f = Path(repo) / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


BODY = """## Summary

SECRET-PR-SUMMARY we argue this is fine.

## Merge Danger

```merge-risk v1
door: two-way
blast_radius: isolated
touches: [logic]
rollback: revert
verdict: auto
reasons:
  - "BLOCK-REASON-TEXT small and reversible"
```
"""


class Judge:
    def __init__(self, tmp_path, codex=True, claude=True):
        self.tmp = tmp_path
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "work")
        base = commit(self.repo, {"README.md": "hi\n", ".merge-risk.json": '{"agentMerge": true}'},
                      "base")
        git(self.repo, "update-ref", "refs/remotes/origin/main", base)
        self.head = commit(self.repo, {
            "src/app.py": "".join(f"LOGIC-BODY-MARKER {i}\n" for i in range(3)),
            "docs/guide.md": "".join(f"DOCS-BODY-MARKER {i}\n" for i in range(7)),
            "pnpm-lock.yaml": "".join(f"LOCK-BODY-MARKER {i}\n" for i in range(5)),
        }, "SECRET-COMMIT-MESSAGE")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for name, on in (("codex", codex), ("claude", claude)):
            if on:
                p = bindir / name
                p.write_text(FAKE.format(py=sys.executable, name=name, NAME=name.upper()))
                p.chmod(0o755)
        self.log = tmp_path / "log.jsonl"
        self.log.write_text("")
        self.answer = tmp_path / "answer.txt"
        self.body = tmp_path / "body.md"
        self.body.write_text(BODY)
        self.env = {**os.environ, **GIT_ENV, "PATH": f"{bindir}:/usr/bin:/bin",
                    "FAKE_LOG": str(self.log), "FAKE_ANSWER": str(self.answer)}

    def run(self, answer=AGREE, agent="claude-code", **env):
        self.answer.write_text(answer)
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "judge", "--body", str(self.body), "--base", "main",
             "--head", self.head, "--agent", agent],
            cwd=self.repo, env={**self.env, **{k: str(v) for k, v in env.items()}}, text=True,
            capture_output=True, timeout=60)
        assert p.returncode == 0, f"judge exited {p.returncode}: {p.stderr or p.stdout}"
        return json.loads(p.stdout)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()]


def test_disagree_gives_the_judge_reason(tmp_path):
    j = Judge(tmp_path)
    out = j.run('{"verdict": "disagree", "reason": "changes the public CLI flag"}')
    assert out["verdict"] == "disagree"
    assert out["reason"] == "judge: changes the public CLI flag"


def test_agree_is_labelled_agree(tmp_path):
    j = Judge(tmp_path)
    out = j.run()
    assert out["verdict"] == "agree"
    assert out["label"] == "agree"
    assert out["sameModel"] is False


def test_invalid_json_is_disagree(tmp_path):
    j = Judge(tmp_path)
    out = j.run("looks fine to me")
    assert out["verdict"] == "disagree"
    assert out["reason"].startswith("judge")
    assert j.calls(), "the judge CLI was never asked"


def test_unknown_verdict_is_disagree(tmp_path):
    j = Judge(tmp_path)
    out = j.run('{"verdict": "maybe", "reason": "unsure"}')
    assert out["verdict"] == "disagree"


def test_no_answer_in_time_is_judge_timeout(tmp_path):
    j = Judge(tmp_path)
    out = j.run(BABYSIT_JUDGE_TIMEOUT=1, FAKE_SLEEP=30)
    assert out["verdict"] == "disagree"
    assert out["reason"] == "judge timeout"


def test_judge_input_is_the_diff_the_block_and_nothing_from_the_author(tmp_path):
    j = Judge(tmp_path)
    j.run()
    calls = j.calls()
    assert len(calls) == 1
    text = json.dumps(calls[0]["argv"]) + calls[0]["stdin"]
    assert "LOGIC-BODY-MARKER" in text, "counted file diff missing"
    assert "src/app.py" in text
    assert "DOCS-BODY-MARKER" not in text and "LOCK-BODY-MARKER" not in text
    for other, n in (("docs/guide.md", "7"), ("pnpm-lock.yaml", "5")):
        lines = [x for x in text.replace("\\n", "\n").splitlines() if other in x]
        assert any(n in x for x in lines), f"no line count for {other}"
    assert "door: two-way" in text and "BLOCK-REASON-TEXT" in text
    # the body's block claims only [logic]; "deps" can come only from the computed categories
    assert "logic" in text and "docs" in text and "deps" in text
    assert "SECRET-PR-SUMMARY" not in text
    assert "SECRET-COMMIT-MESSAGE" not in text


def test_default_timeout_is_ten_minutes(tmp_path):
    """A sitecustomize on PYTHONPATH records the timeout babysit.py gives the judge CLIs."""
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(
        "import json, os, subprocess\n"
        "_run = subprocess.run\n"
        "def run(cmd, *a, **k):\n"
        "    if cmd and cmd[0] in ('codex', 'claude'):\n"
        "        open(os.environ['TIMEOUT_LOG'], 'a').write(json.dumps(k.get('timeout')) + '\\n')\n"
        "    return _run(cmd, *a, **k)\n"
        "subprocess.run = run\n")
    j = Judge(tmp_path)
    j.env.pop("BABYSIT_JUDGE_TIMEOUT", None)
    j.run(PYTHONPATH=hook, TIMEOUT_LOG=tmp_path / "timeouts.log")
    (limit,) = [float(x) for x in (tmp_path / "timeouts.log").read_text().split()]
    assert 590 < limit <= 600, "the shared ten-minute deadline minus a little setup time"


def test_from_claude_code_the_judge_is_codex_exec(tmp_path):
    j = Judge(tmp_path)
    j.run(agent="claude-code")
    calls = j.calls()
    assert [c["name"] for c in calls] == ["codex"]
    assert calls[0]["argv"][0] == "exec"


def test_from_codex_the_judge_is_claude_p(tmp_path):
    j = Judge(tmp_path)
    j.run(agent="codex")
    calls = j.calls()
    assert [c["name"] for c in calls] == ["claude"]
    assert "-p" in calls[0]["argv"] or "--print" in calls[0]["argv"]


def test_codex_missing_falls_back_to_same_model(tmp_path):
    j = Judge(tmp_path, codex=False)
    out = j.run(agent="claude-code")
    assert [c["name"] for c in j.calls()] == ["claude"]
    assert out["verdict"] == "agree"
    assert out["sameModel"] is True
    assert out["label"] == "same-model agree"


def test_same_model_disagree_is_still_disagree(tmp_path):
    j = Judge(tmp_path, codex=False)
    out = j.run('{"verdict": "disagree", "reason": "touches auth"}')
    assert out["verdict"] == "disagree"
    assert out["reason"] == "judge: touches auth"


def test_only_a_plain_reasoned_agree_is_agree(tmp_path):
    """The judge can only make the verdict stricter: nothing but an exact reasoned "agree" passes,
    and the result never carries a verdict that could stand for the gate's auto/human."""
    for answer in ('{"verdict": "auto", "reason": "ok"}', '{"verdict": "human", "reason": "ok"}',
                   '{"verdict": "approve", "reason": "ok"}', '{"verdict": "AGREE", "reason": "ok"}',
                   '{"verdict": true, "reason": "ok"}', '{"verdict": "agree"}',
                   '{"verdict": "agree", "reason": ""}', '["agree"]', 'null'):
        sub = tmp_path / f"case{len(list(tmp_path.iterdir()))}"
        sub.mkdir()
        out = Judge(sub).run(answer)
        assert out["verdict"] == "disagree", f"{answer!r} gave {out}"
        assert out["verdict"] in ("agree", "disagree") and "label" not in out


def test_agree_on_a_human_gate_block_never_becomes_auto(tmp_path):
    """The gate said human (block verdict: human); the judge agreeing must not turn that into an
    auto: the result is only agree/disagree with the known keys, never an auto or human verdict."""
    j = Judge(tmp_path)
    j.body.write_text(BODY.replace("verdict: auto", "verdict: human"))
    out = j.run(AGREE)
    assert out["verdict"] == "agree"
    assert set(out) <= {"verdict", "sameModel", "label"}, out
    assert "auto" not in json.dumps(out) and "human" not in json.dumps(out)
    c = j.calls()[0]
    assert "verdict: human" in json.dumps(c["argv"]) + c["stdin"]


def test_a_judge_that_agrees_but_exits_nonzero_falls_back(tmp_path):
    """A failed run's output is not an answer: the other CLI's agree must not count, the
    same-vendor fallback judges, and its disagreement controls the result."""
    for agent, failing, fallback in (("claude-code", "CODEX", "claude"), ("codex", "CLAUDE", "codex")):
        sub = tmp_path / agent
        sub.mkdir()
        j = Judge(sub)
        agree_file = sub / "agree.txt"
        agree_file.write_text(AGREE)
        out = j.run('{"verdict": "disagree", "reason": "touches auth"}', agent=agent,
                    **{f"FAKE_ANSWER_{failing}": agree_file, f"FAKE_EXIT_{failing}": 3})
        assert [c["name"] for c in j.calls()] == [failing.lower(), fallback], agent
        assert out["verdict"] == "disagree", f"{agent}: {out}"
        assert out["reason"] == "judge: touches auth"
        assert out["sameModel"] is True


def test_a_gate_human_stays_human_whatever_the_judge_says(tmp_path):
    """The block's advisory `verdict: auto` is ignored by the gate. With a one-way door the gate
    says human; a judge agree on that same input is only agree and carries nothing that could
    turn the gate's human into auto."""
    j = Judge(tmp_path)
    j.body.write_text(BODY.replace("door: two-way", "door: one-way"))
    gate = subprocess.run(
        [sys.executable, str(SCRIPT.parent.parent / "pr-risk" / "merge_risk.py"), "gate", "--body",
         str(j.body), "--base", "main", "--head", j.head],
        cwd=j.repo, env=j.env, text=True, capture_output=True)
    assert json.loads(gate.stdout)["verdict"] == "human", gate.stdout + gate.stderr
    out = j.run(AGREE)
    assert out["verdict"] == "agree"
    assert set(out) <= {"verdict", "sameModel", "label"}, out
    assert "auto" not in json.dumps(out) and "human" not in json.dumps(out)
