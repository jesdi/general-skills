
When this correction adds a requirement, update each shared fake or fixture that other locked tests use, so that those tests satisfy the requirement too. List each file you change in `locked`, and each one that holds no test in `fixtures` too.
Then run each whole file of `locked` and each locked test of merged tickets. In `concerns`, name each red test and why it is expected to be red, or write that all are green.
