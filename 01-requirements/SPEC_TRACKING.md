# Specification Tracking Matrix — taskq-api

> On-demand Lazy Load template.
> Canonical spec source: bare `SPEC.md` (project root — see harness
> `check_forward_refs` gate; `01-requirements/SPEC.md` is an ILLEGAL source
> path). Every Ownership / Source / Citation / Reference cell below points to
> root `SPEC.md`, NOT `01-requirements/SPEC.md`.

## Project Info
- Project Name: taskq-api
- Version: v1.0.0
- Created: 2026-08-14

## Specification Status

> **The Status column is machine-refreshed** — `advance-phase` overwrites each
> FR's Status from `build_traceability`'s live code/test scan (IN_PROGRESS once
> code/module exists, VERIFIED once code+test exist). The authoritative status is
> that scan / `quality_manifest.json`, NOT this hand-filled cell. Fill the
> semantic columns (Spec Description / Intent Class / Decision Framework / Notes);
> leave Status to refresh itself (a hand-edit is overwritten on the next advance).
>
> Score authority lives in `quality_manifest.json`. This matrix is a
> human-readable view; it is NOT the SSOT for Gate-scores.

| FR ID | Spec Description | Intent Class | Decision Framework | Status | Notes |
|-------|-----------------|--------------|-------------------|--------|-------|
| FR-01 | 任務資源 CRUD API — POST/GET/LIST/DELETE `/v1/tasks` (cursor pagination, validation 422, unknown 404, conflict 409, scope-driven DELETE 403). | functional / endpoint | SPEC.md §3 FR-01 + §8 #4..#8 acceptance list | DRAFT | 10 acceptance criteria (AC-1.1..AC-1.10); scope `write` for create, `read` for get/list, `admin` for delete; default `limit=50`, max `200`; ownership: `taskq_api.service.tasks` + `taskq_api.api.tasks.routes`. |
| FR-02 | 任務執行端點 — `POST /v1/tasks/{id}/run` (write, 202) via `asyncio.create_subprocess_exec(*shlex.split(command))` (no `shell=True`), timeout `TASKQ_TASK_TIMEOUT`; lifecycle `pending → running → done \| failed \| timeout`; results persisted in `task_results`; `GET /v1/tasks/{id}/runs` (read) for run history. | functional / async execution | SPEC.md §3 FR-02 + §8 #25; `kill()` + `await wait()` no-orphan clause is verbatim from §3 FR-08 body | DRAFT | 6 acceptance criteria (AC-2.1..AC-2.6); high-risk module `taskq_api.service.runner` (per-module TDD); ownership: `taskq_api.service.runner.run_task`, `list_runs`, `taskq_api.api.tasks.run_route`. |
| FR-03 | API Key 認證 — `X-API-Key` required on every `/v1/*` (401 otherwise); SHA-256 hash storage in `api_keys.key_hash`; `hmac.compare_digest` compare; plaintext printed exactly once at `python -m taskq_api key create --scope <scope>`; revocation via `revoked_at`. | functional / authentication | SPEC.md §3 FR-03 + §8 #5, #18 | DRAFT | 6 acceptance criteria (AC-3.1..AC-3.6); AC-3.3 mirrors SPEC.md §8 #18 `key_hash is 64 hex chars`; high-risk module `taskq_api.service.auth`; ownership: `taskq_api.service.auth.verify_api_key`, `create_api_key`, `taskq_api.repository.key_repo`, `taskq_api.__main__ key create`. |
| FR-04 | Scope 授權 — `read < write < admin` inclusive hierarchy; single FastAPI dependency is the only authz decision site; 403 must not leak resource existence. | functional / authorization | SPEC.md §3 FR-04 + §8 #6 (single dependency = canonical §3 FR-04 "授權判定必須在單一中介層(dependency)完成") | DRAFT | 5 acceptance criteria (AC-4.1..AC-4.5); AC-4.5 enumerates every `/v1` route's `dependencies=` and asserts single-authz-dep; ownership: `taskq_api.api.deps.require_scope`, `authenticate`, `taskq_api.service.auth.scope_check`. |
| FR-05 | 流量控制 — per-token token bucket (DB-persisted in `rate_buckets`, row-level lock in single transaction); capacity `TASKQ_RATE_BURST`, refill `TASKQ_RATE_PER_SEC`; 429 + `Retry-After` header; `/healthz` and `/readyz` exempt. | functional / rate limiting | SPEC.md §3 FR-05 + §8 #9; row-level-lock wording derives from SPEC.md §9 R12 | DRAFT | 5 acceptance criteria (AC-5.1..AC-5.5); AC-5.4 fires `2 * TASKQ_RATE_BURST` parallel requests to assert no over-admission race; ownership: `taskq_api.service.ratelimit.consume`, `taskq_api.api.deps.rate_limit`, `taskq_api.repository.rate_repo`. |
| FR-06 | 持久化層與交易邊界 — all data access via `repository/` (only layer that may import `sqlalchemy`); one `Session` per request via context manager; no string-concatenated SQL; `selectinload` / `joinedload`; `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True`. | functional / persistence + transactions | SPEC.md §3 FR-06 + §8 #16, #17, #21; N+1 is an acceptance failure per §2.6 constraints | DRAFT | 5 acceptance criteria (AC-6.1..AC-6.5); high-risk module `taskq_api.repository.session`; ownership: `taskq_api.repository.session.session_scope`, `task_repo`, `key_repo`, `rate_repo`. |
| FR-07 | Schema Migration (Alembic 三步演進) — v1 base tables (`tasks`, `api_keys`), v2 tags many-to-many (`tags`, `task_tags`) + `tasks.name` unique index, v3 data-moving split of `tasks.result_json` into `task_results`; every step reversible; round-trip reversibility acceptance (`upgrade head → write → downgrade -1 → upgrade head` byte-identical); no destructive shortcuts (`op.execute("DROP TABLE …")` forbidden). | functional / schema migration | SPEC.md §3 FR-07 + §8 #12, #13 | DRAFT | 7 acceptance criteria (AC-7.1..AC-7.7); high-risk module `migrations/versions/v3_split_results.py`; must run against real SQLite file (NFR-09 round-specific clause); ownership: `migrations.versions.v1_initial`, `v2_tags`, `v3_split_results`. |
| FR-08 | 非同步執行器 — `asyncio.TaskGroup` background runner; concurrency cap `TASKQ_MAX_CONCURRENT` (excess queues, not unbounded); graceful drain on shutdown up to `TASKQ_DRAIN_TIMEOUT` (over-budget → `interrupted`); timeout via `wait_for` + `process.kill()` + `await process.wait()` (no orphan processes); `asyncio.CancelledError` must propagate, never swallowed by `except Exception`. | functional / async runtime | SPEC.md §3 FR-08 + §8 #25; `except Exception` swallowing test derives from SPEC.md §9 R7 | DRAFT | 5 acceptance criteria (AC-8.1..AC-8.5); AC-8.5 explicitly asserts `CancelledError` is NOT caught by `except Exception` (NFR-03 anti-pattern); high-risk module `taskq_api.service.runner`; ownership: `taskq_api.service.runner.executor`, `shutdown`, `run_with_timeout`. |
| FR-09 | 健康檢查與可觀測性 — `GET /healthz` (live, no auth), `GET /readyz` (no auth; DB reachable AND `alembic current == head` else 503), `GET /v1/metrics` (admin; task counts by status, latency percentiles, rate-limit rejections); "migration not at head" must fail closed. | functional / observability | SPEC.md §3 FR-09 endpoint table + §8 #10, #11; fail-closed clause is verbatim from §3 FR-09 | DRAFT | 6 acceptance criteria (AC-9.1..AC-9.6); AC-9.6 derives from §3 FR-09 "deploying new code without running migrations must fail closed"; ownership: `taskq_api.api.health.healthz`, `readyz`, `metrics`. |
| FR-10 | 錯誤契約 (RFC 7807) — every non-2xx carries `Content-Type: application/problem+json`; body fields exactly `{type, title, status, detail, instance, correlation_id}`; `detail` must not leak internals (no SQL/stack/path/schema); `correlation_id` appears in response header `X-Correlation-Id` and server log. | functional / error contract | SPEC.md §3 FR-10 + §7 error-code map (422/401/403/404/409/429/503/500) | DRAFT | 6 acceptance criteria (AC-10.1..AC-10.6); AC-10.6 exercises every error code in §7 exactly once each; ownership: `taskq_api.errors.problem_json`, `taskq_api.errors.handlers`. |
| NFR-01 | 效能與查詢效率 — `GET /v1/tasks/{id}` p95 < 30 ms (10,000 rows); `GET /v1/tasks?limit=50` p95 < 80 ms (10,000 rows); constant SQL statement count regardless of row count (N+1 is a fail condition). | non-functional / performance | SPEC.md §4 NFR-01 + §8 #14, #15; coverage note: harness `performance` evaluator covers only mean latency, NOT constant SQL count — AC-N1.3 requires dedicated task | DRAFT | 4 acceptance criteria (AC-N1.1..AC-N1.4); measurement tool `pytest-benchmark`; SQL count via SQLAlchemy event-listener counter (1 / 50 / 200 / 10,000 rows must match); dimension `performance`. |
| NFR-02 | HTTP 與資料層安全 — no `shell=True` / `eval(` / `exec(` in source (grep 0 hits); no f-string / `%` / `+` SQL composition; SHA-256 hashed API keys with `hmac.compare_digest`; 403 must not leak resource existence; CORS denies all origins by default (`TASKQ_CORS_ORIGINS`); `bandit -r 03-development/src/` → 0 HIGH, 0 MEDIUM. | non-functional / security | SPEC.md §4 NFR-02 + §8 #16, #17, #23 | DRAFT | 6 acceptance criteria (AC-N2.1..AC-N2.6); AC-N2.4 bandit scan is the canonical verifier; ownership: cross-cutting (`taskq_api.service.auth`, `taskq_api.repository.session`); dimension `security`. |
| NFR-03 | 錯誤處理、交易與非同步正確性 — explicit per-request transaction boundary (success commit / exception rollback via context manager); no bare `except:` or `except Exception: pass`; `asyncio.CancelledError` must propagate (must NOT be swallowed); DB down → `/readyz` 503 with explicit `detail` (no busy-loop retry); task timeout kills child process (no orphan); failing migration leaves DB at previous revision. | non-functional / reliability | SPEC.md §4 NFR-03 + §8 #10, #25; anti-pattern list `bare_except` / `broad_swallow` / `except_base_exception` derives from framework `evaluate_dimension.md` `error_handling` | DRAFT | 5 acceptance criteria (AC-N3.1..AC-N3.5); AC-N3.1 uses `ast-error-handling` scanner; AC-N3.5 ties to FR-07 migration rollback; dimension `error_handling`. |
| NFR-04 | 敏感資料遮蔽 — `stdout_tail` / `stderr_tail` / logs / error bodies must redact lines matching `(sk-[A-Za-z0-9_-]{8,}\|token=\S+\|Bearer\s+\S+\|postgres(ql)?://[^\s]+)` to `[REDACTED]`; `TASKQ_DB_URL` (incl. password) must not appear in any log / error / `/v1/metrics` response; API-key plaintext printed only once at `key create`, never persisted. | non-functional / security | SPEC.md §4 NFR-04 + §8 #20 | DRAFT | 4 acceptance criteria (AC-N4.1..AC-N4.4); regex pattern names verbatim from SPEC.md §4 NFR-04; dimension `security` (same dimension tag as NFR-02). |
| NFR-05 | 文件覆蓋 — 100% public-API docstring coverage with `[FR-XX]` / `[NFR-XX]` references; every FastAPI endpoint in `/openapi.json` carries `summary` + `description`. | non-functional / documentation | SPEC.md §4 NFR-05; metric phrase "public-API docstring coverage of 100%" derives from framework `evaluate_dimension.md` `documentation` dimension formula | DRAFT | 3 acceptance criteria (AC-N5.1..AC-N5.3); AC-N5.1 uses `ast-docstrings`; AC-N5.3 introspects `/openapi.json`; dimension `documentation`. |
| NFR-06 | 架構分層契約 — `.importlinter` at project root enforces `api > service > repository > models` (lower cannot import upper); `config` and `errors` are independence modules; `sqlalchemy` may only be imported by `repository/` (ORM leakage is the guarded anti-pattern); `lint-imports` must exit 0; no `ignore_imports` / downgrade loophole. | non-functional / architecture constraints | SPEC.md §4 NFR-06 + §8 #21 | DRAFT | 5 acceptance criteria (AC-N6.1..AC-N6.5); AC-N6.3 / AC-N6.4 are forced-import counter-tests; ownership: `.importlinter` config; dimension `architecture_constraints`. |
| NFR-07 | 依賴與授權合規 — runtime deps pinned via `==` in `requirements.txt`; transitive deps locked via `requirements.lock`; license allowlist MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF (other licenses forbidden); whole-tree scan via `pip-licenses --format=json --with-system`; SBOM at `08-config/SBOM.json` with `name` / `version` / `license` / `direct\|transitive` discriminator. | non-functional / license compliance | SPEC.md §4 NFR-07 + §8 #22; coverage note: harness `license_compliance` evaluator uses `scancode --license` — does NOT cover transitive-tree scan or SBOM artifact, AC-N7.1..AC-N7.4 each require dedicated task | DRAFT | 4 acceptance criteria (AC-N7.1..AC-N7.4); dimension `license_compliance`. |
| NFR-08 | 變異測試 — `.methodology/harness_config.json` sets `features.mutation_testing: true`; mutation score ≥ 70; scope limited to `service/` + `repository/` (rationale: execution-time budget). | non-functional / mutation testing | SPEC.md §4 NFR-08 + §8 #24; framework CLI invocation `harness_cli.py mutation-test-score --project .` per `evaluate_dimension.md` `mutation_testing` block | DRAFT | 3 acceptance criteria (AC-N8.1..AC-N8.3); AC-N8.2 writes `.methodology/mutation_score.json`; dimension `mutation_testing`. |
| NFR-09 | 驗證真實性 (零 skip 鐵律) — `pytest 03-development/tests -q` skipped count = 0; every test function has ≥ 1 `assert`; no `--ignore` / `-k` / `--deselect` / `collect_ignore` / testpaths-removal exclusions; FR-07 migration tested against real SQLite file (not in-memory mock); `TRACEABILITY_MATRIX.md` `VERIFIED` only on actual pass. | non-functional / test assertion quality | SPEC.md §4 NFR-09 + §8 #1; `zero_assert == 0` phrase derives from framework `evaluate_dimension.md` `test_assertion_quality` | DRAFT | 5 acceptance criteria (AC-N9.1..AC-N9.5); AC-N9.4 ties to FR-07 round-specific clause; dimension `test_assertion_quality`. |
| NFR-10 | 整合覆蓋 — `03-development/tests/integration/` line coverage ≥ 80%; integration tests driven via `httpx.AsyncClient(transport=ASGITransport(app))` (no direct handler calls); covers full CRUD chain + 401/403/404/409/422/429/503 + migration round-trip + rate limit trigger/recovery + graceful drain. | non-functional / integration coverage | SPEC.md §4 NFR-10 + §8 #3; coverage note: harness `integration_coverage` evaluator runs `--cov=03-development/src --cov-report=term-missing`, does NOT enumerate error codes or scan for direct handler calls | DRAFT | 4 acceptance criteria (AC-N10.1..AC-N10.4); AC-N10.2 is a static scan that fails when handler bodies are called directly; dimension `integration_coverage`. |
| NFR-11 | 可讀性 — project MI (LLOC-weighted) ≥ 80; per-function CC ≤ 10; single file ≤ 400 lines; single directory ≤ 15 files; each API handler ≤ 40 lines (business logic drops down into `service/`). | non-functional / readability | SPEC.md §4 NFR-11; score formula uses LLOC-weighted average MI per framework `evaluate_dimension.md` `readability` | DRAFT | 4 acceptance criteria (AC-N11.1..AC-N11.4); AC-N11.1 uses `radon mi -j`; AC-N11.2 uses `radon cc`; dimension `readability`. |
| NFR-12 | 系統驗證目標 — `Makefile`'s `verify-system` target chains `alembic upgrade head` → full test suite → service startup + `/healthz`, `/readyz` smoke → `alembic downgrade base` + `alembic upgrade head` (round-trip); `make verify-system` must exit 0 and print `verify-system: PASS`. | non-functional / verifiability | SPEC.md §4 NFR-12 + §8 #27 | DRAFT | 3 acceptance criteria (AC-N12.1..AC-N12.3); stdout match `verify-system: PASS` derives verbatim from §4 + §8 #27; dimension `execute_verification_target`. |

