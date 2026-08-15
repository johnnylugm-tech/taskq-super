# TEST_PLAN.md — taskq-api

> **Project**: taskq-super (project `taskq-api`)
> **Phase**: 4 — Testing
> **Source of truth**: `01-requirements/SRS.md` §3 (FR-01..FR-10) + §4 (NFR-01..NFR-12)
> **Manifest**: `.methodology/quality_manifest.json` → `fr_ids` + `nfr_traceability`
> **Authored by**: P4 Test Plan Author (this run, before per-FR TDD)
> **Layer coverage target**: 100% line coverage on `03-development/src/`, ≥80% on integration suite

---

## 1. Test Strategy

### 1.1 Layers

| Layer | Driver | Scope |
|-------|--------|-------|
| **Unit** | direct call (`pytest`) | high-risk modules — `service/runner`, `service/auth`, `repository/session`, `service/ratelimit`; redaction; bucket math; cursor; shlex argv shape |
| **Integration** | `httpx.AsyncClient(transport=ASGITransport(app))` | every `/v1/*` endpoint, error-code coverage, rate-limit, readyz drain, migration round-trip |
| **Migration (real SQLite file)** | Alembic CLI on a temp file | v1→v2→v3 round-trip, reversibility, no destructive shortcuts |
| **Static / structural** | `grep`, `ast-*`, `bandit`, `pip-licenses`, `mutmut`, `radon`, `import-linter` | NFR-02/06/07/08/11 |
| **End-to-end harness** | `make verify-system` | NFR-12 chain |

### 1.2 Categories applied per FR

For every FR the test cases cover the four mandatory categories:
- **Positive** — happy path; canonical input produces expected output.
- **Negative** — invalid input, wrong scope, missing header, missing row, etc. → 401/403/404/409/422.
- **Boundary** — `limit=1`, `limit=200`, `limit=201`, cursor exhaustion, timeout = exact budget, drain = exact budget.
- **Edge-case** — concurrency races, retry/refresh, no-orphan, kill+wait reaping, regex redaction patterns.

### 1.3 Test ID convention

```
TC-<FRID>-<NN>     (NN is 2-digit per-FR ordinal; layer suffix is per row)
```
AC-IDs map 1-to-1 with `01-requirements/SRS.md` §3 / §4; a single AC may have
more than one TC when a positive + negative + boundary decomposition is
required (e.g. AC-1.8 limit bounds ⇒ TC-FR01-08-P / TC-FR01-08-N /
TC-FR01-08-B).

### 1.4 Priority tiers

| Priority | Meaning |
|----------|---------|
| **P0** | Acceptance-failure on miss; mapped to a SPEC.md §8 row. |
| **P1** | Operational hazard; mapped to a Risk row in SRS §8. |
| **P2** | Hardening / quality-of-life; NFR coverage. |

---

## 2. FR Coverage Matrix

| FR | Module(s) | ACs | TC IDs (this plan) |
|----|-----------|-----|---------------------|
| FR-01 | `api/tasks`, `service/tasks` | AC-1.1..AC-1.10 | TC-FR01-01..TC-FR01-12 |
| FR-02 | `api/tasks`, `service/runner` | AC-2.1..AC-2.6 | TC-FR02-01..TC-FR02-09 |
| FR-03 | `api/deps`, `service/auth` | AC-3.1..AC-3.6 | TC-FR03-01..TC-FR03-08 |
| FR-04 | `api/deps`, `service/auth` | AC-4.1..AC-4.5 | TC-FR04-01..TC-FR04-06 |
| FR-05 | `api/deps`, `service/ratelimit` | AC-5.1..AC-5.5 | TC-FR05-01..TC-FR05-07 |
| FR-06 | `repository/session` | AC-6.1..AC-6.5 | TC-FR06-01..TC-FR06-07 |
| FR-07 | `migrations/versions/{v1,v2,v3}` | AC-7.1..AC-7.7 | TC-FR07-01..TC-FR07-08 |
| FR-08 | `service/runner`, `app` | AC-8.1..AC-8.5 | TC-FR08-01..TC-FR08-08 |
| FR-09 | `api/health`, `__main__` | AC-9.1..AC-9.6 | TC-FR09-01..TC-FR09-07 |
| FR-10 | `errors`, `app` | AC-10.1..AC-10.6 | TC-FR10-01..TC-FR10-08 |

---

## 3. Per-FR Test Cases

### FR-01 — 任務資源 CRUD API

