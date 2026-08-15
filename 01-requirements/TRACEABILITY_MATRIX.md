# Traceability Matrix — taskq-api

> Requirements Traceability Matrix
> Framework: harness-methodology
> Version: v1.0
> Project: taskq-api (round 2 of 3)
> Source of truth: `SPEC.md` (root) — not `01-requirements/SPEC.md`

---

## Overview

Provides complete **FR -> SRS -> Code -> Test** bidirectional traceability
supporting ASPICE SWE.3 / SYS.4 compliance for the `taskq-api` task-queue HTTP
service. Every functional requirement (FR-01..FR-10) and every non-functional
requirement (NFR-01..NFR-12) in `01-requirements/SRS.md` is mapped to its
canonical SPEC.md citation, its owner module(s) in
`03-development/src/taskq_api/`, and its acceptance-criterion test target.
The Status column is machine-refreshed by `build_traceability`; the
authoritative score is `quality_manifest.json`.

---

## FR <-> Spec Mapping

| FR ID | Functional Requirement | SRS Section | Priority | Status |
|-------|----------------------|-------------|----------|--------|
| FR-01 | Task resource CRUD API — POST/GET/LIST/DELETE `/v1/tasks` (cursor pagination, validation 422, unknown 404, conflict 409, scope-driven DELETE 403) | §3 FR-01 + §8 #4..#8 | HIGH | DRAFT |
| FR-02 | Task execution endpoint — `POST /v1/tasks/{id}/run` (write, 202) via `asyncio.create_subprocess_exec(*shlex.split(command))`; lifecycle `pending → running → done\|failed\|timeout`; results persisted in `task_results`; `GET /v1/tasks/{id}/runs` (read) | §3 FR-02 + §8 #25 | HIGH | DRAFT |
| FR-03 | API Key authentication — `X-API-Key` required on every `/v1/*` (401 otherwise); SHA-256 hash storage in `api_keys.key_hash`; `hmac.compare_digest` compare; plaintext printed exactly once at `python -m taskq_api key create`; revocation via `revoked_at` | §3 FR-03 + §8 #5, #18 | HIGH | DRAFT |
| FR-04 | Scope authorisation — `read < write < admin` inclusive hierarchy; single FastAPI dependency is the only authz decision site; 403 must not leak resource existence | §3 FR-04 + §8 #6 | HIGH | DRAFT |
| FR-05 | Rate limiting — per-token token bucket (DB-persisted in `rate_buckets`, row-level lock); capacity `TASKQ_RATE_BURST`, refill `TASKQ_RATE_PER_SEC`; 429 + `Retry-After` header; `/healthz` and `/readyz` exempt | §3 FR-05 + §8 #9 | HIGH | DRAFT |
| FR-06 | Persistence layer + transaction boundaries — all data access via `repository/`; one `Session` per request via context manager; no string-concatenated SQL; `selectinload`/`joinedload`; `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True` | §3 FR-06 + §8 #16, #17, #21 | HIGH | DRAFT |
| FR-07 | Schema Migration (Alembic three-step evolution) — v1 base tables, v2 tags many-to-many, v3 data-moving split of `tasks.result_json` into `task_results`; every step reversible; round-trip reversibility acceptance; no destructive shortcuts | §3 FR-07 + §8 #12, #13 | HIGH | DRAFT |
| FR-08 | Asynchronous executor — `asyncio.TaskGroup` background runner; concurrency cap `TASKQ_MAX_CONCURRENT` (excess queues); graceful drain on shutdown up to `TASKQ_DRAIN_TIMEOUT` (over-budget → `interrupted`); timeout via `wait_for` + `process.kill()` + `await process.wait()` (no orphan); `asyncio.CancelledError` must propagate, never swallowed by `except Exception` | §3 FR-08 + §8 #25 | HIGH | DRAFT |
| FR-09 | Health checks + observability — `GET /healthz` (live, no auth), `GET /readyz` (DB reachable AND `alembic current == head` else 503), `GET /v1/metrics` (admin; task counts by status, latency percentiles, rate-limit rejections); migration-not-at-head must fail closed | §3 FR-09 + §8 #10, #11 | HIGH | DRAFT |
| FR-10 | Error contract (RFC 7807) — every non-2xx carries `Content-Type: application/problem+json`; body fields exactly `{type, title, status, detail, instance, correlation_id}`; `detail` must not leak internals (no SQL/stack/path/schema); `correlation_id` appears in response header `X-Correlation-Id` and server log | §3 FR-10 + §7 | HIGH | DRAFT |
| NFR-01 | Performance + query efficiency — `GET /v1/tasks/{id}` p95 < 30 ms (10,000 rows); `GET /v1/tasks?limit=50` p95 < 80 ms (10,000 rows); constant SQL statement count regardless of row count (N+1 is a fail condition) | §4 NFR-01 + §8 #14, #15 | HIGH | DRAFT |
| NFR-02 | HTTP + data-layer security — no `shell=True`/`eval(`/`exec(` in source; no f-string/`%`/`+` SQL composition; SHA-256 hashed API keys with `hmac.compare_digest`; 403 must not leak resource existence; CORS denies all origins by default; `bandit -r 03-development/src/` → 0 HIGH, 0 MEDIUM | §4 NFR-02 + §8 #16, #17, #23 | HIGH | DRAFT |
| NFR-03 | Error handling, transactions, async correctness — explicit per-request transaction boundary (commit/rollback via context manager); no bare `except:` or `except Exception: pass`; `asyncio.CancelledError` must propagate; DB down → `/readyz` 503 with explicit detail; task timeout kills child process; failing migration leaves DB at previous revision | §4 NFR-03 + §8 #10, #25 | HIGH | DRAFT |
| NFR-04 | Sensitive-data redaction — `stdout_tail`/`stderr_tail`/logs/error bodies must redact `(sk-[A-Za-z0-9_-]{8,}\|token=\S+\|Bearer\s+\S+\|postgres(ql)?://[^\s]+)` to `[REDACTED]`; `TASKQ_DB_URL` (incl. password) must not appear in any log/error/`/v1/metrics`; API-key plaintext printed only once | §4 NFR-04 + §8 #20 | HIGH | DRAFT |
| NFR-05 | Documentation coverage — 100% public-API docstring coverage with `[FR-XX]`/`[NFR-XX]` references; every FastAPI endpoint in `/openapi.json` carries `summary` + `description` | §4 NFR-05 | MEDIUM | DRAFT |
| NFR-06 | Architectural layering contract — `.importlinter` enforces `api > service > repository > models` (lower cannot import upper); `config` and `errors` are independence modules; `sqlalchemy` may only be imported by `repository/`; `lint-imports` must exit 0; no `ignore_imports`/downgrade loophole | §4 NFR-06 + §8 #21 | HIGH | DRAFT |
| NFR-07 | Dependency + license compliance — runtime deps pinned via `==` in `requirements.txt`; transitive deps locked via `requirements.lock`; license allowlist MIT/BSD-2-Clause/BSD-3-Clause/Apache-2.0/PSF; whole-tree scan via `pip-licenses --format=json --with-system`; SBOM at `08-config/SBOM.json` with `name`/`version`/`license`/`direct\|transitive` | §4 NFR-07 + §8 #22 | HIGH | DRAFT |
| NFR-08 | Mutation testing — `.methodology/harness_config.json` sets `features.mutation_testing: true`; mutation score ≥ 70; scope limited to `service/` + `repository/` (rationale: execution-time budget) | §4 NFR-08 + §8 #24 | MEDIUM | DRAFT |
| NFR-09 | Verification honesty (zero-skip iron rule) — `pytest 03-development/tests -q` skipped count = 0; every test function has ≥ 1 `assert`; no `--ignore`/`-k`/`--deselect`/`collect_ignore`/testpaths-removal exclusions; FR-07 migration tested against real SQLite file; `TRACEABILITY_MATRIX.md` `VERIFIED` only on actual pass | §4 NFR-09 + §8 #1 | HIGH | DRAFT |
| NFR-10 | Integration coverage — `03-development/tests/integration/` line coverage ≥ 80%; integration tests driven via `httpx.AsyncClient(transport=ASGITransport(app))` (no direct handler calls); covers full CRUD chain + 401/403/404/409/422/429/503 + migration round-trip + rate limit trigger/recovery + graceful drain | §4 NFR-10 + §8 #3 | HIGH | DRAFT |
| NFR-11 | Readability — project MI (LLOC-weighted) ≥ 80; per-function CC ≤ 10; single file ≤ 400 lines; single directory ≤ 15 files; each API handler ≤ 40 lines (business logic drops down into `service/`) | §4 NFR-11 | MEDIUM | DRAFT |
| NFR-12 | System verification target — `Makefile`'s `verify-system` target chains `alembic upgrade head` → full test suite → service startup + `/healthz`, `/readyz` smoke → `alembic downgrade base` + `alembic upgrade head` (round-trip); `make verify-system` must exit 0 and print `verify-system: PASS` | §4 NFR-12 + §8 #27 | HIGH | DRAFT |

