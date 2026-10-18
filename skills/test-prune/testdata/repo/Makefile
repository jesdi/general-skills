# Fixture target repo: every target calls the fake engine (see fake_engine.py).
ENGINE = @PYTHON@ $(CURDIR)/fake_engine.py --logs @LOGS@ --root $(CURDIR)

.PHONY: mutation mutation-check mutation-baseline test-candidates prune-gate test crap

mutation:
	$(ENGINE) mutation

mutation-check:
	$(ENGINE) mutation-check

mutation-baseline:
	$(ENGINE) mutation-baseline

test-candidates:
	$(ENGINE) test-candidates $(ARGS)

prune-gate:
	$(ENGINE) prune-gate $(or $(BASE),origin/main) $(or $(HEAD),HEAD)

test:
	$(ENGINE) test

crap:
	$(ENGINE) crap
