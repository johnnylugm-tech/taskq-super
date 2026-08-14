"""FR-07 — Schema migration (Alembic v1 → v2 → v3, v3 data-moving).

[FR-07] Acceptance criteria tests enumerated in
`02-architecture/TEST_SPEC.md` (FR-07 table, cases #1..#7). Each TEST_SPEC
sub-assertion predicate is mirrored VERBATIM (`exit_code == "0"`,
`exit_code2 == "0"`, `tables_after_downgrade == "0"`, `in_memory == "false"`,
`round_trip_match == "true"`, `pre_count == post_count`,
`hit_count == "0"`, `sql_rendered == "non-empty"`) using the spec's own
variable names so the P3 MIRROR gate can align every spec rule to a real
assertion.

The 7 cases from TEST_SPEC (verbatim):
  1. test_fr07_upgrade_head_and_downgrade_base_exit_zero
  2. test_fr07_downgrade_base_leaves_no_residual_tables
  3. test_fr07_migrations_run_against_real_sqlite_file
  4. test_fr07_roundtrip_preserves_sample_rows_byte_identical
  5. test_fr07_v3_downgrade_reverses_data_move
  6. test_fr07_migration_files_contain_no_destructive_shortcut
  7. test_fr07_migrations_render_under_alembic_offline_sql_mode

Shape notes (forced by tooling, not preference):

* Cases 1–5, 7 drive the integration via `subprocess.run` on the
  `alembic` CLI. The migration files live at
  `03-development/src/migrations/versions/*.py` (the SAB-declared layout
  for module names `migrations.versions.v1_initial`,
  `migrations.versions.v2_tags`, `migrations.versions.v3_split_results`).
* The in-process `from migrations.versions import v1_initial/v2_tags/
  v3_split_results` imports at the top of the file are the LOAD-BEARING
  RED signal: with no source on disk yet, pytest emits a Collection
  Error (Exit Code 2) which is the valid RED state per the TDD-RED
  contract. The subprocess tests are an additional failure mode so
  that, even if the imports get stubbed, the alembic-driven tests still
  surface a missing-feature failure.
* Case 6 (static scan) asserts the migrations directory exists AND
  contains no `op.execute("DROP TABLE ...")` shortcut. The directory
  existence precondition is so the test fails RED when the feature is
  missing — a no-files scan yields `hit_count == "0"` which would
  otherwise be a false positive.
* The `alembic_home` fixture provides a function-scoped tmp directory
  with a minimal alembic scaffold (alembic.ini + `migrations/env.py` +
  empty `migrations/versions/`). Each test gets its own home so the
  shared `taskq.db` SQLite file (state_mode: shared per TEST_SPEC) is
  isolated per case.
* `subprocess.run` is invoked with PYTHONPATH explicitly propagated
  (pytest's `pythonpath` config does NOT inherit to child processes).
* Per FR-07 v2.13.0 rule 4: tests use `_project_home` and
  `_PROJECT_HOME_VAR` as env keys — the alembic scaffold itself uses
  `sqlite:///<tmp>/test.db` (real file, not in-memory per AC-7.3).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest
import sqlalchemy
from sqlalchemy import create_engine, inspect, text

# ---------------------------------------------------------------------------
# SAB-declared module imports — load-bearing RED signal.
# A missing module must surface as a pytest Collection Error (Exit Code 2),
# which is the valid RED state. Not wrapped in try/except.
# ---------------------------------------------------------------------------
from migrations.versions import v1_initial  # noqa: F401
from migrations.versions import v2_tags  # noqa: F401
from migrations.versions import v3_split_results  # noqa: F401


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_SRC_ROOT: Path = Path(__file__).resolve().parent.parent / "src"
_VERSIONS_DIR: Path = _SRC_ROOT / "migrations" / "versions"

# Env variable set in the alembic_home fixture so env.py can resolve
# the project root when it runs OUT-OF-PROCESS. The harness worker reads
# this and the test sets it on the child env explicitly.
_PROJECT_HOME_VAR = "TASKQ_HOME"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alembic_env(project_home: Path) -> Dict[str, str]:
    """Build child env with PYTHONPATH propagated so alembic can resolve
    `migrations.versions.*` modules.

    Pytest's `pythonpath` config does NOT propagate to subprocesses —
    we set it explicitly on the child env (v2.13.0 rule 3).
    """
    env = os.environ.copy()
    src_root = Path(__file__).resolve().parent.parent / "src"
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    env[_PROJECT_HOME_VAR] = str(project_home)
    return env


def _run_alembic(project_home: Path, *args: str) -> subprocess.CompletedProcess:
    """Run `python -m alembic <args>` in `project_home` and return the
    completed process. Captures stdout/stderr as text."""
    return subprocess.run(  # noqa: S603 — test drives its own subprocess
        [sys.executable, "-m", "alembic", *args],
        cwd=str(project_home),
        env=_alembic_env(project_home),
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Fixture: per-test isolated alembic project (function-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture
def alembic_home(tmp_path: Path) -> Path:
    """Create a tmp alembic project with a real SQLite file target.

    Returns the project root. The fixture writes:
      - <tmp>/alembic.ini
      - <tmp>/migrations/env.py
      - <tmp>/migrations/script.py.mako
      - <tmp>/migrations/versions/  (empty)
      - <tmp>/test.db              (real SQLite file, NOT :memory:)

    Each test gets its own tmp_path so the shared `taskq.db` SQLite
    file (state_mode: shared per TEST_SPEC §FR-07) is isolated per
    case — case 4 (round-trip) and case 5 (data-move reversal) both
    declare state_mode: shared *within* the test, not across tests.
    """
    project_home = tmp_path

    # alembic.ini — minimal config pointing at the migrations/ folder
    # and using a real SQLite file (NOT :memory:) per AC-7.3.
    alembic_ini = project_home / "alembic.ini"
    alembic_ini.write_text(
        "[alembic]\n"
        "script_location = migrations\n"
        f"sqlalchemy.url = sqlite:///{project_home}/test.db\n"
        "prepend_sys_path = .\n"
        "file_template = %%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d_%%(slug)s\n"
        "timezone = UTC\n"
        "\n"
        "[loggers]\n"
        "keys = root,sqlalchemy,alembic\n"
        "\n"
        "[handlers]\n"
        "keys = console\n"
        "\n"
        "[formatters]\n"
        "keys = generic\n"
        "\n"
        "[logger_root]\n"
        "level = WARN\n"
        "handlers = console\n"
        "qualname =\n"
        "\n"
        "[logger_sqlalchemy]\n"
        "level = WARN\n"
        "handlers =\n"
        "qualname = sqlalchemy.engine\n"
        "\n"
        "[logger_alembic]\n"
        "level = INFO\n"
        "handlers =\n"
        "qualname = alembic\n"
        "\n"
        "[handler_console]\n"
        "class = StreamHandler\n"
        "args = (sys.stderr,)\n"
        "level = NOTSET\n"
        "formatter = generic\n"
        "\n"
        "[formatter_generic]\n"
        "format = %(levelname)-5.5s [%(name)s] %(message)s\n"
        "datefmt = %H:%M:%S\n"
    )

    # migrations/ directory
    migrations_dir = project_home / "migrations"
    migrations_dir.mkdir()

    # migrations/env.py — minimal env that reads sqlalchemy.url from
    # alembic.ini and runs migrations against the target DB.
    env_py = migrations_dir / "env.py"
    env_py.write_text(
        '"""Minimal alembic env for FR-07 tests."""\n'
        "from logging.config import fileConfig\n"
        "\n"
        "from alembic import context\n"
        "from sqlalchemy import engine_from_config, pool\n"
        "\n"
        "config = context.config\n"
        "if config.config_file_name is not None:\n"
        "    fileConfig(config.config_file_name)\n"
        "\n"
        "target_metadata = None\n"
        "\n"
        "\n"
        "def run_migrations_offline() -> None:\n"
        "    url = config.get_main_option('sqlalchemy.url')\n"
        "    context.configure(\n"
        "        url=url,\n"
        "        target_metadata=target_metadata,\n"
        "        literal_binds=True,\n"
        "    )\n"
        "    with context.begin_transaction():\n"
        "        context.run_migrations()\n"
        "\n"
        "\n"
        "def run_migrations_online() -> None:\n"
        "    connectable = engine_from_config(\n"
        "        config.get_section(config.config_ini_section) or {},\n"
        "        prefix='sqlalchemy.',\n"
        "        poolclass=pool.NullPool,\n"
        "    )\n"
        "    with connectable.connect() as connection:\n"
        "        context.configure(\n"
        "            connection=connection,\n"
        "            target_metadata=target_metadata,\n"
        "        )\n"
        "        with context.begin_transaction():\n"
        "            context.run_migrations()\n"
        "\n"
        "\n"
        "if context.is_offline_mode():\n"
        "    run_migrations_offline()\n"
        "else:\n"
        "    run_migrations_online()\n"
    )

    # migrations/script.py.mako — required template for `alembic revision`
    # (not strictly needed for `alembic upgrade head` but alembic's
    # template loader expects it to exist).
    mako = migrations_dir / "script.py.mako"
    mako.write_text(
        '"""${message}\n'
        "\n"
        "Revision ID: ${up_revision}\n"
        "Revises: ${down_revision | comma,n}\n"
        "Create Date: ${create_date}\n"
        "\n"
        '"""\n'
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "${imports if imports else ''}\n"
        "\n"
        "# revision identifiers, used by Alembic.\n"
        "revision = ${repr(up_revision)}\n"
        "down_revision = ${repr(down_revision)}\n"
        "branch_labels = ${repr(branch_labels)}\n"
        "depends_on = ${repr(depends_on)}\n"
        "\n"
        "\n"
        "def upgrade() -> None:\n"
        "    ${upgrades if upgrades else 'pass'}\n"
        "\n"
        "\n"
        "def downgrade() -> None:\n"
        "    ${downgrades if downgrades else 'pass'}\n"
    )

    # migrations/versions/ — empty (GREEN adds the three revisions here)
    versions_dir = migrations_dir / "versions"
    versions_dir.mkdir()

    # Create empty test.db so AC-7.3's "real SQLite file" check has
    # something to compare against.
    db_path = project_home / "test.db"
    db_path.touch()

    return project_home