---

## Spec <-> Code Mapping

> Per-module ownership recorded in SRS.md §2.10 (high-risk modules) and
> §2.13 (Module layout — canonical SPEC.md §6). §1.2 is Scope, §9 is Glossary;
> neither carries per-module ownership.

| SRS Section | Code File | Function/Class | Lines | Status |
|-------------|-----------|----------------|-------|--------|
| §3 FR-01 (CRUD) | `03-development/src/taskq_api/service/tasks.py` | `create_task`, `get_task`, `list_tasks`, `delete_task` | TBD | DRAFT |
| §3 FR-01 (CRUD routes) | `03-development/src/taskq_api/api/tasks.py` | `routes` (POST/GET/LIST/DELETE) | TBD | DRAFT |
| §3 FR-02 (execution) | `03-development/src/taskq_api/service/runner.py` | `run_task`, `list_runs` | TBD | DRAFT |
| §3 FR-02 (run route) | `03-development/src/taskq_api/api/tasks.py` | `run_route` | TBD | DRAFT |
| §3 FR-03 (auth) | `03-development/src/taskq_api/service/auth.py` | `verify_api_key`, `create_api_key` | TBD | DRAFT |
| §3 FR-03 (key repo) | `03-development/src/taskq_api/repository/key_repo.py` | key CRUD | TBD | DRAFT |
| §3 FR-03 (admin entry) | `03-development/src/taskq_api/__main__.py` | `key create` subcommand | TBD | DRAFT |
| §3 FR-04 (authz) | `03-development/src/taskq_api/api/deps.py` | `require_scope`, `authenticate` | TBD | DRAFT |
| §3 FR-04 (scope check) | `03-development/src/taskq_api/service/auth.py` | `scope_check` | TBD | DRAFT |
| §3 FR-05 (rate-limit) | `03-development/src/taskq_api/service/ratelimit.py` | `consume` | TBD | DRAFT |
| §3 FR-05 (rate dep + repo) | `03-development/src/taskq_api/api/deps.py` + `03-development/src/taskq_api/repository/rate_repo.py` | `rate_limit`, bucket read/write | TBD | DRAFT |
| §3 FR-06 (persistence) | `03-development/src/taskq_api/repository/session.py` | `session_scope` (transaction boundary) | TBD | DRAFT |
| §3 FR-06 (task repo) | `03-development/src/taskq_api/repository/task_repo.py` | task CRUD with `selectinload`/`joinedload` | TBD | DRAFT |
| §3 FR-07 (migrations) | `migrations/versions/v1_initial.py` | base tables `tasks`, `api_keys` | TBD | DRAFT |
| §3 FR-07 (migrations) | `migrations/versions/v2_tags.py` | `tags`/`task_tags` + `tasks.name` unique | TBD | DRAFT |
| §3 FR-07 (migrations) | `migrations/versions/v3_split_results.py` | data-moving split into `task_results` | TBD | DRAFT |
| §3 FR-08 (async runner) | `03-development/src/taskq_api/service/runner.py` | `executor`, `shutdown`, `run_with_timeout` | TBD | DRAFT |
| §3 FR-09 (health) | `03-development/src/taskq_api/api/health.py` | `healthz`, `readyz`, `metrics` | TBD | DRAFT |
| §3 FR-10 (errors) | `03-development/src/taskq_api/errors.py` | `problem_json`, `handlers` | TBD | DRAFT |
| §4 NFR-06 (layering) | `.importlinter` (project root) | layers + forbidden contracts | TBD | DRAFT |
| §4 NFR-07 (deps) | `requirements.txt`, `requirements.lock`, `08-config/SBOM.json` | pin + lock + SBOM | TBD | DRAFT |
| §4 NFR-08 (mutation) | `.methodology/harness_config.json`, `.methodology/mutation_score.json` | mutation scope + score | TBD | DRAFT |
| §4 NFR-12 (verify) | `Makefile` | `verify-system` target | TBD | DRAFT |

