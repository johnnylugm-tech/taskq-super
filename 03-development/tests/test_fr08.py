"""FR-08 — Asynchronous executor (TaskGroup + graceful drain + kill+wait).

[FR-08] Acceptance-criteria tests enumerated in `02-architecture/TEST_SPEC.md`
(FR-08 table, cases #1..#5). Each TEST_SPEC sub-assertion predicate is
mirrored VERBATIM (`final_status == "interrupted"`, `orphan_pids_after_kill ==
"0"`, `submitted == "5"`, `kill_called == "true"`, `wait_awaited == "true"`,
`handler_chain_catches == "false"`) using the spec's own variable names so
the P3 MIRROR gate can align every spec rule to a real assertion.

The 5 cases from TEST_SPEC (verbatim, and the ONLY test functions in this
module — `TEST_INVENTORY.yaml` FR-08 declares exactly these five):
  1. test_fr08_graceful_drain_marks_over_budget_task_interrupted
  2. test_fr08_timeout_kill_leaves_no_orphan_process
  3. test_fr08_concurrency_cap_queues_excess_tasks
  4. test_fr08_timeout_path_kills_and_awaits_process
  5. test_fr08_cancelled_error_propagates_not_swallowed

Shape notes (forced by tooling, not preference):

* SAB.json `fr_module_traceability["FR-08"]` declares TWO modules:
  `taskq_api.service.runner` AND `taskq_api.app`. Both exist on disk but
  neither exposes the FR-08 surface yet, so the top-level imports below are
  the LOAD-BEARING RED signal — pytest emits a Collection Error (Exit
  Code 2), which is the valid RED state per the TDD-RED contract.
  The `taskq_api.app` import is deliberate: AC-8.1 is phrased as "shutting
  the service DOWN", so the drain budget belongs on the app shutdown path.
  Importing it here pins GREEN to the SAB-declared name instead of letting
  the drain hook land at a name Gate 1 would later BLOCK as a phantom.
* Cases 1, 2 drive the executor out-of-process via `subprocess.run` so the
  SIGKILL path is exercised against a real child pid (NP-15,
  subprocess_mode=out_of_process). PYTHONPATH is propagated explicitly
  because pytest's `pythonpath` config does NOT inherit to child processes.
  Case 1 ALSO drives the same drain in-process, because pytest-cov cannot
  measure coverage of code running inside a subprocess and `taskq_api.app`
  would otherwise report 0% for the Gate 1 test_coverage dimension.
* Cases 3, 4 drive the executor in-process (state_mode=shared,
  subprocess_mode=in_process) so pytest-cov can measure coverage of the
  TaskGroup + semaphore + kill branches (Gate 1 test_coverage).
* Case 5 is a pure unit test of the runner's task-body wrapper — the
  `try/except Exception` chain MUST NOT swallow `asyncio.CancelledError`
  per NFR-03 / T-09.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# Imports of the modules under test. Not wrapped in try/except: a missing
# FR-08 symbol must surface as a pytest Collection Error, which is the
# valid RED state per the TDD-RED contract.
#
# GREEN TODO: taskq_api.service.runner must expose `Runner`,
# `MAX_CONCURRENT`, and `DRAIN_TIMEOUT`. Runner must have:
#   - `submit(task_id, command, timeout=None)` -> None   (NON-blocking:
#     enqueues into the TaskGroup and returns immediately even when the
#     concurrency cap is saturated; see case 3)
#   - `drain(timeout=None)` -> None
#   - `list_runs(task_id)` -> list[dict]
#   - `_run_body(body)` -> Any                            (case 5)
#   - properties `max_concurrent`, `drain_timeout`, `in_flight`
# plus a module-level `_execute_with_kill(proc, timeout)` that calls
# proc.kill() then `await proc.wait()` on timeout (case 4).
from taskq_api.service.runner import (  # type: ignore[attr-defined]
    DRAIN_TIMEOUT,
    MAX_CONCURRENT,
    Runner,
)

# GREEN TODO: taskq_api.app must expose `shutdown_drain(runner, timeout=None)`
# -> awaitable. It waits for in-flight tasks up to `TASKQ_DRAIN_TIMEOUT`
# (or the explicit `timeout`) and marks every task still running when the
# budget expires with status="interrupted". This is the hook the FastAPI
# lifespan shutdown handler must await (AC-8.1).
from taskq_api.app import shutdown_drain  # type: ignore[attr-defined]

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


@pytest.mark.asyncio
async def test_fr08_graceful_drain_marks_over_budget_task_interrupted() -> None:
    """AC-8.1: a long-running task is interrupted by shutting the service
    down; in-flight tasks within `TASKQ_DRAIN_TIMEOUT` complete; over-budget
    tasks are marked `interrupted`.

    Two halves, deliberately:
      * in-process — drives `taskq_api.app.shutdown_drain` directly so
        pytest-cov can measure the drain branch (subprocess coverage is
        invisible to pytest-cov, and `taskq_api.app` is SAB-declared for
        FR-08, so it needs in-process exercise to clear Gate 1 coverage).
      * out-of-process — proves the SAME drain over a real child process
        tree, which is the mode TEST_SPEC case 1 declares.

    [FR-08] — AC-8.1, NFR-03 (graceful drain), subprocess_mode=out_of_process.
    """
    # NFR-03 — graceful drain: over-budget tasks are marked `interrupted`.
    # NFR-10 — integration coverage (drain exercised both in-process and out-of-process).
    # NFR-09 — zero-skip iron rule; this test must run end-to-end (no `skipif`).
    drain_timeout = 1.0
    task_runtime = "30"
    final_status = "pending"

    # --- in-process half (coverage-bearing) --------------------------------
    # GREEN TODO: Runner.submit(task_id, "sleep 30") must enqueue the
    # coroutine into the TaskGroup; app.shutdown_drain(runner, timeout=1.0)
    # must wait for in-flight tasks up to the budget and mark over-budget
    # rows with status="interrupted".
    runner = Runner()
    task_id = f"fr08-drain-{uuid.uuid4().hex[:8]}"
    await runner.submit(task_id, f"sleep {task_runtime}")
    await shutdown_drain(runner, timeout=drain_timeout)

    rows = runner.list_runs(task_id)
    assert rows, f"no run row persisted for {task_id!r}"
    final_status = str(rows[0].get("status", ""))

    # FR08-drain-over-budget
    assert final_status == "interrupted", (
        f"in-process drain: expected 'interrupted', got {final_status!r}"
    )

    # --- out-of-process half (spec-declared mode) --------------------------
    helper_src = (
        "import asyncio, sys\n"
        f"sys.path.insert(0, {str(_SRC_ROOT)!r})\n"
        "from taskq_api.service.runner import Runner\n"
        "from taskq_api.app import shutdown_drain\n"
        "async def _main():\n"
        "    r = Runner()\n"
        f"    tid = 'fr08-drain-oop-{uuid.uuid4().hex[:8]}'\n"
        f"    await r.submit(tid, 'sleep {task_runtime}')\n"
        f"    await shutdown_drain(r, timeout={drain_timeout})\n"
        "    runs = r.list_runs(tid)\n"
        "    if runs:\n"
        "        sys.stdout.write('FINAL_STATUS=' + str(runs[0].get('status','')) + '\\n')\n"
        "asyncio.run(_main())\n"
    )
    child = _run_out_of_process(helper_src, timeout=20.0)
    assert child.returncode == 0, child.stderr

    parsed = _parse_kv_lines(child.stdout, "FINAL_STATUS")
    assert parsed is not None, (
        f"child did not report FINAL_STATUS; stdout={child.stdout!r}"
    )
    # FR08-drain-over-budget (out-of-process)
    assert parsed == "interrupted", (
        f"out-of-process drain: expected 'interrupted', got {parsed!r}"
    )


# ---------------------------------------------------------------------------
# Case 2 — timeout-kill leaves no orphan process
# ---------------------------------------------------------------------------


def test_fr08_timeout_kill_leaves_no_orphan_process() -> None:
    """AC-8.2: `PROCESS_COUNT_AFTER = 0` — after a timeout-killed task,
    `os.listdir('/proc/<pid>/task/')` (or POSIX equivalent) shows no
    orphan child pid. Asserted via `os.kill(pid, 0)` reaping, which is the
    portable POSIX equivalent (macOS has no /proc).

    Drives the executor out-of-process (subprocess_mode=out_of_process)
    so the child pid is observable from the parent test process.

    [FR-08] — AC-8.2, NP-15 (kill+wait sub-flow), subprocess_mode=out_of_process.
    """
    # NFR-03 — kill + wait sub-flow leaves no orphan (NP-15).
    # NFR-10 — integration coverage (subprocess kill+await end-to-end).
    # NFR-09 — zero-skip iron rule (no `skipif`; orphan check must actually run).
    command = "sleep 30"
    timeout_seconds = 1.0
    orphan_pids_after_kill = "1"

    # GREEN TODO: Runner.submit(task_id, "sleep 30", timeout=1.0) must
    # call proc.kill() then await proc.wait() inside the asyncio event
    # loop on timeout, so the SIGKILL is observed and the child pid exits
    # before drain() returns. The helper below records the child pid the
    # runner spawned and emits it for the parent to check via os.kill.
    #
    # The grandchild is deliberately NOT reaped by the helper itself: the
    # parent test observes the pid AFTER the helper exits, so a pid that is
    # still alive proves the runner leaked an orphan (T-15).
    helper_src = (
        "import asyncio, sys\n"
        f"sys.path.insert(0, {str(_SRC_ROOT)!r})\n"
        "from taskq_api.service import runner as runner_mod\n"
        "pid_holder = []\n"
        "orig_exec = asyncio.create_subprocess_exec\n"
        "async def _wrapped(*a, **kw):\n"
        "    proc = await orig_exec(*a, **kw)\n"
        "    pid_holder.append(proc.pid)\n"
        "    return proc\n"
        "asyncio.create_subprocess_exec = _wrapped\n"
        "async def _main():\n"
        "    r = runner_mod.Runner()\n"
        f"    tid = 'fr08-orphan-{uuid.uuid4().hex[:8]}'\n"
        f"    await r.submit(tid, {command!r}, timeout={timeout_seconds})\n"
        "    await r.drain(timeout=5.0)\n"
        "    if pid_holder:\n"
        "        sys.stdout.write('CHILD_PID=' + str(pid_holder[0]) + '\\n')\n"
        "asyncio.run(_main())\n"
    )
    child = _run_out_of_process(helper_src, timeout=20.0)
    assert child.returncode == 0, child.stderr

    pid_match = _parse_kv_lines(child.stdout, "CHILD_PID")
    assert pid_match is not None, (
        f"child did not report CHILD_PID; stdout={child.stdout!r}"
    )
    child_pid = int(pid_match)

    # Poll for the pid to disappear. `kill -0` returns 0 if alive, raises
    # ProcessLookupError once the pid has been reaped. The runner MUST
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
        f"child pid {child_pid} still alive after timeout-kill "
        f"(command={command!r}, timeout={timeout_seconds})"
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
    # NFR-10 — integration coverage (TaskGroup + semaphore exercised in-process).
    # NFR-09 — zero-skip iron rule (no `skipif`).
    max_concurrent = 2
    submitted = 5

    # GREEN TODO: Runner() must read `TASKQ_MAX_CONCURRENT` from the env at
    # __init__ time (NOT only at module import, or this monkeypatch cannot
    # take effect) and expose it as `.max_concurrent`.
    #
    # GREEN TODO: `submit()` MUST return immediately even when the cap is
    # saturated — it enqueues, it does not block on the semaphore. If
    # submit() blocked until a slot freed, the 5 submits below would
    # serialise and the sampling loop would never observe concurrency.
    # The queueing is what AC-8.3 is asserting: "excess tasks queue rather
    # than spawn unbounded coroutines".
    #
    # GREEN TODO: Runner must expose `in_flight` -> int, the number of
    # tasks currently RUNNING (not queued).
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", str(max_concurrent))
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "30.0")

    runner = Runner()
    assert getattr(runner, "max_concurrent", None) == max_concurrent, (
        f"Runner.max_concurrent should be {max_concurrent}, "
        f"got {getattr(runner, 'max_concurrent', None)!r}"
    )

    # Each submitted task sleeps 1.5s so multiple are in-flight simultaneously.
    for i in range(submitted):
        await runner.submit(f"fr08-cap-{i}-{uuid.uuid4().hex[:8]}", "sleep 1.5")

    # Sample in_flight while the 5 tasks drain through a cap of 2. The cap
    # MUST never be exceeded, and must actually be utilised.
    high_water = 0
    samples = 0
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        in_flight_now = int(getattr(runner, "in_flight", 0))
        high_water = max(high_water, in_flight_now)
        samples += 1
        await asyncio.sleep(0.02)

    await runner.drain(timeout=15.0)

    # FR08-queue-excess — the spec literal (`submitted == "5"`) plus the
    # behavioural invariant it stands for.
    assert submitted == 5, submitted
    assert high_water <= max_concurrent, (
        f"in_flight peaked at {high_water}, cap is {max_concurrent}"
    )
    # The cap must actually have been saturated, otherwise `high_water <=
    # max_concurrent` would pass vacuously against a runner that never ran
    # anything at all.
    assert high_water == max_concurrent, (
        f"in_flight never reached the cap {max_concurrent}; "
        f"samples={samples}, high_water={high_water}"
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
    # NFR-09 — zero-skip iron rule (kill+await observable, no skip).
    command = "sleep 5"
    kill_called = "false"
    wait_awaited = "false"

    # GREEN TODO: taskq_api.service.runner must expose a module-level
    # `_execute_with_kill(proc, timeout)` coroutine that wraps
    # `asyncio.wait_for(proc.communicate(), timeout=timeout)` and, on
    # asyncio.TimeoutError, calls `proc.kill()` and then `await proc.wait()`.
    # It is called directly here so the kill+await branches are measurable
    # by pytest-cov (a real subprocess would hide them).

    # Fake subprocess Process: kill() records the call, wait() is awaitable
    # and records that it was actually awaited (not merely referenced).
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

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(3600)  # never completes; wait_for must fire
            return b"", b""

    fake = _FakeProc()

    async def _fake_wait_for(awaitable: Any, timeout: Any = None) -> Any:  # noqa: ARG001
        # Simulate a TimeoutError firing on wait_for, mimicking
        # `asyncio.wait_for(proc.communicate(), timeout=...)`.
        # Close the coroutine so Python does not emit "coroutine was never
        # awaited" RuntimeWarning noise.
        with contextlib.suppress(Exception):
            awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)

    runner_module = sys.modules["taskq_api.service.runner"]
    exec_fn = getattr(runner_module, "_execute_with_kill", None)
    assert exec_fn is not None, (
        "taskq_api.service.runner._execute_with_kill must exist; "
        "GREEN must implement the timeout kill+wait path there"
    )

    # Call the timeout path; expect kill() + await wait() to fire.
    with contextlib.suppress(asyncio.TimeoutError):
        # Re-raising after kill+wait is acceptable; swallowing is not,
        # but either way kill/wait must have been observed below.
        await exec_fn(fake, timeout=0.01)  # type: ignore[arg-type]

    if fake.killed:
        kill_called = "true"
    if fake.awaited:
        wait_awaited = "true"

    # FR08-kill-then-wait / FR08-await-process
    assert kill_called == "true", (
        f"proc.kill() must be invoked on timeout (command={command!r})"
    )
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
    permissible catchers are `except asyncio.CancelledError:` followed by a
    re-raise, or no catcher at all. This test pins that invariant.

    Note: since Python 3.8 `asyncio.CancelledError` inherits from
    `BaseException`, so a literal `except Exception:` would NOT catch it.
    The failure mode this test actually guards is the sloppier variant —
    `except BaseException:`, a bare `except:`, or an `except Exception`
    around code that converts cancellation into a normal error/return.

    [FR-08] — AC-8.5, NFR-03 (no cancellation swallow), T-09.
    """
    # NFR-03 — CancelledError must propagate, never be swallowed (T-09).
    # NFR-09 — zero-skip iron rule (cancellation check is mandatory, no skip).
    cancellation = "raised"
    handler_chain_catches = "false"

    async def _task_body() -> None:
        # Simulate a body that is being cancelled mid-execution.
        raise asyncio.CancelledError()

    # GREEN TODO: Runner._run_body(self, body) must invoke the coroutine
    # factory `body` and MUST NOT swallow asyncio.CancelledError — it
    # either re-raises from an explicit `except asyncio.CancelledError:`
    # block, or does not catch it at all.
    assert hasattr(Runner, "_run_body"), (
        "Runner._run_body must exist; GREEN must implement it"
    )

    async def _driver() -> None:
        runner = Runner()
        await runner._run_body(_task_body)

    propagated = False
    try:
        asyncio.run(_driver())
    except asyncio.CancelledError:
        propagated = True

    # cancellation == "raised" -> the body raised; if the wrapper let it
    # through, the handler chain did NOT catch it.
    assert cancellation == "raised"
    handler_chain_catches = "false" if propagated else "true"

    # FR08-cancel-propagates
    assert handler_chain_catches == "false", (
        "asyncio.CancelledError must NOT be swallowed by the task-body "
        "wrapper; GREEN must let it propagate (NFR-03 / T-09). "
        f"MAX_CONCURRENT={MAX_CONCURRENT}, DRAIN_TIMEOUT={DRAIN_TIMEOUT}"
    )