## Owner / Phase Allocation

| FR ID | Phase 1 Owner | Phase 2 Owner | Phase 3+ Owner | Source Citation |
|-------|---------------|---------------|----------------|-----------------|
| FR-01 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-01 |
| FR-02 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-02 + §8 #25 |
| FR-03 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-03 + §8 #5, #18 |
| FR-04 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-04 + §8 #6 |
| FR-05 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-05 + §8 #9 |
| FR-06 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-06 + §8 #16, #17, #21 |
| FR-07 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-07 + §8 #12, #13 |
| FR-08 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-08 + §8 #25 |
| FR-09 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-09 + §8 #10, #11 |
| FR-10 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §3 FR-10 + §7 |
| NFR-01 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-01 + §8 #14, #15 |
| NFR-02 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-02 + §8 #16, #17, #23 |
| NFR-03 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-03 + §8 #10, #25 |
| NFR-04 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-04 + §8 #20 |
| NFR-05 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-05 |
| NFR-06 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-06 + §8 #21 |
| NFR-07 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-07 + §8 #22 |
| NFR-08 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-08 + §8 #24 |
| NFR-09 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-09 + §8 #1 |
| NFR-10 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-10 + §8 #3 |
| NFR-11 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-11 |
| NFR-12 | SRS_ENGINEER (A) | ARCHITECT (B) | DEV (C) / QA (D) | SPEC.md §4 NFR-12 + §8 #27 |

## Completeness Verification

| Check | Target | Actual | Status |
|-------|--------|--------|--------|
| FR → SRS mapping | 100% (10/10) | 10/10 | Verified |
| NFR → SRS mapping | 100% (12/12) | 12/12 | Verified |
| FR → SPEC.md citation | 100% (10/10) | 10/10 | Verified |
| NFR → SPEC.md citation | 100% (12/12) | 12/12 | Verified |
| Source path = bare `SPEC.md` (root) | 100% (22/22) | 22/22 | Verified |
| Total FR + NFR coverage | 22/22 | 22/22 | Verified |

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-08-14 | Initial creation — populated 10 FR + 12 NFR from SRS.md; bare `SPEC.md` (root) citations; standard template columns only (FR ID / Spec Description / Intent Class / Decision Framework / Status / Notes); Status left as DRAFT for machine refresh via `build_traceability`; score authority is `quality_manifest.json`. | Agent A (REQUIREMENTS_ENGINEER) |