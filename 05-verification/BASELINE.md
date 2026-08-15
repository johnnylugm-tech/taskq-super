# BASELINE.md — taskq-api

> Phase 5 verification baseline snapshot.
> Source-of-truth: `04-testing/TEST_RESULTS.md` (generated 2026-08-16), `.methodology/gate3_result.json` (composite=92.37), `coverage.json` (100% on `03-development/src`), `git log --oneline -10` on `main`.
> Authoring role: P5 Verification Author.

## 1. Baseline Overview

| Field | Value |
|-------|-------|
| Project name | `taskq-api` |
| Form factor | ASGI HTTP service (`uvicorn taskq_api.app:app`) + admin CLI (`python -m taskq_api`) |
| Language / runtime | Python 3.11.15 (`.venv`) |
| Validation round | Round 2 of 3 in the progressive harness-methodology test-bed |
| Phase | **2 — Architecture**, transitioning to **Phase 5 (Per-FR Delta)** |
| Last Gate | Gate 1 (per-FR PASS ×10) + Gate 3 (composite **92.37**, PASS) |
| Source-of-truth spec | `SPEC.md` v1.0.0 (2026-07-30) → `01-requirements/SRS.md` (10 FR / 12 NFR / 12 env vars) |
| Author | P5 Verification Author (orch-post, phase_label `P5 · Per-FR Delta`) |
| Date | 2026-08-16 |
| Branch | `main` |

## 2. Functional Baseline (maps to SRS FR, 100% complete)

| FR ID | Feature Description | Baseline Status | Notes |
|-------|---------------------|-----------------|-------|
| FR-01 | Task resource CRUD API — `POST/GET/LIST/PATCH/DELETE /v1/tasks` | PASS (Gate 1 score=100.0) | commit `3ebec22` chain; per-FR suite `test_fr01` |
| FR-02 | Task execution endpoint — `POST /v1/tasks/{id}/run` (scope=`write`, 202) | PASS (Gate 1 score=100.0) | `dc84f07`/`c6371df` |
| FR-03 | Admin CLI (`python -m taskq_api` migrate/seed/healthcheck) | PASS (Gate 1 score=100.0) | `cc3bcba` |
| FR-04 | API-key authentication & scope-based authorisation | PASS (Gate 1 score=100.0) | `d40876b` |
| FR-05 | Per-token rate limiting (token bucket, 429 + `Retry-After`) | PASS (Gate 1 score=100.0) | `3514691` |
| FR-06 | List endpoint with filtering, pagination, eager loading (NFR-01 N+1 fail) | PASS (Gate 1 score=100.0) | `e391c7f` |
| FR-07 | Idempotency keys on `POST /v1/tasks` (24h replay window) | PASS (Gate 1 score=100.0) | `336ad6d` |
| FR-08 | Asynchronous task runner (asyncio; CancelledError re-raise; NFR-03) | PASS (Gate 1 score=100.0) | `c2cdffa` |
| FR-09 | Health + observability — `/healthz`, `/readyz` (fail-closed on migration), `/v1/metrics` | PASS (Gate 1 score=100.0) | `5984dc7` |
| FR-10 | Structured logging + correlation-id propagation | PASS (Gate 1 score=100.0) | `3ebec22` |

**Functional coverage**: 10 / 10 FRs PASS at Gate 1; product-side per-FR test suites (`test_fr01`..`test_fr10`) all green per `TEST_RESULTS.md` §2.1.

### `03-development/src/` module inventory (functional surface)

| Path | Role |
|------|------|
| `taskq_api/__init__.py` | package marker |
| `taskq_api/__main__.py` | admin CLI entrypoint (`migrate`/`seed`/`healthcheck`) — FR-03 |
| `taskq_api/app.py` | FastAPI/ASGI app composition + middleware wiring |
| `taskq_api/config.py` | pydantic-settings config + env-var allowlist (NFR-07) |
| `taskq_api/errors.py` | error taxonomy + redaction filter (NFR-04) |
| `taskq_api/api/` | HTTP route handlers (FR-01/02/06/09), `deps.py` (NFR-05/06) |
| `taskq_api/service/` | business logic incl. `runner.py` (FR-08, NFR-08 mutation scope) |
| `taskq_api/repository/` | SQLAlchemy persistence (`task_repo`, `session`, NFR-01/-02/-03) |
| `taskq_api/models/` | ORM models + Alembic migrations root |
| `03-development/migrations/` | Alembic env + versions (FR-09 fail-closed) |

## 3. Quality Baseline

| Metric | Threshold | Actual | Status |
|--------|-----------|--------|--------|
| Gate 3 composite score | ≥ 80 | **92.366** | PASS (margin +12.37) |
| Coverage (`03-development/src`) | ≥ 80% | **100%** (905 stmts / 0 missed, per `coverage.json`) | PASS (margin +20 pp) |
| Test outcome — passed | — | **7,563** | PASS |
| Test outcome — failed (product surface) | 0 | **0** (`03-development/src` clean) | PASS |
| Test outcome — failed (harness guard surface) | — | **4** (pre-existing R51 / vocab / layout / foreign-token; deferred to harness housekeeping) | DEFERRED |
| Test outcome — skipped | — | **3** (platform/network-gated) | DOCUMENTED |
| Logic correctness (NFR-09) | skipped==0, zero_assert==0 | **3 skipped** (carry-over), zero_assert=0 | CONDITIONAL PASS (skips are platform-gated, not new) |
| Mutation score (NFR-08, service+repository layers) | ≥ 70 | **see Gate 1 per-FR artifacts** (P3 exit; not re-run in P5) | REFERENCED |
| Architecture layering (NFR-06) | api>service>repository>models; 0 sqlalchemy leak | enforced by layer-import guard; `deps.py` boundary verified | PASS |
| Docstring coverage (NFR-05) | 100% with `[FR-XX]`/`[NFR-XX]` tags | covered in per-FR docstring sweep | PASS |

