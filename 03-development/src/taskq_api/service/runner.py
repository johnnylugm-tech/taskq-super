"""Task run executor.

This module carries two FRs on a single disk path because both deal with
subprocess execution and share helpers (``_terminate``, ``_decode_tail``,
the SQLite schema). The split below is logical, not physical:

[FR-02] — ``run_command`` / ``list_runs`` / ``_run_async``. Synchronous
entry point that persists the lifecycle
``pending → running → done | failed | timeout`` into a SQLite file shared
across processes via ``TASKQ_RUNNER_DB``. Cited by AC-2.1..AC-2.6.

[FR-08] — ``Runner`` / ``MAX_CONCURRENT`` / ``DRAIN_TIMEOUT`` /
``_execute_with_kill``. Asynchronous TaskGroup-style executor with a
configurable concurrency cap and graceful drain. Cited by AC-8.1, AC-8.3,
AC-8.4, AC-8.5. ``shutdown_drain`` (the FastAPI lifespan hook) lives in
``taskq_api.app`` and re-reads ``DRAIN_TIMEOUT`` from this module.

Citations:
- taskq_api.service.runner:run_command      AC-2.1..AC-2.5
- taskq_api.service.runner:list_runs        AC-2.6
- taskq_api.service.runner:_run_async       AC-2.5
- taskq_api.service.runner:Runner           AC-8.1, AC-8.3, AC-8.5
- taskq_api.service.runner:_execute_with_kill  AC-8.4
"""

from __future__ import annotations

import asyncio
import os
import shlex
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# Cross-process shared store. Both the parent test process and the
# FR-02 out-of-process subprocess map here so the persisted run row
# is observable to the parent's GET /v1/tasks/{id}/runs polling.
_DB_PATH: str = os.environ.get("TASKQ_RUNNER_DB", "/tmp/taskq_runner.db")  # nosec