---

## Code <-> Test Mapping

> Acceptance-criterion target. Detailed AC IDs (AC-NN.M) are the
> machine-decided traceability hooks recorded in SRS.md §3/§4. Each row
> identifies the test surface that exercises the code element.

| Code File | Test File | Coverage | Status |
|-----------|-----------|----------|--------|
| `taskq_api/service/tasks.py` | `03-development/tests/integration/test_tasks_crud.py` | FR-01 AC-1.1..AC-1.10 (201/422/401/200/404/403/204/409) | DRAFT |
| `taskq_api/service/runner.py` (run_task, list_runs) | `03-development/tests/integration/test_tasks_run.py` | FR-02 AC-2.1..AC-2.6 (202, lifecycle, timeout-kill, runs list) | DRAFT |
| `taskq_api/service/auth.py` (verify_api_key, create_api_key) | `03-development/tests/unit/test_auth.py` + `03-development/tests/integration/test_auth_headers.py` | FR-03 AC-3.1..AC-3.6 (401, hash 64-hex, hmac.compare_digest, revocation, healthz exemption) | DRAFT |
| `taskq_api/repository/key_repo.py` | `03-development/tests/unit/test_key_repo.py` | FR-03 AC-3.3 (key_hash shape), AC-3.5 (revoked_at) | DRAFT |
| `taskq_api/__main__.py` (key create) | `03-development/tests/integration/test_admin_cli.py` | FR-03 AC-3.2 (one-print plaintext) | DRAFT |
| `taskq_api/api/deps.py` (require_scope, authenticate) | `03-development/tests/integration/test_scope_authz.py` | FR-04 AC-4.1..AC-4.5 (scope hierarchy, no-leak 403, single-dep enumeration) | DRAFT |
| `taskq_api/service/auth.py` (scope_check) | `03-development/tests/unit/test_scope.py` | FR-04 AC-4.1..AC-4.3 | DRAFT |
| `taskq_api/service/ratelimit.py` + `taskq_api/repository/rate_repo.py` | `03-development/tests/integration/test_rate_limit.py` | FR-05 AC-5.1..AC-5.5 (429 + Retry-After, exemptions, no over-admission, row-level lock) | DRAFT |
| `taskq_api/repository/session.py` (session_scope) | `03-development/tests/unit/test_session.py` + integration lifecycle test | FR-06 AC-6.3, AC-6.5 (one-Session-per-request, rollback, pool_pre_ping) | DRAFT |
| `taskq_api/repository/task_repo.py` (eager loads) | `03-development/tests/integration/test_eager_load.py` + N+1 SQL count | FR-06 AC-6.4 + NFR-01 AC-N1.3 | DRAFT |
| `migrations/versions/v1_initial.py`, `v2_tags.py`, `v3_split_results.py` | `03-development/tests/integration/test_migrations_roundtrip.py` | FR-07 AC-7.1..AC-7.7 (upgrade/downgrade, real SQLite, byte-identical columns, no DROP TABLE shortcut) | DRAFT |
| `taskq_api/service/runner.py` (executor, shutdown, run_with_timeout) | `03-development/tests/integration/test_graceful_drain.py` + `test_async_correctness.py` | FR-08 AC-8.1..AC-8.5 (drain, no orphan, concurrency cap, kill+wait, CancelledError propagation) | DRAFT |
| `taskq_api/api/health.py` | `03-development/tests/integration/test_healthz_readyz.py` + `test_metrics.py` | FR-09 AC-9.1..AC-9.6 (healthz 200, readyz 503 on DB-down or migration-behind-head, metrics admin-only, fail-closed) | DRAFT |
| `taskq_api/errors.py` (problem_json, handlers) | `03-development/tests/integration/test_problem_json.py` | FR-10 AC-10.1..AC-10.6 (content-type, field allowlist, no-detail-leak, correlation_id echo, every error code 401/403/404/409/422/429/503/500) | DRAFT |
| `taskq_api/repository/session.py` (rollback) + `taskq_api/service/runner.py` (CancelledError) + `taskq_api/api/health.py` (readyz 503 on DB-down) + `migrations/versions/*` (rollback-on-failure) | `03-development/tests/unit/test_async_correctness.py` + `03-development/tests/integration/test_healthz_readyz.py` (DB-down case) + `03-development/tests/integration/test_migrations_roundtrip.py` (downgrade-on-failure) + `ast-error-handling` scan over `03-development/src/` | NFR-03 AC-N3.1..AC-N3.5 (zero `bare_except`/`broad_swallow`/`except_base_exception`; CancelledError propagates past `except Exception`; /readyz 503 + RFC 7807 detail with no busy-loop; rollback leaves no row on follow-up read; failing migration leaves DB at previous revision) | DRAFT |
| `taskq_api/api/*.py` (handlers) | `03-development/tests/integration/*.py` (driven via `httpx.AsyncClient(transport=ASGITransport(app))`) | NFR-10 AC-N10.1..AC-N10.4 (≥80% coverage, no direct handler calls, every error code, migration in integration) | DRAFT |
| `03-development/src/taskq_api/**` (security scan) | `bandit -r 03-development/src/` + grep `shell=True`/`eval(`/`exec(` + grep SQL concat | NFR-02 AC-N2.1, AC-N2.2, AC-N2.4 (0 hits, 0 bandit HIGH/MED) | DRAFT |
| `03-development/src/taskq_api/**` (redaction filter) | `03-development/tests/unit/test_redaction.py` | NFR-04 AC-N4.1..AC-N4.4 (regex redaction, no TASKQ_DB_URL leak, one-print API key) | DRAFT |
| `03-development/src/taskq_api/**` (docstrings) | `ast-docstrings` + `03-development/tests/unit/test_docstring_refs.py` + `/openapi.json` introspection | NFR-05 AC-N5.1..AC-N5.3 (100% coverage, [FR-XX]/[NFR-XX] tag, summary+description per endpoint) | DRAFT |
| `.importlinter` | `lint-imports` + forced-import counter-tests in `03-development/tests/unit/test_layering.py` | NFR-06 AC-N6.1..AC-N6.5 (file exists, exit 0, forbidden contract, layers contract, config/errors independence) | DRAFT |
| `requirements.txt` + `requirements.lock` + `08-config/SBOM.json` | `pip-licenses --format=json --with-system` + `03-development/tests/unit/test_sbom.py` | NFR-07 AC-N7.1..AC-N7.4 (whole-tree allowlist, SBOM schema, transitive-lock enforcement) | DRAFT |
| `.methodology/harness_config.json` + `.methodology/mutation_score.json` | `harness_cli.py mutation-test-score --project .` | NFR-08 AC-N8.1..AC-N8.3 (feature flag, score ≥ 70, scope-rationale) | DRAFT |
| `03-development/tests/**` (pytest) | `pytest 03-development/tests -q` + `ast-zero-assert` + `test_no_pytest_exclusions.py` | NFR-09 AC-N9.1..AC-N9.5 (skipped == 0, zero_assert == 0, no `--ignore`/`-k`/`--deselect`, real SQLite for FR-07, VERIFIED only on pass) | DRAFT |
| `03-development/src/taskq_api/**` (radon) | `radon mi -j` + `radon cc` + file/directory/handler size guards | NFR-11 AC-N11.1..AC-N11.4 (MI ≥ 80, CC ≤ 10, ≤ 400 lines/file, ≤ 15 files/dir, ≤ 40 lines/handler) | DRAFT |
| `Makefile` (verify-system) | `make verify-system` | NFR-12 AC-N12.1..AC-N12.3 (exit 0, stdout `verify-system: PASS`, four-step chain) | DRAFT |

