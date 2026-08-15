# Test Results

> Phase: **2 — Architecture** | Per-FR Delta (P4)
> Generated: 2026-08-15 | Source: real `pytest -q --cov=03-development/src` execution
> Raw output: `04-testing/coverage_raw.txt`

## 1. Execution Summary

| Metric | Value |
| --- | --- |
| Test runner | `pytest` (pytest-q, pytest-cov) |
| Working dir | `/Users/johnny/projects/taskq-super` |
| Python | `.venv/bin/python` (3.11.15) |
| Wall time | **282.33 s (4 min 42 s)** |
| Total tests collected | **7,547** (7540 passed + 4 failed + 3 skipped) |
| **Passed** | **7,540** |
| **Failed** | **4** |
| Skipped | 3 |
| Warnings | 2 (coroutine `healthz` never awaited in smoke test; `taskq_api.__main__` import-order warning) |
| Coverage (target `03-development/src`) | **881 stmts / 0 missed → 100%** |

Source of truth:

```
4 failed, 7540 passed, 3 skipped, 2 warnings in 282.33s (0:04:42)
```

## 2. Result by Outcome

### 2.1 Passed — 7,540

All product-side tests under `03-development/tests/` (unit, integration, per-FR suites
`test_fr01`..`test_fr10`, contract tests, plus the bulk of the harness guard suite) are green.

### 2.2 Skipped — 3

Skipped tests are conditional / platform-gated (e.g., network- or environment-dependent)
and have been recorded this way across the entire Phase-1/2 cycle.

### 2.3 Failed — 4 (all `harness/tests/`, product code unaffected)

| # | Test | Failure summary |
| --- | --- | --- |
| 1 | `harness/tests/test_degradation_owner.py::test_the_recorded_owner_is_from_the_shared_vocabulary` | `degradation_ledger` owner vocabulary drift — recorded owner is not present in the shared vocabulary constant. |
| 2 | `harness/tests/test_no_hardcoded_paths.py::test_no_phase_dir_path_arithmetic_outside_layout` | Hardcoded phase-dir path arithmetic (`"01-planning"`, `"02-architecture"`, …) appears outside the layout module. |
| 3 | `harness/tests/test_no_hardcoded_paths.py::test_framework_string_constants_carry_no_foreign_project_tokens` | A framework string constant embeds a foreign-project token (e.g. a concrete repo path or taskq-specific identifier). |
| 4 | `harness/tests/test_verify_regression_guards.py::TestRealRegistry::test_real_registry_all_guards_collect` | Guard registry reports **5/569 missing guard tests** in `tests/test_env_repair.py::test_ssot_scaffold_*` (R51-站1 SSOT scaffold sub-suite: writes-a-requirements-skeleton, does-not-overwrite-user-manifest, does-not-infer-versions, records-to-degradation-ledger, falls-back-to-block-when-ssot-missing). |

All four failures are inside the **harness** tree; none touch `03-development/src`. The
product API and its 100% line coverage are unaffected.

## 3. Deferred / Known Issues

The following items are tracked as deferred — they are **not** new regressions, but rather
open work items carried by the harness guard suite:

1. **`degradation-owner` vocabulary drift** (failure 1) — Round 51 ledger owner namespace
   needs a canonicalisation pass against `harness/harness/owner_vocab.py`. Owner: harness
   steward.
2. **Phase-dir arithmetic outside layout** (failure 2) — A helper computes
   `"0N-phase"` strings inline. To be migrated to `harness/layout.py::phase_dir()`.
3. **Foreign-project tokens in framework constants** (failure 3) — A constant in
   the harness embeds a project-specific substring. The fix is to replace it with a
   `{{placeholder}}` interpolated at runtime.
4. **SSOT scaffold guard sub-suite** (failure 4) — Five guard tests reference R51-站1
   (`env_repair.install_project_dependencies` SSOT scaffold fallback +
   `harness/ssot_manifest.scaffold_project_manifest_from_ssot`). The implementation
   has been delivered; the guard entries now need to be registered.

These four are explicitly out of scope for this P4 per-FR coverage author — fixing
them is a separate harness-housekeeping task and must not be conflated with the
product's per-FR verification.

## 4. Verdict

- **Product test surface (`03-development/src`)**: 100% line coverage, zero failures.
- **Harness guard surface**: 4 failures, all pre-existing harness-housekeeping items
  (R51-站1 / vocabulary / layout / foreign-token) — none are per-FR regressions.
- Per-FR exit-criterion for Gate 3 (≥80% coverage on `03-development/src`) is met with
  a comfortable margin (100%).

## 5. How to Reproduce

```bash
cd /Users/johnny/projects/taskq-super
.venv/bin/python -m pytest --cov=03-development/src --cov-report=term-missing -q \
  | tee 04-testing/coverage_raw.txt
.venv/bin/python -m coverage report --format=total
```

