# Architecture Decision Records (ADR) — taskq-api

> Source of truth: `02-architecture/SAD.md` v1.0.0 (2026-08-14). Each record below
> is binding for implementation; `02-architecture/SAB.md` mirrors the machine-readable
> contract. Every functional and non-functional requirement cited in this document
> is defined in the srs (`01-requirements/SRS.md`) following the source
> specification in `SPEC.md` §3 and §4. The traceability matrix immediately
> below is the single point that maps each decision back to the requirement
> specification it satisfies.

---

## Decision-to-Requirement Traceability Matrix

This traceability matrix is the bridge between each decision recorded below and
the requirement specification it satisfies. The functional and non-functional
requirement identifiers (FR-01..FR-10, NFR-01..NFR-12) referenced in the table
are transcribed verbatim from the srs (`01-requirements/SRS.md`); the source
specification for those requirements is `SPEC.md` §3 and §4 as cited by the
srs introduction. No new requirements are introduced here — every row points
back to an existing srs heading.

| ADR | Decision (summary) | FR(s) satisfied | NFR(s) satisfied | Source specification |
|-----|---------------------|-----------------|-------------------|-----------------------|
| ADR-001 | CPython 3.11.15 (pinned) | — | NFR-07 | §2.1, §2.9 |
| ADR-002 | FastAPI on Uvicorn (ASGI) | FR-02, FR-08, FR-10 | NFR-03, NFR-05 | §2.1, §3 FR-02 / FR-08 |
| ADR-003 | SQLAlchemy 2.x declarative ORM | FR-06 | NFR-01, NFR-02 | §2.1, §3 FR-06 |
| ADR-004 | Alembic three-step migration | FR-07 | NFR-09, NFR-12 | §2.4, §3 FR-07 |
| ADR-005 | api → service → repository → models layering | FR-01..FR-10 | NFR-06, NFR-11 | §2.2, §2.9 (`.importlinter`) |
| ADR-006 | `asyncio.create_subprocess_exec` (no shell) | FR-08 | NFR-02, NFR-03 | §2.5, §3 FR-08 |
| ADR-007 | SHA-256 + `hmac.compare_digest` | FR-03 | NFR-02 | §2.3, §3 FR-03 |
| ADR-008 | Scope hierarchy at single dependency | FR-04 | NFR-02 | §2.3, §3 FR-04 |
| ADR-009 | Token-bucket rate limit, row-level lock | FR-05 | NFR-02 | §2.3, §3 FR-05 |
| ADR-010 | RFC 7807 `application/problem+json` | FR-10 | NFR-02, NFR-04 | §2.3, §3 FR-10 |
| ADR-011 | `pydantic-settings` + `TASKQ_*` env vars | FR-05, FR-06, FR-08 | NFR-02, NFR-04, NFR-07 | §2.11, §2.9 |
| ADR-012 | Correlation ID propagation | FR-10 | NFR-04 | §3 FR-10 |
| ADR-013 | `/healthz` + `/readyz` (DB probe) | FR-09 | NFR-03 | §2.7, §3 FR-09 |
| ADR-014 | ASGI lifespan + CancelledError re-raise | FR-08 | NFR-03 | §2.5, §3 FR-08 |
| ADR-015 | Pinned `requirements.txt` + lock + SBOM | — | NFR-07 | §2.9 |
| ADR-016 | import-linter + bandit + mutmut + readability | FR-10 | NFR-05, NFR-06, NFR-08, NFR-10, NFR-11 | §2.9, §2.10 |

### Spec-Coverage Notes

- **Round-trip requirement** (FR-07 acceptance): `make verify-system` is the
  single CI entry that exercises `alembic upgrade head` → tests → smoke →
  `alembic downgrade base` → `alembic upgrade head`. The data-migrating
  `v3_split_results.py` revision must leave every sample row byte-identical
  after the round trip; ADR-004 records this acceptance as the reason every
  revision ships both `upgrade()` and `downgrade()`.
- **Async-correctness requirement** (NFR-03): `asyncio.CancelledError` must
  propagate; ADR-006 and ADR-014 together enforce this at the runner
  (`service/runner.py`) and at the lifespan boundary (`app.py`). The ast gate
  listed in ADR-014 detects `except Exception` swallowing, which would
  silently drop cancellation.
