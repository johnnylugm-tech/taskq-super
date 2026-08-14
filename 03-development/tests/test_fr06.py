"""FR-06 — Persistence layer + transaction boundaries.

[FR-06] Tests covering the 4 acceptance criteria enumerated in
`02-architecture/TEST_SPEC.md` (FR-06 table, cases #1..#4). Each TEST_SPEC
sub-assertion predicate is mirrored VERBATIM (`hit_count == "0"`,
`session_count_per_request == "1"`, `rollback_on_exception == "true"`,
`pool_pre_ping == "true"`) using the spec's own variable names, so the P3
MIRROR gate can align every spec rule to a real assertion.

The 4 cases from TEST_SPEC (verbatim):
  1. test_fr06_no_shell_true_eval_or_exec_in_source
  2. test_fr06_no_fstring_or_concat_sql_composition
  3. test_fr06_one_session_per_request_lifecycle
  4. test_fr06_engine_uses_pool_size_and_pool_pre_ping

Static tests (#1, #2) walk the source tree under `03-development/src/`
and assert forbidden patterns are absent (AC-6.1 / AC-6.2). Unit tests
(#3, #4) target `taskq_api.repository.session` — the SAB-declared module
for FR-06 — and assert that exactly one SQLAlchemy `Session` is opened
per request lifecycle (AC-6.3) and that the engine is constructed with
`pool_size=TASKQ_DB_POOL_SIZE` and `pool_pre_ping=True` (AC-6.5).

Shape notes:

* Test functions are SYNCHRONOUS. Static scans read files via pathlib
  and assert count==0. Unit tests patch `sqlalchemy.create_engine` and
  a Session-shaped mock to verify lifecycle and engine kwargs.
* The SAB-declared module for FR-06 is `taskq_api.repository.session`
  (already on disk). The existing `transactional()` context manager
  yields a `ProductionStore` placeholder, NOT a real SQLAlchemy
  `Session` — GREEN must wire it to `SessionLocal()` (the session
  factory) and ensure `session.rollback()` is called on exception.
* The in-process unit tests below exercise the SAME invariants the
  production wiring will satisfy. pytest-cov can measure coverage of
  these in-process calls (it CANNOT measure subprocess coverage).
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Standard top-level imports. Not wrapped in try/except: a missing module
# must surface as a pytest Collection Error, which is the valid RED.
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_SRC_ROOT: Path = Path(__file__).resolve().parent.parent / "src"


# ---------------------------------------------------------------------------
# 1. Static grep — no shell=True / eval / exec in source (AC-6.1)
# ---------------------------------------------------------------------------


def test_fr06_no_shell_true_eval_or_exec_in_source() -> None:
    """AC-6.1: `shell=True`, `eval(`, `exec(` must be absent from the source tree.

    FR06-zero-shell-hits: `hit_count == "0"` (TEST_SPEC sub-assertion).
    NFR-02 (security): shell/eval/exec are forbidden everywhere.
    """
    # NFR-02 (security) — AC-N2.1 — shell/eval/exec ban
    # NFR-06 (architecture_constraints) — AC-N6.3 — forbidden contract covers subprocess paths

    # Negative lookbehind so `create_subprocess_exec(` does not match
    # `exec(` (the function name we use everywhere is allowed).
    forbidden_pattern = re.compile(
        r"shell=True|(?<![\w])eval\((?!\s*[\"'])|(?<![\w])exec\("
    )

    hits: list[tuple[Path, int, str]] = []
    for py_file in _SRC_ROOT.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if forbidden_pattern.search(line):
                hits.append((py_file, line_number, line.strip()))

    hit_count = str(len(hits))
    assert hit_count == "0", (
        f"Forbidden shell/eval/exec patterns found in source "
        f"(hit_count={hit_count}): {hits}"
    )


# ---------------------------------------------------------------------------
# 2. Static grep — no f-string / % / + SQL composition (AC-6.2)
# ---------------------------------------------------------------------------


def test_fr06_no_fstring_or_concat_sql_composition() -> None:
    """AC-6.2: no f-string / `%` / `+` composed SQL fragments in source.

    FR06-zero-sql-concat: `hit_count == "0"` (TEST_SPEC sub-assertion).
    NFR-02 (security): string-concatenated SQL is forbidden; ORM or
    parameterised queries only.
    """
    # NFR-02 (security) — AC-N2.2 — SQL concatenation ban
    # NFR-06 (architecture_constraints) — repository-only SQL import

    # Patterns that compose SQL via f-string, %-formatting, or + concat.
    # Each pattern requires an SQL keyword (SELECT/INSERT/UPDATE/DELETE)
    # directly bound to a composition operator (f-string prefix, `%`,
    # or `+`) attached to an identifier — so docstrings, comments, and
    # static SQL strings passed to `text(...)` don't trigger a false
    # positive. The SQL keyword group MUST be wrapped in (?:...) so the
    # outer alternation `|` does not split the surrounding regex
    # (otherwise `[^"']*SELECT\b|INSERT\s+INTO|...` matches just
    # `"SELECT` because the first alternative absorbs the prefix).
    sql_keyword = r"(?:SELECT\b|INSERT\s+INTO|UPDATE\s+\w+|DELETE\s+FROM)"
    # f-string containing SQL keyword
    fstring_pattern = re.compile(rf"(?i)\bf[\"'][^\"']*{sql_keyword}")
    # "%" / "%(" formatting of a SQL string on the same physical line
    pct_pattern = re.compile(rf"(?i)[\"'][^\"']*{sql_keyword}[^\"']*[\"']\s*%")
    # explicit + concat of a SQL string with an identifier on either side
    plus_left = re.compile(
        rf"(?i)[\"'][^\"']*{sql_keyword}[^\"']*[\"']\s*\+\s*[A-Za-z_]\w*"
    )
    plus_right = re.compile(
        rf"(?i)\b[A-Za-z_]\w*\s*\+\s*[\"'][^\"']*{sql_keyword}"
    )

    hits: list[tuple[Path, int, str]] = []
    for py_file in _SRC_ROOT.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # Track multi-line triple-quoted string blocks (docstrings) and
        # implicit adjacent-string-literal concatenation across lines so
        # neither is reported as a SQL composition.
        in_triple: bool = False
        triple_quote: str = ""
        prev_line_ended_with_string: bool = False
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            # Skip pure comment lines — comments are not executable code.
            if stripped.startswith("#"):
                continue
            # Track triple-quoted string state so docstrings don't count.
            if not in_triple:
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    triple_quote = stripped[:3]
                    if line.count(triple_quote) >= 2:
                        # docstring opens and closes on the same line
                        pass
                    else:
                        in_triple = True
                    continue
            else:
                if triple_quote in line:
                    in_triple = False
                    triple_quote = ""
                continue
            # Skip lines that continue an implicit adjacent-string-literal
            # concatenation from the previous line — those are static SQL,
            # not string composition by f-string / `%` / `+`.
            if prev_line_ended_with_string and (
                stripped.startswith('"') or stripped.startswith("'")
            ):
                prev_line_ended_with_string = False
                # Track whether THIS line also ends with a string literal.
                last_quote = max(line.rfind('"'), line.rfind("'"))
                if last_quote > line.rfind("#"):
                    prev_line_ended_with_string = True
                continue
            prev_line_ended_with_string = False
            # Detect explicit single-line SQL string composition.
            if (
                fstring_pattern.search(line)
                or pct_pattern.search(line)
                or plus_left.search(line)
                or plus_right.search(line)
            ):
                hits.append((py_file, line_number, line.strip()))
            # Track whether this line ends with an un-commented string
            # literal (so the NEXT line is treated as a continuation).
            last_quote = max(line.rfind('"'), line.rfind("'"))
            if last_quote > line.rfind("#"):
                tail = line[last_quote + 1 :].rstrip()
                if tail == "" or tail == "+":
                    prev_line_ended_with_string = True

    hit_count = str(len(hits))
    assert hit_count == "0", (
        f"Forbidden f-string / % / + SQL composition found in source "
        f"(hit_count={hit_count}): {hits}"
    )


# ---------------------------------------------------------------------------
# 3. One Session per request lifecycle, rollback on exception (AC-6.3)
# ---------------------------------------------------------------------------


def test_fr06_one_session_per_request_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-6.3: exactly one Session is opened per request; exception triggers rollback.

    FR06-one-session: `session_count_per_request == "1"` (TEST_SPEC sub-assertion).
    FR06-rollback-on-error: `rollback_on_exception == "true"` (TEST_SPEC sub-assertion).
    NFR-03 (error_handling): per-request transaction boundary; rollback on exception.
    """
    # NFR-03 (error_handling) — AC-N3.1 — context-manager transaction boundary

    # Force a clean re-import so any module-level engine/session factory
    # construction happens under our patched symbols.
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("taskq_api.repository"):
            del sys.modules[mod_name]

    # GREEN TODO: `taskq_api.repository.session` must expose
    #   `transactional() -> Iterator[Session]` such that:
    #     * exactly one `Session` is opened per `with transactional():` block
    #     * on normal exit: `session.close()` is called (no rollback)
    #     * on exception inside the with-block: `session.rollback()` is called
    #       and the exception is re-raised
    from taskq_api.repository import session as session_module  # noqa: E402

    # Case A — happy path: one Session opened, closed on normal exit.
    happy_session = MagicMock(spec=Session)
    happy_factory_calls: list[object] = []

    def happy_session_local() -> Session:
        happy_factory_calls.append(happy_session)
        return happy_session

    monkeypatch.setattr(session_module, "SessionLocal", happy_session_local, raising=False)
    # GREEN TODO: `taskq_api.repository.session.SessionLocal` must be a
    # zero-arg callable returning a SQLAlchemy `Session`.

    with session_module.transactional() as session:
        assert session is happy_session

    session_count_per_request = str(len(happy_factory_calls))
    assert session_count_per_request == "1", (
        f"Expected exactly one Session per request lifecycle, got "
        f"{session_count_per_request}"
    )
    assert happy_session.close.called, "Session.close() must be called on normal exit"

    # Case B — exception path: rollback called and exception re-raised.
    error_session = MagicMock(spec=Session)
    error_factory_calls: list[object] = []

    def error_session_local() -> Session:
        error_factory_calls.append(error_session)
        return error_session

    monkeypatch.setattr(session_module, "SessionLocal", error_session_local, raising=False)

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        with session_module.transactional():
            raise _Boom("simulated handler failure")

    rollback_on_exception = "true" if error_session.rollback.called else "false"
    assert rollback_on_exception == "true", (
        "session.rollback() must be called when the handler raises"
    )
    assert len(error_factory_calls) == 1, (
        "Exactly one Session must be opened even on the exception path"
    )


