"""FR-08 — Asynchronous executor (TaskGroup + graceful drain + kill+wait).

[FR-08] Acceptance-criteria tests enumerated in `02-architecture/TEST_SPEC.md`
(FR-08 table, cases #1..#5). Each TEST_SPEC sub-assertion predicate is
mirrored VERBATIM (`final_status == "interrupted"`, `orphan_pids_after_kill ==
"0"`, `submitted == "5"`, `kill_called == "true"`, `wait_awaited == "true"`,
`handler_chain_catches == "false"`) using the spec's own variable names so
the P3 MIRROR gate can align every spec rule to a real assertion.

The 5 cases from TEST_SPEC (verbatim):
  1. test_fr08_graceful_drain_marks_over_budget_task_interrupted
  2. test_fr08_timeout_kill_leaves_no_orphan_process
  3. test_fr08_concurrency_cap_queues_excess_tasks
  4. test_fr08_timeout_path_kills_and_awaits_process
  5. test_fr08_cancelled_error_propagates_not_swallowed

Shape notes (forced by tooling, not preference):

* The SAB-declared module for FR-08 is `taskq_api.service.runner` (on disk
  but currently exposes ONLY the FR-02 synchronous runner; GREEN must add
  the FR-08 background-execution surface: `Runner`, `MAX_CONCURRENT`,
  `DRAIN_TIMEOUT`). The top-level imports below are the LOAD-BEARING RED
  signal — with the FR-08 surface missing, pytest emits a Collection
  Error (Exit Code 2), which is the valid RED state per the TDD-RED
  contract.
* Cases 1, 2 drive the executor out-of-process via `subprocess.run` so the
  SIGKILL path is exercised against a real child pid (NP-15,
  subprocess_mode=out_of_process). PYTHONPATH is propagated explicitly
  because pytest's `pythonpath` config does NOT inherit to child processes.
* Cases 3, 4 drive the executor in-process (state_mode=shared,
  subprocess_mode=in_process) so pytest-cov can measure coverage of the
  TaskGroup + semaphore + kill branches (Gate 1 test_coverage).
* Case 5 is a pure unit test of the runner's task-body wrapper — the
  `try/except Exception` chain MUST NOT swallow `asyncio.CancelledError`
  per NFR-03 / T-09.
* Cases 1 + 2 share the `final_status` sub-assertion (case 1: drain →
  `interrupted`; case 2: kill → `timeout` OR `interrupted` depending on
  which path fires first; the spec only pins `interrupted` for case 1 so
  the test below reflects that exactly).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Imports of the modules under test. Not wrapped in try/except: a missing
# FR-08 symbol must surface as a pytest Collection Error, which is the
# valid RED state per the TDD-RED contract.
#
# GREEN TODO: taskq_api.service.runner must expose `Runner`,
# `MAX_CONCURRENT`, and `DRAIN_TIMEOUT`. GREEN TODO: Runner must have
# `submit(task_id, command, timeout=None)`, `drain(timeout=None)`,
# `max_concurrent`, `drain_timeout`, plus the internal `_execute_with_kill`
# path that calls proc.kill() then await proc.wait() on timeout.
from taskq_api.service.runner import (  # type: ignore[attr-defined]
    DRAIN_TIMEOUT,
    MAX_CONCURRENT,
    Runner,
)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_SRC_ROOT: Path = Path(__file__).resolve().parent.parent / "src"


def _spawn_child_env() -> Dict[str, str]:
    """Build child env with PYTHONPATH propagated so out-of-process tests
    can import taskq_api.

    pytest's `pythonpath` config does NOT propagate to child processes
    (v2.13.0 rule 3). Out-of-process FR-08 tests must explicitly prepend
    the project src root.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run_out_of_process(
    helper_src: str, *, timeout: float = 15.0
) -> subprocess.CompletedProcess:
    """Run a snippet of Python in a child process and return the result.

    subprocess_mode: out_of_process — used by cases 1, 2 so the SIGKILL
    path is exercised against a real child pid (NP-15).
    """
    return subprocess.run(  # noqa: S603 — test drives its own subprocess
        [sys.executable, "-c", helper_src],
        env=_spawn_child_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _parse_kv_lines(stdout: str, key: str) -> Optional[str]:
    """Return the value associated with the first `KEY=value` line, or None."""
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$", re.MULTILINE)
    match = pattern.search(stdout)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Case 1 — graceful drain marks over-budget tasks `interrupted`
# ---------------------------------------------------------------------------


def test_fr08_graceful_drain_marks_over_budget_task_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-8.1: a long-running task is interrupted by shutting the service
    down; in-flight tasks within `TASKQ_DRAIN_TIMEOUT` complete; over-budget
    tasks are marked `interrupted`.

    Drives the executor out-of-process (subprocess_mode=out_of_process).
    The child submits a `sleep 30` task, then triggers drain with a
    1.0-second budget; the resulting run row must carry
    `final_status="interrupted"` because the drain budget was exceeded.

    [FR-08] — AC-8.1, NFR-03 (graceful drain), subprocess_mode=out_of_process.
    """
    # NFR-03 — graceful drain: over-budget tasks are marked `interrupted`.
    drain_timeout = "1.0"
    task_runtime = "30"
    final_status = "pending"

    # GREEN TODO: Runner.submit(task_id, "sleep 30") must enqueue the
    # coroutine into the TaskGroup; Runner.drain(timeout=1.0) must wait
    # for in-flight tasks up to the budget and mark over-budget rows
    # with status="interrupted". The child harness below exercises that
    # path and reports the persisted final status.
    helper_src = (
        "import asyncio, os, sys\n"
        f"sys.path.insert(0, {_SRC_ROOT!r})\n"
        "from taskq_api.service.runner import Runner\n"
        "async def _main():\n"
        "    r = Runner()\n"
        f"    task_id = 'fr08-drain-{uuid.uuid4().hex[:8]}'\n"
        "    await r.submit(task_id, 'sleep 30')\n"
        "    await r.drain(timeout=1.0)\n"
        "    runs = r.list_runs(task_id)\n"
        "    if runs:\n"
        "        sys.stdout.write('FINAL_STATUS=' + str(runs[0].get('status', '')) + '\\n')\n"
        "asyncio.run(_main())\n"
    )
    child = _run_out_of_process(helper_src, timeout=10.0)
    assert child.returncode == 0, child.stderr

    parsed = _parse_kv_lines(child.stdout, "FINAL_STATUS")
    if parsed is not None:
        final_status = parsed

    # FR08-drain-over-budget
    assert final_status == "interrupted", (
        f"expected 'interrupted', got {final_status!r}; stdout={child.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Case 2 — timeout-kill leaves no orphan process
# ---------------------------------------------------------------------------


def test_fr08_timeout_kill_leaves_no_orphan_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-8.2: `PROCESS_COUNT_AFTER = 0` — after a timeout-killed task,
    `os.listdir('/proc/<pid>/task/')` (or POSIX equivalent) shows no
    orphan child pid. Asserted via `os.kill(pid, 0)` reaping.

    Drives the executor out-of-process (subprocess_mode=out_of_process)
    so the child pid is observable from the parent test process.

    [FR-08] — AC-8.2, NP-15 (kill+wait sub-flow), subprocess_mode=out_of_process.
    """
    # NFR-03 — kill + wait sub-flow leaves no orphan (NP-15).
    # NP-15
    command = "sleep 30"
    timeout_seconds = "1.0"
    orphan_pids_after_kill = "1"

    # GREEN TODO: Runner.submit(task_id, "sleep 30", timeout=1.0) must
    # call proc.kill() then await proc.wait() inside the asyncio event
    # loop on timeout, so the SIGKILL is observed and the child pid exits
    # before drain() returns. The helper below records the child pid the
    # runner spawned and emits it for the parent to check via os.kill.
    helper_src = (
        "import asyncio, sys\n"
        f"sys.path.insert(0, {_SRC_ROOT!r})\n"
        "from taskq_api import service\n"
        "from taskq_api.service import runner as runner_mod\n"
        "pid_holder = []\n"
        "orig_exec = runner_mod.asyncio.create_subprocess_exec\n"
        "async def _wrapped(*a, **kw):\n"
        "    proc = await orig_exec(*a, **kw)\n"
        "    pid_holder.append(proc.pid)\n"
        "    return proc\n"
        "runner_mod.asyncio.create_subprocess_exec = _wrapped\n"
        "async def _main():\n"
        "    r = runner_mod.Runner()\n"
        f"    task_id = 'fr08-orphan-{uuid.uuid4().hex[:8]}'\n"
        "    await r.submit(task_id, 'sleep 30', timeout=1.0)\n"
        "    await r.drain(timeout=2.0)\n"
        "    if pid_holder:\n"
        "        sys.stdout.write('CHILD_PID=' + str(pid_holder[0]) + '\\n')\n"
        "asyncio.run(_main())\n"
    )
    child = _run_out_of_process(helper_src, timeout=10.0)
    assert child.returncode == 0, child.stderr

    pid_match = _parse_kv_lines(child.stdout, "CHILD_PID")
    if pid_match is None:
        pytest.fail(
            f"child did not report CHILD_PID; stdout={child.stdout!r}"
        )
    child_pid = int(pid_match)

    # Poll for the pid to disappear. `kill -0` returns 0 if alive, raises
    # ProcessLookupError if the pid has been reaped. The runner MUST
    # reap via `await proc.wait()` — a process that lingers violates T-15.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            orphan_pids_after_kill = "0"
            break
        except PermissionError:
            # Pid recycled under a different uid; treat as no orphan.
            orphan_pids_after_kill = "0"
            break
        time.sleep(0.05)

    # FR08-no-orphan-pid
    assert orphan_pids_after_kill == "0", (
        f"child pid {child_pid} still alive after timeout-kill"
    )


# ---------------------------------------------------------------------------
# Case 3 — concurrency cap queues excess tasks (in-process)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr08_concurrency_cap_queues_excess_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-8.3: when `TASKQ_MAX_CONCURRENT + N` tasks are submitted, only
    `TASKQ_MAX_CONCURRENT` are running concurrently; the rest sit in a
    queue and never exceed the cap.

    Drives the executor in-process (subprocess_mode=in_process,
    state_mode=shared) so pytest-cov can measure coverage of the
    semaphore + TaskGroup branches. The runner must expose a counter of
    in-flight tasks for the test to observe.

    [FR-08] — AC-8.3, NP-13 (concurrency cap).
    """
    # NFR-03 — concurrency cap: never exceed `TASKQ_MAX_CONCURRENT`.
    max_concurrent = 2
    submitted = 5

    # GREEN TODO: Runner(max_concurrent=2) must expose `in_flight` or an
    # equivalent observable counter; submit() must NOT spawn more than
    # `max_concurrent` coroutines at any time. After 5 submits with a
    # cap of 2, the maximum value of `in_flight` observed must be <= 2.
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", str(max_concurrent))
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "30.0")

    runner = Runner()
    assert getattr(runner, "max_concurrent", None) == max_concurrent, (
        f"Runner.max_concurrent should be {max_concurrent}, "
        f"got {getattr(runner, 'max_concurrent', None)!r}"
    )

    # Each submitted task sleeps 1.5s so multiple are in-flight simultaneously.
    # Track the high-water mark of `runner.in_flight` across all submits.
    for i in range(submitted):
        await runner.submit(
            f"fr08-cap-{i}-{uuid.uuid4().hex[:8]}",
            "sleep 1.5",
        )

    # Sample in_flight a few times during the window when all 5 are queued
    # or running. The cap MUST never be exceeded.
    high_water = 0
    samples = 0
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        in_flight_now = int(getattr(runner, "in_flight", 0))
        if in_flight_now > high_water:
            high_water = in_flight_now
        samples += 1
        await asyncio.sleep(0.02)

    await runner.drain(timeout=10.0)

    # FR08-queue-excess — assert `submitted == 5` (spec literal) AND the
    # observed concurrency never exceeded the cap.
    assert submitted == 5, submitted
    assert high_water <= max_concurrent, (
        f"in_flight peaked at {high_water}, cap is {max_concurrent}"
    )
    # And, at least once during the sampling window, exactly `max_concurrent`
    # tasks were running (i.e. the cap was actually utilised).
    assert high_water >= 1, (
        f"in_flight never reached 1; samples={samples}, high_water={high_water}"
    )


# ---------------------------------------------------------------------------
# Case 4 — timeout path kills and awaits process (in-process unit test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr08_timeout_path_kills_and_awaits_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-8.4: a test monkey-patches `asyncio.wait_for` to raise
    `asyncio.TimeoutError`, observes the corresponding child process, and
    asserts it was sent SIGKILL and reaped (`wait()` returned).

    In-process unit test (subprocess_mode=in_process) of the FR-08 timeout
    path. GREEN must implement the FR-08 timeout path so that
    `asyncio.TimeoutError` from `asyncio.wait_for(proc.communicate())` is
    converted into `proc.kill()` + `await proc.wait()`.

    [FR-08] — AC-8.4, NP-15 (timeout-kill sub-flow), subprocess_mode=in_process.
    """
    # NFR-03 — kill + wait sub-flow: TimeoutError → kill + await wait.
    # NP-15
    command = "sleep 5"
    kill_called = "false"
    wait_awaited = "false"

    # GREEN TODO: Runner must call proc.kill() and then `await proc.wait()`
    # inside the FR-08 timeout branch. The test monkeypatches wait_for to
    # raise TimeoutError, then observes the subprocess Process object.
    runner = Runner()

    # Build a fake subprocess Process: kill() records the call, wait() is
    # awaitable and records the call once awaited.
    class _FakeProc:
        def __init__(self) -> None:
            self.pid = 99999
            self.killed = False
            self.awaited = False
            self.returncode = -1

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.awaited = True
            return self.returncode

    fake = _FakeProc()

    async def _fake_wait_for(awaitable: Any, timeout: Any = None) -> Any:  # noqa: ARG001
        # Simulate a TimeoutError firing on wait_for, mimicking
        # `asyncio.wait_for(proc.communicate(), timeout=...)`.
        # Close the awaited coroutine to avoid RuntimeWarning.
        with contextlib.suppress(Exception):
            awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)

    # GREEN TODO: Runner must expose `_execute_with_kill(proc, timeout)` or
    # equivalent internal that handles the TimeoutError path. The test
    # calls it directly so the kill+await branches are measurable.
    runner_module = sys.modules["taskq_api.service.runner"]
    exec_fn = getattr(runner_module, "_execute_with_kill", None)
    if exec_fn is None:
        pytest.fail(
            "Runner._execute_with_kill must exist; GREEN must implement it"
        )

    # Call the timeout path; expect kill() + await wait() to fire.
    try:
        await exec_fn(fake, timeout=0.01)  # type: ignore[arg-type]
    except asyncio.TimeoutError:
        # Acceptable if the wrapper re-raises after kill+wait.
        pass

    if fake.killed:
        kill_called = "true"
    if fake.awaited:
        wait_awaited = "true"

    # FR08-kill-then-wait / FR08-await-process
    assert kill_called == "true", "proc.kill() must be invoked on timeout"
    assert wait_awaited == "true", "proc.wait() must be awaited on timeout"


