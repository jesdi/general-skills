
The driver ran the gate on that commit, {start_sha}, and the gate is red: read the output in {record}. The gate was green before the restore, so a restored file fails with the code of this ticket.
Update each restored file that fails so that it works with the new interface: keep what each test asserts, and list the file in `locked`. When no such update can keep what a test asserts, report BLOCKED with a concern that starts with `locked test contradicts spec:`, then the test and the evidence.