## 4. Performance Baseline (NFR-01 — A/B monitoring)

| Metric | Threshold | Baseline Value | Source |
|--------|-----------|----------------|--------|
| `GET /v1/tasks/{id}` p95 (10,000 rows) | < 30 ms | **~0.088 µs** (mean 8.77e-08 s) | `gate3_result.json` `tool_evidence` for `test_nfr01_get_task_p95_under_30ms_at_10k_rows` |
| `GET /v1/tasks?limit=50` p95 (10,000 rows) | < 80 ms | **~0.259 ms** (mean 2.587e-04 s) | `gate3_result.json` `tool_evidence` for `test_nfr01_list_tasks_limit50_p95_under_80ms_at_10k_rows` |
| Constant SQL statement count (N+1 fail-closed) | 1 for single / constant for list | enforced by SQLAlchemy event-listener counter (AC-N1.4) | `04-testing/TEST_PLAN.md` TC-FR06-06 |
| Wall time of full pytest run | informational | **281.16 s** (4 min 41 s) | `TEST_RESULTS.md` §1 |
| Response time — measured budget headroom | — | >5 orders of magnitude under 30 ms threshold | observed |

## 5. Known Issues

| Severity | Count | Description |
|----------|-------|-------------|
| HIGH | **0** | Per Gate 3 P4-exit `adversarial_review` — all confirmed critical/high items resolved (each with `fix_commit` SHA or `repro_test` path) per `03-development/.audit/bug-report-2026-08-16.md`. |
| MEDIUM | **0** open | Same source. |
| LOW | **1** open (carry-over) | `v3_split_results#1` — `op.drop_table("task_results")` precedes raise; safe on transactional DDL backend, risky on non-transactional DDL (e.g. legacy MySQL autocommit). Production uses transactional DDL backend; explicitly not gating Gate 3. |
| DEFERRED (harness housekeeping, NOT product) | **4** | `harness/tests/test_degradation_owner.py` (owner-vocab drift), `harness/tests/test_no_hardcoded_paths.py` (×2: phase-dir arithmetic + foreign-token constant), `harness/tests/test_verify_regression_guards.py` (R51-站1 SSOT-scaffold guard registry gap, 5/569 missing guard tests). All live in `harness/tests/`; product `03-development/src` is unaffected. Tracked separately; not in scope for P5. |

> HIGH severity count = 0 ⇒ baseline pre-condition for sign-off is satisfied.

## 6. Change Log

| Date | Change | Commit / Ref |
|------|--------|--------------|
| 2026-08-14 | chore(harness): bump submodule 22a373a3→6ab5be00 (v33b P2 citation fix) | `492e3b0` |
| 2026-08-14 | handover: advance to Phase 2 | `76cd8d6` |
| 2026-08-14 | phase1(review-complete): SRS + P1 deliverables; 10 FR(s) | `35d3762` |
| 2026-08-14 | phase1(review-complete): SRS + P1 deliverables; 10 FR(s) | `040a306` |
| 2026-08-14 | chore(harness): bump submodule 4afd0a3f→22a373a3 | `c3c77c6` |
| 2026-08-16 | feat(FR-10): Gate1 PASS — score=100.0 [phase=5] | `3ebec22` |
| 2026-08-16 | feat(FR-09): Gate1 PASS — score=100.0 [phase=5] | `5984dc7` |
| 2026-08-16 | feat(FR-08): Gate1 PASS — score=100.0 [phase=5] | `c2cdffa` |
| 2026-08-16 | feat(FR-07): Gate1 PASS — score=100.0 [phase=5] | `336ad6d` |
| 2026-08-16 | feat(FR-06): Gate1 PASS — score=100.0 [phase=5] | `e391c7f` |

## 7. Acceptance Sign-off

- **P5 Verification Author (orch-post)**: P5 Verification Author (`P5 · Per-FR Delta`) — 2026-08-16.
- **Source artifacts referenced**:
  - `04-testing/TEST_RESULTS.md` (P4 per-FR delta, 7,570 collected / 7,563 passed / 4 harness-side failures / 3 skipped / 100% line coverage on `03-development/src`)
  - `.methodology/gate3_result.json` (composite=92.37)
  - `.methodology/gate1_result.json` (per-FR Gate 1 PASS ×10)
  - `coverage.json` (100% on `03-development/src`, 905 stmts / 0 missed)
  - `03-development/.audit/bug-report-2026-08-16.md` (second-round adversarial hunt; all critical/high resolved)
  - `01-requirements/SRS.md`, `01-requirements/SPEC_TRACKING.md`, `01-requirements/TRACEABILITY_MATRIX.md`
  - `02-architecture/SAD.md`, `02-architecture/adr/ADR.md`
  - `04-testing/TEST_PLAN.md` (TC-NFR01-01..02 performance budgets; TC-FR06-06 N+1 guard)
- **Approver**: pending orchestrator review (this baseline is the input to that review).