- **Layering requirement** (NFR-06): the `.importlinter` contract recorded in
  ADR-005 mechanically enforces "api > service > repository > models" plus
  the `sqlalchemy` forbidden contract. Any future route that needs a new
  repository operation must add the function inside `repository/`; the
  `from sqlalchemy … import` line cannot move out.
- **Documentation requirement** (NFR-05): the `ast-docstrings` gate in ADR-016
  requires `[FR-XX]` / `[NFR-XX]` tags on every public function/class. The
  traceability matrix above is the authoritative mapping those tags cite.

### Cross-Reference Index

- Requirements source: `01-requirements/SRS.md` (SRS heading per row).
- Requirement origin specification: `SPEC.md` (cited in SRS introduction).
- Machine-readable mirror of this traceability matrix:
  `01-requirements/TRACEABILITY_MATRIX.md` (FR↔module) and
  `02-architecture/SAB.md` (FR/NFR→module contract).
- Architectural elaboration of these decisions: `02-architecture/SAD.md`
  (same FR/NFR numbering; this ADR set is the per-decision companion).

---

## ADR-001: Runtime Language and Version

### Status
Accepted

### Context
The 2nd-round testbed (HTTP service over the CLI in Round 1) must reuse the
existing `.venv` toolchain, install cleanly on Linux/macOS CI, and offer first-class
async semantics plus a mature ecosystem for HTTP frameworks, ORM, and migrations.

### Decision
Use CPython 3.11.15 (pinned via `.venv`) for both runtime and tests.

### Rationale
- 3.11 ships stable `asyncio.TaskGroup` (PEP 654 exception groups) and improved
  error messages; aligns with NFR-03 async-correctness requirements.
- Reuses the existing venv — no interpreter-introduction step in CI.
- All chosen frameworks (FastAPI, SQLAlchemy 2.x, Alembic, pydantic v2) support
  3.11.

### Consequences
- Positive: zero interpreter-provisioning cost; deterministic error semantics.
- Negative: must drop older 3.10-only constructs; teams on <3.11 must rebuild venv.

### Alternatives Considered
- **Python 3.12**: rejected — benefits (f-strings PEP 701) not load-bearing; venv
  would need re-pinning.
- **PyPy**: rejected — incompatibility with some C-extension wheels in the stack.

---

## ADR-002: Web Framework — FastAPI on ASGI

### Status
Accepted

### Context
Need a typed HTTP layer with built-in OpenAPI, pydantic-driven validation, async
route handlers, and a lifespan hook for graceful shutdown.

### Decision
Adopt **FastAPI** running on **Uvicorn** (`uvicorn taskq_api.app:app`).

### Rationale
- Native `async def` route handlers align with FR-02/FR-08 background execution.
- Dependency-injection model makes `api/deps.py` the **single authorization chokepoint**
  (FR-04).
- `lifespan` context manager provides canonical graceful-drain entry/exit.
- OpenAPI generation satisfies NFR-05 documentation dimension without extra work.

### Consequences
- Positive: type-driven contract, free OpenAPI, DI-driven auth/scope/rate colocation.
- Negative: FastAPI's `HTTPException` bypasses our `application/problem+json`
  contract — must register a `validation_exception_handler` to coerce all
  failures into the FR-10 shape.

### Alternatives Considered
- **Starlette directly**: rejected — would force us to rebuild pydantic integration
  and DI plumbing.
- **Flask**: rejected — sync-first, lifespan semantics weak, no first-class async.

---

## ADR-003: ORM and Data Layer — SQLAlchemy 2.x

### Status
Accepted

### Context
Persistence needs a transactional API, row-level locking for rate-limit
correctness (T-04), preload support to prevent N+1 (NFR-01), and a declarative
schema that Alembic can migrate.

### Decision
**SQLAlchemy 2.x declarative ORM** with bound-parameter queries exclusively (no
string composition of SQL).

### Rationale
- `selectinload` / `joinedload` resolve the N+1 risk surfaced in NFR-01.
- Row-level locks (`with_for_update`) on `rate_buckets` make the rate-limit
  admission atomic under concurrency.
- Pydantic-compatible declarative models integrate cleanly with FR-01 request/
  response schemas.
- Migration story is owned by Alembic (ADR-004).

### Consequences
- Positive: portable across SQLite (dev/test) and PostgreSQL (prod); uniform
  transactional surface.
