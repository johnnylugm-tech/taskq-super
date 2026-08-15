# Coverage Report

> Phase: **2 — Architecture** | Per-FR Delta (P4)
> Generated: 2026-08-15 | Source: real `pytest --cov=03-development/src --cov-report=term-missing -q` execution
> Raw output: `04-testing/coverage_raw.txt`

## 1. Overall Coverage

| Metric | Value |
| --- | --- |
| Target module tree | `03-development/src` |
| Total statements | **881** |
| Missed statements | **0** |
| **Line coverage** | **100%** |
| Gate-3 threshold | ≥ 80% → **PASS** (margin: +20 pp) |

Raw totals from `coverage report --format=total`:

```
TOTAL  100
```

Per-module `term-missing` rows (verbatim from `coverage_raw.txt`):

```
Name                                                         Stmts   Miss  Cover   Missing
------------------------------------------------------------------------------------------
03-development/src/migrations/__init__.py                        0      0   100%
03-development/src/migrations/versions/__init__.py               0      0   100%
03-development/src/migrations/versions/v1_initial.py            19      0   100%
03-development/src/migrations/versions/v2_tags.py               17      0   100%
03-development/src/migrations/versions/v3_split_results.py      28      0   100%
03-development/src/sitecustomize.py                             26      0   100%
03-development/src/taskq_api/__init__.py                         0      0   100%
03-development/src/taskq_api/__main__.py                        29      0   100%
03-development/src/taskq_api/api/__init__.py                     0      0   100%
03-development/src/taskq_api/api/deps.py                        23      0   100%
03-development/src/taskq_api/api/health.py                     100      0   100%
03-development/src/taskq_api/api/tasks.py                       45      0   100%
03-development/src/taskq_api/app.py                             47      0   100%
03-development/src/taskq_api/config.py                          21      0   100%
03-development/src/taskq_api/errors.py                          69      0   100%
03-development/src/taskq_api/models/__init__.py                  0      0   100%
03-development/src/taskq_api/models/orm.py                      13      0   100%
03-development/src/taskq_api/models/schemas.py                  33      0   100%
03-development/src/taskq_api/repository/__init__.py              0      0   100%
03-development/src/taskq_api/repository/key_repo.py             17      0   100%
03-development/src/taskq_api/repository/rate_repo.py            93      0   100%
03-development/src/taskq_api/repository/session.py              25      0   100%
03-development/src/taskq_api/repository/task_repo.py            11      0   100%
03-development/src/taskq_api/service/__init__.py                 0      0   100%
03-development/src/taskq_api/service/auth.py                    29      0   100%
03-development/src/taskq_api/service/ratelimit.py               23      0   100%
03-development/src/taskq_api/service/runner.py                 172      0   100%
03-development/src/taskq_api/service/tasks.py                   41      0   100%
------------------------------------------------------------------------------------------
TOTAL                                                          881      0   100%
```

## 2. Per-Module Breakdown

### 2.1 HTTP / API layer (`taskq_api/api/*`, `app.py`, `__main__.py`)

| Module | Stmts | Miss | Cover |
| --- | --- | --- | --- |
| `api/health.py` | 100 | 0 | **100%** |
| `api/tasks.py` | 45 | 0 | **100%** |
| `api/deps.py` | 23 | 0 | **100%** |
| `api/__init__.py` | 0 | 0 | 100% (init only) |
| `app.py` | 47 | 0 | **100%** |
| `__main__.py` | 29 | 0 | **100%** |
| `config.py` | 21 | 0 | **100%** |

Sub-total: 265 stmts · 0 missed.

### 2.2 Service layer (`taskq_api/service/*`)

| Module | Stmts | Miss | Cover |
| --- | --- | --- | --- |
| `service/runner.py` | 172 | 0 | **100%** |
| `service/tasks.py` | 41 | 0 | **100%** |
| `service/auth.py` | 29 | 0 | **100%** |
| `service/ratelimit.py` | 23 | 0 | **100%** |
| `service/__init__.py` | 0 | 0 | 100% (init only) |

Sub-total: 265 stmts · 0 missed. The 172-statement `runner.py` (the largest single
module in the product) is fully exercised by the runner integration suite.

### 2.3 Repository / persistence (`taskq_api/repository/*`)

| Module | Stmts | Miss | Cover |
| --- | --- | --- | --- |
| `repository/rate_repo.py` | 93 | 0 | **100%** |
| `repository/task_repo.py` | 11 | 0 | **100%** |
| `repository/key_repo.py` | 17 | 0 | **100%** |
| `repository/session.py` | 25 | 0 | **100%** |
| `repository/__init__.py` | 0 | 0 | 100% (init only) |

Sub-total: 146 stmts · 0 missed.

### 2.4 Schemas, models, errors, migrations

| Module | Stmts | Miss | Cover |
| --- | --- | --- | --- |
| `errors.py` | 69 | 0 | **100%** |
| `models/schemas.py` | 33 | 0 | **100%** |
| `models/orm.py` | 13 | 0 | **100%** |
| `models/__init__.py` | 0 | 0 | 100% (init only) |
| `migrations/versions/v3_split_results.py` | 28 | 0 | **100%** |
| `migrations/versions/v1_initial.py` | 19 | 0 | **100%** |
| `migrations/versions/v2_tags.py` | 17 | 0 | **100%** |
| `migrations/versions/__init__.py` | 0 | 0 | 100% (init only) |
| `migrations/__init__.py` | 0 | 0 | 100% (init only) |
| `sitecustomize.py` | 26 | 0 | **100%** |

Sub-total: 205 stmts · 0 missed.

## 3. Uncovered Lines

**None.** Every statement under `03-development/src` is exercised by the test
suite (`Missing` column is empty for every row, including the 100-statement
`api/health.py` and the 172-statement `service/runner.py`).

## 4. Gate-3 Threshold Check

| Check | Required | Observed | Verdict |
| --- | --- | --- | --- |
| `03-development/src` line coverage | ≥ 80% | **100%** | **PASS** |
| Per-module uncovered lines in any business module | 0 critical | 0 | **PASS** |
| Skipped tests carrying missing-code rationale | — | 3 (platform/network, pre-existing) | **PASS** |

## 5. How to Reproduce

```bash
cd /Users/johnny/projects/taskq-super
.venv/bin/python -m pytest \
  --cov=03-development/src \
  --cov-report=term-missing \
  -q \
  | tee 04-testing/coverage_raw.txt
.venv/bin/python -m coverage report --format=total
```
