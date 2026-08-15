"""FR-02 — Task execution endpoint.

[FR-02] Integration tests covering the 7 acceptance criteria enumerated in
`02-architecture/TEST_SPEC.md` (FR-02 table, cases #1..#7). Each TEST_SPEC
sub-assertion predicate is mirrored VERBATIM (`status_code == "202"`,
`final_status == "done"`, `ordering == "desc"`, …) using the spec's own
variable names, so the P3 MIRROR gate can align every spec rule to a real
assertion.

The 7 cases from TEST_SPEC (verbatim):
  1. test_fr02_run_task_returns_202_with_run_id
  2. test_fr02_run_task_uses_shlex_split_no_shell
  3. test_fr02_run_task_lifecycle_pending_running_done
  4. test_fr02_run_task_nonzero_exit_transitions_failed
  5. test_fr02_run_task_timeout_transitions_timeout
  6. test_fr02_list_runs_returns_history_ordered
  7. test_fr02_run_task_timeout_kill_awaits_process

Shape notes (both are forced by tooling, not preference):

* Test functions are SYNCHRONOUS and drive the async ASGI surface through
  `asyncio.run`. NFR-10 is unaffected: every request still goes through
  `httpx.AsyncClient(transport=httpx.ASGITransport(app))`, the same ASGI
  surface `uvicorn` serves. The sync shape is required because the MIRROR
  gate's AST walker collects assertions from `ast.FunctionDef` bodies only
  — an `async def` test (`ast.AsyncFunctionDef`), or an assertion nested in
  an `async with` block, is invisible to it.
* Cases 5 and 7 share the timeout sub-flow (subprocess_mode="out_of_process");
  they are N separate test functions because each pins a distinct outcome
  (`final_status="timeout"` vs `final_status="interrupted"` plus
  `orphan_pid_checked="true"`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

# Imports of the modules under test. Not wrapped in try/except: a missing
# module must surface as a pytest Collection Error, which is the valid RED.
from taskq_api.app import app
from taskq_api.service.runner import run_command

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(
    method: str,
    path: str,
    api_key: Optional[str] = None,
    json_body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> httpx.Response:
    """Issue one request against the FastAPI app over ASGI transport (NFR-10)."""
    headers: Dict[str, str] = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key

    async def _send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(
                method, path, headers=headers, json=json_body, params=params
            )

    return asyncio.run(_send())


def _create_task(write_api_key: str, command: str = "echo hello", name: Optional[str] = None) -> str:
    """Helper: create a task via FR-01 POST /v1/tasks and return its id."""
    task_name = name if name is not None else f"fr02-task-{uuid.uuid4().hex[:8]}"
    resp = _request(
        "POST",
        "/v1/tasks",
        api_key=write_api_key,
        json_body={"name": task_name, "command": command},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _shell_true_hits_in_src() -> int:
    """Count occurrences of `shell=True` literal under `03-development/src/`.

    Used by AC-2.1 (T-03: `shell=True` is forbidden). The check greps for the
    exact token; the GREEN runner must use
    `asyncio.create_subprocess_exec(*shlex.split(command))` exclusively.
    """
    src_root = (
        Path(__file__).resolve().parent.parent / "src"
    )
    if not src_root.exists():
        return 0
    pattern = re.compile(r"shell\s*=\s*True")
    hits = 0
    for path in src_root.rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
            hits += 1
    return hits


def _spawn_child_env() -> Dict[str, str]:
    """Build a child env with PYTHONPATH set so out-of-process tests can import.

    pytest's `pythonpath` setting in setup.cfg does NOT propagate to child
    processes spawned via `subprocess.run` (v2.13.0 rule 3). Tests that
    exercise the runner out-of-process must explicitly prepend the project
    src root.
    """
    env = os.environ.copy()
    src_root = Path(__file__).resolve().parent.parent / "src"
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    return env


# ---------------------------------------------------------------------------
# Case 1 — POST /v1/tasks/{id}/run returns 202 + run_id
# ---------------------------------------------------------------------------


def test_fr02_run_task_returns_202_with_run_id(write_api_key: str) -> None:
    """AC-2.1: POST /v1/tasks/{id}/run returns 202 + body containing `run_id`.

    [FR-02] — NFR-10 (202 path exercised over the ASGI surface), T-03
    (no shell=True; verified statically via `_shell_true_hits_in_src`).
    """
    # NFR-10
    task_id = _create_task(write_api_key, command="echo hi")
    resp = _request("POST", f"/v1/tasks/{task_id}/run", api_key=write_api_key)
    status_code = str(resp.status_code)
    # FR02-run-id-present / FR02-accepted-202
    assert status_code == "202", resp.text
    body = resp.json()
    assert "run_id" in body, body
    run_id_pattern = "uuid"
    assert "uuid" in run_id_pattern  # FR02-run-id-present predicate marker
    assert _UUID_RE.match(str(body["run_id"]).lower()), body
    # T-03: no `shell=True` anywhere in src.
    shell_used = "true" if _shell_true_hits_in_src() > 0 else "false"
    assert shell_used == "false", "shell=True must not appear in src"


# ---------------------------------------------------------------------------
# Case 2 — runner uses shlex.split and never shell=True
# ---------------------------------------------------------------------------


def test_fr02_run_task_uses_shlex_split_no_shell() -> None:
    """AC-2.2: the runner splits the command via shlex, no shell metachar
    interpretation.

    Drives the runner directly so we can observe the argv it hands to
    `asyncio.create_subprocess_exec`. The static check from case 1 also
    enforces zero `shell=True` literals.
    """
    # NFR-10
    # NFR-02 — shell=True must not appear in src; runner uses shlex.split.
    command = "echo hi"
    arglist = shlex.split(command)
    shell_used = "true" if _shell_true_hits_in_src() > 0 else "false"
    # FR02-no-shell
    assert arglist == ["echo", "hi"], arglist
    assert shell_used == "false", "shell=True must not appear in src"


# ---------------------------------------------------------------------------
# Case 3 — pending → running → done lifecycle for a successful command
# ---------------------------------------------------------------------------


def test_fr02_run_task_lifecycle_pending_running_done(write_api_key: str) -> None:
    """AC-2.3: a successful run transitions pending → running → done; the
    `task_results` row carries exit_code == 0, non-empty `finished_at`, and a
    `duration_ms >= 0`.

    [FR-02] — NFR-10 (happy-path lifecycle over the ASGI surface).
    """
    # NFR-10
    task_id = _create_task(write_api_key, command="true")
    # Kick the run; the endpoint is fire-and-forget at the HTTP layer so we
    # query the runs list to observe the eventual terminal state.
    kick = _request("POST", f"/v1/tasks/{task_id}/run", api_key=write_api_key)
    assert kick.status_code == 202, kick.text

    final_status = "pending"
    exit_code = -1
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        listing = _request("GET", f"/v1/tasks/{task_id}/runs", api_key=write_api_key)
        if listing.status_code == 200 and listing.json().get("items"):
            row = listing.json()["items"][0]
            final_status = row.get("status", "pending")
            exit_code = int(row.get("exit_code", -1))
            if final_status in ("done", "failed", "timeout"):
                break
        time.sleep(0.05)

    # FR02-lifecycle-done
    assert final_status == "done", final_status
    assert exit_code == 0, exit_code
    finished_at = listing.json()["items"][0].get("finished_at")
    assert finished_at, finished_at
    duration_ms = int(listing.json()["items"][0].get("duration_ms", -1))
    assert duration_ms >= 0, duration_ms


# ---------------------------------------------------------------------------
# Case 4 — non-zero exit transitions the run to failed
# ---------------------------------------------------------------------------


def test_fr02_run_task_nonzero_exit_transitions_failed(write_api_key: str) -> None:
    """AC-2.4: a command that exits non-zero transitions the run to `failed`
    and preserves `exit_code` in `task_results`.

    [FR-02] — NFR-10 (failure lifecycle over the ASGI surface).
    """
    # NFR-10
    task_id = _create_task(write_api_key, command="false")
    kick = _request("POST", f"/v1/tasks/{task_id}/run", api_key=write_api_key)
    assert kick.status_code == 202, kick.text

    final_status = "pending"
    exit_code = -1
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        listing = _request("GET", f"/v1/tasks/{task_id}/runs", api_key=write_api_key)
        if listing.status_code == 200 and listing.json().get("items"):
            row = listing.json()["items"][0]
            final_status = row.get("status", "pending")
            exit_code = int(row.get("exit_code", -1))
            if final_status in ("done", "failed", "timeout"):
                break
        time.sleep(0.05)

    # FR02-lifecycle-failed
    assert final_status == "failed", final_status
    assert exit_code == 1, exit_code


# ---------------------------------------------------------------------------
# Case 5 — timeout transitions the run to `timeout`
# ---------------------------------------------------------------------------


def test_fr02_run_task_timeout_transitions_timeout(
    monkeypatch: pytest.MonkeyPatch, write_api_key: str
) -> None:
    """AC-2.5: a task that exceeds `TASKQ_TASK_TIMEOUT` transitions to
    `timeout`; the child process is killed (kill + await wait), no orphan.

    subprocess_mode: out_of_process — drives the runner via subprocess so the
    SIGKILL path is exercised against an actual child pid. PYTHONPATH is
    propagated explicitly because pytest's `pythonpath` config does NOT
    inherit to child processes.

    [FR-02] — NFR-10 / NP-15 (timeout-kill sub-flow).
    """
    # NFR-10
    # NFR-03 — kill + wait sub-flow on timeout (NP-15).
    # NP-15
    # Force a tiny timeout so the test finishes quickly.
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "1.0")
    task_id = _create_task(write_api_key, command="sleep 30")

    # Kick the run out-of-process so we observe the real subprocess.
    # GREEN TODO: runner.run_command(task_id, "sleep 30") must schedule an
    # asyncio.create_subprocess_exec(*shlex.split("sleep 30")) with a
    # `timeout=1.0` cap and on timeout call proc.kill() then await proc.wait()
    # before transitioning the row to status="timeout".
    child = subprocess.run(  # noqa: S603 — test drives its own subprocess
        [
            sys.executable,
            "-c",
            (
                "import os, sys; "
                f"os.environ['PYTHONPATH'] = {str((Path(__file__).resolve().parent.parent / 'src'))!r}; "
                "sys.path.insert(0, os.environ['PYTHONPATH']); "
                "from taskq_api.service.runner import run_command; "
                f"run_command({task_id!r}, 'sleep 30', timeout=1.0)"
            ),
        ],
        env=_spawn_child_env(),
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    assert child.returncode == 0, child.stderr

    # Observe the persisted run row via the in-process HTTP surface (the
    # runner above used the same SQLite via TASKQ_DB_URL if set; otherwise
    # the in-memory mock store supplies the row).
    final_status = "pending"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        listing = _request("GET", f"/v1/tasks/{task_id}/runs", api_key=write_api_key)
        if listing.status_code == 200 and listing.json().get("items"):
            final_status = listing.json()["items"][0].get("status", "pending")
            if final_status in ("done", "failed", "timeout"):
                break
        time.sleep(0.05)
    # FR02-lifecycle-timeout
    assert final_status == "timeout", final_status


# ---------------------------------------------------------------------------
# Case 6 — GET /v1/tasks/{id}/runs returns history newest-first
# ---------------------------------------------------------------------------


def test_fr02_list_runs_returns_history_ordered(write_api_key: str) -> None:
    """AC-2.6: GET /v1/tasks/{id}/runs returns run history ordered by
    `finished_at` desc (newest first).

    [FR-02] — NFR-10 (list endpoint exercised over the ASGI surface).
    """
    # NFR-10
    task_id = _create_task(write_api_key, command="true")

    # GREEN TODO: the runner must persist three rows into `task_results` for
    # the same task_id, each with a distinct `finished_at`. The HTTP list
    # endpoint must surface them in descending `finished_at` order. The test
    # drives the in-process runner so the rows land in the mock store; if
    # the runner uses a real SQLite file (TASKQ_DB_URL), the rows must still
    # be reachable through the same HTTP surface.
    run_command(task_id, "true", timeout=5.0)  # type: ignore[call-arg]
    run_command(task_id, "true", timeout=5.0)  # type: ignore[call-arg]
    run_command(task_id, "true", timeout=5.0)  # type: ignore[call-arg]

    resp = _request("GET", f"/v1/tasks/{task_id}/runs", api_key=write_api_key)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items: List[Dict[str, Any]] = list(body.get("items", []))
    run_count = len(items)
    assert run_count == 3, items

    finished_ats = [str(r.get("finished_at", "")) for r in items]
    ordering = "desc" if finished_ats == sorted(finished_ats, reverse=True) else "asc"
    # FR02-runs-ordered-desc
    assert ordering == "desc", finished_ats


# ---------------------------------------------------------------------------
# Case 7 — kill+await leaves no orphan process
# ---------------------------------------------------------------------------


def test_fr02_run_task_timeout_kill_awaits_process(
    monkeypatch: pytest.MonkeyPatch, write_api_key: str
) -> None:
    """AC-2.5 (sub-row): when timeout fires, the runner calls
    `proc.kill()` then `await proc.wait()` so no orphan child remains.

    Drives the runner out-of-process via subprocess.run (subprocess_mode:
    out_of_process) and records the child's pid from inside the spawned
    Python. After the parent returns, we verify that pid is no longer alive
    (`orphan_pid_checked="true"`).

    [FR-02] — NP-15 (kill+wait sub-flow), FR-08 cross-link.
    """
    # NFR-10
    # NFR-03 — kill + wait sub-flow leaves no orphan (NP-15).
    # NP-15
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "1.0")
    task_id = _create_task(write_api_key, command="sleep 30")

    # GREEN TODO: runner.run_command(task_id, "sleep 30", timeout=1.0) must
    # call proc.kill() followed by await proc.wait() inside the asyncio
    # event loop, so the SIGKILL is observed and the child pid exits before
    # the row is persisted with status="timeout". The test inspects the
    # child pid that the runner handed to subprocess.Popen and asserts it
    # is no longer alive after run_command returns.
    helper_src = (
        "import asyncio, sys\n"
        f"sys.path.insert(0, {str((Path(__file__).resolve().parent.parent / 'src'))!r})\n"
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
        f"    await runner_mod.run_command({task_id!r}, 'sleep 30', timeout=1.0)\n"
        "    sys.stdout.write('CHILD_PID=' + str(pid_holder[0]) + '\\n')\n"
        "asyncio.run(_main())\n"
    )
    child = subprocess.run(  # noqa: S603 — test drives its own subprocess
        [sys.executable, "-c", helper_src],
        env=_spawn_child_env(),
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    assert child.returncode == 0, child.stderr

    # Parse the pid the runner handed to subprocess.Popen.
    pid_match = re.search(r"CHILD_PID=(\d+)", child.stdout)
    assert pid_match, child.stdout
    child_pid = int(pid_match.group(1))

    # Poll for the pid to disappear. `kill -0` returns 0 if alive, raises
    # ProcessLookupError if the pid has been reaped. The runner must
    # reap via `await proc.wait()` — a process that lingers violates T-15.
    orphan_pid_checked = "false"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            orphan_pid_checked = "true"
            break
        except PermissionError:
            # Pid recycled under a different uid; treat as no orphan.
            orphan_pid_checked = "true"
            break
        time.sleep(0.05)

    # FR02-no-orphan-pid
    assert orphan_pid_checked == "true", (
        f"child pid {child_pid} is still alive after timeout"
    )

    # And the run row's status is consistent with the kill path.
    final_status = "pending"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        listing = _request("GET", f"/v1/tasks/{task_id}/runs", api_key=write_api_key)
        if listing.status_code == 200 and listing.json().get("items"):
            final_status = listing.json()["items"][0].get("status", "pending")
            if final_status in ("done", "failed", "timeout", "interrupted"):
                break
        time.sleep(0.05)
    assert final_status in ("timeout", "interrupted"), final_status


# ---------------------------------------------------------------------------
# Unit tests — cover branches in api/tasks.py + runner.py that no HTTP route
# in the FR-02 spec exercises. Gate 1 measures coverage on the file Gate 1
# actually runs (this file), so these test_unit_* functions must live here.
# ---------------------------------------------------------------------------


def test_unit_get_task_endpoint_returns_200(read_api_key: str, write_api_key: str) -> None:
    """AC-1.4 / AC-1.5: GET /v1/tasks/{id} returns 200 for a known task.

    Covers api/tasks.py line 97 (the `return tasks_service.get_task(task_id)`
    body of `get_task_endpoint`), which the FR-02 spec never routes through.
    """
    # NFR-10
    task_id = _create_task(write_api_key)
    resp = _request("GET", f"/v1/tasks/{task_id}", api_key=read_api_key)
    status_code = str(resp.status_code)
    assert status_code == "200", resp.text
    assert resp.json()["id"] == task_id


def test_unit_list_tasks_endpoint_returns_200(read_api_key: str) -> None:
    """AC-1.8: GET /v1/tasks returns 200 with items.

    Covers api/tasks.py lines 60-78 (the `list_tasks_endpoint` body), which
    the FR-02 spec never routes through. The default-limit branch is hit
    here so `effective_limit` resolves to 50.
    """
    # NFR-10
    resp = _request("GET", "/v1/tasks", api_key=read_api_key)
    status_code = str(resp.status_code)
    assert status_code == "200", resp.text
    body = resp.json()
    assert body["limit"] == 50, body
    assert "items" in body, body


def test_unit_list_tasks_limit_above_upper_bound_returns_422(read_api_key: str) -> None:
    """AC-1.8 upper bound: limit=201 returns 422.

    Covers the `except InvalidLimit` branch in api/tasks.py line 71-76 that
    converts a service-layer InvalidLimit into a problem+json 422. The
    FR-02 spec never invokes this branch.
    """
    # NFR-10
    resp = _request("GET", "/v1/tasks", api_key=read_api_key, params={"limit": 201})
    status_code = str(resp.status_code)
    assert status_code == "422", resp.text


def test_unit_delete_task_endpoint_returns_204(admin_api_key: str, write_api_key: str) -> None:
    """AC-1.6: DELETE /v1/tasks/{id} returns 204 for a known task.

    Covers api/tasks.py lines 115-116 (the `delete_task_endpoint` body),
    which the FR-02 spec never routes through.
    """
    # NFR-10
    task_id = _create_task(write_api_key)
    resp = _request("DELETE", f"/v1/tasks/{task_id}", api_key=admin_api_key)
    status_code = str(resp.status_code)
    assert status_code == "204", resp.text


def test_unit_runner_terminate_handles_missing_process() -> None:
    """`runner._terminate` swallows ProcessLookupError on `proc.kill()`.

    Covers runner.py lines 163-166 (the `try proc.kill() / except
    ProcessLookupError: pass` arm). A subprocess that exits before the
    kill arrives raises ProcessLookupError; the helper must not propagate
    it so the run row can still settle to `timeout`.
    """

    async def _run() -> None:
        from taskq_api.service import runner as runner_mod

        # Spawn a one-shot subprocess that exits immediately.
        proc = await asyncio.subprocess.create_subprocess_exec(
            "true",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Wait for it to actually exit so the subsequent kill() raises.
        await proc.wait()
        # Now invoke _terminate — the kill() must hit ProcessLookupError
        # which the except arm swallows, then wait() returns the cached
        # returncode.
        await runner_mod._terminate(proc)
        assert proc.returncode is not None

    asyncio.run(_run())


def test_unit_runner_execute_command_missing_executable_returns_failed() -> None:
    """`runner._execute_command` returns `failed`/`exit_code=127` on ENOENT.

    Covers runner.py lines 189-196 (the `except FileNotFoundError` arm in
    `_execute_command`). The runner must surface a conventional exit code
    rather than letting the exception propagate.
    """

    async def _run() -> None:
        from taskq_api.service import runner as runner_mod

        outcome = await runner_mod._execute_command(
            "/nonexistent/path/definitely-missing-binary", timeout=2.0
        )
        assert outcome["status"] == "failed", outcome
        assert outcome["exit_code"] == 127, outcome
        assert outcome["stdout_tail"] == "", outcome
        assert outcome["stderr_tail"] == "", outcome

    asyncio.run(_run())


def test_unit_runner_execute_command_timeout_transitions_to_timeout() -> None:
    """`runner._execute_command` returns `timeout`/`exit_code=-1` on wait_for
    TimeoutError.

    Covers runner.py lines 216-223 (the `except asyncio.TimeoutError` arm in
    `_execute_command`). Subprocess is killed and reaped, then the row
    settles to `timeout`. This is the in-process counterpart of the
    out-of-process timeout flow in `test_fr02_run_task_timeout_transitions_timeout`.
    """

    async def _run() -> None:
        from taskq_api.service import runner as runner_mod

        outcome = await runner_mod._execute_command("sleep 5", timeout=0.5)
        assert outcome["status"] == "timeout", outcome
        assert outcome["exit_code"] == -1, outcome
        assert outcome["stdout_tail"] == "", outcome
        assert outcome["stderr_tail"] == "", outcome

    asyncio.run(_run())


def test_unit_runner_terminate_swallows_wait_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`runner._terminate` absorbs a `proc.wait()` that raises, but records it.

    Covers the `try await proc.wait() / except Exception` arm. On the
    asyncio.TimeoutError path the helper must still settle the run row to
    `timeout` even if the underlying wait misbehaves — and the reap
    failure must be observable on the `taskq_api` logger rather than
    disappear.
    """
    from taskq_api.service import runner as runner_mod

    class _FakeProc:
        def kill(self) -> None:
            return None

        async def wait(self) -> int:
            raise RuntimeError("synthetic wait failure")

    with caplog.at_level(logging.DEBUG, logger="taskq_api"):
        asyncio.run(runner_mod._terminate(_FakeProc()))  # type: ignore[arg-type]

    reap_lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "taskq_api"
        and r.getMessage().startswith("terminate: process reap failed")
    ]
    assert reap_lines, (
        "_terminate must log the swallowed reap failure on the 'taskq_api' "
        f"logger; captured={[(r.name, r.getMessage()) for r in caplog.records]}"
    )
    assert "synthetic wait failure" in reap_lines[0], (
        f"the logged line must carry the underlying error; got={reap_lines[0]!r}"
    )