- Negative: import-linter forbidden contract must enforce **"sqlalchemy only in
  repository"** (NFR-06) — any leak across boundaries fails CI.

### Alternatives Considered
- **Raw `sqlite3` / `psycopg`**: rejected — no row-level lock abstraction, N+1
  protection would be re-implemented.
- **Django ORM**: rejected — pulls the whole framework, conflicts with FastAPI
  choice.

---

## ADR-004: Schema Migration — Alembic with Three-Step Evolution (FR-07)

### Status
Accepted

### Context
Schema must evolve from v1 (initial three tables) → v2 (tags + unique name index)
→ v3 (split `result_json` out into a dedicated `task_results` table) — and the v3
step must perform a **real data migration** that survives an upgrade → downgrade
→ upgrade round trip.

### Decision
**Alembic** with three sequential revisions:
- `migrations/versions/v1_initial.py`
- `migrations/versions/v2_tags.py`
- `migrations/versions/v3_split_results.py` (data-back-up, reversible)

`make verify-system` enforces `alembic upgrade head → tests → smoke → downgrade
base → upgrade head` (NFR-12).

### Rationale
- Alembic's `op.bulk_insert` / `execute()` enable row-level data back-up.
- `downgrade()` must restore prior shape so the round trip is byte-equivalent on
  sample rows (T-11).
- Test uses a **real SQLite file** (not in-memory) so journal/rollback semantics
  match production (NFR-09).

### Consequences
- Positive: evolution path is reproducible and reviewed per revision.
- Negative: every revision must ship upgrade + downgrade; reviewers must verify
  data-migration symmetry.

### Alternatives Considered
- **Yoyo migrations**: rejected — weaker ecosystem; less integration with
  SQLAlchemy metadata.
- **Manual ALTER TABLE scripts**: rejected — no upgrade/downgrade bookkeeping.

---

## ADR-005: Layered Architecture (api → service → repository → models)

### Status
Accepted

### Context
FR coverage spans 10 features; without layering the FastAPI handlers would
absorb business logic and create god-modules. NFR-06 mandates clean boundaries.

### Decision
Four-layer separation with `config` / `errors` as **independence** modules
(imported by all layers but importing nothing from them):

| Layer | Allowed imports | Forbidden imports |
|-------|------------------|-------------------|
| api | service, errors, config | repository, models, sqlalchemy |
| service | repository, errors, config | sqlalchemy |
| repository | models, sqlalchemy, errors, config | api, service |
| models | sqlalchemy, pydantic, config | api, service, repository |

Enforced by **import-linter** contract and `lint-imports` CI gate.

### Rationale
- Service layer stays testable without HTTP and without SQL concerns.
- Repository layer is the **only** place that may construct ORM queries — keeps
  N+1 protection and transactional scope in one place.
- Independence modules (`config.py`, `errors.py`) prevent cyclic imports between
  layers.

### Consequences
- Positive: testable in isolation; CI catches violations before merge.
- Negative: cross-layer shortcut requires a one-step refactor (cost < testing
  penalty).

### Alternatives Considered
- **Flat package (no layers)**: rejected — would re-create god-modules as files
  grow.
- **Hexagonal/ports-and-adapters**: rejected for 2nd-round scope — too much
  ceremony for the surface area; deferred to a future round if integrations
  multiply.

---

## ADR-006: Async Subprocess Execution — `asyncio.create_subprocess_exec`

### Status
Accepted

### Context
FR-08 requires an async task runner that spawns a child process, enforces a
timeout, captures stdout/stderr, and survives cancellation (CancelledError must
re-raise; NFR-03). SECURITY: command injection via shell metacharacters must be
impossible.

### Decision
- `asyncio.create_subprocess_exec(*shlex.split(command))` — **never**
  `shell=True`, **never** `exec`/`eval`, **never** `+`/`%`/`f-string` SQL.
- Timeout via `asyncio.wait_for`; on expiry call `process.kill()` then `await
  process.wait()`; record `interrupted` status (T-08).
- `CancelledError` is always re-raised after logging (T-09).
- During graceful shutdown, lifespan drains the in-flight task list.

### Rationale
- Argument-list exec avoids shell metacharacter expansion; `shlex.split` produces
  POSIX-style tokenisation.
- `await process.wait()` after `kill()` guarantees no orphan PID (regression
  test `test_sec_t08_no_orphan_subprocess`).
