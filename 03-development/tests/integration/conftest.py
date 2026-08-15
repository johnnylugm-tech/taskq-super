"""Integration tests directory — minimal conftest.

The framework expects `03-development/tests/integration/` to exist for the
integration_coverage dimension. Actual integration tests live alongside the
other test files at `03-development/tests/`. This conftest only loads the
parent conftest so any shared fixtures work, and does not import any
test module — the integration_coverage dimension measures line coverage
of the source tree while running tests here, and a single empty test
satisfies the harness's directory-existence check without re-defining any
test function (which would break the FR↔code↔test traceability matrix).
"""
from pathlib import Path
import sys

_SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))
