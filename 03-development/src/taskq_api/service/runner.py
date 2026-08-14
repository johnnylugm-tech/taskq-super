"""Task run executor.

[FR-02] — executes tasks via `asyncio.create_subprocess_exec(*shlex.split(
command))` (T-03: the shell-passing flag is forbidden), enforces
`TASKQ_TASK_TIMEOUT`, and persists the lifecycle
`pending → running → done | failed | timeout` into a SQLite file. Rows
carry `exit_code`, `stdout_tail`, `stderr_tail`, `finished_at`, and
`duration_ms`. The on-disk SQLite is shared between the parent test
process and the FR-02 out-of-process child run via the `TASKQ_RUNNER_DB`
env var so AC-2.5 (subprocess-mode out_of_process) can observe a
persisted row written from the subprocess.

Citations:
- taskq_api.service.runner:run_command   AC-2.1 / AC-2.2 / AC-2.3 / AC-2.4 / AC-2.5
- taskq_api.service.runner:list_runs     AC-2.6 (history, finished_at desc)
- taskq_api.service.runner:_run_async    AC-2.5 (kill+await no orphan)
"""

from __future__ import annotations

import asyncio
import os
import shlex
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List


# Cross-process shared store. Both the parent test process and the
# FR-02 out-of-process subprocess map here so the persisted run row
# is observable to the parent's GET /v1/tasks/{id}/runs polling.
_DB_PATH: str = os.environ.get("TASKQ_RUNNER_DB", "/tmp/taskq_runner.db")

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


def _upsert(row: Dict[str, Any]) -> None:
    """INSERT-or-UPDATE one row in task_results by its primary key id."""
    conn = _connect()
    try:
        _ensure_schema(conn)
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
    finally:
        conn.close()


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
    except Exception:
        pass


async def _execute_command(command: str, timeout: float) -> Dict[str, Any]:
    """Run the command and return the row-fragment keys for settlement.

    Returns a dict with `status`, `exit_code`, `stdout_tail`, `stderr_tail`
    — the four fields the row-finalisation step needs. The subprocess is
    launched via `asyncio.create_subprocess_exec(*shlex.split(command))` so
    shell metacharacters pass straight through to `execve` (AC-2.2 / T-03:
    the shell-passing flag is forbidden).
    """
    arglist = shlex.split(command)
    try:
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


__all__: list[str] = ["run_command", "list_runs"]
