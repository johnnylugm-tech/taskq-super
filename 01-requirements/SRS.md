# Software Requirements Specification (SRS) — taskq-api

> **Source of truth**: `SPEC.md` (v1.0.0, 2026-07-30, 10 FR / 12 NFR / 12 env vars).
> This SRS is authored in **INGESTION MODE**: every `### FR-01..FR-10` and
> `### NFR-01..NFR-12` heading below is transcribed verbatim from `SPEC.md` §3
> and §4 respectively. No invention, no silent omission — any deferred or
> ambiguous clause is captured under §7 Open Issues with a `NFR-99` /
> `FR-XX-deferred` tag.

---

## 1. Introduction

### 1.1 Project identity

| Field | Value |
|-------|-------|
| Project name | `taskq-api` |
| Purpose | Task-queue HTTP service — submit, query, execute shell-command tasks over a REST API; persist to a relational database through SQLAlchemy; evolve the schema with Alembic; authenticate with hashed API keys, authorise by scope, and throttle per token |
| Language | Python 3.11 |
| Form factor | ASGI service, launched via `uvicorn taskq_api.app:app`; also a `python -m taskq_api` admin entry (migrate / seed / healthcheck) |
| Validation round | Round 2 of 3 in the progressive harness-methodology test-bed (round 1 = `taskq-plus` CLI; round 3 = TypeScript, deferred) |

### 1.2 Scope

`taskq-api` provides a layered four-tier HTTP service (`api > service >
repository > models`, NFR-06) that exposes the endpoints documented in §3 and
honors the non-functional guarantees in §4. It is the second test-bed round for
the harness methodology and exists specifically to surface signals that
neither of the previous test-beds could reach:

| Previously uncovered axis | This round's countermeasure | Clause |
|---|---|---|
| No HTTP layer → `security` only saw subprocess calls | REST API + API-key auth + per-token scope + rate limiting | FR-03/04/05, NFR-02 |
| No database → ORM, transactions, connection pools, N+1 all absent | SQLAlchemy ORM + explicit transaction boundaries + N+1 assertions | FR-06, NFR-01 |
| "Schema migration" was a hand-rolled JSON `version` field, and tests were skipped | Alembic three-revision evolution with one data-moving step and reversible downgrades | FR-07, NFR-03 |
| No async → framework scanners have never met `async def` | async endpoints + asyncio background runner | FR-08, NFR-03 |
| Shallow dependency tree | fastapi / sqlalchemy / alembic / uvicorn plus transitives, lock-file pinned | NFR-07 |
| Integration tests only ever drove a CLI subprocess | `httpx.ASGITransport` end-to-end including every error code | NFR-10 |

### 1.3 Definitions

See §9 Glossary.

---

## 2. Constraints

These constraints are binding: a build that violates any of them is out of scope
for acceptance — NFR-06 / NFR-07 / NFR-09 / NFR-11 measure specific aspects of
them.

### 2.1 Technical constraints

- Python 3.11.
- FastAPI ASGI application (`uvicorn taskq_api.app:app`).
- SQLAlchemy 2.x with explicit `Session` transaction boundaries (FR-06).
- Alembic for schema migrations (FR-07).
- `asyncio.create_subprocess_exec` for task execution — **`shell=True` is
  forbidden everywhere** (NFR-02 / NFR-03).

### 2.2 Architecture constraints

- Four layers `api > service > repository > models` enforced by a mandatory
  `.importlinter` contract (NFR-06).
- `config` and `errors` are independence modules.
- **`sqlalchemy` may only be imported by `repository/`** — ORM leakage into
  the business layer is the anti-pattern this round guards against (NFR-06).

### 2.3 Security constraints

- API keys stored as SHA-256 hashes and compared with `hmac.compare_digest`
  (FR-03, NFR-02).
- 403 responses must not reveal whether the resource exists (FR-04).
- No string-concatenated SQL anywhere (FR-06, NFR-02).
- CORS denies all origins by default (NFR-02).
- Error bodies must not carry stack traces, SQL, or file paths (FR-10, NFR-02).

### 2.4 Migration constraints

- Three revisions — v1 base tables, v2 tags many-to-many, **v3 moves
  `tasks.result_json` into a `task_results` table with real data migration**
  (FR-07).
- `upgrade head` → sample write → `downgrade -1` → `upgrade head` must leave
  every column byte-identical (FR-07).

### 2.5 Async correctness constraints

- `asyncio.CancelledError` must propagate — it must never be swallowed by
  `except Exception` (FR-08, NFR-03).
- Task timeouts must actually kill the child process (`kill()` then
  `await wait()`), leaving no orphans (FR-08, NFR-03).
- Shutdown drains in-flight work up to `TASKQ_DRAIN_TIMEOUT` (FR-08).

### 2.6 Query efficiency constraints

- Relationship loads must be explicit (`selectinload` / `joinedload`).
- **N+1 is an acceptance failure** — the list endpoint's SQL statement count
  must be constant regardless of how many rows come back (NFR-01).

### 2.7 Readiness constraints

- `/readyz` returns 503 when the database is unreachable **or** when
  `alembic current` is not at head — deploying new code without running the
  migration must fail closed (FR-09).

### 2.8 Verification-honesty constraints

- Same zero-skip rule as round 1 (NFR-09).
- The three-step migration must be tested against a **real database file**, not
  a mock, and may not be downgraded to a skip on the grounds that "migration
  logic is hard to test" (NFR-09).

### 2.9 Configuration files (mandatory, not optional)

| File | Purpose | Backing clause |
|------|---------|----------------|
| `.importlinter` | layers contract + `sqlalchemy` forbidden contract | NFR-06 |
| `requirements.txt` + `requirements.lock` | pin + transitive lock | NFR-07 |
| `requirements-dev.txt` | `import-linter`, `pip-licenses`, `mutmut`, `pytest-benchmark`, `httpx` | NFR-06/07/08/10 |
| `alembic.ini` + `migrations/versions/` | three revisions | FR-07 |
| `.env.example` | all 12 `TASKQ_*` env vars declared | FR-05/06/08, NFR-02 |
| `.methodology/harness_config.json` | `features.mutation_testing: true`; `crg_cohesion_healthy` at default | NFR-08 |
| `Makefile` | `verify-system` target with migration round-trip | NFR-12 |

### 2.10 High-risk modules (per-module TDD required)

- `taskq_api.service.runner` (async subprocess)
- `taskq_api.service.auth` (auth + scope)
- `taskq_api.repository.session` (transaction boundary)
- `migrations/versions/v3_split_results.py` (data-moving migration)

### 2.11 Env-var inventory (canonical: SPEC.md §5.1)

| Variable | Default | Purpose |
|----------|---------|---------|
| `TASKQ_DB_URL` | `sqlite:///./taskq.db` | database connection string (**never logged** — NFR-04) |
| `TASKQ_DB_POOL_SIZE` | `5` | connection pool size (FR-06) |
| `TASKQ_TASK_TIMEOUT` | `10.0` | per-task subprocess timeout (seconds) |
| `TASKQ_MAX_CONCURRENT` | `8` | background execution concurrency cap (FR-08) |
| `TASKQ_DRAIN_TIMEOUT` | `30.0` | graceful drain budget on shutdown (FR-08) |
| `TASKQ_RATE_BURST` | `20` | token bucket capacity (FR-05) |
| `TASKQ_RATE_PER_SEC` | `5.0` | token refill rate (FR-05) |
| `TASKQ_CORS_ORIGINS` | (empty) | comma-separated allowlist; empty = deny all (NFR-02) |
| `TASKQ_LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |
| `TASKQ_LOG_FORMAT` | `json` | json / text |
| `TASKQ_HOST` | `127.0.0.1` | bind address (not public by default) |
| `TASKQ_PORT` | `8000` | bind port |

### 2.12 Database schema inventory (canonical: SPEC.md §5.2)

| Table | Revision | Key columns |
|-------|----------|-------------|
| `tasks` | v1 | `id` (uuid), `command`, `name`, `status`, `created_at` |
| `api_keys` | v1 | `id`, `key_hash` (sha256), `scope`, `created_at`, `revoked_at` |
| `rate_buckets` | v1 | `key_id` (FK), `tokens`, `updated_at` |
| `tags` | v2 | `id`, `label` |
| `task_tags` | v2 | `task_id`, `tag_id` (composite PK) |
| `task_results` | **v3** | `id`, `task_id` (FK), `exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at` |

`tasks.result_json` is created in v1 and removed in v3, its data migrated into
`task_results` — the focus of the round-trip reversibility acceptance test.

### 2.13 Module layout (canonical: SPEC.md §6)

```
03-development/src/taskq_api/
├── app.py                 # FastAPI assembly
├── config.py              # TASKQ_* env (independence)
├── errors.py              # RFC 7807 (independence, FR-10)
├── models/                # L1 — ORM tables + pydantic schemas
├── repository/            # L2 — the ONLY layer allowed to import sqlalchemy
│   ├── session.py         # Session + transaction context manager — high-risk
│   ├── task_repo.py
│   ├── key_repo.py
│   └── rate_repo.py
├── service/               # L3 — business logic, no ORM leakage
│   ├── tasks.py           # FR-01
│   ├── runner.py          # FR-02/08 async runner — high-risk
│   ├── auth.py            # FR-03/04 — high-risk
│   └── ratelimit.py       # FR-05
└── api/                   # L4 — FastAPI routes
    ├── deps.py            # single authn/authz decision point (FR-04)
    ├── tasks.py
    └── health.py