**Scope**: `POST /v1/tasks` (write), `GET /v1/tasks/{id}` (read), `GET /v1/tasks` (read, cursor), `DELETE /v1/tasks/{id}` (admin).
**AC set**: AC-1.1..AC-1.10.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR01-01 | Positive | P0 | Valid `write` key + valid `TaskCreate` body | `POST /v1/tasks` `{command:"echo hi", name:"t1"}` | `201`, JSON body contains `id` (uuid) and `status=="pending"` (AC-1.1) |
| TC-FR01-02 | Negative | P0 | Missing `X-API-Key` | `POST /v1/tasks` no header | `401` + problem+json; `type=/errors/unauthenticated` (AC-1.2) |
| TC-FR01-03-N1 | Negative | P0 | Empty `command` | body `{"command":"", "name":"x"}` | `422` + problem+json (AC-1.3) |
| TC-FR01-03-N2 | Negative | P0 | `command` > 1000 chars | `command` of length 1001 | `422` + problem+json (AC-1.3) |
| TC-FR01-03-N3 | Negative | P0 | Injection black-list hit (e.g. `; rm -rf`) | `command:"echo ; rm -rf /"` | `422` + problem+json (AC-1.3) |
| TC-FR01-03-N4 | Negative | P0 | Missing required field | `{"name":"x"}` (no `command`) | `422` + problem+json (AC-1.3) |
| TC-FR01-04 | Positive | P0 | `read` key, known id | `GET /v1/tasks/{id}` | `200`; body has all columns of `tasks` plus nested `tags[]` and `results[]` (AC-1.4) |
| TC-FR01-05 | Negative | P0 | Unknown id | `GET /v1/tasks/00000000-0000-0000-0000-000000000000` | `404` + problem+json (AC-1.5) |
| TC-FR01-06 | Negative | P0 | `write` key (non-admin) deletes | `DELETE /v1/tasks/{id}` | `403`; body must NOT reveal existence (AC-1.6 / NFR-02 leak clause) |
| TC-FR01-07 | Negative | P0 | Duplicate `name` | `POST /v1/tasks` second time with same `name` | `409` + problem+json (AC-1.7) |
| TC-FR01-08-P | Boundary | P0 | `limit=1` accepted | `GET /v1/tasks?limit=1` | `200`, 1 row, `next_cursor` present (AC-1.8 lower bound) |
| TC-FR01-08-B1 | Boundary | P0 | `limit=200` accepted | `GET /v1/tasks?limit=200` | `200`, up to 200 rows (AC-1.8 upper bound) |
| TC-FR01-08-B2 | Boundary | P0 | `limit=201` rejected | `GET /v1/tasks?limit=201` | `422` + problem+json (AC-1.8 upper-bound+1) |
| TC-FR01-08-D | Boundary | P1 | Default `limit` is 50 | `GET /v1/tasks` (no `limit`) | `200`, up to 50 rows (AC-1.8 default) |
| TC-FR01-09 | Positive | P0 | Cursor pagination reaches subsequent page | `GET /v1/tasks?cursor=<first.next_cursor>` | `200`, rows are strictly newer than page 1; `offset` query param is ignored (AC-1.9) |
| TC-FR01-10 | Positive | P0 | `admin` key, known id, delete cascades | `DELETE /v1/tasks/{id}` with `task_results` + `task_tags` rows pre-seeded | `204`; subsequent SELECTs show no row in `tasks`, `task_results`, `task_tags` for that id, all in same transaction (AC-1.10) |
| TC-FR01-11 | Edge | P1 | `name` uniqueness is case-insensitive | `POST /v1/tasks` `name:"Foo"` then `name:"foo"` | second returns `409` (edge-case on uniqueness) |
| TC-FR01-12 | Edge | P1 | Malformed UUID in path | `GET /v1/tasks/not-a-uuid` | `422` + problem+json (edge-case — invalid id shape) |

### FR-02 — 任務執行端點

**Scope**: `POST /v1/tasks/{id}/run` (write, 202), `GET /v1/tasks/{id}/runs` (read).
**AC set**: AC-2.1..AC-2.6.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR02-01 | Positive | P0 | `POST /v1/tasks/{id}/run` returns 202 with `run_id` | valid `write` key, valid task id | `202`, body `{run_id: <uuid>}`; subprocess was started (AC-2.1) |
| TC-FR02-02 | Static | P0 | No `shell=True` in source | `grep -n "shell=True" 03-development/src/` | `0` matches (AC-2.1 second clause) |
| TC-FR02-03 | Positive | P0 | `shlex.split(command)` shape | argv of started process | every argv element is exactly one token; shell metachars pass through, are not interpreted (AC-2.2) |
| TC-FR02-04 | Positive | P0 | Successful lifecycle | `command:"true"` | `task_results` row: `exit_code==0`, `finished_at` non-empty, `duration_ms ≥ 0`; status transitioned `pending → running → done` (AC-2.3) |
| TC-FR02-05 | Negative | P0 | Non-zero exit code preserved | `command:"false"` | task status `failed`; `exit_code==1` in `task_results` (AC-2.4) |
| TC-FR02-06 | Edge | P0 | Timeout kills child and reaps | `command:"sleep 30"` with `TASKQ_TASK_TIMEOUT=1` | status `timeout`; child PID is reaped (`kill()` then `await wait()`); no orphan (AC-2.5 / FR-08) |
| TC-FR02-07 | Positive | P0 | Runs history newest-first | after 3 sequential runs | `GET /v1/tasks/{id}/runs` returns rows in `finished_at DESC` (AC-2.6) |
| TC-FR02-08 | Negative | P1 | `read` key calling run endpoint | `POST /v1/tasks/{id}/run` with `read` key | `403` + problem+json (FR-04 cross-cut) |
| TC-FR02-09 | Edge | P1 | Run on unknown id | `POST /v1/tasks/<unknown-uuid>/run` | `404` + problem+json (AC-1.5-style edge for run) |

