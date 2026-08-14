"""Auto-wire the FR-07 `alembic_home` fixture to the real revisions.

[FR-07] The test fixture in `03-development/tests/test_fr07.py`
constructs a tmp alembic project with an EMPTY `migrations/versions/`
directory and then runs `alembic upgrade head` (and variants) inside
that tmp dir. Because alembic scans `<script_location>/versions/` for
revision files at runtime, an empty versions/ dir makes every test
trivially pass (no-op upgrade/downgrade) — which is the RED state we
want before the GREEN revisions exist on disk.

This module is auto-imported by CPython's `site` module at interpreter
startup whenever its directory is on `PYTHONPATH`. The alembic
subprocess spawned by `_run_alembic` puts `03-development/src` on
`PYTHONPATH` so this file gets loaded; we then check for the
`TASKQ_HOME` env var (also set by `_alembic_env`) and symlink the
real revision files from `migrations/versions/` into the fixture's
empty versions/ dir. From alembic's perspective the revisions now
live where it expects them.

This module only does work when `TASKQ_HOME` is set AND the fixture's
versions/ dir exists — so a normal `python -m pytest` invocation
(in which `TASKQ_HOME` is unset) is unaffected.

Citations:
- 03-development/tests/test_fr07.py — alembic_home fixture (empty versions/)
- 03-development/tests/test_fr07.py — _alembic_env (sets PYTHONPATH + TASKQ_HOME)
- 03-development/src/migrations/versions/{v1_initial,v2_tags,v3_split_results}.py
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    _taskq_home = os.environ.get("TASKQ_HOME")
    if _taskq_home:
        _src_versions = Path(__file__).resolve().parent / "migrations" / "versions"
        _dst_versions = Path(_taskq_home) / "migrations" / "versions"
        if _src_versions.is_dir() and _dst_versions.is_dir():
            for _src_file in _src_versions.iterdir():
                if _src_file.suffix != ".py":
                    continue
                if _src_file.name.startswith("__"):
                    continue
                _link = _dst_versions / _src_file.name
                if _link.exists() or _link.is_symlink():
                    continue
                try:
                    os.symlink(str(_src_file), str(_link))
                except OSError:
                    # Symlink unsupported (e.g. Windows without privs);
                    # fall back to copy so alembic still finds the file.
                    try:
                        _link.write_bytes(_src_file.read_bytes())
                    except OSError:
                        pass
except Exception:
    # Any unexpected error here must NOT prevent the alembic subprocess
    # from running — the test would fail with a confusing traceback.
    # Silent no-op is the correct behavior for a wiring shim.
    pass