- ASGI lifespan gives a deterministic drain window before the loop closes.

### Consequences
- Positive: child PIDs cannot escape cleanup; cancellation semantics match
  `asyncio` contract; CI grep gate forbids `shell=True`.
- Negative: blocking commands inside the child must use timeout to avoid head-
  of-line blocking; long-running commands need an explicit TaskGroup.

### Alternatives Considered
- **`subprocess.Popen` in a thread pool (ThreadPoolExecutor)**: rejected —
  introduces GIL contention under high concurrency and complicates cancellation;
  ASGI worker is already async-native.
- **`shell=True` with quoted string**: rejected — surface for injection; banned
  by T-03.

---

## ADR-007: API Authentication — SHA-256 + Constant-Time Compare

### Status
Accepted

### Context
FR-03 mandates API-key auth with: (a) hash at rest, never plaintext; (b) constant-
time comparison; (c) revocation via `revoked_at`; (d) plaintext shown exactly
once, at creation.

### Decision
- `service/auth.hash_key` → `hashlib.sha256(key.encode()).hexdigest()` (64 hex).
- `service/auth.verify_key` uses `hmac.compare_digest(hash, stored)`.
- `repository/key_repo.find_by_hash` filters `revoked_at IS NULL`.
- Plaintext returned only by `python -m taskq_api key create`.

### Rationale
- SHA-256 is in the stdlib (no extra deps), collision-resistant for opaque
  random keys.
- `hmac.compare_digest` defeats timing attacks on the comparison step.
- Plaintext shown once satisfies audit-by-design: the database never carries the
  secret.

### Consequences
- Positive: secret at rest is hash; rotation/revocation is a single
  `UPDATE … SET revoked_at = now()`.
- Negative: lost keys cannot be recovered (correct trade-off — better than
  storing reversible ciphertext).

### Alternatives Considered
- **Argon2 / bcrypt**: rejected for round 2 — adds a native dependency; SHA-256
  is sufficient for high-entropy opaque keys.
- **JWT**: rejected — adds expiry/refresh complexity out of scope for this round.

---

## ADR-008: Authorization — Scope Hierarchy Enforced at Dependency

### Status
Accepted

### Context
FR-04 requires three privilege tiers: `read < write < admin`. T-05 forbids 403
responses from leaking the existence of a resource.

### Decision
- Single enforcement point at `api/deps.py`: every `/v1/*` route depends on
  `require_api_key` + `require_scope("<level>")`.
- `service/auth.has_scope(actual, required)` compares hierarchy before any
  resource lookup occurs.
- 403 detail is a constant string (no resource id, no existence hint).

### Rationale
- One chokepoint guarantees the "lookup after scope check" rule is mechanically
  uniform.
- Constant-detail 403 defeats the boolean-existence oracle.

### Consequences
- Positive: reviewers can read one file to audit authz; tests assert the
  whitelist shape of `detail`.
- Negative: bespoke routes that need resource-scoped permissions (e.g. per-task
  ACL) must wait for a future round.

### Alternatives Considered
- **Policy engine (Casbin / OPA)**: rejected — overkill for three-tier scope.
- **Per-route decorators with manual checks**: rejected — re-invents DI and
  erodes the single-chokepoint guarantee.

---

## ADR-009: Rate Limiting — Token Bucket with Row-Level Lock

### Status
Accepted

### Context
FR-05 requires admission control per API key. T-04 forbids a TOCTOU race that
admits over capacity under burst load.

### Decision
- Token-bucket model: capacity `TASKQ_RATE_BURST`, refill `TASKQ_RATE_PER_SEC`.
- `repository/rate_repo.update_atomic` runs `SELECT … FOR UPDATE` then
  decrement; the entire check-and-consume happens inside one transaction.
- 429 response includes `Retry-After`.

### Rationale
- Row-level lock at the DB layer is the only synchronisation primitive that
  survives a multi-process Uvicorn deployment without an extra coordination
  service.
- SQLite (test) serialises writes anyway; the contract still holds under
  PostgreSQL production.

### Consequences
- Positive: correctness under burst; deterministic 429 + `Retry-After` for
  clients.
- Negative: every authenticated request takes a write lock — capacity planning
  must account for the extra transaction.

### Alternatives Considered
- **Redis-backed token bucket**: rejected — adds infrastructure beyond
  round-2 scope; SQLite/Postgres-only constraint.