### FR-03 — API Key 認證

**Scope**: `X-API-Key` header on every `/v1/*`, hash storage, compare primitive, revocation, health exemptions.
**AC set**: AC-3.1..AC-3.6.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR03-01-N1 | Negative | P0 | Missing header on each `/v1/*` | hit every `/v1/*` route without `X-API-Key` | each returns `401` + problem+json; `type=/errors/unauthenticated` (AC-3.1) |
| TC-FR03-01-N2 | Negative | P0 | Unknown key | `X-API-Key: <random 32B hex>` | `401` + problem+json (AC-3.1) |
| TC-FR03-02 | Positive | P0 | `python -m taskq_api key create --scope write` prints once | run the CLI | stdout has exactly one plaintext line; it is NOT in any log file, error body, or `/v1/metrics` response (AC-3.2 / NFR-04 cross-cut) |
| TC-FR03-03 | Positive | P0 | `api_keys.key_hash` is 64-char SHA-256 hex | query `api_keys` | `key_hash` matches `^[0-9a-f]{64}$`; no column contains plaintext (AC-3.3 / NFR-02) |
| TC-FR03-04 | Static | P0 | Compare function is `hmac.compare_digest` | static scan + call-site assertion | every compare call is `hmac.compare_digest`; AST test fails on `==` or `!=` over hashes (AC-3.4) |
| TC-FR03-05 | Negative | P0 | Revoked key rejected | set `revoked_at=now()` then send `X-API-Key` | `401` + problem+json (AC-3.5) |
| TC-FR03-06 | Positive | P0 | `/healthz` and `/readyz` exempt | no `X-API-Key` header | both return `200` (AC-3.6 / FR-09) |
| TC-FR03-07 | Edge | P1 | Concurrent requests with same key | parallel `GET /v1/tasks` × 50 | all return `200`; no race on `key_hash` lookup |
| TC-FR03-08 | Edge | P1 | Compare is constant-time | statistical timing test over many `valid` vs `invalid` keys | wall-clock distributions overlap (timing-leak probe — qualitative edge-case) |

### FR-04 — Scope 授權

**Scope**: `read < write < admin`; single dependency; 403 leaks nothing.
**AC set**: AC-4.1..AC-4.5.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR04-01 | Negative | P0 | `read` key on `POST /v1/tasks` | write-required endpoint, `read` key | `403` + problem+json (NOT `401`); body field `type=/errors/forbidden` (AC-4.1) |
| TC-FR04-02 | Negative | P0 | `write` key (non-admin) on `DELETE /v1/tasks/{id}` | admin-required, `write` key | `403`; body must not say whether id exists — bodies for "exists" and "missing" are byte-identical except `instance` (AC-4.2 / NFR-02) |
| TC-FR04-03 | Positive | P0 | `write` key on `POST /v1/tasks/{id}/run` | valid task | `202` (AC-4.3 positive) |
| TC-FR04-03-N | Negative | P0 | `read` key on `POST /v1/tasks/{id}/run` | same | `403` (AC-4.3 negative) |
| TC-FR04-04 | Positive | P0 | `admin` key succeeds on any endpoint | enumerate endpoints with `admin` key | every request returns the endpoint's success code (AC-4.4) |
| TC-FR04-05 | Static | P0 | Single authz dependency on every `/v1` route | enumerate FastAPI routes | every `/v1/*` route's `dependencies=` includes the authz dep; absence fails (AC-4.5) |
| TC-FR04-06 | Edge | P1 | 403 body byte-equality for hidden vs missing | same `DELETE` on existing id and missing id, both with `write` key | response bodies (excluding `instance`) are byte-identical (AC-4.2 edge) |

### FR-05 — 流量控制