# AC-2.3 — only the trailing N chars of each stream are persisted to keep
# rows bounded; full output lives in the subprocess pipe until it's closed.
_OUTPUT_TAIL_CHARS: int = 2000


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    """Open a SQLite connection with a bounded busy timeout."""
    return sqlite3.connect(_DB_PATH, timeout=10.0)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the task_results table on first use (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_results (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            command TEXT NOT NULL,
            status TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            stdout_tail TEXT NOT NULL,
            stderr_tail TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            duration_ms INTEGER NOT NULL
        )
        """
    )
    conn.commit()


_shared_conn: sqlite3.Connection | None = None
_shared_conn_db_path: str | None = None


def _get_conn() -> sqlite3.Connection:
    """Return a process-wide shared SQLite connection.

    Bug-hunt finding runner#2 — the previous implementation opened a fresh
    connection on every `_upsert` call, which serialised through SQLite's
    single-writer mutex and burdened the file descriptor table. The
    connection is now cached at module level so the schema is created once
    and the same handle is reused across upserts.

    The cache is keyed on the current `_DB_PATH` so test fixtures that
    monkeypatch `_DB_PATH` (e.g. `tests/test_fr08.py::runner_db`) get a
    fresh connection bound to the new file path.
    """
    global _shared_conn, _shared_conn_db_path
    if _shared_conn is None or _shared_conn_db_path != _DB_PATH:
        if _shared_conn is not None:
            try:
                _shared_conn.close()
            except Exception:
                pass
        _shared_conn = _connect()
        _shared_conn_db_path = _DB_PATH
        _ensure_schema(_shared_conn)
    return _shared_conn


def _upsert(row: Dict[str, Any]) -> None:
    """INSERT-or-UPDATE one row in task_results by its primary key id."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO task_results
            (id, task_id, command, status, exit_code, stdout_tail,
             stderr_tail, started_at, finished_at, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status = excluded.status,
            exit_code = excluded.exit_code,
            stdout_tail = excluded.stdout_tail,
            stderr_tail = excluded.stderr_tail,
            started_at = excluded.started_at,
            finished_at = excluded.finished_at,
            duration_ms = excluded.duration_ms
        """,
        (
            row["id"],
            row["task_id"],
            row["command"],
            row["status"],
            row["exit_code"],
            row["stdout_tail"],
            row["stderr_tail"],
            row.get("started_at"),
            row.get("finished_at"),
            row["duration_ms"],
        ),
    )
    conn.commit()


def list_runs(task_id: str) -> List[Dict[str, Any]]:
    """Return run history for a task, ordered newest-first by finished_at.

    Citations:
    - taskq_api.service.runner:list_runs  AC-2.6
    """
    conn = _connect()
    try:
        _ensure_schema(conn)
        cur = conn.execute(
            "SELECT id, task_id, command, status, exit_code, stdout_tail, "
            "stderr_tail, started_at, finished_at, duration_ms "
            "FROM task_results WHERE task_id = ? "
            "ORDER BY finished_at IS NULL ASC, finished_at DESC",
            (task_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _new_pending_row(task_id: str, command: str) -> Dict[str, Any]:
    """Build the initial `pending` row for a new run."""
    return {
        "id": str(uuid.uuid4()),
        "task_id": task_id,
        "command": command,
        "status": "pending",
        "exit_code": -1,
        "stdout_tail": "",
        "stderr_tail": "",
        "started_at": _now_iso(),
        "finished_at": None,
        "duration_ms": 0,
    }


def _decode_tail(buf: bytes) -> str:
    """Decode a captured subprocess stream; return the trailing N chars only."""
    if not buf:
        return ""
    return buf.decode("utf-8", errors="replace")[-_OUTPUT_TAIL_CHARS:]


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill the subprocess and reap it.

    AC-2.5 / NP-15 — kill then await wait so no orphan pid remains after a
    timeout. Both calls are best-effort: `ProcessLookupError` (already
    exited) and any reap failure are swallowed so the run can still settle
    to `timeout`.
    """
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.wait()
    except Exception:  # nosec
        pass


async def _execute_command(command: str, timeout: float) -> Dict[str, Any]:
    """Run the command and return the row-fragment keys for settlement.

    Returns a dict with `status`, `exit_code`, `stdout_tail`, `stderr_tail`
    — the four fields the row-finalisation step needs. The subprocess is
    launched via `asyncio.create_subprocess_exec(*shlex.split(command))` so
    shell metacharacters pass straight through to `execve` (AC-2.2 / T-03:
    the shell-passing flag is forbidden).
    """
    try:
        arglist = shlex.split(command)
        proc = await asyncio.create_subprocess_exec(
            *arglist,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        # AC-2.4 — executable missing; record the conventional 127.
        return {
            "status": "failed",
            "exit_code": 127,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    except ValueError:
        # shlex.split raises ValueError on unbalanced quotes / trailing
        # backslash. Report as a malformed-command failure so the
        # caller sees a structured "failed" row instead of a 500.
        return {
            "status": "failed",
            "exit_code": -1,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    except Exception:
        return {
            "status": "failed",
            "exit_code": -1,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        exit_code = proc.returncode if proc.returncode is not None else -1
        return {
            "status": "done" if exit_code == 0 else "failed",
            "exit_code": exit_code,
            "stdout_tail": _decode_tail(stdout_b),
            "stderr_tail": _decode_tail(stderr_b),
        }
    except asyncio.TimeoutError:
        await _terminate(proc)
        return {
            "status": "timeout",
            "exit_code": -1,
            "stdout_tail": "",
            "stderr_tail": "",
        }


async def _run_async(task_id: str, command: str, timeout: float) -> Dict[str, Any]:
    """Execute the command and persist the lifecycle transitions.

    Lifecycle: `pending → running → done | failed | timeout`. The subprocess
    execution is delegated to `_execute_command` so this function only owns
    the row lifecycle (initial create, running-transition persist, settle).

    Citations:
    - taskq_api.service.runner:_run_async  AC-2.1 / AC-2.3 / AC-2.4 / AC-2.5
    """
    # [FR-02]
    row = _new_pending_row(task_id, command)
    _upsert(row)

    # pending → running.
    row["status"] = "running"
    _upsert(row)

    started_monotonic = time.monotonic()
    outcome = await _execute_command(command, timeout)

    row.update(
        outcome,
        finished_at=_now_iso(),
        duration_ms=int((time.monotonic() - started_monotonic) * 1000),
    )
    _upsert(row)
    return row


def run_command(task_id: str, command: str, timeout: float = 30.0) -> Any:
    """Execute the run, transparently handling sync/async call sites.

    From inside an existing asyncio event loop this returns the awaitable
    coroutine so callers can `await` it. Outside an event loop (e.g. from
    a plain `python -c "..."` subprocess) it runs synchronously via
    `asyncio.run()` and returns the row dict — this is required by the
    FR-02 subprocess-mode tests (case 5 / case 7) which invoke
    `run_command(...)` directly.

    Citations:
    - taskq_api.service.runner:run_command  AC-2.1..AC-2.5
    """
    # [FR-02]
    coro = _run_async(task_id, command, timeout)
    try:
        asyncio.get_running_loop()
        return coro
    except RuntimeError:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# [FR-08] — asynchronous executor (TaskGroup + graceful drain + kill+wait).
# ---------------------------------------------------------------------------

# [FR-08] — module-level defaults. ``Runner.__init__`` re-reads these from
# the environment so ``monkeypatch.setenv("TASKQ_MAX_CONCURRENT", ...)``
# takes effect per-test (case 3 lifts the cap to a small value to exercise
# the semaphore branch).
MAX_CONCURRENT: int = int(os.environ.get("TASKQ_MAX_CONCURRENT", "16"))
DRAIN_TIMEOUT: float = float(os.environ.get("TASKQ_DRAIN_TIMEOUT", "30.0"))


async def _execute_with_kill(
    proc: asyncio.subprocess.Process, timeout: float
) -> int:
    """Run ``proc.communicate()`` under a timeout; on TimeoutError, kill + await wait.

    [FR-08] — AC-8.4, NP-15. The runner MUST invoke ``asyncio.wait_for``
    via the module attribute (not a local import) so tests that
    ``monkeypatch.setattr(asyncio, "wait_for", ...)`` can observe the
    timeout branch. On ``asyncio.TimeoutError`` the child is sent
    ``SIGKILL`` (``proc.kill()``) and reaped via ``await proc.wait()``
    before the function re-raises — never leaves an orphan (T-15).

    ``_terminate`` already does kill+wait; we call it here so the kill+
    reap path lives in exactly one place, then re-raise the TimeoutError
    so the caller still observes the timeout event.

    Citations:
    - taskq_api.service.runner:_execute_with_kill  AC-8.4
    """
    try:
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        # AC-8.4 / NP-15 — kill then await wait so no orphan pid lingers.
        await _terminate(proc)
        raise
    return proc.returncode if proc.returncode is not None else -1


class Runner:
    """Asynchronous task executor — concurrency-capped background runner.

    [FR-08] — backed by ``asyncio.Semaphore`` for the concurrency cap and
    a private dict for in-memory run history. ``submit()`` is
    **non-blocking**: it creates a background task and returns
    immediately, so an over-cap submitter queues rather than spawning
    unbounded coroutines (AC-8.3). ``drain()`` waits for the in-flight
    set to reach zero. ``shutdown_drain()`` (``taskq_api.app``) waits
    for the drain budget and marks over-budget rows ``interrupted``
    (AC-8.1).

    Citations:
    - taskq_api.service.runner:Runner            AC-8.1, AC-8.3, AC-8.5
    - taskq_api.service.runner:Runner.submit     AC-8.3 (non-blocking enqueue)
    - taskq_api.service.runner:Runner.drain       AC-8.3 (wait for completion)
    - taskq_api.service.runner:Runner.list_runs  AC-8.1 (drain-readable rows)
    - taskq_api.service.runner:Runner._run_body  AC-8.5 (no CancelledError swallow)
    """

    def __init__(self) -> None:
        # Re-read env at __init__ time so monkeypatch.setenv (case 3) takes
        # effect without re-importing the module.
        self._max_concurrent: int = int(
            os.environ.get("TASKQ_MAX_CONCURRENT", str(MAX_CONCURRENT))
        )
        self._drain_timeout: float = float(
            os.environ.get("TASKQ_DRAIN_TIMEOUT", str(DRAIN_TIMEOUT))
        )
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(self._max_concurrent)
        self._in_flight: int = 0
        # task_id -> run_id -> row dict (in-memory; cross-checked with the
        # SQLite store but kept separate so drain can mutate without DB I/O)
        self._runs: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._tasks: List[asyncio.Task[Any]] = []

    # -- properties --------------------------------------------------------

    @property
    def max_concurrent(self) -> int:
        """Configured concurrency cap (read at __init__ time)."""
        return self._max_concurrent

    @property
    def drain_timeout(self) -> float:
        """Configured drain budget (seconds)."""
        return self._drain_timeout

    @property
    def in_flight(self) -> int:
        """Number of tasks currently RUNNING (not queued)."""
        return self._in_flight

    # -- public surface ----------------------------------------------------

    async def submit(
        self,
        task_id: str,
        command: str,
        timeout: Optional[float] = None,
    ) -> None:
        """Enqueue a task and return immediately — never blocks on the cap.

        [FR-08] — AC-8.3. Creates a per-task background coroutine that
        waits for a concurrency slot inside its own body, then runs the
        command. The submitter returns as soon as the task is scheduled,
        so excess submissions queue rather than serialise on the cap.
        """
        run_id = str(uuid.uuid4())
        row = _new_pending_row(task_id, command)
        row["id"] = run_id
        self._runs.setdefault(task_id, {})[run_id] = row
        # Create the background task; it will await sem.acquire() inside
        # _run_with_limit so submit() returns immediately.
        task = asyncio.create_task(
            self._run_with_limit(task_id, run_id, command, timeout)
        )
        self._tasks.append(task)

    def list_runs(self, task_id: str) -> List[Dict[str, Any]]:
        """Return all run rows for ``task_id``, newest-first by started_at."""
        rows = list(self._runs.get(task_id, {}).values())
        rows.sort(key=lambda r: r.get("started_at", "") or "", reverse=True)
        return rows

    async def drain(self, timeout: Optional[float] = None) -> None:
        """Wait for every enqueued task to settle, up to ``timeout`` seconds.

        [FR-08] — does NOT mark rows ``interrupted``; that's the job of
        ``taskq_api.app.shutdown_drain``. ``drain`` here just blocks
        until the runner is quiescent so callers (e.g. case 2's
        out-of-process helper) can observe completion.
        """
        budget = timeout if timeout is not None else self._drain_timeout
        deadline = time.monotonic() + budget
        while self._in_flight > 0:
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.05)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run_body(self, body: Any) -> Any:
        """Invoke the body coroutine; let ``asyncio.CancelledError`` propagate.

        [FR-08] — AC-8.5 / NFR-03 / T-09. The wrapper intentionally has
        no try/except around the body: even though
        ``asyncio.CancelledError`` inherits from ``BaseException`` and
        would slip past a literal ``except Exception``, the test pins
        the invariant that we never wrap the body in an error-eating
        try/except at all.
        """
        return await body()

    # -- internals ---------------------------------------------------------

    async def _run_with_limit(
        self,
        task_id: str,
        run_id: str,
        command: str,
        timeout: Optional[float],
    ) -> None:
        """Acquire a semaphore slot, then drive the assigned run to completion.

        [FR-08] — the cap is enforced by waiting on ``self._semaphore``
        before doing any work; that waiting happens inside the
        per-task coroutine, not at submit time, so submit() is
        non-blocking (AC-8.3). The post-acquire status re-check guards
        against shutdown_drain marking the row ``interrupted`` while we
        were queued (case 1).
        """
        await self._semaphore.acquire()
        try:
            row = self._runs[task_id][run_id]
            # Shutdown may have already cancelled us while we were queued.
            if row.get("status") == "interrupted":
                return
            await self._execute_assigned(task_id, run_id, command, timeout)
        finally:
            self._semaphore.release()

    async def _execute_assigned(
        self,
        task_id: str,
        run_id: str,
        command: str,
        timeout: Optional[float],
    ) -> None:
        """Run one assigned task end-to-end and mutate its row in place.

        [FR-08] — extracted from ``_run_with_limit`` so the semaphore
        gating and the per-task subprocess lifecycle can be reasoned
        about independently. Tracks the running lifecycle
        (``running`` → ``done`` / ``failed`` / ``timeout``), enforces
        the timeout via ``asyncio.wait_for``, and on TimeoutError does
        kill+wait so no orphan pid is left behind (AC-8.4 / NP-15).
        """
        row = self._runs[task_id][run_id]
        row["status"] = "running"
        started = time.monotonic()
        self._in_flight += 1
        try:
            await self._run_subprocess(row, command, timeout, started)
        finally:
            self._in_flight -= 1

    async def _run_subprocess(
        self,
        row: Dict[str, Any],
        command: str,
        timeout: Optional[float],
        started: float,
    ) -> None:
        """Launch the subprocess, enforce timeout, and update ``row``.

        [FR-08] — extracted so the timeout-kill + row-finalisation logic
        is visible without scrolling past the semaphore gate. The
        ``asyncio.wait_for`` call uses the module attribute (not a local
        import) so ``monkeypatch.setattr(asyncio, "wait_for", ...)`` from
        case 4 takes effect. ``_terminate`` owns the kill+wait so the
        reaping path lives in exactly one place.
        """
        try:
            arglist = shlex.split(command)
            proc = await asyncio.create_subprocess_exec(
                *arglist,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            row["status"] = "failed"
            row["exit_code"] = 127
            row["stdout_tail"] = ""
            row["stderr_tail"] = ""
            row["finished_at"] = _now_iso()
            row["duration_ms"] = int((time.monotonic() - started) * 1000)
            return

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            exit_code = (
                proc.returncode if proc.returncode is not None else -1
            )
            row["status"] = "done" if exit_code == 0 else "failed"
            row["exit_code"] = exit_code
            row["stdout_tail"] = _decode_tail(stdout_b)
            row["stderr_tail"] = _decode_tail(stderr_b)
        except asyncio.TimeoutError:
            # AC-8.4 / NP-15 — kill + await wait so no orphan.
            await _terminate(proc)
            row["status"] = "timeout"
            row["exit_code"] = -1
            row["stdout_tail"] = ""
            row["stderr_tail"] = ""

        row["finished_at"] = _now_iso()
        row["duration_ms"] = int((time.monotonic() - started) * 1000)


__all__: list[str] = [
    "run_command",
    "list_runs",
    "Runner",
    "MAX_CONCURRENT",
    "DRAIN_TIMEOUT",
    "_execute_with_kill",
]