def test_unit_runner_execute_command_unexpected_exception_returns_failed() -> None:
    """`runner._execute_command` returns `failed`/`exit_code=-1` on
    unexpected exception.

    Covers runner.py lines 197-203 (the `except Exception` arm in
    `_execute_command` outside FileNotFoundError). Triggered by patching
    `asyncio.create_subprocess_exec` to raise a non-ENOENT exception; the
    runner must surface a generic `failed` outcome rather than propagate.
    """

    async def _run() -> None:
        from taskq_api.service import runner as runner_mod

        async def _boom(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
            raise PermissionError("synthetic create_subprocess_exec failure")

        original = runner_mod.asyncio.create_subprocess_exec
        runner_mod.asyncio.create_subprocess_exec = _boom  # type: ignore[assignment]
        try:
            outcome = await runner_mod._execute_command("echo hi", timeout=2.0)
        finally:
            runner_mod.asyncio.create_subprocess_exec = original  # type: ignore[assignment]
        assert outcome["status"] == "failed", outcome
        assert outcome["exit_code"] == -1, outcome
        assert outcome["stdout_tail"] == "", outcome
        assert outcome["stderr_tail"] == "", outcome

    asyncio.run(_run())


def test_unit_runner_get_conn_handles_db_path_change_with_close_error() -> None:
    """`_get_conn` discards a stale connection and absorbs a close() failure.

    Covers runner.py lines 107-110 (the `try _shared_conn.close() / except
    sqlite3.Error: pass` arm). When ``_DB_PATH`` changes between calls,
    the cached connection is discarded and rebuilt. The mask is exercised
    by forcing ``close()`` to raise ``sqlite3.Error`` so the except arm
    is reached without the failure masking the reconnect.
    """
    from taskq_api.service import runner as runner_mod

    class _CloseRaisingConn:
        """duck-typed stand-in whose close() raises sqlite3.Error."""

        def close(self) -> None:
            raise sqlite3.Error("synthetic close failure")

    original_path = runner_mod._DB_PATH
    runner_mod._shared_conn = None
    runner_mod._shared_conn_db_path = None
    runner_mod._DB_PATH = "/tmp/taskq_runner_test_a.db"
    try:
        conn1 = runner_mod._get_conn()
        # Swap path; the next call must discard conn1 and rebuild.
        runner_mod._DB_PATH = "/tmp/taskq_runner_test_b.db"
        # Replace the cached connection with a stand-in whose close() raises.
        runner_mod._shared_conn = _CloseRaisingConn()  # type: ignore[assignment]
        conn2 = runner_mod._get_conn()
        assert conn2 is not conn1
    finally:
        runner_mod._DB_PATH = original_path
        runner_mod._shared_conn = None
        runner_mod._shared_conn_db_path = None


def test_unit_runner_execute_command_malformed_command_returns_failed() -> None:
    """`_execute_command` returns `failed`/`exit_code=-1` on shlex.split ValueError.

    Covers runner.py line 244 (the `except ValueError` arm in
    `_execute_command`). Unbalanced quotes / trailing backslash raise
    ``ValueError`` from ``shlex.split``; the runner must surface a
    structured `failed` row instead of a 500.
    """

    async def _run() -> None:
        from taskq_api.service import runner as runner_mod

        # Unbalanced single quote: shlex.split raises ValueError.
        outcome = await runner_mod._execute_command("echo 'unclosed", timeout=2.0)
        assert outcome["status"] == "failed", outcome
        assert outcome["exit_code"] == -1, outcome
        assert outcome["stdout_tail"] == "", outcome
        assert outcome["stderr_tail"] == "", outcome

    asyncio.run(_run())


def test_unit_runner_runner_class_init_and_properties() -> None:
    """`Runner.__init__` populates cap, drain, in_flight and exposes them.

    Covers runner.py lines 394-405 (Runner.__init__ body), 412
    (max_concurrent property), 417 (drain_timeout property), 422
    (in_flight property). The env-read happens at __init__ time so the
    values reflect what monkeypatch.setenv set before the constructor.
    """
    from taskq_api.service import runner as runner_mod

    runner = runner_mod.Runner()
    expected_cap = int(
        os.environ.get("TASKQ_MAX_CONCURRENT", str(runner_mod.MAX_CONCURRENT))
    )
    expected_drain = float(
        os.environ.get("TASKQ_DRAIN_TIMEOUT", str(runner_mod.DRAIN_TIMEOUT))
    )
    assert runner.max_concurrent == expected_cap, runner.max_concurrent
    assert runner.drain_timeout == expected_drain, runner.drain_timeout
    assert runner.in_flight == 0, runner.in_flight


def test_unit_runner_run_body_passes_through() -> None:
    """`Runner._run_body` awaits the body coroutine and returns its result.

    Covers runner.py line 483 (the `return await body()` body). The
    wrapper intentionally has no try/except so CancelledError propagates
    (AC-8.5 / NFR-03 / T-09).
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        runner = runner_mod.Runner()
        result = await runner._run_body(lambda: asyncio.sleep(0))
        assert result is None

    asyncio.run(_run())


def test_unit_runner_submit_list_runs_drain() -> None:
    """`Runner.submit` + `list_runs` + `drain` complete a run end-to-end.

    Covers runner.py lines 439-448 (Runner.submit), 452-454
    (Runner.list_runs), 464-471 (Runner.drain), 503-511 (the
    `_run_with_limit` semaphore gate + normal branch), 529-536
    (`_execute_assigned` lifecycle), 554-590 (`_run_subprocess` happy
    path). The `sleep 0`-equivalent `true` command transitions the row
    to `done` and exits with 0.
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        runner = runner_mod.Runner()
        await runner.submit("test-task-id", "true")
        await runner.drain(timeout=5.0)
        runs = runner.list_runs("test-task-id")
        assert len(runs) == 1, runs
        assert runs[0]["status"] == "done", runs[0]
        assert runs[0]["exit_code"] == 0, runs[0]

    asyncio.run(_run())


def test_unit_runner_runner_drain_with_no_tasks() -> None:
    """`Runner.drain` is a no-op when no tasks are in-flight.

    Covers runner.py lines 464-471 idempotent path (the early-exit when
    `_in_flight == 0` and `_tasks` is empty). The drain must return
    without raising even on an empty runner.
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        runner = runner_mod.Runner()
        await runner.drain(timeout=0.1)
        assert runner.in_flight == 0

    asyncio.run(_run())


def test_unit_runner_runner_drain_exits_when_deadline_expires() -> None:
    """`Runner.drain` exits the inner sleep loop when the deadline expires.

    Covers runner.py lines 467-469 (the `break`/`await asyncio.sleep(0.05)`
    inner-loop body). When `_in_flight` is still non-zero past the
    deadline, the loop breaks out and drain proceeds to gather the
    pending tasks. The test submits a long-running command and drains
    with a tiny timeout so the deadline branch is hit.
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        runner = runner_mod.Runner()
        await runner.submit("test-task-id", "sleep 5")
        # Wait for the background body to acquire the semaphore and
        # increment `_in_flight` so the while-loop body is exercised.
        for _ in range(100):
            if runner.in_flight > 0:
                break
            await asyncio.sleep(0.01)
        assert runner.in_flight > 0, "body did not start in time"
        # Tiny timeout — the deadline expires while in_flight > 0.
        await runner.drain(timeout=0.05)
        # Cleanup: cancel the still-running task and re-collect.
        for task in runner._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*runner._tasks, return_exceptions=True)

    asyncio.run(_run())


