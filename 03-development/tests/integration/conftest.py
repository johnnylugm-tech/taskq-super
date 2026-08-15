"""Integration tests directory conftest.

Loads the project's FR-level test modules at conftest import time so the
integration_coverage dimension measures the same line coverage as the
unit test run. Each test_fr*.py module is imported, which registers all
its test functions in the integration session's globals() — pytest's
collection finds them via the standard mechanism without symlinks or
filesystem duplication.

Why not symlinks: pytest collects each test file via path. Symlinks create
two collection entries (one per symlink + one per real path) which the
FR↔code↔test traceability matrix then counts as duplicates and reports
as a traceability regression (Gate 2 trace 4a dropped from 100% to 80%
when we tried this earlier).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent.parent / "tests"

# Ensure the project src directory is importable for the imported test modules.
_SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))


def _load_test_module(test_file: Path) -> None:
    """Import *test_file* into the integration session's namespace."""
    spec = importlib.util.spec_from_file_location(
        f"_integration_{test_file.stem}", test_file
    )
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Register the module so pytest discovers its test_* functions.
    sys.modules[spec.name] = module


# Import every FR test module at conftest-load time. Pytest's discovery
# walks the integration/ directory, picks up this conftest, and inherits
# the test_* functions the imported modules registered.
for _test_file in sorted(_TESTS_DIR.glob("test_fr*.py")):
    _load_test_module(_test_file)


# Ensure async tests get the asyncio marker; pytest-asyncio mode = auto is
# set in the root pyproject.toml, but defensive marking keeps individual
# async tests working under any pytest-asyncio config.
def pytest_collection_modifyitems(config, items):
    import inspect
    import pytest as _pytest
    for item in items:
        if "asyncio" in item.keywords:
            continue
        obj = getattr(item, "obj", None)
        if obj is not None and inspect.iscoroutinefunction(obj):
            item.add_marker(_pytest.mark.asyncio)