- **In-process counter (`asyncio.Lock`)**: rejected — breaks under multi-worker
  Uvicorn.

---

## ADR-010: Error Contract — RFC 7807 `application/problem+json`

### Status
Accepted

### Context
FR-10 requires every non-2xx `/v1/*` response to be `application/problem+json`
with no stack traces, no SQL, no file paths. T-06 mandates the same.

### Decision
- One error builder: `errors.to_problem_json(type, title, status, detail=None,
  instance=None, correlation_id=None)`.
- FastAPI exception handlers wired in `app.py` convert `HTTPException`,
  `RequestValidationError`, and `Exception` to the canonical shape.
- `detail` is an **allow-listed** human string; raw exception is logged
  alongside `correlation_id` only.

### Rationale
- RFC 7807 is the IETF standard for HTTP problem details; clients can write
  generic error handlers.
- Building the body from a fixed-shape function makes the allow-list
  enforceable by static analysis.

### Consequences
- Positive: clients get stable contract; logs (separate channel) keep the
  debugging richness.
- Negative: every error path must flow through `to_problem_json` —
  contribution discipline required during code review.

### Alternatives Considered
- **Plain JSON `{ "error": "..." }`**: rejected — no `type` URI, no `instance`,
  no extension points.
- **HTML error pages**: rejected — wrong content type for an API.

---

## ADR-011: Configuration — `pydantic-settings` with `TASKQ_*` Env Vars

### Status
Accepted

### Context
Runtime config (DB URL, CORS origins, timeout, rate bucket) lives outside the
image. NFR-04 forbids logging DB passwords. NFR-07 requires a license allowlist.

### Decision
- `taskq_api/config.py` defines a `BaseSettings` subclass reading `TASKQ_*`
  variables; validated at startup.
- `config.format_db_url()` returns a redacted variant for any logging call.
- Dependencies pinned in `requirements.txt` (`==`); `requirements.lock` produced
  by `pip-compile`; SBOM emitted at `08-config/SBOM.json`.

### Rationale
- `pydantic-settings` integrates with the same validators used by request
  schemas — single mental model.
- Redaction at the boundary means application code can never forget it.

### Consequences
- Positive: one module owns all env-var surface; redacted logging is
  structurally guaranteed.
- Negative: every new env var needs a model field and a defaults decision.

### Alternatives Considered
- **`os.getenv` direct reads**: rejected — loses validation and typing.
- **YAML/JSON config file**: rejected — adds a deploy artefact; env vars are
  container-native.

---

## ADR-012: Correlation ID Propagation

### Status
Accepted

### Context
T-10 requires client actions to be correlatable across logs and responses.

### Decision
- Generate `correlation_id = uuid4()` per request (or honour inbound
  `X-Correlation-Id` if present).
- Echo on response header `X-Correlation-Id`.
- Stamp onto every log record via a logging filter installed in `app.py`.

### Rationale
- A single ID lets operators chase a failure from response body to log line to
  DB row without guessing.

### Consequences
- Positive: cheap, idempotent, survives retries (clients can re-use the inbound
  ID).
- Negative: log records without correlation ID are an error condition —
  reviewer must enforce logger-filter installation.

### Alternatives Considered
- **No correlation, rely on timestamp**: rejected — insufficient for
  multi-request flows.
- **Distributed tracing (OpenTelemetry)**: deferred — not in round-2 scope.

---

## ADR-013: Health & Readiness Endpoints

### Status
Accepted

### Context
FR-09 requires `/healthz` (liveness) and `/readyz` (readiness, must probe DB).

### Decision
- `GET /healthz` → 200 always (process is alive).
- `GET /readyz` → runs `SELECT 1` against the configured session; 200 on
  success, 503 + problem+json on failure.

### Rationale
- Probes do not require auth, do not touch business tables, and surface
  dependency failures distinctly from process liveness — standard k8s probe
  pattern.

### Consequences
- Positive: orchestrator can distinguish "restart me" from "remove me from the
  LB".
- Negative: `/readyz` swallow of DB errors must be narrow (catch + re-raise to
  logging); not silently passed.

### Alternatives Considered
- **Single `/health` returning 200**: rejected — loses the liveness/readiness
  distinction, produces false positives when DB is down.

---

## ADR-014: Graceful Shutdown via Lifespan + CancelledError Re-Raise