---

## Completeness Verification

| Check | Target | Actual | Status |
|-------|--------|--------|--------|
| FR coverage in matrix | 100% (10/10) | 10/10 | Verified |
| NFR coverage in matrix | 100% (12/12) | 12/12 | Verified |
| FR <-> Spec citation (bare `SPEC.md` root) | 100% (10/10) | 10/10 | Verified |
| NFR <-> Spec citation (bare `SPEC.md` root) | 100% (12/12) | 12/12 | Verified |
| Spec <-> Code mapping (SRS §2.10 / §2.13 owner modules) | 100% (23/23 rows) | 23/23 | Verified |
| Code <-> Test mapping (per-module test surface, distinct req IDs) | 100% (22/22 req IDs) | 22/22 | Verified |
| Acceptance-criterion hooks (AC-NN.M identifiers) | 22/22 FRs covered | 22/22 | Verified |
| Test coverage (Phase 3+) | TOTAL 100% per SPEC.md §8 #2; integration ≥80% per SPEC.md §8 #3 (NFR-10); P3 harness phase-gate ≥70% | TBD (Phase 3 measurement) | In Progress |

---

## ASPICE Compliance

| ASPICE Capability | Status |
|-------------------|--------|
| SWE.3.B.SP1 Task-to-work-product traceability | Verified |
| SWE.3.B.SP2 Bidirectional traceability | Verified |
| SWE.3.B.SP3 Traceability consistency | Verified |

---

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-08-14 | Initial creation — populated 10 FR + 12 NFR from SRS.md and SPEC_TRACKING.md; bare `SPEC.md` (root) citations throughout; bidirectional FR↔Code↔Test mapping per SRS §1.2 and §2.10 owner modules; acceptance-criterion hooks (AC-NN.M) referenced; Status left as DRAFT for machine refresh via `build_traceability`; score authority is `quality_manifest.json`. | Agent A (REQUIREMENTS_ENGINEER) |