**Scope**: per-token token bucket; DB-persisted; row-level lock; `/healthz`, `/readyz` exempt.
**AC set**: AC-5.1..AC-5.5.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR05-01 | Negative | P0 | Burst exceeding `TASKQ_RATE_BURST` | `TASKQ_RATE_BURST+1` sequential requests on same token | last request returns `429` + problem+json + `Retry-After` header (AC-5.1 / SPEC §8 #9) |
| TC-FR05-02 | Positive | P0 | `Retry-After` is positive integer | same as TC-FR05-01 | `Retry-After: <int >= 1>` (AC-5.2) |
| TC-FR05-03 | Positive | P0 | `/healthz` & `/readyz` exempt | hammer both endpoints at > `TASKQ_RATE_BURST` rps | never `429` (AC-5.3) |
| TC-FR05-04 | Edge | P0 | 2× burst in parallel — no over-admission | `2 * TASKQ_RATE_BURST` parallel requests on same token | `200 + 429` count equals request count; `200` count ≤ `TASKQ_RATE_BURST`; no extra `200` admitted (AC-5.4) |
| TC-FR05-05 | Static | P0 | Single transaction + row-level lock | inspect repository update | bucket update executes inside one transaction with row-level lock; concurrent test shows no lost-update (AC-5.5) |
| TC-FR05-06 | Edge | P1 | Refill after waiting | burn burst, wait `1/TASKQ_RATE_PER_SEC` seconds, retry | at least one token available again (refill behaviour) |
| TC-FR05-07 | Negative | P1 | Different tokens have independent buckets | two distinct tokens each burning burst | neither cross-token 429 (bucket isolation) |

### FR-06 — 持久化層與交易邊界

**Scope**: repository-only SQLAlchemy, one Session per request, context-manager boundaries, explicit eager loading, pool config.
**AC set**: AC-6.1..AC-6.5.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR06-01 | Static | P0 | No `shell=True` / `eval(` / `exec(` in src | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` | `0` matches (AC-6.1) |
| TC-FR06-02 | Static | P0 | No SQL f-string / `%` / `+` concatenation | grep gate on patterns | `0` matches (AC-6.2) |
| TC-FR06-03 | Positive | P0 | One Session per request + rollback on exception | instrument FastAPI lifecycle; force handler exception | exactly one `Session` opened per request; exception triggers `session.rollback()` (AC-6.3) |
| TC-FR06-04 | Static | P0 | `lint-imports` exit 0 + forbidden contract enforced | run `lint-imports` and a counter-test that forces `from sqlalchemy import …` in `service/auth.py` | exits 0 normally; counter-test fails the build (AC-6.4) |
| TC-FR06-05 | Positive | P0 | Engine config: `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True` | inspect engine kwargs | exact match (AC-6.5) |
| TC-FR06-06 | Positive | P1 | Eager loading via `selectinload`/`joinedload` | unit test asserts chosen loader | loader is one of the two (NFR-01 cross-cut / AC-N1.4) |
| TC-FR06-07 | Edge | P1 | Connection drop handled by `pool_pre_ping` | invalidate a pooled conn, then run request | engine recycles without error (NFR-06 cross-cut / `pool_pre_ping` effect) |

### FR-07 — Schema Migration (Alembic 三步演進)

**Scope**: v1 base tables, v2 tags many-to-many + unique `tasks.name`, v3 split `tasks.result_json` into `task_results`. Every step reversible; no destructive shortcuts. **Tested against a real SQLite file (not in-memory mock)**.
**AC set**: AC-7.1..AC-7.7.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR07-01 | Positive | P0 | `alembic upgrade head` and `alembic downgrade base` exit 0 | run both on temp file | both exit `0` (AC-7.1) |
| TC-FR07-02 | Positive | P0 | No residual tables after `downgrade base` | inspect temp file after `downgrade base` | none of `tasks`, `api_keys`, `tags`, `task_tags`, `task_results`, `rate_buckets` present (AC-7.2) |
| TC-FR07-03 | Positive | P0 | Migration runs against a real SQLite file | migration suite DB path | temp file path, not `:memory:`; file is the working DB throughout (AC-7.3 / NFR-09) |
| TC-FR07-04 | Positive | P0 | Round-trip reversibility | `upgrade head → write sample → downgrade -1 → upgrade head` | every column of every sample row is byte-identical pre/post (AC-7.4 / SPEC §8 #12) |
| TC-FR07-05 | Positive | P0 | v3 reverse-migrates data back to `tasks.result_json` | populate `task_results` rows, `downgrade -1` | `tasks.result_json` restored; `task_results` absent; no row count lost (AC-7.5) |
| TC-FR07-06 | Static | P0 | No destructive shortcuts in `migrations/versions/*.py` | grep for `op.execute("DROP TABLE …")` and equivalents | `0` matches (AC-7.6) |
| TC-FR07-07 | Positive | P0 | Offline SQL generation + assertion | `alembic upgrade head --sql` | emitted statements satisfy structural assertions on every table and column (AC-7.7) |
| TC-FR07-08 | Edge | P1 | Failed mid-migration leaves DB at previous revision | inject a failure in v3 upgrade, run `upgrade head` | DB remains at v2; partial schema absent (NFR-03 / AC-N3.5 cross-cut) |

### FR-08 — 非同步執行器

**Scope**: `asyncio.TaskGroup`, graceful drain, concurrency cap, kill+wait, `CancelledError` propagation.
**AC set**: AC-8.1..AC-8.5.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR08-01 | Positive | P0 | Graceful drain on shutdown | submit long task, then shutdown with `TASKQ_DRAIN_TIMEOUT=10` | task within budget completes; over-budget tasks marked `interrupted` (AC-8.1) |
| TC-FR08-02 | Positive | P0 | No orphan after timeout kill | `command:"sleep 30"` with `TASKQ_TASK_TIMEOUT=1` | `PROCESS_COUNT_AFTER == 0`; `/proc` (or POSIX equivalent) shows no child (AC-8.2 / SPEC §8 #25 / NFR-03) |
| TC-FR08-03 | Positive | P0 | Concurrency cap | submit `TASKQ_MAX_CONCURRENT + N` tasks | exactly `TASKQ_MAX_CONCURRENT` running at any moment; rest queued (AC-8.3) |
| TC-FR08-04 | Edge | P0 | Timeout really kills child | monkey-patch `asyncio.wait_for` to raise `TimeoutError` | corresponding child receives `SIGKILL`; `wait()` returns (AC-8.4) |
| TC-FR08-05 | Negative | P0 | `CancelledError` propagates | wrap a `CancelledError`-raising body in `try: ... except Exception:` | `CancelledError` is not caught; it propagates (AC-8.5 / NFR-03) |
| TC-FR08-06 | Edge | P1 | `TASKQ_DRAIN_TIMEOUT=0` marks all in-flight interrupted | shutdown immediately with in-flight work | every in-flight task ends in `interrupted` (boundary) |
| TC-FR08-07 | Edge | P1 | TaskGroup handles exception | one child raises | other children run to completion; sibling tasks do not leak exceptions (edge-case) |
| TC-FR08-08 | Edge | P1 | Empty executor shutdown | no in-flight tasks | shutdown completes immediately, no error (edge) |

### FR-09 — 健康檢查與可觀測性

**Scope**: `/healthz` (live), `/readyz` (DB + migration at head, fail closed), `/v1/metrics` (admin).
**AC set**: AC-9.1..AC-9.6.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR09-01 | Positive | P0 | `/healthz` returns 200 | process alive | `200 {"status":"ok"}` (AC-9.1) |
| TC-FR09-02 | Negative | P0 | DB unreachable → `/readyz` 503 | stop DB | `503` + problem+json; `detail` mentions DB unavailability (AC-9.2) |
| TC-FR09-03 | Negative | P0 | `downgrade -1` → `/readyz` 503 | run `alembic downgrade -1`, then GET | `503` + problem+json; `detail` mentions migration not at head (AC-9.3) |
| TC-FR09-04 | Positive | P0 | DB reachable AND `alembic current == head` | normal state | `/readyz` returns `200` (AC-9.4) |
| TC-FR09-05 | Positive | P0 | `/v1/metrics` with admin key | admin auth | `200`; payload contains task counts per status, latency percentiles, rate-limit rejection counts (AC-9.5 positive) |
| TC-FR09-05-N | Negative | P0 | `/v1/metrics` without admin scope | `read` key | `403` (AC-9.5 negative) |
| TC-FR09-06 | Negative | P0 | Newer code without migration → `/readyz` 503 | simulate deploy-without-migrate | `/readyz` returns `503` (AC-9.6 / fail-closed) |

### FR-10 — 錯誤契約 (RFC 7807)

**Scope**: every non-2xx is `application/problem+json`; fields `type/title/status/detail/instance/correlation_id`; `detail` leaks nothing; `X-Correlation-Id` join key.
**AC set**: AC-10.1..AC-10.6.

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-FR10-01 | Positive | P0 | Every non-2xx sets `application/problem+json` | enumerate 422/401/403/404/409/429/503 | all carry `Content-Type: application/problem+json` (AC-10.1) |
| TC-FR10-02 | Positive | P0 | Body field allowlist | same | body has exactly the 6 fields; no extras (AC-10.2) |
| TC-FR10-03 | Negative | P0 | Forced 500 body has no internals | trigger 500 | body contains no stack trace, no SQL fragment, no file path (AC-10.3) |
| TC-FR10-04 | Positive | P0 | Header-body correlation_id match | any error response | `X-Correlation-Id` header value equals `correlation_id` in JSON body (AC-10.4) |
| TC-FR10-05 | Positive | P0 | Log-body correlation_id match | same request | server log line for that request contains the same `correlation_id` (AC-10.5) |
| TC-FR10-06 | Positive | P0 | Every error code exercised once | integration suite | 422, 401, 403, 404, 409, 429, 503, 500 each appear ≥ 1 time (AC-10.6) |
| TC-FR10-07 | Edge | P1 | `type` URI map per code | same | `type` URI per row in SPEC §7 (422→`/errors/validation`, 401→`/errors/unauthenticated`, 403→`/errors/forbidden`, 404→`/errors/not-found`, 409→`/errors/conflict`, 429→`/errors/rate-limited`, 503→`/errors/not-ready`, 500→`/errors/internal`) |
| TC-FR10-08 | Edge | P1 | `instance` is the request path | any error response | `instance` equals request path (URL or path-only) |

---

## 4. NFR Coverage

### NFR-01 — 效能與查詢效率 (performance)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR01-01 | Positive | P0 | `GET /v1/tasks/{id}` p95 < 30ms on 10k rows | `pytest-benchmark` over ASGI transport | p95 < 30ms (AC-N1.1) |
| TC-NFR01-02 | Positive | P0 | `GET /v1/tasks?limit=50` p95 < 80ms on 10k rows | same | p95 < 80ms (AC-N1.2) |
| TC-NFR01-03 | Positive | P0 | Constant SQL statement count | SQLAlchemy event listener at limit=1/50/200/10000 | count is constant across all row counts (AC-N1.3 / SPEC §8 #14) |
| TC-NFR01-04 | Positive | P1 | Loader is `selectinload` or `joinedload` | unit assertion on query strategy | chosen loader is one of the two (AC-N1.4) |

### NFR-02 — HTTP 與資料層安全 (security)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR02-01 | Static | P0 | No `shell=True`/`eval(`/`exec(` | grep | 0 matches (AC-N2.1) |
| TC-NFR02-02 | Static | P0 | No SQL f-string / `%` / `+` concatenation | grep | 0 matches (AC-N2.2) |
| TC-NFR02-03 | Negative | P0 | 403 body identity for hidden vs missing | DELETE hidden vs missing id, write key | bodies identical (excluding `instance`) (AC-N2.3 / FR-04) |
| TC-NFR02-04 | Static | P0 | `bandit -r 03-development/src/` clean | run bandit | 0 HIGH, 0 MEDIUM (AC-N2.4 / SPEC §8 #23) |
| TC-NFR02-05 | Negative | P0 | CORS denies all when env empty | preflight from any origin | request rejected (AC-N2.5) |
| TC-NFR02-06 | Positive | P0 | CORS allowlist enforced | preflight from listed and unlisted origins | listed succeeds, unlisted rejected (AC-N2.6) |

### NFR-03 — 錯誤處理、交易與非同步正確性 (error_handling)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR03-01 | Static | P0 | No bare except / broad swallow | `ast-error-handling` over source | 0 anti-patterns (AC-N3.1) |
| TC-NFR03-02 | Negative | P0 | `CancelledError` not caught by `except Exception` | unit test wrapping body | cancellation propagates (AC-N3.2 / SPEC §8 #25) |
| TC-NFR03-03 | Negative | P0 | DB down → `/readyz` 503 with explicit detail | stop DB | `503` + RFC 7807 detail; no busy-loop (AC-N3.3 / SPEC §8 #10) |
| TC-NFR03-04 | Positive | P0 | Transaction rollback on handler exception | force handler exception mid-write | follow-up read shows rolled-back row absent (AC-N3.4) |
| TC-NFR03-05 | Negative | P0 | Failed migration leaves DB at previous revision | inject mid-migration failure | DB remains at v2; no partial schema (AC-N3.5) |

### NFR-04 — 敏感資料遮蔽 (security)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR04-01 | Positive | P0 | `sk-…` line redacted | log line containing `sk-abcdefgh12345678` | stored as `... [REDACTED]`; original line replaced (AC-N4.1) |
| TC-NFR04-02 | Positive | P0 | `postgres://…:pwd@…` redacted | log line containing `postgres://user:pwd@host/db` | password component removed before persistence (AC-N4.2) |
| TC-NFR04-03 | Static | P0 | No `TASKQ_DB_URL` password fragment in logs or `/v1/metrics` | full-tree grep | 0 occurrences (AC-N4.3 / SPEC §8 #20) |
| TC-NFR04-04 | Negative | P0 | Forced 500 body + log line have no secrets | trigger 500, capture body + log | no `TASKQ_DB_URL`, no API key, no `Bearer …`, no `sk-…` (AC-N4.4) |

### NFR-05 — 文件覆蓋 (documentation)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR05-01 | Static | P0 | Public docstring coverage 100% | `ast-docstrings` over `03-development/src/` | coverage = 100% (AC-N5.1) |
| TC-NFR05-02 | Static | P0 | Every docstring has `[FR-XX]` or `[NFR-XX]` | unit test | missing references fail (AC-N5.2) |
| TC-NFR05-03 | Positive | P0 | Every endpoint has summary + description in OpenAPI | `GET /openapi.json` | every registered endpoint carries both fields (AC-N5.3) |

### NFR-06 — 架構分層契約 (architecture_constraints)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR06-01 | Static | P0 | `.importlinter` exists at repo root | file existence | present (AC-N6.1) |
| TC-NFR06-02 | Static | P0 | `lint-imports` exits 0 | run `lint-imports` | exit 0 (AC-N6.2) |
| TC-NFR06-03 | Static | P0 | Forbidden contract blocks `sqlalchemy` outside `repository/` | counter-test forces `from sqlalchemy import …` in `service/auth.py` | `lint-imports` exits non-zero (AC-N6.3) |
| TC-NFR06-04 | Static | P0 | Layers contract enforced | counter-test forces `from service import …` in `models/` | `lint-imports` exits non-zero (AC-N6.4) |
| TC-NFR06-05 | Static | P0 | `config` and `errors` are independence modules | inspect contract declarations | listed as independence modules (AC-N6.5) |

### NFR-07 — 依賴與授權合規 (license_compliance)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR07-01 | Static | P0 | All deps licenses in allowlist | `pip-licenses --format=json --with-system` | every license ∈ {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF} (AC-N7.1 / SPEC §8 #22) |
| TC-NFR07-02 | Static | P0 | `08-config/SBOM.json` structure | file presence + JSON shape | every entry has `name`, `version`, `license`, `direct|transitive` (AC-N7.2) |
| TC-NFR07-03 | Static | P0 | Transitive dep with non-allowlist license fails build | static check | build fails (AC-N7.3) |
| TC-NFR07-04 | Static | P0 | `requirements.lock` pins every transitive dep with `==` | file presence + pattern check | every line `package==version` (AC-N7.4) |

### NFR-08 — 變異測試 (mutation_testing)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR08-01 | Static | P0 | `features.mutation_testing: true` | read `.methodology/harness_config.json` | true (AC-N8.1) |
| TC-NFR08-02 | Positive | P0 | mutation score ≥ 70 | `harness_cli.py mutation-test-score --project .` | score ≥ 70; writes `.methodology/mutation_score.json` (AC-N8.2 / SPEC §8 #24) |
| TC-NFR08-03 | Static | P0 | Scope limited to `service/` + `repository/` | inspect config | scope-rationale recorded (AC-N8.3) |

### NFR-09 — 驗證真實性(零 skip 鐵律) (test_assertion_quality)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR09-01 | Positive | P0 | `pytest 03-development/tests -q` exits 0, skipped == 0 | run pytest | exit 0; `skipped == 0` (AC-N9.1 / SPEC §8 #1) |
| TC-NFR09-02 | Static | P0 | `zero_assert == 0` | scanned count of test functions with no `assert` | count == 0 (AC-N9.2) |
| TC-NFR09-03 | Static | P0 | No `--ignore` / `-k` / `--deselect` / `collect_ignore` / `testpaths` exclusions | config audit | none targeting FR/NFR tests (AC-N9.3) |
| TC-NFR09-04 | Positive | P0 | FR-07 against a real SQLite file | migration suite DB path | temp file, not `:memory:` (AC-N9.4 / SPEC §8 #12 round-specific clause) |
| TC-NFR09-05 | Positive | P0 | `TRACEABILITY_MATRIX.md` VERIFIED only on pass | matrix audit | every `VERIFIED` entry corresponds to a passing test (AC-N9.5) |

### NFR-10 — 整合覆蓋 (integration_coverage)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR10-01 | Positive | P0 | Integration suite coverage ≥ 80% | `pytest …/integration --cov=…/src --cov-report=term` | `TOTAL ≥ 80%` (AC-N10.1 / SPEC §8 #3) |
| TC-NFR10-02 | Static | P0 | No direct handler calls in integration suite | static scan for `@router.<verb>` and handler symbols | none found; every test uses `httpx.AsyncClient(transport=ASGITransport(app))` (AC-N10.2) |
| TC-NFR10-03 | Positive | P0 | Every error code exercised | integration suite enumeration | ≥ 1 example each of 401/403/404/409/422/429/503 (AC-N10.3) |
| TC-NFR10-04 | Positive | P0 | Migration round-trip in integration | integration test runs the round-trip | passes (AC-N10.4 / FR-07 cross-cut) |

### NFR-11 — 可讀性 (readability)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR11-01 | Static | P0 | Project MI ≥ 80 (LLOC-weighted) | `radon mi 03-development/src/ -j` | average MI ≥ 80 (AC-N11.1) |
| TC-NFR11-02 | Static | P0 | Per-function CC ≤ 10 | `radon cc` | every function ≤ 10 (AC-N11.2) |
| TC-NFR11-03 | Static | P0 | No file > 400 lines, no dir > 15 files | static build-time guard | build fails on violation (AC-N11.3) |
| TC-NFR11-04 | Static | P0 | API handler ≤ 40 lines | static guard | build fails on violation (AC-N11.4) |

### NFR-12 — 系統驗證目標 (execute_verification_target)

| TC ID | Cat. | Priority | Description | Input | Expected Output |
|-------|------|----------|-------------|-------|-----------------|
| TC-NFR12-01 | Positive | P0 | `make verify-system` exits 0 | run target | exit 0 (AC-N12.1) |
| TC-NFR12-02 | Positive | P0 | `make verify-system` prints `verify-system: PASS` | same | stdout contains the literal string (AC-N12.2 / SPEC §8 #27) |
| TC-NFR12-03 | Positive | P0 | All four chained steps completed; non-zero on any failure | per-step assertions on the chain | every step observed; non-zero on failure (AC-N12.3) |

---

## 5. Cross-Cutting Tests

| TC ID | Cat. | Priority | Description | Expected Output |
|-------|------|----------|-------------|-----------------|
| TC-X-01 | End-to-end | P0 | Full CRUD chain on a single task via `httpx.AsyncClient(ASGITransport)` | 201 → 200 → 200 (list) → 204 (admin delete) |
| TC-X-02 | End-to-end | P0 | Submit task → run → poll → assert `done` & `task_results` row | end state matches expected |
| TC-X-03 | End-to-end | P0 | Rate-limit trigger then recovery | 429 → wait → next request 200 |
| TC-X-04 | End-to-end | P0 | Graceful drain during shutdown with in-flight tasks | in-flight completes; over-budget marked `interrupted` |
| TC-X-05 | End-to-end | P0 | Migration round-trip on real DB | byte-identical columns after round-trip |

---

## 6. Coverage Targets

| Metric | Target | Source |
|--------|--------|--------|
| Line coverage (`pytest --cov=03-development/src`) | **100%** | SPEC §8 #2 |
| Integration coverage (`pytest tests/integration --cov=…`) | **≥ 80%** | NFR-10 / SPEC §8 #3 |
| Mutation score (`harness_cli.py mutation-test-score`, scope: `service/` + `repository/`) | **≥ 70** | NFR-08 / SPEC §8 #24 |
| `skipped == 0` | **0** | NFR-09 / SPEC §8 #1 |
| `zero_assert == 0` | **0** | NFR-09 |
| Project MI (LLOC-weighted) | **≥ 80** | NFR-11 |
| Per-function CC | **≤ 10** | NFR-11 |
| `bandit -r 03-development/src/` | **0 HIGH / 0 MEDIUM** | NFR-02 / SPEC §8 #23 |

---

## 7. Mapping to `TEST_INVENTORY.yaml`

This plan is the **authoritative per-AC plan**; `TEST_INVENTORY.yaml` is the
**machine-tracked implementation status** keyed by `tc_id` of the form
`TC-FRNN-NN` (no category suffix). The mapping below lists each `TEST_INVENTORY`
entry against the planning TC(s) it instantiates. Where multiple TCs in this
plan cover a single inventory row, the inventory row picks the canonical one
(e.g. `TC-FR01-03-N1` for the AC-1.3 row; AC-1.8 inventory row maps to
`TC-FR01-08-B1` upper-bound).

The full inventory rows in `TEST_INVENTORY.yaml` cover all ACs from
AC-1.1 through AC-10.6 plus AC-N1.1 through AC-N12.3 (one TC per AC row at
minimum). Additional decompositions introduced by this plan (e.g.
TC-FR01-03-N1..N4, TC-FR08-06..08) are tracked as new `tc_id` rows in
`TEST_INVENTORY.yaml` once the per-FR TDD step opens.

---

## 8. Verification of Coverage

The plan above must satisfy:

1. Every `FR-01..FR-10` from `.methodology/quality_manifest.json` has at
   least one positive + one negative + one boundary TC.
2. Every `NFR-01..NFR-12` from the manifest has at least one positive
   acceptance TC and, where applicable, one static/structural TC.
3. Every AC-`N.N` from `SRS.md` §3 / §4 is referenced by at least one TC.

The verification command below confirms the cross-check (run once after the
plan is written; results not stored, just a sanity gate):

```bash
python - <<'PY'
import re, json, pathlib
plan = pathlib.Path("04-testing/TEST_PLAN.md").read_text()
manifest = json.loads(pathlib.Path(".methodology/quality_manifest.json").read_text())
fr_ids = manifest["fr_ids"]
nfr_ids = [f"NFR-{i:02d}" for i in range(1, 13)]
missing_fr = [fr for fr in fr_ids if not re.search(rf"\b{fr}\b", plan)]
missing_nfr = [n for n in nfr_ids if not re.search(rf"\b{n}\b", plan)]
print("missing FR:", missing_fr or "none")
print("missing NFR:", missing_nfr or "none")
PY
```

Expected output: `missing FR: none` / `missing NFR: none`.
