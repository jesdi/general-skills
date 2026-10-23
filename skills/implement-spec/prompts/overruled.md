
This call is a correction after a ruling, not a new test set. The implementer or the fixer reported that a locked test contradicts the spec or the ticket, and the operator ruled that the test is wrong: read `concerns` in {blocked_report}.
Correct only the tests that those concerns name, so that they state what the spec and the ticket require. Keep each other test as it is.
The branch can hold commits of the implementer, and the tree can hold its uncommitted work: change neither, and commit only the test files you correct.
A corrected test fails because the behavior is missing: run it, print that failing output, and give its failure line in `red`. A corrected test can be green, because the implementer's code is there: then break the production line that the test protects, run the test, print its failing output, give that line in `red`, and revert the edit. In `locked`, list each test file you corrected.
When a concern needs no test change, leave the test as it is and add the concern to `not_addressed`: `finding` is its text, copied exactly from this list, and `reason` is why the test stands:
{overruled}
