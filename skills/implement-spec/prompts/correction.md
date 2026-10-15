
This call is a correction of the locked tests, not a new test set. Read in {findings} each finding of kind `fixture` and each critical or important finding of kind `test` on a locked file.
A `fixture` finding: a locked test encodes a stale path, format, fixture layout or setup detail. Correct only that detail and keep what each test asserts.
A `test` finding: a locked test is too weak, for example it stays green when the production line it protects is broken. Make the test assert the behavior of its criterion so that it fails on that break. Do not remove an assertion.
For this you may read the implementation.
The implementation exists, so a corrected test is green: for `red`, break the production line that the test protects, run the test, print its failing output, then revert the edit. In `locked`, list each test file you corrected: test file paths only, not test node ids. Unlike `mutations[].test` of the reviewer, `locked` takes no `::<test name>`.