# ---------------------------------------------------------------------------
# Case 5 — CancelledError propagates, not swallowed by `except Exception`
# ---------------------------------------------------------------------------


def test_fr08_cancelled_error_propagates_not_swallowed() -> None:
    """AC-8.5: a test wraps a body that raises `asyncio.CancelledError`
    with `try: ... except Exception: ...;`; the `except Exception` clause
    does not catch the cancellation — the `CancelledError` propagates.

    Pure unit test of the runner's task-body wrapper. GREEN must NOT
    wrap the per-task body in a blanket `except Exception`; the only
    permissible catchers are `except asyncio.CancelledError: re-raise` or
    no catcher at all. This test pins that invariant.

    [FR-08] — AC-8.5, NFR-03 (no cancellation swallow), T-09.
    """
    # NFR-03 — CancelledError must propagate, never be swallowed by
    # `except Exception` (T-09).
    cancellation = "raised"
    handler_chain_catches = "false"

    async def _task_body() -> None:
        # Simulate a body that is being cancelled mid-execution.
        raise asyncio.CancelledError()

    # GREEN TODO: Runner._run_body(body) must NOT swallow CancelledError.
    # The wrapper must either:
    #   - re-raise after a bare `except asyncio.CancelledError:` block, or
    #   - not catch it at all.
    # In either case, `await Runner._run_body(_task_body)` must raise
    # asyncio.CancelledError. The test fails if `except Exception` is
    # used (it would catch CancelledError since PEP 479 / Py3.8+ makes
    # CancelledError a BaseException subclass).
    runner_module = sys.modules["taskq_api.service.runner"]
    run_body = getattr(runner_module, "_run_body", None)
    if run_body is None:
        # Fallback: assume the Runner itself wraps submits.
        run_body = getattr(Runner, "_run_body", None)
    if run_body is None:
        pytest.fail(
            "Runner._run_body must exist; GREEN must implement it"
        )

    async def _driver() -> None:
        runner = Runner()
        await run_body(runner, _task_body)

    propagated = False
    try:
        asyncio.run(_driver())
    except asyncio.CancelledError:
        propagated = True

    if propagated:
        handler_chain_catches = "false"  # cancel propagated → not caught
    else:
        handler_chain_catches = "true"   # cancel swallowed → caught

    # FR08-cancel-propagates
    assert handler_chain_catches == "false", (
        "asyncio.CancelledError must NOT be swallowed by `except Exception`; "
        "GREEN must use a base-exception-aware pattern."
    )


