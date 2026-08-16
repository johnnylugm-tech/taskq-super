# RISK_MITIGATION_PLANS — Phase 7 Formal Mitigation Plans

> **Scope**: HIGH risks only (Risk Score = Likelihood × Impact ≥ 9) per `RISK_REGISTER.md`.
> **Threshold**: 12 of 17 risks qualify. Each plan below defines owner, deadline, current mitigation evidence, residual risk, and verification command.
> **Format**: per-risk section with [Owner] [Deadline] [Status] [Verification Command] headers.

## Plan Index

| Risk | Score | Plan Status |
|------|-------|-------------|
| R1 — v3 migration data loss | 15 | Monitored |
| R2 — SQL injection | 10 | Mitigated |
| R3 — API key disclosure | 15 | Mitigated |
| R4 — 403 resource-existence leak | 9 | Mitigated |
| R5 — N+1 collapse | 20 | Mitigated |
| R6 — Error body internals leak | 12 | Mitigated |
| R7 — CancelledError swallowed | 9 | Mitigated |
| R8 — Orphan subprocess on timeout | 9 | Mitigated |
| R9 — Deployment without migration | 15 | Mitigated |
| R10 — DB pool exhaustion | 9 | Mitigated |
| R11 — Transitive license drift | 9 | Mitigated |
| R13 — Mutation score regression | 9 | Active |

---

## R1 — v3 schema migration data loss