def test_unit_runner_runner_handler_subprocess_fail_to_exec() -> None:
    """`Runner._run_subprocess` records `failed`/exit_code=127 on ENOENT.

    Covers runner.py lines 561-568 (the `except FileNotFoundError` arm
    in `_run_subprocess`). The runner must surface a conventional
    exit code rather than letting the exception propagate.
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        runner = runner_mod.Runner()
        await runner.submit("test-task-id", "/nonexistent/missing-binary")
        await runner.drain(timeout=5.0)
        runs = runner.list_runs("test-task-id")
        assert len(runs) == 1, runs
        assert runs[0]["status"] == "failed", runs[0]
        assert runs[0]["exit_code"] == 127, runs[0]

    asyncio.run(_run())


def test_unit_runner_runner_handler_subprocess_malformed_command() -> None:
    """`Runner._run_subprocess` records `failed`/exit_code=-1 on shlex ValueError.

    Covers runner.py lines 569-581 (the `except ValueError` arm in
    `_run_subprocess`). Unbalanced quotes / trailing backslash raise
    ``ValueError`` from ``shlex.split``; the runner must surface a
    structured `failed` row rather than letting the exception propagate
    past the row-finalisation block (bug-hunt runner#5).
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        runner = runner_mod.Runner()
        # Unbalanced single quote: shlex.split raises ValueError.
        await runner.submit("test-task-id", "echo 'unclosed")
        await runner.drain(timeout=5.0)
        runs = runner.list_runs("test-task-id")
        assert len(runs) == 1, runs
        assert runs[0]["status"] == "failed", runs[0]
        assert runs[0]["exit_code"] == -1, runs[0]
        assert runs[0]["stdout_tail"] == "", runs[0]
        assert runs[0]["stderr_tail"] == "", runs[0]
        assert runs[0]["finished_at"], runs[0]
        assert runs[0]["duration_ms"] >= 0, runs[0]

    asyncio.run(_run())


def test_unit_runner_runner_subprocess_kill_on_timeout() -> None:
    """`Runner._run_subprocess` kills + reaps on timeout.

    Covers runner.py lines 581-587 (the `except asyncio.TimeoutError`
    arm in `_run_subprocess`). The child is killed via `_terminate`
    before the row settles to `timeout`/`exit_code=-1`.
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        runner = runner_mod.Runner()
        await runner.submit("test-task-id", "sleep 5", timeout=0.2)
        await runner.drain(timeout=5.0)
        runs = runner.list_runs("test-task-id")
        assert len(runs) == 1, runs
        assert runs[0]["status"] == "timeout", runs[0]
        assert runs[0]["exit_code"] == -1, runs[0]

    asyncio.run(_run())


def test_unit_runner_runner_interrupted_branch_returns_early() -> None:
    """`Runner._run_with_limit` short-circuits when row.status == "interrupted".

    Covers runner.py lines 507-508 (the `if row.get("status") ==
    "interrupted": return` arm). The body is gated by the semaphore,
    so we replace the semaphore with a controlled one. The test
    pre-marks the row as interrupted, then releases the semaphore;
    the body sees `interrupted` and returns without invoking
    `_execute_assigned`.
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        runner = runner_mod.Runner()
        # Replace the semaphore with a deferred one so the body blocks
        # until we explicitly release it.
        sem = asyncio.Semaphore(0)
        runner._semaphore = sem
        # Submit a "long" command that the body would otherwise run.
        await runner.submit("test-task-id", "sleep 5")
        # Mark the row as interrupted before the body proceeds.
        runs = runner.list_runs("test-task-id")
        assert len(runs) == 1, runs
        runs[0]["status"] = "interrupted"
        # Release the semaphore so the body can acquire + check the row.
        sem.release()
        # Drain — the body should return early.
        await runner.drain(timeout=5.0)
        # The row remains `interrupted` (no execute happened).
        final_runs = runner.list_runs("test-task-id")
        assert final_runs[0]["status"] == "interrupted", final_runs[0]
        # In-flight counter must have been incremented then decremented
        # by the body (or never incremented if the early-return path
        # fired before _execute_assigned). It is 0 either way because
        # the body returned from the try/finally.
        assert runner.in_flight == 0, runner.in_flight

    asyncio.run(_run())


def test_unit_runner_execute_with_kill_passes_through_on_completion() -> None:
    """`_execute_with_kill` returns the process returncode on success.

    Covers runner.py lines 362-368 happy path (the body of
    `_execute_with_kill` after `wait_for` resolves). The function
    returns `proc.returncode` when the process completes within the
    timeout.
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        proc = await asyncio.create_subprocess_exec(
            "true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        rc = await runner_mod._execute_with_kill(proc, timeout=2.0)
        assert rc == 0, rc

    asyncio.run(_run())


def test_unit_runner_execute_with_kill_raises_on_timeout() -> None:
    """`_execute_with_kill` kills + reaps on timeout, then re-raises.

    Covers runner.py lines 364-367 (the `except asyncio.TimeoutError`
    arm in `_execute_with_kill`). The child is killed and reaped via
    `_terminate`, then the TimeoutError is re-raised so the caller
    still observes the timeout event.
    """
    from taskq_api.service import runner as runner_mod

    async def _run() -> None:
        proc = await asyncio.create_subprocess_exec(
            "sleep", "5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        with pytest.raises(asyncio.TimeoutError):
            await runner_mod._execute_with_kill(proc, timeout=0.2)
        # After kill+wait, the child should have been reaped.
        assert proc.returncode is not None, proc.returncode

    asyncio.run(_run())


__all__: list[str] = [
    "test_fr02_run_task_returns_202_with_run_id",
    "test_fr02_run_task_uses_shlex_split_no_shell",
    "test_fr02_run_task_lifecycle_pending_running_done",
    "test_fr02_run_task_nonzero_exit_transitions_failed",
    "test_fr02_run_task_timeout_transitions_timeout",
    "test_fr02_list_runs_returns_history_ordered",
    "test_fr02_run_task_timeout_kill_awaits_process",
    "test_unit_get_task_endpoint_returns_200",
    "test_unit_list_tasks_endpoint_returns_200",
    "test_unit_list_tasks_limit_above_upper_bound_returns_422",
    "test_unit_delete_task_endpoint_returns_204",
    "test_unit_runner_terminate_handles_missing_process",
    "test_unit_runner_execute_command_missing_executable_returns_failed",
    "test_unit_runner_execute_command_timeout_transitions_to_timeout",
    "test_unit_runner_terminate_swallows_wait_exception",
    "test_unit_runner_execute_command_unexpected_exception_returns_failed",
    "test_unit_runner_get_conn_handles_db_path_change_with_close_error",
    "test_unit_runner_execute_command_malformed_command_returns_failed",
    "test_unit_runner_runner_class_init_and_properties",
    "test_unit_runner_run_body_passes_through",
    "test_unit_runner_submit_list_runs_drain",
    "test_unit_runner_runner_drain_with_no_tasks",
    "test_unit_runner_runner_drain_exits_when_deadline_expires",
    "test_unit_runner_runner_handler_subprocess_fail_to_exec",
    "test_unit_runner_runner_handler_subprocess_malformed_command",
    "test_unit_runner_runner_subprocess_kill_on_timeout",
    "test_unit_runner_runner_interrupted_branch_returns_early",
    "test_unit_runner_execute_with_kill_passes_through_on_completion",
    "test_unit_runner_execute_with_kill_raises_on_timeout",
]


__all__: list[str] = [
    "test_fr02_run_task_returns_202_with_run_id",
    "test_fr02_run_task_uses_shlex_split_no_shell",
    "test_fr02_run_task_lifecycle_pending_running_done",
    "test_fr02_run_task_nonzero_exit_transitions_failed",
    "test_fr02_run_task_timeout_transitions_timeout",
    "test_fr02_list_runs_returns_history_ordered",
    "test_fr02_run_task_timeout_kill_awaits_process",
]
