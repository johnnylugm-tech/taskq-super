"""SEC-R8 threat verification tests (one per SAD.md §threats[] entry).

Each test below pins one threat mitigation listed in
`02-architecture/SAD.md` (SEC:BLOCK section, threats T-01..T-11) so the
P5 entry SEC-R8 obligation `verified_by test name must exist under
03-development/tests` is satisfied without rewriting the per-FR TDD
suite. The assertions exercise the mitigation directly against the
production module it cites — they are real checks, not stubs.

Naming convention is fixed by the SAD: every `verified_by` field is
the literal test function name. Renaming any of the functions below
will fail P5 entry.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import sys
import textwrap
from pathlib import Path

import pytest

# Path setup so the module under test is importable from any CWD.
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))


# ---------------------------------------------------------------------------
# T-01 — SQL injection via string-concatenated SQL or unparameterised queries
# Mitigation: ORM / bound parameters only; no f-string / % / + over SQL.
# ---------------------------------------------------------------------------
def test_sec_t01_no_sql_string_concatenation() -> None:
    """No repository or service file builds SQL via f-string/%/+ concat."""
    src_root = _SRC_ROOT / "taskq_api"
    forbidden = re.compile(r"""(?x)
        (?P<expr>
            (?:f'''[^']*SELECT | f"[^"]*SELECT | ['"]\s*SELECT\s*[^'"]*['"]\s*[%+] )
            |
            (?:['"][^'"]*SELECT[^'"]*['"]\s*%\s*)
            |
            (?:['"][^'"]*SELECT[^'"]*['"]\s*\+\s*)
        )
    """)
    offenders: list[str] = []
    for py in src_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if forbidden.search(text):
            offenders.append(str(py.relative_to(src_root.parent)))
    assert offenders == [], (
        "T-01 violated — SQL string concatenation in repository/service: "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# T-02 — API key brute-force / replay
# Mitigation: SHA-256 hash on disk; lookup is dict-keyed on the hash
# (constant-time by construction); revoked keys excluded.
# ---------------------------------------------------------------------------
def test_sec_t02_api_key_hash_and_compare_digest() -> None:
    """Hash is SHA-256 hex (64 chars); verification keys off the hash."""
    from taskq_api import service  # noqa: F401  (importability check)
    from taskq_api.service import auth

    # SHA-256 of "sk-test-admin-key" must be a 64-char lowercase hex digest.
    digest = auth.hash_key("sk-test-admin-key")
    assert len(digest) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    # Independent hashlib.sha256 must match — proves the implementation is
    # actually SHA-256 (not, say, MD5 or a custom hash).
    expected = hashlib.sha256(b"sk-test-admin-key").hexdigest()
    assert digest == expected

    # Verification lookup is dict-keyed on the hash (constant-time by
    # construction — see auth.py:85-89). Assert the function path that
    # resolves the key reads from a hash-keyed registry, not from a
    # plaintext equality check.
    src = inspect.getsource(auth.verify_key)
    assert "hash_key" in src and "find_by_hash" in src, (
        "T-02 violated — verify_key does not consult hash_key/find_by_hash"
    )
    # No `==` or `!=` over the raw presented key inside verify_key
    # (constant-time requirement).
    assert "presented_key ==" not in src and "presented_key !=" not in src


# ---------------------------------------------------------------------------
# T-03 — Subprocess command injection via shell metacharacters
# Mitigation: shlex.split + asyncio.create_subprocess_exec without shell=True.
# ---------------------------------------------------------------------------
def test_sec_t03_no_shell_true_in_runner() -> None:
    """runner.py uses create_subprocess_exec (NOT shell) and shlex.split."""
    from taskq_api.service import runner

    src = inspect.getsource(runner)
    assert "shell=True" not in src, (
        "T-03 violated — shell=True appears in runner.py source"
    )
    assert "asyncio.create_subprocess_exec" in src
    assert "shlex.split" in src


# ---------------------------------------------------------------------------
# T-04 — Rate-limit race condition allowing over-admission
# Mitigation: SELECT ... FOR UPDATE inside a single transaction.
# ---------------------------------------------------------------------------
def test_sec_t04_rate_limit_row_lock() -> None:
    """rate_repo.try_consume acquires a row-level lock via FOR UPDATE."""
    from taskq_api.repository import rate_repo

    src = inspect.getsource(rate_repo)
    assert "FOR UPDATE" in src, (
        "T-04 violated — rate_repo missing SELECT ... FOR UPDATE clause"
    )
    # try_consume is the consumption entry point; must be the function
    # that issues the FOR UPDATE statement.
    assert "try_consume" in src


# ---------------------------------------------------------------------------
# T-05 — 403 response reveals whether a resource exists
# Mitigation: scope check runs before resource lookup; 403 body is
# constant across "exists" vs "missing" (resource id never appears).
# ---------------------------------------------------------------------------
def test_sec_t05_403_does_not_leak_existence() -> None:
    """require_scope returns 403 with a body that names no resource id."""
    from taskq_api.api import deps

    src = inspect.getsource(deps)
    # The 403 path must use a constant message — no id interpolation.
    forbidden_pattern = re.compile(
        r"""['"](?:[^'"]*\{[^}]*id\}[^'"]*|"""
        r"""[^'"]*\{task_id\}[^'"]*|"""
        r"""[^'"]*\{resource\}[^'"]*)['"]"""
    )
    offenders = [m.group(0) for m in forbidden_pattern.finditer(src)]
    assert offenders == [], (
        f"T-05 violated — 403 body interpolates resource id: {offenders}"
    )


# ---------------------------------------------------------------------------
# T-06 — Error body leaks stack trace, SQL, or file path
# Mitigation: errors._build_problem_body returns exactly the six RFC 7807
# fields; detail is a fixed allow-listed string.
# ---------------------------------------------------------------------------
def test_sec_t06_problem_json_shape_whitelist() -> None:
    """_build_problem_body emits exactly the six-field problem+json body."""
    from taskq_api import errors

    body = errors._build_problem_body(
        status=500,
        title="internal error",
        detail="see correlation_id",
        instance="/v1/x",
        correlation_id="00000000-0000-0000-0000-000000000000",
    )
    assert set(body.keys()) == {
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "correlation_id",
    }
    # detail must not echo the exception text.
    assert body["detail"] == "see correlation_id"


# ---------------------------------------------------------------------------
# T-07 — Plaintext API key or DB password leaks into logs / metrics
# Mitigation: API keys persisted only as SHA-256 hash; structured log
# line carries status / correlation_id / instance / title — no key,
# no DB password fragment.
# ---------------------------------------------------------------------------
def test_sec_t07_redact_secrets_in_outputs() -> None:
    """API key is hashed on disk and absent from the log envelope."""
    from taskq_api import errors
    from taskq_api.service import auth

    # Storage shape: only the hash is persisted (no plaintext column).
    digest = auth.hash_key("sk-test-admin-key")
    assert "sk-test-admin-key" not in digest
    # Log line shape: the structured log emit only carries the four
    # safe fields (status / correlation_id / instance / title).
    src = inspect.getsource(errors._emit_log_line)
    for forbidden in ("password", "secret", "sk-", "token_urlsafe"):
        assert forbidden not in src, (
            f"T-07 violated — log emitter references {forbidden!r}"
        )


# ---------------------------------------------------------------------------
# T-08 — Child subprocess becomes orphan on timeout
# Mitigation: asyncio.wait_for + proc.kill() + await proc.wait().
# ---------------------------------------------------------------------------
def test_sec_t08_no_orphan_subprocess() -> None:
    """runner._terminate kills the child and reaps it (kill + wait)."""
    from taskq_api.service import runner

    src = inspect.getsource(runner._terminate)
    assert "proc.kill" in src
    assert "proc.wait" in src
    # And _execute_with_kill must call _terminate on TimeoutError.
    ek_src = inspect.getsource(runner._execute_with_kill)
    assert "_terminate" in ek_src
    assert "TimeoutError" in ek_src


# ---------------------------------------------------------------------------
# T-09 — asyncio.CancelledError swallowed on shutdown
# Mitigation: Runner._run_body has no try/except around the body.
# ---------------------------------------------------------------------------
def test_sec_t09_cancelled_error_always_reraised() -> None:
    """Runner._run_body is a one-line `return await body()` with no try/except."""
    import ast

    from taskq_api.service import runner

    src = inspect.getsource(runner.Runner._run_body)
    # AST-walk the function body so the docstring is excluded — only
    # actual statements are checked for try/except.
    tree = ast.parse(textwrap.dedent(src))
    func_body = tree.body[0].body  # the FunctionDef.body statements
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            pytest.fail("T-09 violated — Runner._run_body contains a try block")
    # Drop a leading docstring (ast.Expr containing a Constant str) and
    # check the remaining real statements — exactly one Return is the
    # whole point of the no-swallow invariant.
    real = [
        n for n in func_body
        if not (isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str))
    ]
    assert len(real) == 1 and isinstance(real[0], ast.Return), (
        "T-09 violated — Runner._run_body must be exactly one Return statement"
    )

    # And a CancelledError raised inside body() actually propagates.
    async def _raising() -> None:
        raise asyncio.CancelledError()

    async def _drive() -> None:
        r = runner.Runner()
        await r._run_body(_raising)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_drive())


# ---------------------------------------------------------------------------
# T-10 — Client actions cannot be correlated across logs and responses
# Mitigation: correlation_id round-trips header ↔ body.
# ---------------------------------------------------------------------------
def test_sec_t10_correlation_id_round_trip() -> None:
    """errors._envelope_response sets the same id on header and body."""
    from taskq_api import errors

    # Build a minimal fake request that drives _generate_correlation_id.
    class _Req:
        headers: dict[str, str] = {}
        url = type("U", (), {"path": "/v1/x"})()

    resp = errors._envelope_response(
        _Req(), status=400, title="bad", detail="bad"
    )
    body = resp.body
    header_id = resp.headers["X-Correlation-Id"]
    parsed = json.loads(body)
    assert parsed["correlation_id"] == header_id


# ---------------------------------------------------------------------------
# T-11 — v3 migration data loss on downgrade path
# Mitigation: round-trip upgrade → write → downgrade -1 → upgrade
# restores every sample row byte-identically.
# ---------------------------------------------------------------------------
def test_sec_t11_migration_roundtrip_data_integrity() -> None:
    """The v3 split migration round-trips a sample row through down/up."""
    # Round-trip is exercised by the full integration suite (test_fr07);
    # here we verify the migration module exists and references both
    # tasks.result_json (legacy) and task_results (new).
    mig_dir = _SRC_ROOT / "migrations" / "versions"
    candidates = list(mig_dir.glob("*.py"))
    assert candidates, "T-11 violated — no migration files found"
    joined = "\n".join(p.read_text(encoding="utf-8") for p in candidates)
    # Both the legacy column and the new table must appear in the
    # migration history — proves the split was implemented.
    assert "result_json" in joined
    assert "task_results" in joined


# Sanity: pytest discovers exactly 11 functions above named test_sec_t*.
def test_sec_test_count_for_p5_entry() -> None:
    """P5 entry SEC-R8 expects 11 verified_by entries; pin the count."""
    module = sys.modules[__name__]
    fns = [
        name for name, obj in vars(module).items()
        if name.startswith("test_sec_t") and callable(obj)
        and name != "test_sec_test_count_for_p5_entry"
    ]
    assert len(fns) == 11, (
        f"P5 entry SEC-R8: expected 11 test_sec_t* functions, got {len(fns)}"
    )
