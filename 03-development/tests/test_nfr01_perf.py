"""NFR-01 — `taskq_api.repository.task_repo` read-path latency budgets.

[NFR-01] `02-architecture/SAD.md` maps NFR-01 to
`taskq_api.repository.task_repo` with the target **p95 < 30 ms**, and
`02-architecture/TEST_SPEC.md` (NFR table, cases #1 and #2) names the two
measuring tests reproduced here verbatim:

  1. test_nfr01_get_task_p95_under_30ms_at_10k_rows
       row_count="10000"; percentile="p95"; budget_ms="30"
  2. test_nfr01_list_tasks_limit50_p95_under_80ms_at_10k_rows
       row_count="10000"; limit="50"; percentile="p95"; budget_ms="80"

Shape notes (forced by tooling, not preference):

* Both cases take the `benchmark` fixture, so they are the suite the
  `performance` dimension measures (`pytest --benchmark-only`). Without
  them pytest-benchmark collects nothing and the dimension has no score.
* `benchmark.pedantic(rounds=..., iterations=1)` instead of the auto-
  calibrating `benchmark(...)` call: a fixed sample count keeps the p95
  index well defined and bounds the cost of these two cases inside the
  full-suite runs (`make verify-system`, the coverage run, and every
  mutmut mutant run) that also execute them.
* The store is seeded by assigning its backing dict directly. The
  `insert()` path scans every existing row for a duplicate `name`, so
  seeding 10 000 rows through it is O(n²) — that cost belongs to the
  write path, and the budget under test here is the read path.
* `setup.cfg` carries `-p benchmark` in `addopts` for the same reason it
  carries `-p asyncio`: the framework runs mutmut with
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, so the fixture these tests need
  must be requested explicitly.
"""

from __future__ import annotations

import math
from typing import Any, Dict

# `taskq_api` is importable because conftest.py (loaded before this module)
# puts 03-development/src on sys.path — same as every other test module here.
from taskq_api.repository.task_repo import get_task, list_tasks

# TEST_SPEC parameters, kept as named constants so the assertions below can
# mirror the spec's own predicates.
_ROW_COUNT = 10_000
_LIMIT = 50
_TARGET_ID = "bench-task-09999"
_ROUNDS_POINT_READ = 500
_ROUNDS_PAGE_READ = 200


def _seed(store: Any, row_count: int) -> None:
    """Populate *store* with *row_count* rows via its backing dict.

    `created_at` is strictly increasing so `list_paginated`'s sort order is
    deterministic (it sorts on that field).
    """
    store.tasks = {
        f"bench-task-{i:05d}": {
            "id": f"bench-task-{i:05d}",
            "name": f"bench-task-{i:05d}",
            "status": "pending",
            "created_at": f"2026-01-01T00:00:00.{i:06d}Z",
        }
        for i in range(row_count)
    }


def _p95_ms(stats: Any) -> float:
    """p95 of a pytest-benchmark `Stats` object, in milliseconds.

    `stats.data` holds one duration per round, in SECONDS.
    """
    data = sorted(stats.data)
    idx = max(0, min(len(data) - 1, math.ceil(0.95 * len(data)) - 1))
    return data[idx] * 1000.0


# ---------------------------------------------------------------------------
# Case 1 — get_task p95 < 30 ms at 10 000 rows
# ---------------------------------------------------------------------------


def test_nfr01_get_task_p95_under_30ms_at_10k_rows(benchmark: Any, task_store: Any) -> None:
    """NFR-01: single-row fetch p95 stays inside the 30 ms budget at 10k rows.

    [NFR-01] — TEST_SPEC NFR case #1 (NP-06).
    """
    _seed(task_store, _ROW_COUNT)
    row_count = str(len(task_store.tasks))
    percentile = "p95"
    budget_ms = "30"
    # NFR-01 — row_count="10000"
    assert row_count == "10000"

    row: Dict[str, Any] = benchmark.pedantic(
        get_task,
        args=(task_store, _TARGET_ID),
        rounds=_ROUNDS_POINT_READ,
        iterations=1,
    )

    # The measured call must actually have returned the requested row —
    # a benchmark over a miss would measure the wrong code path.
    assert row is not None
    assert row["id"] == _TARGET_ID

    p95_ms = _p95_ms(benchmark.stats.stats)
    assert percentile == "p95"
    assert p95_ms < float(budget_ms), (
        f"NFR-01 breach: get_task {percentile}={p95_ms:.3f}ms exceeds the "
        f"{budget_ms}ms budget at row_count={row_count}"
    )


# ---------------------------------------------------------------------------
# Case 2 — list_tasks(limit=50) p95 < 80 ms at 10 000 rows
# ---------------------------------------------------------------------------


def test_nfr01_list_tasks_limit50_p95_under_80ms_at_10k_rows(
    benchmark: Any, task_store: Any
) -> None:
    """NFR-01: first cursor page (limit=50) p95 stays inside 80 ms at 10k rows.

    [NFR-01] — TEST_SPEC NFR case #2 (NP-06).
    """
    _seed(task_store, _ROW_COUNT)
    row_count = str(len(task_store.tasks))
    limit = str(_LIMIT)
    percentile = "p95"
    budget_ms = "80"
    # NFR-01 — row_count="10000"; limit="50"
    assert row_count == "10000"
    assert limit == "50"

    page, next_cursor = benchmark.pedantic(
        list_tasks,
        args=(task_store, None, _LIMIT, None),
        rounds=_ROUNDS_PAGE_READ,
        iterations=1,
    )

    # A full page plus a continuation cursor: the measured call has to be
    # the paginated read the budget is about, not an empty result.
    assert len(page) == _LIMIT
    assert next_cursor == f"bench-task-{_LIMIT - 1:05d}"

    p95_ms = _p95_ms(benchmark.stats.stats)
    assert percentile == "p95"
    assert p95_ms < float(budget_ms), (
        f"NFR-01 breach: list_tasks(limit={limit}) {percentile}={p95_ms:.3f}ms "
        f"exceeds the {budget_ms}ms budget at row_count={row_count}"
    )