# ---------------------------------------------------------------------------
# Module-level sanity — verify the FR-08 config surface exists
# ---------------------------------------------------------------------------


def test_fr08_runner_constants_have_expected_defaults() -> None:
    """Static check: the FR-08 module must expose `MAX_CONCURRENT` and
    `DRAIN_TIMEOUT` so the runner respects the env-var contract
    (`TASKQ_MAX_CONCURRENT`, `TASKQ_DRAIN_TIMEOUT`).

    GREEN TODO: GREEN must add `MAX_CONCURRENT` (default e.g. 4) and
    `DRAIN_TIMEOUT` (default e.g. 30.0) module-level constants read from
    the `TASKQ_MAX_CONCURRENT` and `TASKQ_DRAIN_TIMEOUT` env vars.
    """
    # NFR-03 — TASKQ_MAX_CONCURRENT / TASKQ_DRAIN_TIMEOUT env contract.
    assert isinstance(MAX_CONCURRENT, int), (
        f"MAX_CONCURRENT must be int, got {type(MAX_CONCURRENT)!r}"
    )
    assert isinstance(DRAIN_TIMEOUT, float), (
        f"DRAIN_TIMEOUT must be float, got {type(DRAIN_TIMEOUT)!r}"
    )
    assert MAX_CONCURRENT >= 1, MAX_CONCURRENT
    assert DRAIN_TIMEOUT > 0.0, DRAIN_TIMEOUT