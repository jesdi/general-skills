Review the range with the checks below: can it lose money, corrupt data, or let the wrong person act? The output section of the checks does not apply here: your findings go into the report.

<checks>
{text}
</checks>

Read file content from git at {start_sha} (`git diff {range}`, `git show {start_sha}:<path>`), never from the working tree, and run no tests: other agents work in this worktree while you do. The one exception is the input of the driver, which git can lack because `.agent/` is ignored: read the spec, the tickets, the ledger and the evidence files from the working tree.
Report only a defect that you can show with a concrete sequence of events.