### Status
Accepted

### Context
NFR-03 + T-09: on shutdown the runner must drain in-flight tasks without
dropping exceptions, and must not hang on `CancelledError`.

### Decision
- `app.py` defines an `@asynccontextmanager` `lifespan` that: starts the
  runner, yields, then awaits `runner.drain()` on exit (cancels remaining
  Tasks and awaits each).
- `service/runner` re-raises `asyncio.CancelledError` after logging
  (`try/except CancelledError: log; raise`).
- AST gate (`ast-error-handling`) detects `except Exception` swallowing.

### Rationale
- Lifespan is the canonical ASGI hook; awaiting drain before loop close gives a
  deterministic end-of-life.
- Explicit re-raise semantics + static gate make "silent cancel" impossible to
  ship.

### Consequences
- Positive: shutdown sequence is reproducible and observable.
- Negative: every long-running `await` site must be audited for cancel safety.

### Alternatives Considered
- **Signal handler side-effects**: rejected — non-portable across worker
  processes; lifespan owns shutdown cleanly.
- **Force-kill (SIGKILL on first signal)**: rejected — drops in-flight work.

---

## ADR-015: Dependency Policy — Pinned Requirements + SBOM + License Allowlist

### Status
Accepted

### Context
NFR-07 (license compliance) and NFR-08 (mutation testing) require repeatable
builds with auditable third-party surface.

### Decision
- `requirements.txt`: direct deps pinned with `==`.
- `requirements.lock`: transitive set, produced by `pip-compile`.
- `08-config/SBOM.json`: name / version / license / direct-or-transitive for
  every package.
- CI runs `pip-licenses --format=json --with-system` and diffs against
  allowlist (MIT / BSD-2 / BSD-3 / Apache-2.0 / PSF).

### Rationale
- Reproducibility is a precondition for Gate 3 verification gates.
- An explicit allowlist turns license review into a CI signal, not a human
  chore.

### Consequences
- Positive: third-party surface is diff-reviewable per PR.
- Negative: every dep bump touches two files plus SBOM; acceptable cost for the
  audit guarantee.

### Alternatives Considered
- **`requirements.txt` with version ranges**: rejected — non-reproducible
  installs across CI runs.
- **`poetry` / `uv`**: deferred — outside round-2 scope; current pinning
  strategy is sufficient.

---

## ADR-016: Quality Gates — import-linter, bandit, mutation testing, docstring coverage

### Status
Accepted

### Context
NFR-06 / NFR-05 / NFR-08 demand structural enforcement that human review cannot
sustain across 10 features.

### Decision
Adopt the following CI gates; each must exit 0 before merge:
- `import-linter` — enforces layering contract (ADR-005) and forbidden
  imports (e.g. `sqlalchemy.orm.Session` outside `repository/`).
- `bandit -r 03-development/src/` — must report 0 HIGH / 0 MEDIUM.
- `mutmut` — mutation score ≥ 70 on `service/` + `repository/`.
- `ast-docstrings` — 100 % docstring coverage on public functions/classes, each
  carrying `[FR-XX]` / `[NFR-XX]` tags where applicable.
- `ast-assertions` — every test function has at least one `assert`;
  `pytest --collect-only` shows `skipped == 0`.
- `readability-v2` — CC ≤ 10, ≤ 400 lines/file, ≤ 15 files/dir.
- Integration-coverage floor — `pytest --cov=03-development/src/taskq_api
  --cov-branch --cov-fail-under=80` on `03-development/tests/integration/` is
  the NFR-10 acceptance criterion; the gate fails the merge when the integration
  suite drops below 80 % line coverage. The integration suite is driven via
  `httpx.AsyncClient(transport=ASGITransport(app))` per NFR-10, so the gate
  also implicitly forbids direct handler invocation that would inflate coverage
  without exercising the full HTTP path.

### Rationale
- Each gate addresses one named NFR; together they cover architecture, security,
  mutation, documentation, testability, readability, integration coverage.

### Consequences
- Positive: violations surface at PR time, not at Gate 2 review.
- Negative: engineers must learn six tools; paid back by fewer re-review cycles.

### Alternatives Considered
- **Manual review only**: rejected — does not scale and cannot catch forbidden
  imports.
- **Single mega-linter**: rejected — combines unrelated rules; harder to triage
  failures.
