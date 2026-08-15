"""Bug-hunt repro tests — Gate 3 adversarial_review (Round 2).

These tests drive NEW confirmed findings from the 2026-08-16 hunt against
.git HEAD f7c365f4 (post-fix1 / post-resolve5 state). The previous hunt
fixed the FR-02 path (`runner._execute_command` at the old line 189) but
left the FR-08 path (`Runner._run_subprocess` at line 555) with the same
bug pattern: `shlex.split()` is inside a try block that only catches
`FileNotFoundError`, so a `ValueError` from unbalanced quotes propagates
out of `_run_subprocess`, leaving the in-memory row stuck in
`status="running"` forever.

findings covered:
- runner#5 (MEDIUM, NEW): Runner.submit() with unbalanced-quote command
  leaves the row stuck in "running" — same root cause as runner#1 but
  reachable via the Runner.submit() API surface (FR-08 path) which the
  previous fix did not cover.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Path setup so the module under test is importable.
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))


# ---------------------------------------------------------------------------
# runner#5 — Runner.submit() with unbalanced quotes leaves row stuck "running"
# ---------------------------------------------------------------------------


async def test_runner_submit_unbalanced_quote_does_not_leak_running_row() -> None:
    """runner.py:554-568 — `_run_subprocess` calls `shlex.split(command)`
    inside a try block that only catches `FileNotFoundError`. A
    `ValueError` from unbalanced quotes propagates out of
    `_run_subprocess` BEFORE the row's `status` / `finished_at` /
    `duration_ms` are updated, so the in-memory row stays in
    `status="running"` indefinitely. The previous hunt fixed the
    equivalent FR-02 path (`_execute_command` at the old line 189) but
    missed this FR-08 path.

    The fix mirrors the FR-02 pattern: catch `ValueError` alongside
    `FileNotFoundError` and mark the row as `status="failed",
    exit_code=-1` so `runner.list_runs(...)` reports a terminal status
    instead of a perpetually-running row.
    """
    from taskq_api.service.runner import Runner

    runner = Runner()
    task_id = "repro-bug-runner-submit-shlex"
    # Canonical unbalanced-quote input that triggers `ValueError: No
    # closing quotation` inside `shlex.split`.
    command = 'echo "unbalanced'

    await runner.submit(task_id, command, timeout=2.0)
    await runner.drain(timeout=2.0)

    rows = runner.list_runs(task_id)
    assert len(rows) == 1, (
        f"expected exactly one row for {task_id}, got {len(rows)}: {rows!r}"
    )
    (row,) = rows
    # The row MUST reach a terminal status. Before the fix, this stays
    # "running" forever because `_run_subprocess` raises before reaching
    # the row-update code path at runner.py:589-590.
    assert row["status"] != "running", (
        f"unbalanced-quote command must not leak a 'running' row; "
        f"row stays in status={row['status']!r} — shlex.split "
        f"ValueError propagated past the row-update block at "
        f"runner.py:589-590. Fix: extend the except clause on "
        f"runner.py:561 to also catch ValueError and mark the row "
        f"status='failed', exit_code=-1."
    )
    # The failed row must carry a finished_at timestamp and a non-zero
    # duration — i.e. it actually went through the row-finalisation
    # code path (runner.py:589-590).
    assert row.get("finished_at") is not None, (
        f"row must record finished_at once the run has settled; "
        f"row={row!r}"
    )
    assert row.get("duration_ms", -1) >= 0, (
        f"row must record a non-negative duration; row={row!r}"
    )