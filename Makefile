# taskq-super: Phase 3 verify-system target
# NFR-12 (verifiability): end-to-end "system works" check.
# Runs the full test suite + a CLI smoke path; exit 0 means PASS.
PYTHON ?= /Users/johnny/projects/taskq-super/.venv/bin/python

.PHONY: verify-system
verify-system:
	@cd /Users/johnny/projects/taskq-super && \
		PYTHONPATH=03-development/src $(PYTHON) -m pytest 03-development/tests -q --tb=no --no-header \
		&& PYTHONPATH=03-development/src $(PYTHON) -m taskq_api --help >/dev/null