- **Owner**: Migration author (`migrations/versions/v3_split_results.py`)
- **Deadline**: 2026-08-30 (carry-over sub-risk R14 also due)
- **Status**: Monitored — round-trip test green at Gate 4; sub-risk R14 still open
- **Mitigation actions**:
  1. Three-step Alembic revision with `downgrade()` for every `upgrade()` (FR-07). [Done]
  2. Round-trip acceptance test: `upgrade head` → write sample → `downgrade -1` → `upgrade head` → assert column-by-column equality (§8 #12). [Done — Gate 4 PASS]
  3. R14 carry-over: add `INSERT ... ON CONFLICT DO NOTHING` to `_backfill_task_results` and produce ADR pinning production DDL backend to transactional. [Pending]
- **Residual risk**: Low — relies on transactional-DDL backend; ADR locks the assumption.
- **Verification command**:
  ```bash
  pytest 03-development/tests/integration -k "migration_round_trip or v3_split" -v
  alembic upgrade head && alembic downgrade base && alembic upgrade head
  ```

## R2 — SQL injection

- **Owner**: Security reviewer + import-linter CI gate
- **Deadline**: 2026-09-15 (next dependency-update window)
- **Status**: Mitigated — Gate 3/4 security 100/100; bandit clean; threat-model T-01 resolved
- **Mitigation actions**:
  1. All SQL via SQLAlchemy ORM or bound parameters (FR-06). [Done]
  2. `import-linter` forbidden contract: only `repository/` may import `sqlalchemy` (NFR-06). [Done]
  3. CI grep gate for f-string/`%`/`+` SQL concatenation (§8 #17). [Done]
  4. `bandit -r 03-development/src/` must return 0 HIGH/0 MEDIUM (NFR-02). [Done]
- **Residual risk**: Low — additive protection: extend grep gate to detect `text(some_string)` SQL fragments.
- **Verification command**:
  ```bash
  bandit -r 03-development/src/ -f json
  grep -rnE "(f['\"][^'\"]*SELECT|% .*SELECT|\+ .*SELECT)" 03-development/src/ || echo "0 hits"
  lint-imports
  ```

## R3 — API key disclosure

- **Owner**: Auth service author + secrets_scanning CI gate
- **Deadline**: 2026-09-01 (next release window)
- **Status**: Mitigated — Gate 3/4 secrets_scanning 100/100; threat-model T-02 + T-07 resolved
- **Mitigation actions**:
  1. SHA-256 hashed storage, `hmac.compare_digest` constant-time compare (FR-03). [Done]
  2. Plaintext printed **once** at `key create`; never written to disk. [Done]
  3. `revoked_at` soft revocation. [Done]
  4. Redaction regex in logs/metrics: `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` (NFR-04). [Done]
  5. `gitleaks` CI gate threshold = 100. [Done]
- **Residual risk**: Low — extension: enforce minimum key entropy via policy gate; current key format is opaque hex, which is sufficient.
- **Verification command**:
  ```bash
  gitleaks detect --source . --no-git
  pytest 03-development/tests/unit -k "auth or key or redaction"
  ```

## R4 — 403 resource-existence leak

- **Owner**: API/Auth author
- **Deadline**: 2026-09-01
- **Status**: Mitigated — threat-model T-05 resolved; §8 #6 integration test green
- **Mitigation actions**:
  1. Authorization判定集中在 `require_scope` dependency (`api/deps.py`) **before** resource lookup (FR-04). [Done]
  2. 403 body must not echo resource id. [Done]
  3. §8 #6 integration test: same 403 body for existing vs unknown id. [Done]
- **Residual risk**: Low — every new `/v1/*` endpoint must inherit the dependency; add CRG community check.
- **Verification command**:
  ```bash
  pytest 03-development/tests/integration -k "test_403_does_not_leak_existence"
  ```

## R5 — N+1 query collapse

- **Owner**: Repository author + benchmark suite
- **Deadline**: 2026-09-01
- **Status**: Mitigated — Gate 3/4 performance 100/100; benchmarks pass
- **Mitigation actions**:
  1. List endpoints use `selectinload`/`joinedload` (FR-06). [Done]
  2. SQLAlchemy event listener counts statements per request, asserted constant regardless of `limit` (NFR-01 / §8 #14). [Done]
  3. `pytest-benchmark` p95 targets: < 30ms single, < 80ms list-limit-50 at 10k rows (§8 #15). [Done — 8.77e-08s / 2.587e-04s]
  4. CRG community-cohesion review on every new endpoint. [Process — every PR]
- **Residual risk**: Medium — likelihood is 5 (almost certain to regress on new endpoints). The mitigation is verification-heavy; if bench suite is not extended on each new list endpoint, score silently regresses.
- **Verification command**:
  ```bash
  pytest 03-development/tests/performance -v --benchmark-only
  pytest 03-development/tests/integration -k "test_n_plus_one_statement_count"
  ```

## R6 — Error body internals leak

- **Owner**: Errors module author + integration test suite
- **Deadline**: 2026-09-01
- **Status**: Mitigated — threat-model T-06 resolved; integration tests present
- **Mitigation actions**:
  1. RFC 7807 envelope (`type`/`title`/`status`/`detail`/`instance`/`correlation_id`) in `errors.py` (FR-10). [Done]
  2. `detail` whitelist; global exception handler strips internals in `app.py`. [Done]
  3. §8 #19 integration test triggers 500 and asserts no stack/SQL/path in body. [Done]
- **Residual risk**: Low — every new exception path must be tested; add a fuzzer test that throws random exceptions and asserts body purity.
- **Verification command**:
  ```bash
  pytest 03-development/tests/integration -k "test_500_body_no_internals"
  ```

## R7 — `asyncio.CancelledError` swallowed

- **Owner**: Async runner author + ast-error-handling scanner
- **Deadline**: 2026-09-01
- **Status**: Mitigated — error_handling 83.3/100 (10/12 with_handler, 0 anti-patterns); threat-model T-09 resolved
- **Mitigation actions**:
  1. Explicit rule: never catch `CancelledError` under bare `except Exception` (NFR-03). [Done]
  2. AST scanner flags `except Exception:` and bare `except:`. [Done]
  3. §8 #25 integration test: shutdown mid-task → drain within `TASKQ_DRAIN_TIMEOUT`, no orphans. [Done]
- **Residual risk**: Low — 2/12 functions without explicit handler; verify they are intentionally bare (`__init__.py` re-exports or pure delegation).
- **Verification command**:
  ```bash
  pytest 03-development/tests/integration -k "test_graceful_drain_no_orphan"
  harness-cli run error_handling --scope 03-development/src
  ```

## R8 — Orphan subprocess on timeout

- **Owner**: Runner author + integration test
- **Deadline**: 2026-09-01
- **Status**: Mitigated — integration tests assert zero orphans on shutdown; threat-model T-08 resolved
- **Mitigation actions**:
  1. Timeout path: `process.kill()` followed by `await process.wait()` (FR-08). [Done]
  2. `asyncio.wait_for` enforced. [Done]
  3. Integration test triggers timeout and inspects `ps` for orphan ppids (NFR-10). [Done]
- **Residual risk**: Low — recently fixed `runner#5` (FR-08 path) showed the same bug pattern as FR-02; lesson captured.
- **Verification command**:
  ```bash
  pytest 03-development/tests/integration -k "test_subprocess_orphan"
  ```

## R9 — Deployment without migration

- **Owner**: Health module author + ops runbook
- **Deadline**: 2026-09-01
- **Status**: Mitigated — Gate 3/4 health endpoints green; §8 #11 acceptance test green
- **Mitigation actions**:
  1. `/readyz` checks `alembic current == head` and DB reachability (FR-09). [Done]
  2. Mismatch → **503** with explicit `detail` (DB vs migration). [Done]
  3. `make verify-system` runs migration round-trip on every CI build. [Done]
- **Residual risk**: Low — operational guardrail: deploy scripts must pre-flight `/readyz` before cutover.
- **Verification command**:
  ```bash
  alembic downgrade -1 && curl -fsS http://localhost:8000/readyz | grep not-ready
  alembic upgrade head && curl -fsS http://localhost:8000/readyz | grep ok
  ```

## R10 — DB connection pool exhaustion

- **Owner**: Repository author
- **Deadline**: 2026-09-15
- **Status**: Mitigated — pool config in place; no observed exhaustion in benchmarks
- **Mitigation actions**:
  1. `pool_size=TASKQ_DB_POOL_SIZE` (default 5) + `pool_pre_ping=True` (FR-06). [Done]
  2. `TASKQ_MAX_CONCURRENT=8` caps background execution (FR-08). [Done]
  3. `/readyz` reports pool saturation. [Done]
  4. Load test in benchmark suite. [Done]
- **Residual risk**: Low — production sizing requires capacity test before traffic shift.
- **Verification command**:
  ```bash
  pytest 03-development/tests/performance -k "test_pool_saturation"
  ```

## R11 — Transitive dependency license drift

- **Owner**: Build engineer
- **Deadline**: 2026-09-15 (next `requirements.txt` update)
- **Status**: Mitigated — license_compliance 100/100; 60 files scanned
- **Mitigation actions**:
  1. `requirements.txt` pins direct deps with `==`. [Done]
  2. `requirements.lock` pins transitive deps. [Done]
  3. CI: `pip-licenses --format=json --with-system` against allowlist {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF} (NFR-07 / §8 #22). [Done]
  4. SBOM at `08-config/SBOM.json` (§10). [Done]
- **Residual risk**: Low — every dep bump must rerun the gate; gate is in CI.
- **Verification command**:
  ```bash
  pip-licenses --format=json --with-system | jq -e 'all(.[]; .license | IN("MIT","BSD-2-Clause","BSD-3-Clause","Apache-2.0","PSF"))'
  ```

## R13 — Mutation score regression

- **Owner**: Test author for service/ and repository/ layers
- **Deadline**: 2026-09-01 (monitor every CI run; alert on drop ≥ 1)
- **Status**: **Active** — score 73.3 (killed=11/survived=4); margin over threshold 70 is 3.3 points; 415 absolute survivors cluster in `rate_repo.py` and `key_repo.py`
- **Mitigation actions**:
  1. Mutation scope locked to `service/` + `repository/` per NFR-08 (`harness_config.json` records scope). [Done]
  2. CI gate: build fails on score drop ≥ 1 point. [Pending — confirm gate enforcement]
  3. Add per-mutator assertion in `rate_repo.py` and `key_repo.py` tests to drive score upward. [Pending]
  4. Add `mutmut` to local pre-commit so author catches regressions before push. [Pending]
- **Residual risk**: High if CI gate is not actually wired — a silent regression to 71 would pass; subsequent regression to 69 would block. Recommend adding an explicit fail-on-drop check.
- **Verification command**:
  ```bash
  mutmut run --scope 03-development/src/taskq_api/repository,03-development/src/taskq_api/service
  mutmut results | tail -1   # expect "score >= 70"
  jq -e '.score >= 70' .methodology/mutation_score.json
  ```

---

## Cross-Cutting Notes

- All HIGH risks except R13 are currently mitigated by Gate 3/4 evidence; ongoing work is maintenance and continuous verification.
- R13 is the only HIGH risk in **Active** status and requires explicit CI gate wiring + targeted test additions in `rate_repo.py` / `key_repo.py`.
- R1 has a carry-over sub-risk (R14) with deadline 2026-08-30 — addressed via the same plan as R1.
- The `validate-handoff --from-phase 6` check must pass before this deliverable is consumed; quality_manifest.json records Gate 4 quality_complete=true (Phase7_STAGE_PASS.md confirms 100.0 composite).