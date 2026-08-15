"""Make tests/integration a regular package so mypy treats its conftest.py
as `tests.integration.conftest` rather than a duplicate top-level `conftest`
module.

Pytest discovery still works because pytest treats regular packages and
namespace packages identically for collection purposes.
"""
