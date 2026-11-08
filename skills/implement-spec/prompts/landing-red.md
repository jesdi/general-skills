
An earlier driver version passed review and then ran the full gate on {landing_sha}, the commit that would land on {pr_branch}, and it is red: read the output in {landing_record}. The finding from the `gate` in the findings file is that record. Make it green with a commit on {branch}; the driver runs the fast gate on your commit, a re-review, and the full gate after every ticket has merged.