# ---------------------------------------------------------------------------
# 4. Engine uses pool_size=TASKQ_DB_POOL_SIZE and pool_pre_ping=True (AC-6.5)
# ---------------------------------------------------------------------------


def test_fr06_engine_uses_pool_size_and_pool_pre_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-6.5: SQLAlchemy engine built with pool_size + pool_pre_ping from env.

    FR06-pool-pre-ping-set: `pool_pre_ping == "true"` (TEST_SPEC sub-assertion).
    NFR-01 (performance): connection pool sized for predictable access.
    """
    # NFR-01 (performance) — pool sized for predictable connection access

    import sqlalchemy

    captured_kwargs: dict[str, object] = {}
    real_create_engine = sqlalchemy.create_engine

    def capturing_create_engine(url: object, **kwargs: object) -> object:
        captured_kwargs.update(kwargs)
        # Build the real engine with the captured kwargs so that any code
        # path that inspects the engine after construction still works.
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(sqlalchemy, "create_engine", capturing_create_engine)

    # Reload the session module under the patched create_engine so any
    # module-level engine construction picks up our capturing function.
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("taskq_api.repository"):
            del sys.modules[mod_name]
    session_module = importlib.import_module("taskq_api.repository.session")

    # GREEN TODO: `taskq_api.repository.session` must construct a SQLAlchemy
    # engine via `sqlalchemy.create_engine(...)` with kwargs
    #   pool_size=int(os.environ.get("TASKQ_DB_POOL_SIZE", "5"))
    #   pool_pre_ping=True
    # The cleanest place is a module-level call captured by this test;
    # alternatively expose `get_engine()` that builds on demand.

    # If the module didn't trigger create_engine at import time, exercise
    # the public accessor if available — otherwise the assertion below
    # will catch the missing wiring.
    if not captured_kwargs and hasattr(session_module, "get_engine"):
        session_module.get_engine()  # type: ignore[attr-defined]

    expected_pool_size = int(os.environ.get("TASKQ_DB_POOL_SIZE", "5"))
    assert captured_kwargs.get("pool_size") == expected_pool_size, (
        f"create_engine must be called with pool_size={expected_pool_size}; "
        f"got pool_size={captured_kwargs.get('pool_size')!r}; "
        f"all kwargs={captured_kwargs!r}"
    )

    pool_pre_ping = captured_kwargs.get("pool_pre_ping")
    pool_pre_ping_flag = "true" if pool_pre_ping is True else "false"
    assert pool_pre_ping_flag == "true", (
        f"create_engine must be called with pool_pre_ping=True; "
        f"got pool_pre_ping={pool_pre_ping!r}; "
        f"all kwargs={captured_kwargs!r}"
    )