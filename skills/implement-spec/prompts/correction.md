
This call is a fixture correction, not a new test. The review found that a locked test encodes a stale path, format, fixture layout or setup detail: read each finding of kind `fixture` in {findings}.
Correct only that detail in the locked test file and keep what each test asserts. For this you may read the implementation.
The implementation exists, so a corrected test is green: for `red`, break the production line that the test protects, run the test, print its failing output, then revert the edit. In `locked`, list each test file you corrected.