migrations/versions/
├── v1_initial.py
├── v2_tags.py
└── v3_split_results.py    # data migration + reversible downgrade — high-risk
```

Layering (enforced by `.importlinter`, NFR-06): `api > service > repository >
models`; `config` / `errors` independent; `sqlalchemy` importable only from
`repository/`.

---

## 3. Functional Requirements

> Each FR below is transcribed from SPEC.md §3. Every AC carries an `AC-NN.M`
> identifier; TEST_SPEC.md cites these in Phase 2.

### FR-01: 任務資源 CRUD API

| Method | Path | scope | Behaviour |
|--------|------|-------|-----------|
| `POST` | `/v1/tasks` | `write` | Create task; body validated by `TaskCreate` pydantic model |
| `GET` | `/v1/tasks/{id}` | `read` | Fetch single task with all fields |
| `GET` | `/v1/tasks` | `read` | Paginated list, supports `?status=`, `?limit=`, `?cursor=` |
| `DELETE` | `/v1/tasks/{id}` | `admin` | Delete task (including result rows, in same transaction) |

Validation rules follow round-1 FR-01 (non-empty, ≤1000 chars, injection
black-list, unique name); violation → **HTTP 422** + problem+json. Unknown
id → **HTTP 404** + problem+json. Pagination is **cursor-based** (offset is
forbidden — large-table offset scan is an N+1 cousin). Default `limit` is 50,
upper bound 200; exceeding the upper bound → 422.

**Acceptance criteria (FR-01)**

> **DERIVED: SPEC.md §3 FR-01 body + §8 #4..#8 acceptance list** —
> AC-1.1..AC-1.10 decompose the canonical FR-01 endpoint table into
> individually testable commands; each criterion cites the SPEC.md §8 row
> it derives from, and the wording follows the canonical FR-01 endpoints
> (POST 422/201, GET 200/404, DELETE 403/204) plus the cursor-pagination
> and `limit` rules stated in canonical §3 FR-01.

- **AC-1.1** — `POST /v1/tasks` with a valid `write` key and a body that
  satisfies `TaskCreate` returns **HTTP 201** with a task id. *(SPEC.md §8 #4)*
- **AC-1.2** — `POST /v1/tasks` without an `X-API-Key` header returns **HTTP
  401** + problem+json. *(SPEC.md §8 #5)*
- **AC-1.3** — `POST /v1/tasks` with a body that violates `TaskCreate` (empty
  `command`, length > 1000, injection black-list hit, or missing required
  fields) returns **HTTP 422** + problem+json.
- **AC-1.4** — `GET /v1/tasks/{id}` with a valid `read` key returns **HTTP 200**
  with all columns of `tasks` (`id`, `command`, `name`, `status`, `created_at`,
  tags, results) for a known id.
- **AC-1.5** — `GET /v1/tasks/{unknown}` with a valid `read` key returns
  **HTTP 404** + problem+json. *(SPEC.md §8 #7)*
- **AC-1.6** — `DELETE /v1/tasks/{id}` with a `write`-scope key (non-admin)
  returns **HTTP 403** + problem+json; the response body must not reveal
  whether `id` exists. *(SPEC.md §8 #6)*
- **AC-1.7** — `POST /v1/tasks` with a duplicate `name` returns **HTTP 409**
  + problem+json. *(SPEC.md §8 #8)*
- **AC-1.8** — `GET /v1/tasks?limit=N` accepts `N ∈ [1, 200]`; default `limit`
  is 50; `N > 200` → **HTTP 422** + problem+json.
- **AC-1.9** — `GET /v1/tasks` uses **cursor-based pagination** (no offset
  parameter); subsequent pages are reachable through `?cursor=`.
- **AC-1.10** — `DELETE /v1/tasks/{id}` with an `admin` key and a known id
  succeeds **HTTP 204**; the same transaction deletes any matching rows in
  `task_results` and `task_tags`.

### FR-02: 任務執行端點

`POST /v1/tasks/{id}/run` (`write`) → **HTTP 202 Accepted**, body contains a
`run_id`. Actual execution proceeds via `asyncio.create_subprocess_exec(*shlex
.split(command))`; **`shell=True` is forbidden**, timeout is
`TASKQ_TASK_TIMEOUT`. Status machine: `pending → running → done | failed |
timeout`. Execution results are written into the `task_results` table (FR-07's
v3 schema) with columns `exit_code` / `stdout_tail` / `stderr_tail` /
`duration_ms` / `finished_at`. `GET /v1/tasks/{id}/runs` (`read`) returns the
task's run history, newest first.

**Acceptance criteria (FR-02)**

> **DERIVED: SPEC.md §3 FR-02 body + §8 #25** — AC-2.1..AC-2.6 decompose
> the canonical FR-02 (one run endpoint, one runs endpoint, status machine,
> results table columns) into individually testable commands. The
> `kill()`+`await wait()` no-orphan clause is verbatim from §3 FR-08 body.

- **AC-2.1** — `POST /v1/tasks/{id}/run` returns **HTTP 202** with a body
  containing a `run_id`; the started subprocess did not use `shell=True`
  (verified by `grep -n shell=True 03-development/src/` returning 0 matches).
- **AC-2.2** — The started subprocess runs `shlex.split(command)` — every
  element of `argv` is one token; shell metacharacters are passed through to
  `execve`, never interpreted by a shell.
- **AC-2.3** — Successful execution transitions the task through
  `pending → running → done`; the resulting row in `task_results` carries
  `exit_code == 0`, non-empty `finished_at`, and a `duration_ms` ≥ 0.
- **AC-2.4** — A task whose subprocess exits non-zero transitions to
  `failed`; `exit_code` is preserved in `task_results`.
- **AC-2.5** — A task that exceeds `TASKQ_TASK_TIMEOUT` transitions to
  `timeout`; the child process is killed (`kill()` then `await wait()`), no
  orphan remains. *(SPEC.md §8 #25; FR-08)*
- **AC-2.6** — `GET /v1/tasks/{id}/runs` returns the run history, ordered
  newest-first by `finished_at` desc.

### FR-03: API Key 認證

All `/v1/*` endpoints require `X-API-Key`; missing or invalid → **HTTP 401** +
problem+json. Keys are stored as **SHA-256 hashes** in `api_keys`; plaintext
is never persisted. Comparison uses `hmac.compare_digest` (constant time).
Keys are minted by `python -m taskq_api key create --scope <scope>`; the
plaintext is **printed exactly once at creation**. Keys whose `revoked_at` is
non-null are treated as invalid. `/healthz` and `/readyz` do not require auth
(FR-09).

**Acceptance criteria (FR-03)**

> **DERIVED: SPEC.md §3 FR-03 body + §8 #5, #18** — AC-3.1..AC-3.6
> decompose the canonical FR-03 (header requirement, hash storage,
> `hmac.compare_digest` compare, `revoked_at`, `/healthz`/`/readyz`
> exemptions) into individually testable commands; AC-3.3 directly mirrors
> SPEC.md §8 #18's `key_hash is 64 hex chars` assertion.

- **AC-3.1** — Every `/v1/*` endpoint, when called without `X-API-Key` or
  with an unknown key, returns **HTTP 401** + problem+json. *(SPEC.md §8 #5)*
- **AC-3.2** — `python -m taskq_api key create --scope write` prints exactly
  one plaintext line on stdout, then exits; the line is never written to a
  log, error, or metrics endpoint.
- **AC-3.3** — A query against the `api_keys` table shows `key_hash` is a
  64-character hex string (SHA-256); no column contains the plaintext.
  *(SPEC.md §8 #18)*
- **AC-3.4** — The compare function is `hmac.compare_digest`; a unit test
  asserts the call site, not the effect.
- **AC-3.5** — A key whose `revoked_at` is non-null is rejected with
  **HTTP 401** on every subsequent request.
- **AC-3.6** — `GET /healthz` and `GET /readyz` succeed **without** an
  `X-API-Key` header. *(FR-09)*

### FR-04: Scope 授權

Each key carries a scope — `read` < `write` < `admin` (inclusive hierarchy).
Endpoint-required scopes are listed in the FR-01/02 tables; insufficient scope
→ **HTTP 403** + problem+json, and the body **must not leak whether the
resource exists**. The authz decision must be made in a **single dependency** —
a test asserts that every `/v1` route passes through it.

**Acceptance criteria (FR-04)**

> **DERIVED: SPEC.md §3 FR-04 body + §8 #6** — AC-4.1..AC-4.5 decompose
> the canonical FR-04 (scope hierarchy `read < write < admin`, single
> dependency decision point, no-existence-leak in 403) into individually
> testable commands; AC-4.5's "every /v1 route's `dependencies=` includes
> the single authz dep" derives verbatim from canonical §3 FR-04's
> "授權判定必須在單一中介層(dependency)完成" line.

- **AC-4.1** — A `read`-key calling `POST /v1/tasks` returns **HTTP 403**;
  the body is RFC 7807 problem+json, not a 401.
- **AC-4.2** — A `write`-key (non-admin) calling `DELETE /v1/tasks/{id}`
  returns **HTTP 403** + problem+json; the body must not say whether `id`
  exists. *(SPEC.md §8 #6)*
- **AC-4.3** — A `write`-key calling `POST /v1/tasks/{id}/run` returns
  **HTTP 202**; a `read`-key calling the same endpoint returns **HTTP 403**.
- **AC-4.4** — An `admin`-key calling any endpoint succeeds.
- **AC-4.5** — A dependency-graph test enumerates every `/v1` route's
  `dependencies=` and asserts each one includes the single authz dependency.

### FR-05: 流量控制

Per-token token bucket: capacity `TASKQ_RATE_BURST`, refill rate
`TASKQ_RATE_PER_SEC`. Over-limit → **HTTP 429** + problem+json + `Retry-After`
header (seconds). Bucket state lives in the database (consistent across
workers); updates happen in a single transaction with row-level lock.
`/healthz` and `/readyz` are not rate-limited.

**Acceptance criteria (FR-05)**

> **DERIVED: SPEC.md §3 FR-05 body + §8 #9** — AC-5.1..AC-5.5 decompose
> the canonical FR-05 (token bucket parameters, DB-persisted state,
> row-level lock, health exemptions) into individually testable commands.
> AC-5.4's "2× burst parallel from same token" + AC-5.5's row-level-lock
> wording derive from SPEC.md §9 R12 risk row "單一交易 + row-level lock".

- **AC-5.1** — `TASKQ_RATE_BURST` consecutive requests within the same
  refilling window, on the same token, return **HTTP 429** + problem+json +
  `Retry-After` header on the over-budget request. *(SPEC.md §8 #9)*
- **AC-5.2** — `Retry-After` is a positive integer (seconds) computed against
  the current bucket state.
- **AC-5.3** — `GET /healthz` and `GET /readyz` are exempt from the rate
  limit; repeated calls from the same token never return 429.
- **AC-5.4** — A concurrency test fires `2 * TASKQ_RATE_BURST` requests in
  parallel from the same token; the number of 429s plus 2xx responses equals
  the request count; no extra 2xx is admitted (no over-admission race).
- **AC-5.5** — Bucket state lives in `rate_buckets`; updates acquire a
  row-level lock in a single transaction (no lost-update race).

### FR-06: 持久化層與交易邊界

All data access goes through `repository/`; the business layer must not hold a
`Session`. One `Session` per request, transaction boundary explicit: success
commits, exception rolls back — guaranteed by a context manager. **String-
concatenated SQL is forbidden**; always ORM or parameterised (NFR-02).
Relationship loads must be explicit (`selectinload` / `joinedload`) — **N+1
is an acceptance failure** (NFR-01). Pool: `pool_size=TASKQ_DB_POOL_SIZE`,
`pool_pre_ping=True`.

**Acceptance criteria (FR-06)**

> **DERIVED: SPEC.md §3 FR-06 body + §8 #16, #17, #21** — AC-6.1..AC-6.5
> decompose the canonical FR-06 (one Session per request, context-manager
> boundaries, no raw SQL, explicit eager loading, pool_pre_ping) into
> individually testable commands; AC-6.1, AC-6.2, AC-6.4 cite SPEC.md §8
> rows verbatim.

- **AC-6.1** — `grep -rn "shell=True\|eval(\|exec(" 03-development/src/`
  returns **0** hits. *(SPEC.md §8 #16)*
- **AC-6.2** — A static-grep scan for f-string / `%` / `+` composed SQL
  fragments returns **0** hits across the source tree. *(SPEC.md §8 #17)*
- **AC-6.3** — A test enumerates the FastAPI request lifecycle and asserts
  that for every request exactly one `Session` is opened and that an
  exception in the handler triggers `session.rollback()`.
- **AC-6.4** — `lint-imports` exits **0**, and the forbidden contract blocks
  any `service/` or `api/` module from importing `sqlalchemy`.
  *(SPEC.md §8 #21)*
- **AC-6.5** — SQLAlchemy engine is constructed with `pool_size=
  TASKQ_DB_POOL_SIZE` and `pool_pre_ping=True`.

### FR-07: Schema Migration (Alembic 三步演進)

Three revisions, every step must have a working `downgrade`:

| revision | upgrade content | downgrade requirement |
|----------|----------------|------------------------|
| **v1** | create `tasks` and `api_keys` tables | drop both tables |
| **v2** | add `tags`, `task_tags` (many-to-many) + `tasks.name` unique index | drop new tables and index, leave v1 data intact |
| **v3** | **data-moving**: split `tasks.result_json` into a separate `task_results` table, migrate existing data, then drop the original column | reverse-migrate back into `tasks.result_json` then drop `task_results`; **no data loss** |

`alembic upgrade head` and `alembic downgrade base` must both succeed.
**Round-trip reversibility acceptance**: `upgrade head` → write sample data →
`downgrade -1` → `upgrade head`, every sample column must be byte-identical
(v3 data migration is the focus of this clause). Destructive shortcuts such as
`op.execute("DROP TABLE ...")` are forbidden in place of a real downgrade.
The migration files themselves are included in test coverage (via Alembic's
offline SQL generation + assertions).

**Acceptance criteria (FR-07)**

> **DERIVED: SPEC.md §3 FR-07 body + §8 #12, #13** — AC-7.1..AC-7.7
> decompose the canonical FR-07 (three revisions, v1/v2/v2/v3 table map,
> data move in v3, no destructive shortcuts) into individually testable
> commands; AC-7.2's "no residual tables" wording derives from canonical
> §8 #13's "exit 0, 無殘留表", and AC-7.5 directly mirrors §3 FR-07's
> "資料不得遺失" clause.

- **AC-7.1** — `alembic upgrade head` and `alembic downgrade base` each
  exit **0** without error. *(SPEC.md §8 #13)*
- **AC-7.2** — After `alembic downgrade base` the database contains no
  residual tables from `tasks`, `api_keys`, `tags`, `task_tags`,
  `task_results`, or `rate_buckets`.
- **AC-7.3** — The three-step migration runs against a **real SQLite file**
  (not an in-memory mock); the file is the working DB throughout.
  *(SPEC.md §8 #12; NFR-09 round-specific clause)*
- **AC-7.4** — The round-trip `upgrade head → write sample → downgrade -1 →
  upgrade head` test reads back every column of every sample row and asserts
  equality with the pre-round-trip value. *(SPEC.md §8 #12)*
- **AC-7.5** — `alembic downgrade -1` after v3 cleanly reverses v3's data
  move; `tasks.result_json` is restored, `task_results` is gone, no rows
  were lost between the two upgrades.
- **AC-7.6** — A scan of the `migrations/versions/*.py` files asserts that
  none contains `op.execute("DROP TABLE ...")` (or equivalent destructive
  shortcuts that bypass a proper downgrade).
- **AC-7.7** — The migration files are exercised by Alembic's offline SQL
  generation (`alembic upgrade head --sql`) followed by an SQL-level
  assertion over the emitted statements.

### FR-08: 非同步執行器

Background execution is managed via `asyncio.TaskGroup`; on shutdown the
service performs **graceful drain** (waits for in-flight tasks up to
`TASKQ_DRAIN_TIMEOUT`; over-budget tasks are marked `interrupted`). Concurrency
cap `TASKQ_MAX_CONCURRENT`; excess tasks queue rather than spawn unbounded
coroutines. Task timeouts are enforced with `asyncio.wait_for`; the timeout
**must actually kill the child process** (`process.kill()` followed by
`await process.wait()`) — no orphan processes. Cancellation semantics:
`asyncio.CancelledError` **must propagate**; it must never be swallowed by
`except Exception` (NFR-03).

**Acceptance criteria (FR-08)**

> **DERIVED: SPEC.md §3 FR-08 body + §8 #25** — AC-8.1..AC-8.5 decompose
> the canonical FR-08 (asyncio.TaskGroup, drain budget, concurrency cap,
> kill()+await wait() on timeout, CancelledError propagation) into
> individually testable commands; AC-8.5's `except Exception` swallowing
> test derives from SPEC.md §9 R7 risk row.

- **AC-8.1** — A long-running task is interrupted by shutting the service
  down; in-flight tasks within `TASKQ_DRAIN_TIMEOUT` complete; over-budget
  tasks are marked `interrupted`. *(SPEC.md §8 #25)*
- **AC-8.2** — `PROCESS_COUNT_AFTER = 0` — `os.listdir('/proc/<pid>/task/')`
  (or POSIX equivalent) shows no orphan child process after a timeout-killed
  task. *(SPEC.md §8 #25; NFR-03)*
- **AC-8.3** — When `TASKQ_MAX_CONCURRENT + N` tasks are submitted, only
  `TASKQ_MAX_CONCURRENT` are running concurrently; the rest sit in a queue
  and never exceed the cap.
- **AC-8.4** — A test monkey-patches `asyncio.wait_for` to raise
  `asyncio.TimeoutError`, observes the corresponding child process, and
  asserts it was sent `SIGKILL` and reaped (`wait()` returned).
- **AC-8.5** — A test wraps a body that raises `asyncio.CancelledError`
  with `try: ... except Exception: ... ;`; the `except Exception` clause
  does not catch the cancellation — the `CancelledError` propagates.
  *(NFR-03)*

### FR-09: 健康檢查與可觀測性

| Endpoint | Auth | Behaviour |
|----------|------|-----------|
| `GET /healthz` | none | process alive → 200 `{"status":"ok"}` |
| `GET /readyz` | none | DB reachable **and** `alembic current == head` → 200; otherwise **503** with the body indicating which condition failed |
| `GET /v1/metrics` | `admin` | task counts by status, execution latency percentiles, rate-limit rejection counts |

`/readyz`'s "migration not at head" check is critical: deploying new code
without running migrations must **fail closed**.

**Acceptance criteria (FR-09)**

> **DERIVED: SPEC.md §3 FR-09 endpoint table + §8 #10, #11** —
> AC-9.1..AC-9.6 decompose the canonical FR-09 three-endpoint table
> (`/healthz`, `/readyz`, `/v1/metrics`) into individually testable
> commands; AC-9.2, AC-9.3, AC-9.4 cite SPEC.md §8 rows verbatim and
> AC-9.6 derives from canonical §3 FR-09's "deploying new code without
> running migrations must fail closed" clause.

- **AC-9.1** — `GET /healthz` returns **HTTP 200** with
  `{"status":"ok"}` while the process is alive. *(SPEC.md §8 #10 setup)*
- **AC-9.2** — When the database is unreachable, `GET /readyz` returns
  **HTTP 503** with a body explaining the database condition.
  *(SPEC.md §8 #10)*
- **AC-9.3** — After `alembic downgrade -1`, `GET /readyz` returns **HTTP
  503** with a body explaining that migration is not at head.
  *(SPEC.md §8 #11)*
- **AC-9.4** — `GET /readyz` returns **HTTP 200** only when DB is reachable
  **and** `alembic current == head`.
- **AC-9.5** — `GET /v1/metrics` with an `admin` key returns task counts
  per status, latency percentiles, and rate-limit rejections; without an
  `admin` key it returns **HTTP 403**.
- **AC-9.6** — After deploying a newer code revision without running the
  migration, `GET /readyz` returns **HTTP 503**.

### FR-10: 錯誤契約 (RFC 7807)

Every non-2xx response carries `Content-Type: application/problem+json`. Body
fields: `type` (URI), `title`, `status`, `detail`, `instance`,
`correlation_id`. **`detail` must not leak internals**: no SQL statements,
stack traces, file paths, or schema introspection. `correlation_id` appears
both in the response header `X-Correlation-Id` and in the server log; it is
the join key. Error-code map: 422 validation / 401 unauthenticated / 403
insufficient scope / 404 unknown resource / 409 name conflict / 429 over
limit / 503 not-ready / 500 other.

**Acceptance criteria (FR-10)**

> **DERIVED: SPEC.md §3 FR-10 body + §7 error map** — AC-10.1..AC-10.6
> decompose the canonical FR-10 (RFC 7807 envelope, field allowlist, no
> detail leaks, X-Correlation-Id join key, error-code map) into
> individually testable commands; AC-10.6's "every error code in §7 once
> each" derives from canonical SPEC.md §7's eight rows plus §8's per-code
> acceptance rows.

- **AC-10.1** — Every non-2xx response sets
  `Content-Type: application/problem+json`. *(SPEC.md §8 #19 setup)*
- **AC-10.2** — The body for any non-2xx response carries exactly the fields
  `type`, `title`, `status`, `detail`, `instance`, `correlation_id`; no
  others.
- **AC-10.3** — A response to an exception (forced 500) carries no
  stack trace, no SQL fragment, no file path in its `detail` or anywhere in
  the body. *(SPEC.md §8 #19)*
- **AC-10.4** — `correlation_id` value from the response header
  `X-Correlation-Id` matches the `correlation_id` in the JSON body.
- **AC-10.5** — The same `correlation_id` appears in the server log line
  emitted for that request.
- **AC-10.6** — Every error code in SPEC.md §7's table is exercised in the
  integration suite — 422, 401, 403, 404, 409, 429, 503, and 500 — exactly
  once each.

---

## 4. Non-Functional Requirements

> Each NFR below is transcribed from SPEC.md §4 with its canonical `dimension`.
> Every dimension is verified against `harness/harness/ssi/prompts/evaluate_dimension.md`'s
> current roster. Acceptance criteria carry `AC-Nx.y` identifiers; a coverage
> note is attached where the dimension's evaluation method is narrower than
> the NFR demands (the AC then requires a dedicated implementation task in
> Phase 3 onward).

### NFR-01: 效能與查詢效率

- **dimension**: `performance`

`GET /v1/tasks/{id}` on 10,000 rows: **p95 < 30ms** (excluding network,
measured via ASGI transport). `GET /v1/tasks?limit=50` on 10,000 rows:
**p95 < 80ms**. **N+1 is a fail condition**: the list endpoint must issue a
constant number of SQL statements per request regardless of how many rows
come back — asserted via SQLAlchemy event-listener count. Measurement tool:
`pytest-benchmark`.

**Acceptance criteria (NFR-01)**

> **DERIVED: SPEC.md §4 NFR-01 body + §8 #14, #15** — AC-N1.1..AC-N1.4
> decompose the canonical NFR-01 (id p95 < 30ms, list p95 < 80ms at 10k
> rows, constant SQL count) into individually testable commands;
> AC-N1.1/AC-N1.2 numbers cite SPEC.md §11 monitoring thresholds verbatim.

- **AC-N1.1** — `pytest-benchmark` on `GET /v1/tasks/{id}` against 10,000
  rows reports p95 < 30ms. *(SPEC.md §8 #15; §11 monitoring threshold)*
- **AC-N1.2** — `pytest-benchmark` on `GET /v1/tasks?limit=50` against
  10,000 rows reports p95 < 80ms.
- **AC-N1.3** — A SQLAlchemy event-listener counts statements emitted by
  `GET /v1/tasks`; the count is the same for 1 row, 50 rows, 200 rows,
  and 10,000 rows. *(SPEC.md §8 #14)*
- **AC-N1.4** — The relationship load uses `selectinload` or `joinedload`;
  the unit test asserts the chosen loader, not just the result count.

> **Coverage note (NFR-01 → `performance`)**: the harness `performance`
> evaluator (`evaluate_dimension.md` Step 1 `performance`) reads
> `.sessi-work/benchmark_report.json` and applies a mean-latency formula only
> (`mean > 3000ms → −50`, `mean > 1000ms → −25`). It does NOT assert a
> constant SQL statement count. AC-N1.3 therefore requires a dedicated
> implementation task in Phase 3 that gates the SQL count assertion outside
> the dimension's own scoring path.

### NFR-02: HTTP 與資料層安全

- **dimension**: `security`

Across the codebase, `shell=True`, `eval(`, `exec(` are forbidden (grep 0
hits). **String-concatenated SQL is forbidden** — no f-string / `%` / `+`
fragments composing SQL; ORM or parameterised queries only (verified by grep
plus code review). API keys stored hashed; compared with `hmac.compare_digest`
(FR-03). 403 responses must not leak resource existence (FR-04). Error body
must not contain stack / SQL / path (FR-10). CORS **denies all origins by
default**; allowlist is `TASKQ_CORS_ORIGINS`. `bandit -r 03-development/src/`:
**0 HIGH, 0 MEDIUM**.

**Acceptance criteria (NFR-02)**

> **DERIVED: SPEC.md §4 NFR-02 body + §8 #16, #17, #23** —
> AC-N2.1..AC-N2.6 decompose the canonical NFR-02 (shell/eval/exec ban,
> SQL concatenation ban, hashed key compare, 403 no-leak, CORS
> deny-by-default, bandit 0/0) into individually testable commands;
> AC-N2.1, AC-N2.2, AC-N2.4 cite SPEC.md §8 rows verbatim.

- **AC-N2.1** — `grep -rn "shell=True\|eval(\|exec(" 03-development/src/`
  returns **0** matches. *(SPEC.md §8 #16)*
- **AC-N2.2** — A static scan for f-string / `%` / `+` composed SQL
  fragments returns **0** matches across the source tree.
  *(SPEC.md §8 #17)*
- **AC-N2.3** — A 403 response body for a hidden resource looks identical to
  one for a missing one. *(SPEC.md §8 #6)*
- **AC-N2.4** — `bandit -r 03-development/src/` reports 0 HIGH and 0 MEDIUM
  findings. *(SPEC.md §8 #23)*
- **AC-N2.5** — With `TASKQ_CORS_ORIGINS` empty, a CORS preflight request
  from any origin is rejected by the server.
- **AC-N2.6** — With `TASKQ_CORS_ORIGINS` set to a specific origin list, a
  CORS request from an unlisted origin is rejected; a request from a listed
  origin succeeds.

### NFR-03: 錯誤處理、交易與非同步正確性

- **dimension**: `error_handling`

Per-request transaction boundary is explicit: success commit, exception
rollback, guaranteed by context manager (FR-06). **No** bare `except:` or
`except Exception: pass` is allowed. **`asyncio.CancelledError` must NOT be
swallowed** — it must re-raise (async's specific swallowing trap). Database
connection failure → `/readyz` 503 with explicit `detail`; no silent
infinite-retry. Task timeout must really terminate the child process, no
orphans (FR-08). Migration failure → transaction rollback, DB remains at the
previous revision (FR-07).

**Acceptance criteria (NFR-03)**

> **DERIVED: SPEC.md §4 NFR-03 body + §8 #10, #25** —
> AC-N3.1..AC-N3.5 decompose the canonical NFR-03 (transaction boundary,
> no bare except, CancelledError propagation, /readyz 503 on DB down,
> migration rollback) into individually testable commands; AC-N3.1's
> anti-pattern names derive from the framework
> `evaluate_dimension.md` `error_handling` anti-pattern list, NOT from
> SPEC.md text — the harness tool is the implementation-side authority.

- **AC-N3.1** — `ast-error-handling` reports zero `bare_except`,
  `broad_swallow`, or `except_base_exception` patterns in
  `03-development/src/`. *(coverage: `evaluate_dimension.md` `error_handling`
  anti-pattern list)*
- **AC-N3.2** — A unit test asserts that an async function whose body raises
  `asyncio.CancelledError` does **not** get caught by `except Exception`;
  the cancellation propagates out of the handler. *(SPEC.md §8 #25)*
- **AC-N3.3** — With the database temporarily down, `GET /readyz` returns
  503 + RFC 7807 detail; the service does not busy-loop retrying the
  connection. *(SPEC.md §8 #10)*
- **AC-N3.4** — Triggering a transaction-boundary exception during a
  request causes the rollback; the rolled-back row is absent on a follow-up
  read.
- **AC-N3.5** — A failing migration leaves the database at the previous
  revision (no partial schema). *(FR-07)*

### NFR-04: 敏感資料遮蔽

- **dimension**: `security`

`stdout_tail` / `stderr_tail` / logs / error bodies must redact lines matching
`(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
— the entire line is replaced with `[REDACTED]` before persistence or
emission. **Database connection strings** (including password) must not
appear in any log, error message, or `/v1/metrics` response. API-key
plaintext is printed only once, at `key create`; never persisted.

**Acceptance criteria (NFR-04)**

> **DERIVED: SPEC.md §4 NFR-04 body + §8 #20** — AC-N4.1..AC-N4.4
> decompose the canonical NFR-04 (regex redaction, DB URL no-log, API key
> one-print) into individually testable commands; AC-N4.1's regex pattern
> names derive verbatim from canonical SPEC.md §4 NFR-04.

- **AC-N4.1** — A log line containing `sk-abcdefgh12345678` is captured by
  the redaction filter and stored as `... [REDACTED]` (original line
  replaced).
- **AC-N4.2** — A log line containing `postgres://user:pwd@host/db` is
  captured and the password component is removed before persistence.
- **AC-N4.3** — A full-tree grep over logs and over `/v1/metrics` output
  produces **0** occurrences of the `TASKQ_DB_URL` password fragment.
  *(SPEC.md §8 #20)*
- **AC-N4.4** — Forcing a 500 emits an error body; the body and the log
  line for the same request both contain no `TASKQ_DB_URL` value, no API
  key, no `Bearer …` token, no `sk-…` secret. *(SPEC.md §8 #20; FR-10)*

### NFR-05: 文件覆蓋

- **dimension**: `documentation`

Every public function/class carries a docstring containing `[FR-XX]` or
`[NFR-XX]` references — coverage **100%**. Every API endpoint has `summary`
and `description` in OpenAPI (`/openapi.json` assertions).

**Acceptance criteria (NFR-05)**

> **DERIVED: SPEC.md §4 NFR-05 body** — AC-N5.1..AC-N5.3 decompose the
> canonical NFR-05 (100% docstring coverage with [FR-XX]/[NFR-XX]
> references, OpenAPI summary + description per endpoint) into
> individually testable commands; AC-N5.1's metric phrase "public-API
> docstring coverage of 100%" derives from the harness
> `evaluate_dimension.md` `documentation` dimension formula (rounds
> `100 × public_with_docstring / total_public`).

- **AC-N5.1** — `ast-docstrings` reports public-API docstring coverage of
  **100%** over `03-development/src/`. *(coverage: harness
  `documentation` dimension)*
- **AC-N5.2** — A docstring-coverage unit test asserts that every public
  symbol's docstring contains at least one `[FR-XX]` or `[NFR-XX]`
  reference; missing references fail the test.
- **AC-N5.3** — FastAPI's `/openapi.json` lists every registered endpoint
  with both `summary` and `description`; an integration test asserts presence
  of each.

### NFR-06: 架構分層契約

- **dimension**: `architecture_constraints`

`/project` root must contain `.importlinter` declaring the layers contract:

```
api > service > repository > models
```

Upper layers may import lower layers; **lower layers may not import upper
layers**. `config` and `errors` are independence modules. **Additional
forbidden contract**: no layer outside `repository` may import `sqlalchemy` —
ORM leakage into the business layer is the anti-pattern this round guards.
`lint-imports` must **exit 0**. Removing `.importlinter`, wildcard
`ignore_imports`, or downgrading the contract just to pass is prohibited.

**Acceptance criteria (NFR-06)**

> **DERIVED: SPEC.md §4 NFR-06 body + §8 #21** — AC-N6.1..AC-N6.5
> decompose the canonical NFR-06 (mandatory .importlinter, layers
> contract `api > service > repository > models`, config/errors
> independence, sqlalchemy forbidden contract) into individually
> testable commands; AC-N6.2, AC-N6.3 cite SPEC.md §8 row verbatim.

- **AC-N6.1** — `.importlinter` exists at the project root.
- **AC-N6.2** — `lint-imports` exits **0**. *(SPEC.md §8 #21)*
- **AC-N6.3** — The forbidden contract blocks `service/auth.py` from
  importing `sqlalchemy`; a forced import makes `lint-imports` exit
  non-zero. *(SPEC.md §8 #21)*
- **AC-N6.4** — The layers contract enforces `api > service > repository >
  models`; a forced `from service import …` inside `models/` makes
  `lint-imports` exit non-zero.
- **AC-N6.5** — `config` and `errors` are independence modules — they have
  no inbound or outbound contract edge; the contract declarations list them
  as independence modules.

### NFR-07: 依賴與授權合規

- **dimension**: `license_compliance`

All runtime deps pinned via `==` in `requirements.txt`; transitive deps
fully locked via `requirements.lock`. Allowed licenses: MIT / BSD-2-Clause /
BSD-3-Clause / Apache-2.0 / PSF; any other license → that dep must not be
used. **Scan scope must include the full dependency tree** (direct +
transitive), evidence command: `pip-licenses --format=json --with-system`.
Produce SBOM at `08-config/SBOM.json`, every dep has `name` / `version` /
`license` / `direct|transitive`.

**Acceptance criteria (NFR-07)**

> **DERIVED: SPEC.md §4 NFR-07 body + §8 #22** — AC-N7.1..AC-N7.4
> decompose the canonical NFR-07 (== pinning, transitive lock via
> requirements.lock, allowlist of 5 named licenses, whole-tree scan via
> `pip-licenses --with-system`, SBOM at 08-config/SBOM.json with
> `direct|transitive` discriminator) into individually testable commands;
> AC-N7.1 cites SPEC.md §8 #22 verbatim.

- **AC-N7.1** — `pip-licenses --format=json --with-system` reports every
  package (direct and transitive); every reported license is in the
  allowlist. *(SPEC.md §8 #22)*
- **AC-N7.2** — `08-config/SBOM.json` exists; every entry has `name`,
  `version`, `license`, and `direct|transitive` (one of the two).
- **AC-N7.3** — A static check fails the build if a transitive dep with a
  non-allowlist license appears in the lock file.
- **AC-N7.4** — `requirements.lock` exists and pins every transitive dep
  with `==`.

> **Coverage note (NFR-07 → `license_compliance`)**: the harness
> `license_compliance` evaluator in `evaluate_dimension.md` uses
> `scancode --license`. That scan covers the LICENSE presence/keyword match
> per file in the source tree. The two clauses that are NOT covered by
> `scancode` alone are: (a) the transitive-tree license report via
> `pip-licenses --with-system`, (b) production of the `08-config/SBOM.json`
> artifact with the `direct|transitive` discriminator. AC-N7.1, AC-N7.2,
> AC-N7.3, and AC-N7.4 each require a dedicated implementation task in
> Phase 3 onward, separate from the dimension's own scoring path.

### NFR-08: 變異測試

- **dimension**: `mutation_testing`

`.methodology/harness_config.json` sets `features.mutation_testing: true`.
**mutation score ≥ 70**. Scope limited to `service/` and `repository/` —
the configuration notes the rationale (execution-time budget).

**Acceptance criteria (NFR-08)**

> **DERIVED: SPEC.md §4 NFR-08 body + §8 #24** — AC-N8.1..AC-N8.3
> decompose the canonical NFR-08 (`features.mutation_testing: true`,
> mutation score ≥ 70, scope limited to `service/` + `repository/`) into
> individually testable commands; AC-N8.2's harness CLI invocation derives
> from framework `evaluate_dimension.md` `mutation_testing` block ("Call
> the framework command: harness_cli.py mutation-test-score").

- **AC-N8.1** — `.methodology/harness_config.json` carries
  `features.mutation_testing: true`.
- **AC-N8.2** — `harness_cli.py mutation-test-score --project .` reports a
  `score ≥ 70` and writes `.methodology/mutation_score.json` with that
  score. *(SPEC.md §8 #24)*
- **AC-N8.3** — The mutation score is computed over `service/` and
  `repository/` only; the configuration records the scope-rationale note.

### NFR-09: 驗證真實性(零 skip 鐵律)

- **dimension**: `test_assertion_quality`

No FR/NFR verification test may be `pytest.skip` / `skipif` / `xfail` / a
no-assertion stub. `pytest 03-development/tests -q` **skipped count must
be 0**. Every test function has at least one `assert`
(`zero_assert == 0`). **Anti-fabrication clause**: tests may not be excluded
via `--ignore` / `-k` / `--deselect` / `collect_ignore` / directory
removal from `testpaths`. **Round-specific clause**: FR-07's three-step
migration must be tested against a **real database** (SQLite file, not in-
memory mock), and round-trip reversibility verified by actual data
comparison. Skipping on the grounds that "migration logic is hard to test"
is **not** permitted — that is precisely the failure pattern from the
previous rounds. `TRACEABILITY_MATRIX.md`'s `VERIFIED` may only be set
when a test has actually run and passed.

**Acceptance criteria (NFR-09)**

> **DERIVED: SPEC.md §4 NFR-09 body + §8 #1** — AC-N9.1..AC-N9.5
> decompose the canonical NFR-09 (zero skip, zero zero-assert, no
> pytest-level exclusions, FR-07 against real SQLite, VERIFIED only on
> pass) into individually testable commands; AC-N9.2's `zero_assert == 0`
> phrase derives from the framework `evaluate_dimension.md`
> `test_assertion_quality` dimension.

- **AC-N9.1** — `pytest 03-development/tests -q` exits 0 and reports
  `skipped == 0`. *(SPEC.md §8 #1)*
- **AC-N9.2** — A scanned count of test functions with zero `assert`
  statements is **0**. *(coverage: harness `test_assertion_quality`)*
- **AC-N9.3** — Build-time check: pytest configuration contains no
  `--ignore`, `-k`, `--deselect`, `collect_ignore`, or `testpaths`
  exclusions targeting any FR/NFR-mapped tests.
- **AC-N9.4** — FR-07's migration test suite runs against a real SQLite
  file (not an in-memory mock); the file is the working DB throughout.
  *(round-specific clause; SPEC.md §8 #12)*
- **AC-N9.5** — `TRACEABILITY_MATRIX.md` entries for FR-NN / NFR-NN carry
  `VERIFIED` only when the corresponding test has run and passed.

### NFR-10: 整合覆蓋

- **dimension**: `integration_coverage`

`03-development/tests/integration/` line coverage **≥ 80%**. Integration tests
must be driven via `httpx.AsyncClient(transport=ASGITransport(app))`; **no
direct handler function calls**. At minimum: full CRUD chain; one example
each of 401 / 403 / 404 / 409 / 422 / 429 / 503; migration round-trip; rate
limit trigger and recovery; graceful drain.

**Acceptance criteria (NFR-10)**

> **DERIVED: SPEC.md §4 NFR-10 body + §8 #3** — AC-N10.1..AC-N10.4
> decompose the canonical NFR-10 (integration suite ≥ 80% line coverage,
> `httpx.ASGITransport` driver only, every error code plus migration
> round-trip) into individually testable commands; AC-N10.1's totals
> derive from SPEC.md §11 monitoring threshold "整合覆蓋率 ≥ 80%".

- **AC-N10.1** — `pytest 03-development/tests/integration --cov=
  03-development/src --cov-report=term` reports `TOTAL ≥ 80%`.
  *(SPEC.md §8 #3)*
- **AC-N10.2** — A static scan of `03-development/tests/integration` finds
  no direct call to a `@router.<verb>` symbol or to a handler function
  body; every test goes through `httpx.AsyncClient(transport=
  ASGITransport(app))`. *(SPEC.md §8 #3 intent)*
- **AC-N10.3** — The integration suite contains at least one example each of
  401 / 403 / 404 / 409 / 422 / 429 / 503. *(FR-10 / SPEC.md §8 #5..#11)*
- **AC-N10.4** — The migration round-trip is exercised in the integration
  suite, not only in unit. *(FR-07 / SPEC.md §8 #12)*

> **Coverage note (NFR-10 → `integration_coverage`)**: the harness
> `integration_coverage` evaluator runs the integration suite with `--cov=
  03-development/src --cov-report=term-missing` and reports the TOTAL line
> coverage percentage. AC-N10.2 (no direct handler calls) is NOT covered
> by that measurement — it requires a static scan that fails when handler
> bodies are called directly. AC-N10.3 (every error code exercised) is
> enforced only by the integration suite's own test count; the
> dimension's tool does not enumerate error codes.

### NFR-11: 可讀性

- **dimension**: `readability`

Project MI (LLOC-weighted) **≥ 80**; per-function CC **≤ 10**. Single file
≤ 400 lines; single directory ≤ 15 files. Each API handler ≤ 40 lines
(business logic must drop down into `service/`).

**Acceptance criteria (NFR-11)**

> **DERIVED: SPEC.md §4 NFR-11 body** — AC-N11.1..AC-N11.4 decompose the
> canonical NFR-11 (MI ≥ 80, CC ≤ 10, ≤ 400 lines/file, ≤ 15 files/dir,
> ≤ 40 lines/handler) into individually testable commands; AC-N11.1's
> score formula uses LLOC-weighted average MI from the framework
> `evaluate_dimension.md` `readability` dimension.

- **AC-N11.1** — `radon mi 03-development/src/ -j` averages an MI ≥ 80
  across all source files. *(coverage: harness `readability`)*
- **AC-N11.2** — `radon cc` per-function `CC ≤ 10` for every function.
- **AC-N11.3** — A static check fails the build when any single file
  exceeds 400 lines or any single directory exceeds 15 files.
- **AC-N11.4** — A static check fails the build when any API handler
  function exceeds 40 lines (business logic must drop to `service/`).

### NFR-12: 系統驗證目標

- **dimension**: `execute_verification_target`

`Makefile`'s `verify-system` target chains:
1. `alembic upgrade head`
2. full test suite
3. service startup + `/healthz`, `/readyz` smoke
4. `alembic downgrade base` followed by `alembic upgrade head` (round-trip
   validation)

`make verify-system` must **exit 0** and print `verify-system: PASS` on
stdout.

**Acceptance criteria (NFR-12)**

> **DERIVED: SPEC.md §4 NFR-12 body + §8 #27** — AC-N12.1..AC-N12.3
> decompose the canonical NFR-12 four-step chain (upgrade head → tests →
> service smoke → round-trip downgrade + upgrade) into individually
> testable commands; AC-N12.2's "stdout contains `verify-system: PASS`"
> derives verbatim from canonical §4 NFR-12 plus §8 #27 row.

- **AC-N12.1** — `make verify-system` exits **0**.
- **AC-N12.2** — `make verify-system` prints `verify-system: PASS` on
  stdout. *(SPEC.md §8 #27)*
- **AC-N12.3** — The four chained steps (upgrade head / tests / smoke /
  migration round-trip) each completed; the Makefile target exits
  non-zero on any step's failure.

---

## 5. Acceptance Criteria Summary

> Below is the consolidated 27-item acceptance set transcribed verbatim from
> SPEC.md §8. Each row carries the machine-decidable command and expected
> output the build must produce.

| # | Command | Expected |
|---|---------|----------|
| 1 | `pytest 03-development/tests -q` | all green, **skipped count 0** (NFR-09) |
| 2 | `pytest 03-development/tests --cov=03-development/src --cov-report=term` | TOTAL **100%** |
| 3 | `pytest 03-development/tests/integration --cov=03-development/src --cov-report=term` | TOTAL **≥ 80%** (NFR-10) |
| 4 | `POST /v1/tasks` (valid write key) | 201 + task id |
| 5 | `POST /v1/tasks` (no `X-API-Key`) | **401** + problem+json |
| 6 | `DELETE /v1/tasks/{id}` (write key, non-admin) | **403**, body does not reveal whether id exists |
| 7 | `GET /v1/tasks/{unknown}` | **404** + problem+json |
| 8 | `POST /v1/tasks` duplicate name | **409** |
| 9 | consecutive requests exceeding `TASKQ_RATE_BURST` | **429** + `Retry-After` header |
| 10 | DB stopped → `GET /readyz` | **503**, detail names DB unavailability |
| 11 | `alembic downgrade -1` → `GET /readyz` | **503**, detail names migration-not-at-head |
| 12 | `alembic upgrade head` → write sample → `downgrade -1` → `upgrade head` | sample columns identical (**v3 data migration reversible** — FR-07) |
| 13 | `alembic downgrade base` | exit 0, no residual tables |
| 14 | `GET /v1/tasks?limit=50` (10,000 rows) SQL statement count | **constant** (row-count independent — N+1 guard, NFR-01) |
| 15 | `GET /v1/tasks/{id}` p95 (10,000 rows) | **< 30 ms** (NFR-01) |
| 16 | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` | **0 hits** |
| 17 | scan for SQL string-concatenation (f-string / `%` / `+`) | **0 hits** (NFR-02) |
| 18 | query `api_keys` table | no plaintext; `key_hash` is 64 hex chars (NFR-02) |
| 19 | trigger a 500, inspect response body | no stack / SQL / file path (FR-10 / NFR-02) |
| 20 | full text of logs and `/v1/metrics` | contains no `TASKQ_DB_URL` password fragment (NFR-04) |
| 21 | `lint-imports` | **exit 0**; `service`/`api` layer import `sqlalchemy` is blocked (NFR-06) |
| 22 | `pip-licenses --format=json --with-system` | every dep's license ∈ allowlist (NFR-07) |
| 23 | `bandit -r 03-development/src/` | 0 HIGH, 0 MEDIUM |
| 24 | `mutmut run` then `mutmut results` | mutation score **≥ 70** (NFR-08) |
| 25 | service shut down with in-flight tasks | graceful drain; over-budget tasks marked `interrupted`; no orphan processes (FR-08) |
| 26 | `grep -c "^TASKQ_" .env.example` | **12** (§5.1 all env vars declared) |
| 27 | `make verify-system` | exit 0 with `verify-system: PASS` on stdout (NFR-12) |

### HTTP status → problem+json type map (canonical: SPEC.md §7)

| Status | Condition | `type` |
|--------|-----------|--------|
| 422 | request-body validation failed | `/errors/validation` |
| 401 | missing or invalid API key | `/errors/unauthenticated` |
| 403 | insufficient scope (leaks nothing) | `/errors/forbidden` |
| 404 | unknown task id | `/errors/not-found` |
| 409 | duplicate task name | `/errors/conflict` |
| 429 | rate limit exceeded (+ `Retry-After`) | `/errors/rate-limited` |
| 503 | DB down or migration behind head | `/errors/not-ready` |
| 500 | any other (no stack/SQL/path in body) | `/errors/internal` |

`asyncio.CancelledError` is on none of these rows — it propagates (NFR-03).
A task timeout is `HTTP 200` with `status: timeout` (the run endpoint
returns the current task state, not an error).

---

## 6. Out-of-Scope

- Authentication providers beyond the project's own API-key model (no OIDC,
  OAuth, SAML).
- Multi-tenant isolation (one logical deployment; isolation is the deployer's
  concern).
- Task scheduling with cron expressions (only on-demand execution;
  `POST /v1/tasks/{id}/run`).
- Distributed task execution across multiple nodes (background runner is
  per-process with `TASKQ_MAX_CONCURRENT` cap; cross-node is a future
  round's concern).
- WebSocket / SSE streaming of execution output (only `task_results`'s
  `stdout_tail` / `stderr_tail` are persisted on completion).
- PostgreSQL production deployment specifics (the data model is ORM-only;
  SQLite is the dev/test target — see §2.1).
- Customer-facing observability beyond `/v1/metrics` (no third-party
  exporter, no alerting).

---

## 7. Open Issues

This section captures anything that cannot yet be transcribed verbatim from
`SPEC.md` without interpretation; these become `FR-XX-deferred` or `NFR-99`
items and are the responsibility of later phases.

- **NFR-99** — `evaluate_dimension.md`'s `mutmut 2.x` block pre-warns that
  on macOS Homebrew Python 3.11+ the legacy `python` runner fails with
  `FileNotFoundError`. SPEC.md §4 NFR-08 says only
  `mutmut run` / `mutmut results`. The canonical runner the framework will
  actually call is `harness_cli.py mutation-test-score --project .`; the
  SPEC must explicitly endorse the framework CLI in a future revision.
  Measurement boundary owned by the harness; the SRS cannot resolve which
  binary is authoritative between SPEC.md and the harness.
- **NFR-99** — SPEC.md §4 NFR-08 says "Scope limited to `service/` and
  `repository/`"; the rationale is described only as "execution-time
  budget". No explicit budget value is given. Future SPEC revision should
  pin the budget (e.g. ≤ 30 minutes wall-clock).
- **NFR-99** — SPEC.md §5.1 lists 12 env vars with defaults; SPEC.md §8 #26
  asserts `grep -c "^TASKQ_" .env.example == 12`. The exact format of each
  variable's `.env.example` entry (comment, default, equals sign, no quotes
  vs quotes) is not pinned. Any future automation that parses the file must
  treat this as ambiguous.
- **NFR-99** — FR-02 says subprocess execution uses
  `asyncio.create_subprocess_exec(*shlex.split(command))`; the handler for
  non-zero exit codes documents only the `failed` terminal state. Whether
  exit codes ≥ 128 (signal-terminated) are routed to `failed` or `timeout`
  is unspecified.
- **FR-08-deferred** — FR-08's "interrupted" terminal state for tasks that
  exceed `TASKQ_DRAIN_TIMEOUT` is mentioned; no SPEC.md section describes
  whether the partial `task_results` row (if any) is kept or discarded for
  interrupted tasks.
- **NFR-99** — FR-05's "row-level lock" depends on the underlying database's
  capability. SQLite (dev/test target) locks the whole database for write;
  PostgreSQL row-level `SELECT … FOR UPDATE` is the production target. The
  SRS cannot prove row-level lock semantics without a per-engine check.

---

## 8. Risks

| ID | Risk | Mitigation |
|----|------|------------|
| R1 | **v3 data migration loses data** | round-trip test against a real DB, column-by-column (FR-07; SPEC.md §8 #12) |
| R2 | SQL injection | no concatenation + ORM/parameterised + grep gate (NFR-02) |
| R3 | API key leak | hashed storage + constant-time compare + printed once (FR-03) |
| R4 | 403 reveals resource existence | authorise before lookup (FR-04) |
| R5 | N+1 collapses on a large table | explicit eager loading + SQL count assertion (NFR-01) |
| R6 | error body leaks internals | fixed RFC 7807 fields + detail allowlist (FR-10) |
| R7 | **swallowed `CancelledError` hangs shutdown** | explicit ban + assertion (NFR-03) |
| R8 | timeout leaves orphan processes | `kill()` + `await wait()` (FR-08) |
| R9 | deploy without migration | `/readyz` fails closed (FR-09) |
| R10 | connection pool exhaustion | `pool_pre_ping` + concurrency cap (FR-06/08) |
| R11 | transitive dep with incompatible license | lock file + whole-tree scan (NFR-07) |
| R12 | rate bucket race over-admits | single transaction + row-level lock (FR-05) |

---

## 9. Glossary

| Term | Definition |
|------|------------|
| API key | the opaque token the client supplies via `X-API-Key`; stored only as a SHA-256 hash (`api_keys.key_hash`); plaintext is never persisted |
| scope | per-key authority level: `read` < `write` < `admin` (inclusive hierarchy); endpoints require a scope ≥ their declared level |
| token bucket | the rate-limit primitive; capacity `TASKQ_RATE_BURST`, refill `TASKQ_RATE_PER_SEC`, persisted per-key in `rate_buckets` |
| task | a unit of asynchronous work; identified by UUID; lifecycle `pending → running → done | failed | timeout` (+ `interrupted` on shutdown overrun) |
| run | one execution of a task's `command`; written into `task_results` on completion; multiple runs per task are allowed |
| migration | an Alembic revision; this round ships three — v1 (base tables), v2 (tags), v3 (split `task_results`) |
| round-trip | the full `alembic upgrade head → write sample → downgrade -1 → upgrade head` cycle used as FR-07's reversibility proof |
| graceful drain | the shutdown behaviour that waits for in-flight tasks up to `TASKQ_DRAIN_TIMEOUT`; over-budget tasks become `interrupted` |
| problem+json | RFC 7807 error envelope: `type`, `title`, `status`, `detail`, `instance`, `correlation_id` — see FR-10 |
| correlation_id | per-request UUID surfaced both in the response header `X-Correlation-Id` and in the server log; pair the two by it |
| ASGI | the async-server interface used by FastAPI; `uvicorn taskq_api.app:app` is the entry |
| ORM | SQLAlchemy 2.x declarative models; `sqlalchemy` may only be imported by `repository/` (NFR-06) |
| layering | the four-tier architecture `api > service > repository > models`; enforced by `.importlinter` (NFR-06) |
| SBOM | Software Bill of Materials — `08-config/SBOM.json`, listing every direct+transitive dep with `name`/`version`/`license`/`direct|transitive` (NFR-07) |
| mutation testing | `mutmut 2.x`-based scoring over `service/` + `repository/`; score ≥ 70 (NFR-08) |
| docstring coverage | harness-computed `% of public symbols with a docstring`; targets 100% (NFR-05) |

---

## FR Block (machine-readable)

<!-- FR:START -->
```json
{
  "version": "1.0.0",
  "created_at": "2026-08-14",
  "phase": 1,
  "project": "taskq-api",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "Task resource CRUD API — POST/GET/LIST/DELETE /v1/tasks with cursor pagination, validation 422, unknown 404, conflict 409, scope-driven DELETE 403.",
      "implementation_functions": [
        "taskq_api.service.tasks.create_task",
        "taskq_api.service.tasks.get_task",
        "taskq_api.service.tasks.list_tasks",
        "taskq_api.service.tasks.delete_task",
        "taskq_api.api.tasks.routes"
      ],
      "verification_method": "Integration tests via httpx.ASGITransport covering 201/200/200/204 paths plus 401/403/404/409/422 cases; command in SPEC.md §8 #4..#8."
    },
    {
      "id": "FR-02",
      "description": "Task execution endpoint — POST /v1/tasks/{id}/run (write, 202) using asyncio.create_subprocess_exec (no shell=True) with TASKQ_TASK_TIMEOUT; lifecycle pending→running→done|failed|timeout; results into task_results; GET /v1/tasks/{id}/runs (read).",
      "implementation_functions": [
        "taskq_api.service.runner.run_task",
        "taskq_api.service.runner.list_runs",
        "taskq_api.api.tasks.run_route"
      ],
      "verification_method": "Integration tests for 202 / 200 runs / timeout-killed processes / no shell=True (SPEC.md §8 #25)."
    },
    {
      "id": "FR-03",
      "description": "API key authentication — X-API-Key required on /v1/* (401 otherwise); SHA-256 hash storage; hmac.compare_digest compare; plaintext printed once at creation; revocation via revoked_at.",
      "implementation_functions": [
        "taskq_api.service.auth.verify_api_key",
        "taskq_api.service.auth.create_api_key",
        "taskq_api.repository.key_repo",
        "taskq_api.__main__ key create"
      ],
      "verification_method": "Unit + integration tests covering 401 paths, hashing, revocation; SPEC.md §8 #5 and #18."
    },
    {
      "id": "FR-04",
      "description": "Scope authorization — read < write < admin (inclusive); single dependency; 403 leaks nothing.",
      "implementation_functions": [
        "taskq_api.api.deps.require_scope",
        "taskq_api.api.deps.authenticate",
        "taskq_api.service.auth.scope_check"
      ],
      "verification_method": "Test enumerates every /v1 route's dependencies and asserts presence of the single authz dep; SPEC.md §8 #6."
    },
    {
      "id": "FR-05",
      "description": "Rate limiting — per-token token bucket (DB-persisted, row-level lock); capacity TASKQ_RATE_BURST, refill TASKQ_RATE_PER_SEC; 429 + Retry-After header.",
      "implementation_functions": [
        "taskq_api.service.ratelimit.consume",
        "taskq_api.api.deps.rate_limit",
        "taskq_api.repository.rate_repo"
      ],
      "verification_method": "Burst test (SPEC.md §8 #9) and concurrency test for no over-admission race."
    },
    {
      "id": "FR-06",
      "description": "Persistence layer + transaction boundaries — all data access via repository/; one Session per request via context manager; no raw SQL; selectinload/joinedload; pool_pre_ping.",
      "implementation_functions": [
        "taskq_api.repository.session.session_scope",
        "taskq_api.repository.task_repo",
        "taskq_api.repository.key_repo",
        "taskq_api.repository.rate_repo"
      ],
      "verification_method": "Lifecycle test for one-Session-per-request + rollback; lint-imports; grep gates (SPEC.md §8 #16, #17, #21)."
    },
    {
      "id": "FR-07",
      "description": "Schema migration — Alembic v1 base tables, v2 tags many-to-many, v3 data-moving split of tasks.result_json into task_results; every step reversible; no destructive shortcuts.",
      "implementation_functions": [
        "migrations.versions.v1_initial",
        "migrations.versions.v2_tags",
        "migrations.versions.v3_split_results"
      ],
      "verification_method": "Real-DB round-trip test with column-by-column equality (SPEC.md §8 #12, #13); offline-SQL emitted (SPEC.md §8 alembic upgrade head --sql)."
    },
    {
      "id": "FR-08",
      "description": "Asynchronous executor — asyncio.TaskGroup; concurrency cap TASKQ_MAX_CONCURRENT; graceful drain TASKQ_DRAIN_TIMEOUT (mark overrun as interrupted); timeout via wait_for + kill() + wait(); CancelledError propagates.",
      "implementation_functions": [
        "taskq_api.service.runner.executor",
        "taskq_api.service.runner.shutdown",
        "taskq_api.service.runner.run_with_timeout"
      ],
      "verification_method": "Drain test (SPEC.md §8 #25), orphan-process check, CancelledError propagation assertion (NFR-03)."
    },
    {
      "id": "FR-09",
      "description": "Health + observability — GET /healthz (live), GET /readyz (DB + alembic current == head, fail closed), GET /v1/metrics (admin) for task counts / latency / rejections.",
      "implementation_functions": [
        "taskq_api.api.health.healthz",
        "taskq_api.api.health.readyz",
        "taskq_api.api.health.metrics"
      ],
      "verification_method": "DB-down 503, migration-behind-head 503 (SPEC.md §8 #10, #11); metrics endpoint admin-only."
    },
    {
      "id": "FR-10",
      "description": "Error contract (RFC 7807) — application/problem+json on every non-2xx; body fields type/title/status/detail/instance/correlation_id; detail leaks nothing; X-Correlation-Id in response + log.",
      "implementation_functions": [
        "taskq_api.errors.problem_json",
        "taskq_api.errors.handlers"
      ],
      "verification_method": "Integration test exercising every status 401/403/404/409/422/429/503/500 once; body field allowlist; 500 body contains no internals (SPEC.md §8 #19)."
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "description": "GET /v1/tasks/{id} p95 < 30ms on 10k rows; GET /v1/tasks?limit=50 p95 < 80ms on 10k rows; constant SQL statement count (no N+1) verified via SQLAlchemy event listener.",
      "test_method": "pytest-benchmark (SPEC.md §8 #15) and a SQL-statement-count constant assertion with limit 1/50/200/10000 (SPEC.md §8 #14)."
    },
    {
      "id": "NFR-02",
      "type": "security",
      "description": "No shell=True / eval( / exec( in source; no f-string/%/+ SQL composition; SHA-256 API keys with hmac.compare_digest; 403 leaks nothing; CORS deny-by-default; bandit 0 HIGH, 0 MEDIUM.",
      "test_method": "grep gates (SPEC.md §8 #16, #17), bandit scan (SPEC.md §8 #23), 403 body identity test, CORS preflight denial test."
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "description": "Per-request transaction boundaries (commit/rollback via context manager); no bare except / except Exception: pass; asyncio.CancelledError propagates; DB failure → /readyz 503; failed migration leaves DB at previous revision; timeout kills child process.",
      "test_method": "ast-error-handling scan + unit test asserting CancelledError propagation + /readyz 503 test + rollback test + orphan-process check."
    },
    {
      "id": "NFR-04",
      "type": "security",
      "description": "Redact sk-.../token=.../Bearer .../postgres(ql)://... lines before persist/emit; TASKQ_DB_URL (and password) absent from logs, errors, /v1/metrics; API-key plaintext printed only once at creation.",
      "test_method": "Log/metric scan for TASKQ_DB_URL fragments (SPEC.md §8 #20), redaction unit tests for each regex pattern, API-key-create one-print assertion."
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "description": "100% public-API docstring coverage with [FR-XX]/[NFR-XX] references; every API endpoint in OpenAPI with summary + description.",
      "test_method": "ast-docstrings coverage (harness documentation dimension), docstring-reference check, OpenAPI /openapi.json introspection."
    },
    {
      "id": "NFR-06",
      "type": "layering",
      "description": "Mandatory .importlinter: layers contract api > service > repository > models + config/errors independence + forbidden contract banning sqlalchemy outside repository/.",
      "test_method": "lint-imports exit 0 (SPEC.md §8 #21); each forbidden contract enforced by a counter-test that forces the import."
    },
    {
      "id": "NFR-07",
      "type": "licensing",
      "description": "== pinning in requirements.txt; transitive lock via requirements.lock; allowlist MIT/BSD-2-Clause/BSD-3-Clause/Apache-2.0/PSF; whole-tree scan via pip-licenses --with-system; SBOM at 08-config/SBOM.json with direct|transitive.",
      "test_method": "pip-licenses --with-system (SPEC.md §8 #22) + presence/structure assertion over 08-config/SBOM.json + transitive-lock assertion."
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "description": "features.mutation_testing: true in harness_config.json; mutation score ≥ 70 over service/ + repository/.",
      "test_method": "harness_cli.py mutation-test-score (writes .methodology/mutation_score.json; SPEC.md §8 #24); scope-rationale recorded in harness_config.json."
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "description": "Zero pytest skips; zero zero-assert tests; no --ignore / -k / --deselect / collect_ignore / testpaths-removal exclusions; FR-07 tested against a real SQLite file (not in-memory mock); TRACEABILITY_MATRIX.md VERIFIED only on pass.",
      "test_method": "pytest -q (SPEC.md §8 #1), assertion-density scan (harness test_assertion_quality), exclusion-policy audit, migration-real-DB round-trip test."
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "description": "Integration suite line coverage ≥ 80%; driven via httpx.ASGITransport (no direct handler calls); every error code (401/403/404/409/422/429/503) plus migration round-trip, rate limit trigger/recovery, graceful drain.",
      "test_method": "Integration coverage report (SPEC.md §8 #3), static scan forbidding direct handler calls, error-code enumeration test, migration round-trip test in integration (SPEC.md §8 #12)."
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "description": "Project MI ≥ 80; per-function CC ≤ 10; single file ≤ 400 lines; single directory ≤ 15 files; API handler ≤ 40 lines (business logic in service/).",
      "test_method": "radon mi / radon cc scans + static build-time guards for file/directory/handler size."
    },
    {
      "id": "NFR-12",
      "type": "verifiability",
      "description": "Makefile verify-system chain: alembic upgrade head → tests → /healthz,/readyz smoke → alembic downgrade base + upgrade head (round-trip); exit 0 with 'verify-system: PASS' on stdout.",
      "test_method": "make verify-system invocation (SPEC.md §8 #27) plus per-step exit assertions and stdout match."
    }
  ]
}
```
<!-- FR:END -->

> Each entry above corresponds 1-to-1 with the `### FR-NN` and `### NFR-NN`
> sections in §3 and §4; omission is a `SRS-FR-BLOCK` exit-checklist failure
> (`check_srs_structure` blocks on missing IDs in `functional_requirements`).