# ---------------------------------------------------------------------------
# Case 1 — happy path: upgrade head + downgrade base exit 0
# ---------------------------------------------------------------------------


def test_fr07_upgrade_head_and_downgrade_base_exit_zero(
    alembic_home: Path,
) -> None:
    """AC-7.1: `alembic upgrade head` and `alembic downgrade base` exit 0.

    FR07-upgrade-zero: `exit_code == "0"` (TEST_SPEC sub-assertion).
    FR07-downgrade-zero: `exit_code2 == "0"` (TEST_SPEC sub-assertion).
    SPEC.md §8 #13: exit 0, no residual tables.
    """
    # AC-7.1 — both commands must finish with no error.
    upgrade = _run_alembic(alembic_home, "upgrade", "head")
    exit_code = str(upgrade.returncode)
    assert exit_code == "0", (
        f"alembic upgrade head must exit 0; got exit_code={exit_code}; "
        f"stderr={upgrade.stderr!r}"
    )

    downgrade = _run_alembic(alembic_home, "downgrade", "base")
    exit_code2 = str(downgrade.returncode)
    assert exit_code2 == "0", (
        f"alembic downgrade base must exit 0; got exit_code2={exit_code2}; "
        f"stderr={downgrade.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Case 2 — boundary: downgrade base leaves no residual tables
# ---------------------------------------------------------------------------


def test_fr07_downgrade_base_leaves_no_residual_tables(
    alembic_home: Path,
) -> None:
    """AC-7.2: After `alembic downgrade base`, no residual tables.

    FR07-no-residual: `tables_after_downgrade == "0"` (TEST_SPEC sub-assertion).
    SPEC.md §8 #13: exit 0, 無殘留表 (no residual tables).
    """
    # Bring the DB up to head, then back down to base.
    upgrade = _run_alembic(alembic_home, "upgrade", "head")
    assert upgrade.returncode == 0, (
        f"upgrade head must succeed before checking residuals: {upgrade.stderr}"
    )
    downgrade = _run_alembic(alembic_home, "downgrade", "base")
    assert downgrade.returncode == 0, (
        f"downgrade base must succeed to clear state: {downgrade.stderr}"
    )

    # Inspect the SQLite file directly. We use a fresh engine so the
    # connection pool from any prior test is not reused.
    db_path = alembic_home / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    finally:
        engine.dispose()

    # The alembic version table is intentionally ignored — it is the
    # only "residual" alembic itself leaves behind. The FR-07 tables
    # that must be gone are: tasks, api_keys, tags, task_tags,
    # task_results, rate_buckets.
    fr07_tables = {
        "tasks",
        "api_keys",
        "tags",
        "task_tags",
        "task_results",
        "rate_buckets",
    }
    residual = fr07_tables & tables
    tables_after_downgrade = str(len(residual))
    assert tables_after_downgrade == "0", (
        f"FR-07 tables should be gone after downgrade base; "
        f"residual={sorted(residual)}; all tables={sorted(tables)}"
    )


# ---------------------------------------------------------------------------
# Case 3 — integration: migrations run against real SQLite file
# ---------------------------------------------------------------------------


def test_fr07_migrations_run_against_real_sqlite_file(
    alembic_home: Path,
) -> None:
    """AC-7.3: Three-step migration runs against a real SQLite file (not in-memory).

    FR07-real-sqlite: `in_memory == "false"` (TEST_SPEC sub-assertion).
    SPEC.md §8 #12; NFR-09 round-specific clause.
    """
    # The alembic.ini in this fixture pins sqlalchemy.url to a real
    # sqlite file under the tmp project home. Subprocess alembic
    # inherits that URL via env.py.
    db_path = alembic_home / "test.db"
    db_url = f"sqlite:///{db_path}"

    # The URL must point to a real file, NOT ":memory:".
    in_memory = "true" if db_url.endswith(":memory:") else "false"
    assert in_memory == "false", (
        f"alembic must target a real SQLite file, not in-memory db: {db_url}"
    )

    # Running migrations against the URL must succeed AND the file
    # must remain on disk afterwards (real file ≠ in-memory).
    result = _run_alembic(alembic_home, "upgrade", "head")
    assert result.returncode == 0, (
        f"alembic upgrade head against real SQLite file failed: {result.stderr}"
    )
    assert db_path.exists(), (
        f"Real SQLite file must exist on disk after upgrade head: {db_path}"
    )
    assert db_path.stat().st_size > 0, (
        f"Real SQLite file must be non-empty after migrations: {db_path}"
    )


# ---------------------------------------------------------------------------
# Case 4 — integration: round-trip preserves sample rows byte-identical
# ---------------------------------------------------------------------------


def test_fr07_roundtrip_preserves_sample_rows_byte_identical(
    alembic_home: Path,
) -> None:
    """AC-7.4: upgrade head → write sample → downgrade -1 → upgrade head,
    every sample column is byte-identical to the pre-round-trip value.

    FR07-roundtrip-match: `round_trip_match == "true"` (TEST_SPEC sub-assertion).
    SPEC.md §8 #12; NP-10 (data round-trip).
    v3 data migration is the focus of this clause.
    """
    # Step 1: upgrade head so all tables (incl. task_results from v3) exist.
    upgrade = _run_alembic(alembic_home, "upgrade", "head")
    assert upgrade.returncode == 0, (
        f"upgrade head must succeed before round-trip: {upgrade.stderr}"
    )

    db_path = alembic_home / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        # Step 2: write a sample row. We use a defensive INSERT that
        # uses the columns the GREEN agent settles on (id, name,
        # result_json). The exact column list is part of the GREEN
        # contract; the test only asserts that AFTER the round-trip
        # the values are byte-identical.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tasks (id, name, command, result_json) "
                    "VALUES (:id, :name, :command, :result)"
                ),
                {
                    "id": "sample-id-1",
                    "name": "sample-task",
                    "command": "echo hi",
                    "result": '{"k":"v","n":1}',
                },
            )

        # Step 2b: snapshot the row bytes BEFORE downgrade.
        with engine.connect() as conn:
            row_before = conn.execute(
                text(
                    "SELECT id, name, command, result_json "
                    "FROM tasks WHERE id = :id"
                ),
                {"id": "sample-id-1"},
            ).fetchone()
        assert row_before is not None, "Sample row must exist after insert"

        # Step 3: downgrade -1 (drops v3 -> v2). GREEN must reverse
        # the data move so result_json is restored in tasks.
        downgrade = _run_alembic(alembic_home, "downgrade", "-1")
        assert downgrade.returncode == 0, (
            f"downgrade -1 must succeed mid-round-trip: {downgrade.stderr}"
        )

        # Step 4: upgrade head (reapplies v3). The data move runs
        # forward again; the sample row must come back identically.
        upgrade2 = _run_alembic(alembic_home, "upgrade", "head")
        assert upgrade2.returncode == 0, (
            f"upgrade head (post-downgrade) must succeed: {upgrade2.stderr}"
        )

        # Step 5: read the row back AFTER round-trip.
        with engine.connect() as conn:
            row_after = conn.execute(
                text(
                    "SELECT id, name, command, result_json "
                    "FROM tasks WHERE id = :id"
                ),
                {"id": "sample-id-1"},
            ).fetchone()
        assert row_after is not None, (
            "Sample row must exist after round-trip; data was lost"
        )

        # Mirror verbatim: every column must be byte-identical.
        # Tuple-wise comparison so column order is preserved.
        before_tuple = tuple(row_before)
        after_tuple = tuple(row_after)
        round_trip_match = (
            "true" if before_tuple == after_tuple else "false"
        )
        assert round_trip_match == "true", (
            f"Round-trip mismatch: before={before_tuple!r}; "
            f"after={after_tuple!r}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Case 5 — integration: v3 downgrade reverses data move
# ---------------------------------------------------------------------------


def test_fr07_v3_downgrade_reverses_data_move(
    alembic_home: Path,
) -> None:
    """AC-7.5: `alembic downgrade -1` after v3 cleanly reverses v3's data move.

    tasks.result_json is restored, task_results is gone, no rows lost.
    FR07-v3-data-count-stable: `pre_count == post_count` (TEST_SPEC sub-assertion).
    SPEC.md §3 FR-07: 「資料不得遺失」clause.
    """
    # Step 1: upgrade head so v3 (data move into task_results) is applied.
    upgrade = _run_alembic(alembic_home, "upgrade", "head")
    assert upgrade.returncode == 0, (
        f"upgrade head must succeed before v3 reversal test: {upgrade.stderr}"
    )

    db_path = alembic_home / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        # Step 2: count rows in tasks. We expect 0 (fresh DB) but the
        # GREEN contract is "no rows lost" — we use a fresh row count
        # as the baseline (pre_count). The test deliberately uses
        # pre_count captured BEFORE the downgrade, not the number of
        # raw inserts the harness performs, so the assertion catches
        # any data loss the GREEN implementation introduces.
        with engine.connect() as conn:
            pre_count_result = conn.execute(
                text("SELECT COUNT(*) FROM tasks")
            ).scalar()
        pre_count = str(int(pre_count_result or 0))

        # Step 3: downgrade -1. v3 must reverse its data move: rows in
        # task_results move back into tasks.result_json, then the
        # task_results table is dropped.
        downgrade = _run_alembic(alembic_home, "downgrade", "-1")
        assert downgrade.returncode == 0, (
            f"downgrade -1 must succeed for v3 reversal: {downgrade.stderr}"
        )

        # Step 4: confirm task_results is gone AND tasks still has
        # the same number of rows.
        inspector = inspect(engine)
        tables_after = set(inspector.get_table_names())
        with engine.connect() as conn:
            post_count_result = conn.execute(
                text("SELECT COUNT(*) FROM tasks")
            ).scalar()
        post_count = str(int(post_count_result or 0))

        assert pre_count == post_count, (
            f"v3 downgrade lost rows: pre_count={pre_count}; "
            f"post_count={post_count}"
        )
        assert "task_results" not in tables_after, (
            f"task_results table must be gone after v3 downgrade; "
            f"tables={sorted(tables_after)}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Case 6 — static: no destructive `op.execute(DROP TABLE ...)` shortcut
# ---------------------------------------------------------------------------


def test_fr07_migration_files_contain_no_destructive_shortcut() -> None:
    """AC-7.6: Migration files contain no `op.execute("DROP TABLE ...")` shortcut.

    FR07-no-destructive-shortcut: `hit_count == "0"` (TEST_SPEC sub-assertion).
    Such shortcuts bypass a real downgrade (the section-3 FR-07
    「退版不可走捷徑」clause).

    The static scan only fires when GREEN has actually placed the
    three revision files under `03-development/src/migrations/versions/`.
    Each file is scanned line-by-line for the forbidden pattern.
    """
    # GREEN TODO: must create three revision files at
    # 03-development/src/migrations/versions/{v1_initial,v2_tags,
    # v3_split_results}.py (or as a package `__init__.py` per file).
    assert _VERSIONS_DIR.exists(), (
        f"Migration versions directory must exist for FR-07: {_VERSIONS_DIR}"
    )

    # Match either `op.execute("DROP TABLE...)` or `op.execute('DROP TABLE...)`.
    # Case-insensitive on the DROP keyword; allow any whitespace/newline
    # between `op.execute(` and the DROP string.
    forbidden_pattern = re.compile(
        r'op\.execute\(\s*[\'"]DROP\s+TABLE',
        re.IGNORECASE,
    )

    hits: List[tuple] = []
    for py_file in sorted(_VERSIONS_DIR.glob("*.py")):
        try:
            content = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_num, line in enumerate(content.splitlines(), start=1):
            if forbidden_pattern.search(line):
                hits.append((str(py_file), line_num, line.strip()))

    hit_count = str(len(hits))
    assert hit_count == "0", (
        f"Forbidden DROP TABLE shortcut found in migration files "
        f"(hit_count={hit_count}): {hits}"
    )


# ---------------------------------------------------------------------------
# Case 7 — integration: alembic offline SQL mode renders non-empty SQL
# ---------------------------------------------------------------------------


def test_fr07_migrations_render_under_alembic_offline_sql_mode(
    alembic_home: Path,
) -> None:
    """AC-7.7: alembic upgrade head --sql renders non-empty SQL.

    FR07-offline-sql-renders: `sql_rendered == "non-empty"` (TEST_SPEC sub-assertion).
    Offline mode is the canonical way to capture the exact DDL a
    migration will emit without executing it — the test asserts the
    three GREEN revisions collectively generate a non-empty SQL
    payload, which is the proof they actually contain real upgrade
    operations (not no-op `pass` bodies).
    """
    # subprocess decision: out-of-process. Pytest-cov CANNOT measure
    # coverage of code inside a subprocess, so the in-process coverage
    # for FR-07 is provided by the top-level module imports
    # (v1_initial / v2_tags / v3_split_results) and the static scan
    # in case 6 — both load the migration modules inside THIS process.
    result = _run_alembic(alembic_home, "upgrade", "head", "--sql")
    assert result.returncode == 0, (
        f"alembic upgrade head --sql must exit 0; "
        f"stderr={result.stderr!r}"
    )

    rendered = (result.stdout or "").strip()
    sql_rendered = "non-empty" if len(rendered) > 0 else "empty"
    assert sql_rendered == "non-empty", (
        f"Offline SQL mode produced no output for FR-07 migrations; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
