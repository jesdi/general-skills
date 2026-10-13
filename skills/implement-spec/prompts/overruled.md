
This call is a correction after a ruling, not a new test set. The implementer reported that a locked test contradicts the spec or the ticket, and the operator ruled that the test is wrong: read `concerns` in {blocked_report}.
Correct only the tests that those concerns name, so that they state what the spec and the ticket require. Keep each other test as it is.
The branch can hold commits of the implementer, and the tree can hold its uncommitted work: change neither, and commit only the test files you correct.
A corrected test fails because the behavior is missing: run it and print that failing output. In `locked`, list each test file you corrected.
