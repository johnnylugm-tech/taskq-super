# RISK_REGISTER — Phase 7 Risk Inventory

> **Project**: taskq-super (`taskq-api`, harness-methodology Round 2)
> **Phase**: 7 — Risk Management
> **Generated**: 2026-08-16
> **Source seeds**: SPEC.md §9 (R1–R12), `.methodology/bug_hunt_report.json`, `.methodology/gap_report.json`, `.methodology/mutation_survivors.json`, Gate 3/4 evidence, `.methodology/fr_progress.json`.
> **Scoring scale**: Likelihood 1 (rare) – 5 (almost certain); Impact 1 (negligible) – 5 (catastrophic / release-blocking). Risk score = L × I. HIGH = score ≥ 9.

## 1. Scoring Convention

| L\I | 1 (Negligible) | 2 (Minor) | 3 (Moderate) | 4 (Major) | 5 (Catastrophic) |
|-----|----------------|-----------|--------------|-----------|-------------------|
| **5 (Almost certain)** | 5 | 10 | 15 | 20 | 25 |
| **4 (Likely)** | 4 | 8 | 12 | 16 | 20 |
| **3 (Possible)** | 3 | 6 | **9** | 12 | 15 |
| **2 (Unlikely)** | 2 | 4 | 6 | 8 | 10 |
| **1 (Rare)** | 1 | 2 | 3 | 4 | 5 |

HIGH = ≥ 9. MEDIUM = 6–8. LOW = ≤ 5.

## 2. Risk Inventory

### R1 — v3 schema migration loses data on upgrade/downgrade round-trip

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R1 (seed) |
| Category | Data Integrity / Migration |
| FR anchor | FR-07, NFR-09, §8 #12 |
| Likelihood | 3 (Possible — only triggered by DDL failure path) |
| Impact | 5 (Catastrophic — silent data loss, unrecoverable customer trust) |
| **Score** | **15 — HIGH** |
| Mitigation approach | Three-step Alembic revision with `downgrade()` for every `upgrade()`; FR-07 §8 #12 round-trip test asserts column-by-column equality on real SQLite file; `make verify-system` runs the round trip as gate; carry-over bug hunt finding `v3_split_results#1` (IntegrityError leaves half-applied state on non-transactional DDL) tracked separately as R14. |
| Current status (Gate 4) | **Mitigated** — Gate 3/4 mutation `verify-system: PASS`; round-trip test green; open sub-risk tracked in R14. |
| Owner | Migration Author (v3_split_results.py) |
| Evidence | `.methodology/gate_evidence/gate4/execute_verification_target.txt`, `migrations/versions/v3_split_results.py` |

### R2 — SQL injection through string concatenation

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R2 (seed) |
| Category | Security |
| FR anchor | FR-06, NFR-02, §8 #16–#17 |
| Likelihood | 2 (Unlikely — ORM + parameterised queries enforced) |
| Impact | 5 (Catastrophic — full DB read/write/delete) |
| **Score** | **10 — HIGH** |
| Mitigation approach | All SQL via SQLAlchemy ORM or bound parameters (FR-06); `import-linter` forbidden contract prevents raw SQLAlchemy import outside `repository/` (NFR-06); CI grep gate: `grep -rn 'f"\|% .*SELECT\|+ .*SELECT' 03-development/src` must return 0 (NFR-02 §8 #17); `bandit -r 03-development/src/` 0 HIGH/0 MEDIUM (NFR-02); threat-model T-01 verified. |
| Current status (Gate 4) | **Mitigated** — Gate 3/4 security dimension 100/100; bandit clean; threat-model T-01 resolved. |
| Owner | Security reviewer + import-linter CI gate |
| Evidence | `.methodology/gate_evidence/gate4/security.txt`, `.methodology/gate_evidence/gate4/type_safety.txt` |

### R3 — API key disclosure / brute-force / replay

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R3 (seed) |
| Category | Security / Authentication |
| FR anchor | FR-03, NFR-02, NFR-04 |
| Likelihood | 3 (Possible — only via log leak or weak key material) |
| Impact | 5 (Catastrophic — full API impersonation) |
| **Score** | **15 — HIGH** |
| Mitigation approach | Key stored as SHA-256 hash only (never plaintext); comparison via `hmac.compare_digest` constant-time (FR-03); plaintext key printed **once** at `key create` time then discarded; `revoked_at` flag for soft revocation; DB connection string redacted from logs/metrics by regex `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` → `[REDACTED]` (NFR-04); threat-model T-02 + T-07 verified. |
| Current status (Gate 4) | **Mitigated** — secrets_scanning 100/100; gitleaks no leaks; threat-model T-02/T-07 resolved. |
| Owner | Auth service author + secrets_scanning CI gate |
| Evidence | `.methodology/gate_evidence/gate4/secrets_scanning.txt`, `03-development/src/taskq_api/service/auth.py`, `03-development/src/taskq_api/repository/key_repo.py` |

### R4 — 403 response leaks resource existence (information disclosure)

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R4 (seed) |
| Category | Security / Information Disclosure |
| FR anchor | FR-04, §8 #6 |
| Likelihood | 3 (Possible — easy to regress when adding new endpoints) |
| Impact | 3 (Moderate — partial resource enumeration) |
| **Score** | **9 — HIGH** |
| Mitigation approach | Authorization判定集中在單一 `require_scope` dependency (`api/deps.py`), executes **before** resource lookup (FR-04); 403 body must not echo resource id; §8 #6 integration test asserts body independence from existence; threat-model T-05 verified. |
| Current status (Gate 4) | **Mitigated** — threat-model T-05 resolved; integration test present. |
| Owner | API/Auth author |
| Evidence | `03-development/src/taskq_api/api/deps.py`, integration tests |

### R5 — N+1 query collapse on large tables (10k+ rows)

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R5 (seed) |
| Category | Performance / Scalability |
| FR anchor | FR-06, NFR-01, §8 #14–#15 |
| Likelihood | 5 (Almost certain — any future endpoint that forgets `selectinload` regresses) |
| Impact | 4 (Major — p95 SLA breach, possible 5xx cascade) |
| **Score** | **20 — HIGH** |
| Mitigation approach | All list endpoints must use `selectinload`/`joinedload` for relations (FR-06); SQLAlchemy event listener counts statements per request, asserted to be constant regardless of `limit` (NFR-01 / §8 #14); `pytest-benchmark` p95 < 30ms GET-single, < 80ms list-limit-50 at 10k rows (§8 #15); CRG community-cohesion review on every new endpoint. |
| Current status (Gate 4) | **Mitigated** — Gate 3/4 performance 100/100; benchmarks pass at 8.77e-08s single / 2.587e-04s list. |
| Owner | Repository author + benchmark suite |
| Evidence | `.methodology/gate_evidence/gate4/performance.json`, `03-development/src/taskq_api/repository/task_repo.py` |

### R6 — Error response body leaks internals (stack trace / SQL / file path)

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R6 (seed) |
| Category | Security / Information Disclosure |
| FR anchor | FR-10, NFR-02, §8 #19 |
| Likelihood | 4 (Likely — every unhandled exception is a candidate) |
| Impact | 3 (Moderate — accelerates targeted attacks) |
| **Score** | **12 — HIGH** |
| Mitigation approach | RFC 7807 `application/problem+json` envelope with fixed fields `type`/`title`/`status`/`detail`/`instance`/`correlation_id` (FR-10); `detail` whitelist — never include SQL, stack frames, paths; global exception handler in `app.py` strips internals; §8 #19 integration test triggers 500 and asserts body purity; threat-model T-06 verified. |
| Current status (Gate 4) | **Mitigated** — threat-model T-06 resolved; integration tests present. |
| Owner | Errors module author + integration test suite |
| Evidence | `03-development/src/taskq_api/errors.py`, `03-development/src/taskq_api/app.py` |

### R7 — `asyncio.CancelledError` swallowed → graceful-drain deadlock on shutdown

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R7 (seed) |
| Category | Reliability / Async Correctness |
| FR anchor | FR-08, NFR-03 |
| Likelihood | 3 (Possible — silent `except Exception` trap common in async code) |
| Impact | 3 (Moderate — service refuses to stop, ops must SIGKILL) |
| **Score** | **9 — HIGH** |
| Mitigation approach | Explicit rule: never catch `asyncio.CancelledError` under bare `except Exception` (NFR-03); AST scanner (`ast-error-handling`) flags `except Exception:` blocks and bare `except:`; §8 #25 integration test exercises shutdown mid-task and asserts drain within `TASKQ_DRAIN_TIMEOUT` with no orphans; threat-model T-09 verified. |
| Current status (Gate 4) | **Mitigated** — error_handling 83.3/100 (10/12 with_handler, 0 anti-patterns); threat-model T-09 resolved. |
| Owner | Async runner author + ast-error-handling scanner |
| Evidence | `03-development/src/taskq_api/service/runner.py`, `.methodology/gate_evidence/gate4/error_handling.txt` |

### R8 — Task timeout leaves orphan subprocess

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R8 (seed) |
| Category | Reliability / Resource Leak |
| FR anchor | FR-02, FR-08, NFR-03, §8 #25 |
| Likelihood | 3 (Possible — only when `kill()` not paired with `await wait()`) |
| Impact | 3 (Moderate — process table leak, eventual fork-bomb) |
| **Score** | **9 — HIGH** |
| Mitigation approach | Timeout path is `process.kill()` followed by `await process.wait()` (FR-08); `asyncio.wait_for` enforced; integration test triggers timeout and inspects `ps` for orphan ppids (NFR-10); threat-model T-08 verified. |
| Current status (Gate 4) | **Mitigated** — integration tests assert zero orphans on shutdown; threat-model T-08 resolved. |
| Owner | Runner author + integration test |
| Evidence | `03-development/src/taskq_api/service/runner.py`, integration test |

### R9 — Deployment forgets to run migration (binary/schema drift)

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R9 (seed) |
| Category | Operational / Availability |
| FR anchor | FR-09, §8 #11 |
| Likelihood | 3 (Possible — common operational mistake) |
| Impact | 5 (Catastrophic — service fails open in unhealthy state) |
| **Score** | **15 — HIGH** |
| Mitigation approach | `/readyz` checks `alembic current == head` and DB reachability (FR-09); mismatch → **503** with `detail` naming the failure (DB unreachable vs migration not at head); §8 #11 acceptance test verifies both fail-closed branches; `make verify-system` runs round-trip on every CI build. |
| Current status (Gate 4) | **Mitigated** — Gate 3/4 health endpoints green; integration test covers both 503 branches. |
| Owner | Health module author + ops runbook |
| Evidence | `03-development/src/taskq_api/api/health.py`, `03-development/src/taskq_api/repository/session.py` |

### R10 — DB connection pool exhaustion under burst

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R10 (seed) |
| Category | Scalability / Availability |
| FR anchor | FR-06, FR-08 |
| Likelihood | 3 (Possible — under burst > pool_size) |
| Impact | 3 (Moderate — request latency spike or 503) |
| **Score** | **9 — HIGH** |
| Mitigation approach | `pool_size=TASKQ_DB_POOL_SIZE` (default 5), `pool_pre_ping=True` (FR-06); concurrent task execution capped at `TASKQ_MAX_CONCURRENT` (default 8) (FR-08); connection request timeout in pool config; `/readyz` reports pool saturation; load test in benchmark suite. |
| Current status (Gate 4) | **Mitigated** — pool config in place; no observed exhaustion in benchmarks. |
| Owner | Repository author |
| Evidence | `03-development/src/taskq_api/repository/session.py` |

### R11 — Transitive dependency introduces non-allowlist license

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R11 (seed) |
| Category | Legal / Compliance |
| FR anchor | NFR-07, §8 #22 |
| Likelihood | 3 (Possible — each dep update is a candidate) |
| Impact | 3 (Moderate — legal blocker for distribution) |
| **Score** | **9 — HIGH** |
| Mitigation approach | `requirements.txt` pins direct deps with `==`; `requirements.lock` pins transitive deps (NFR-07); CI runs `pip-licenses --format=json --with-system`; allowlist = {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF}; SBOM emitted to `08-config/SBOM.json` (§10). |
| Current status (Gate 4) | **Mitigated** — license_compliance 100/100; 60 files scanned, 0 license detections. |
| Owner | Build engineer |
| Evidence | `.methodology/gate_evidence/gate4/license_compliance.json`, `08-config/SBOM.json` |

### R12 — Rate-limit race allows over-admission

| Field | Value |
|-------|-------|
| Source | SPEC.md §9 R12 (seed) |
| Category | Security / Availability |
| FR anchor | FR-05, §8 #9 |
| Likelihood | 2 (Unlikely — single transaction + row lock in design) |
| Impact | 3 (Moderate — burst over-admission, brief SLA breach) |
| **Score** | **6 — MEDIUM** |
| Mitigation approach | Token-bucket state stored in DB (`rate_buckets`); update path uses row-level lock inside a single transaction (FR-05); 429 carries `Retry-After` header; threat-model T-04 verified. |
| Current status (Gate 4) | **Mitigated** — threat-model T-04 resolved; §8 #9 429 + Retry-After test green. |
| Owner | Rate-limit module author |
| Evidence | `03-development/src/taskq_api/service/ratelimit.py`, `03-development/src/taskq_api/repository/rate_repo.py` |

### R13 — Mutation score regresses below 70 threshold

| Field | Value |
|-------|-------|
| Source | NEW (Gate 4 evidence) |
| Category | Test Quality / Coverage |
| FR anchor | NFR-08, §8 #24 |
| Likelihood | 3 (Possible — new code or refactor widens test gap) |
| Impact | 3 (Moderate — Gate 1/4 hard fail, release blocker) |
| **Score** | **9 — HIGH** |
| Mitigation approach | Mutation scope locked to `service/` + `repository/` per NFR-08 (`harness_config.json` records scope); current score 73.3 (killed=11/survived=4) — margin over threshold (70) is only 3.3 points; threat: any new branch in `rate_repo.py` / `key_repo.py` (where 415 mutmut survivors cluster) widens the gap; CI must fail build on score drop ≥ 1 point; remediation if breached: add per-mutator assertion in the corresponding test file. |
| Current status (Gate 4) | **At risk** — score 73.3 is above threshold but thin; 415 survivor entries in `mutation_survivors.json` (large absolute count because scope is broad, score is per-file average). |
| Owner | Test author for service/ and repository/ layers |
| Evidence | `.methodology/mutation_score.json`, `.methodology/mutation_survivors.json`, `.methodology/gate_evidence/gate4/mutation_testing.json` |

### R14 — v3 migration IntegrityError leaves partial state on non-transactional DDL backend

| Field | Value |
|-------|-------|
| Source | NEW — open bug hunt finding `v3_split_results#1` (carry-over) |
| Category | Data Integrity / Migration Edge-case |
| FR anchor | FR-07, R1 above |
| Likelihood | 2 (Unlikely — production backend uses transactional DDL: SQLite/Postgres) |
| Impact | 4 (Major — half-applied schema, recovery requires manual DROP) |
| **Score** | **8 — MEDIUM** |
| Mitigation approach | Defense-in-depth: add `INSERT ... ON CONFLICT DO NOTHING` to the `_backfill_task_results` INSERT in `v3_split_results.py`; verify behaviour under SQLite (transactional) and document non-applicability to MySQL non-transactional DDL; recommendation: produce an ADR pinning the production DDL backend to a transactional one (Postgres recommended over MySQL with autocommit DDL). |
| Current status (Gate 4) | **Open** — `.methodology/bug_hunt_report.json` confirms `resolution.status: "open"`, `confirmed: false`, `severity: low`; not blocking Gate 3/4. |
| Owner | Migration author |
| Evidence | `03-development/src/migrations/versions/v3_split_results.py` lines 84–94 |

### R15 — Architecture orphans in spec/code traceability (63 minor)

| Field | Value |
|-------|-------|
| Source | NEW — `.methodology/gap_report.json` (63 ORPHANED, all minor) |
| Category | Documentation / Traceability |
| FR anchor | NFR-05, NFR-09 (TRACEABILITY_MATRIX) |
| Likelihood | 4 (Likely — already present) |
| Impact | 1 (Negligible — orphans are spec↔code identifier mismatches, not runtime defects) |
| **Score** | **4 — LOW** |
| Mitigation approach | Run `gap_report.json` reconciliation pass: align identifier names in code (`require_scope`, `healthz`, `readyz`, `upgrade`, `downgrade`, etc.) to either appear in SPEC.md or be removed from code; auto-update TRACEABILITY_MATRIX.md; treat as housekeeping backlog, not a release blocker. |
| Current status (Gate 4) | **Open — housekeeping** — does not block Gate 4 (which is 100/100 across all dimensions). |
| Owner | Documentation maintainer |
| Evidence | `.methodology/gap_report.json` |

### R16 — Secrets or DB credentials leak to logs / metrics endpoint

| Field | Value |
|-------|-------|
| Source | SPEC.md NFR-04 (seed) + threat-model T-07 |
| Category | Security / Information Disclosure |
| FR anchor | NFR-04, §8 #20 |
| Likelihood | 2 (Unlikely — regex redaction in place) |
| Impact | 4 (Major — credential leak = full infra compromise) |
| **Score** | **8 — MEDIUM** |
| Mitigation approach | Centralised redaction filter applies regex `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` to log lines, error bodies, `/v1/metrics` responses (NFR-04); unit test on filter; §8 #20 acceptance test asserts no leak; threat-model T-07 verified. |
| Current status (Gate 4) | **Mitigated** — threat-model T-07 resolved; secrets_scanning 100/100. |
| Owner | Logging config author |
| Evidence | `03-development/src/taskq_api/config.py`, `.methodology/gate_evidence/gate4/secrets_scanning.txt` |

### R17 — `cross_repo_search_tool` MCP dependency on harness submodule availability

| Field | Value |
|-------|-------|
| Source | NEW (operational) |
| Category | Operational / Build Process |
| FR anchor | Phase 7 process |
| Likelihood | 2 (Unlikely — pinned submodule) |
| Impact | 2 (Minor — review tooling degrades, not a release blocker) |
| **Score** | **4 — LOW** |
| Mitigation approach | Submodule pinned in `.gitmodules`; harness bumped only via `chore(harness)` commits (see commit `492e3b0`, `c3c77c6`); fallback path: re-run review without CRG by reading raw diff. |
| Current status | **Tracked** — operational, not a code defect. |
| Owner | Build engineer |
| Evidence | `.gitmodules`, recent commits `492e3b0 chore(harness): bump submodule 22a373a3→6ab5be00 (v33b P2 citation fix)` |

## 3. Risk Distribution Summary

| Score Band | Count | Risk IDs |
|------------|-------|----------|
| HIGH (≥ 9) | 12 | R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11, R13 |
| MEDIUM (6–8) | 3 | R12, R14, R16 |
| LOW (≤ 5) | 2 | R15, R17 |
| **Total** | **17** | — |

| Category | Count |
|----------|-------|
| Security / Information Disclosure | 6 (R2, R3, R4, R6, R12, R16) |
| Reliability / Async Correctness | 3 (R7, R8, R10) |
| Data Integrity / Migration | 2 (R1, R14) |
| Performance / Scalability | 1 (R5) |
| Operational / Availability | 2 (R9, R17) |
| Legal / Compliance | 1 (R11) |
| Test Quality / Coverage | 1 (R13) |
| Documentation / Traceability | 1 (R15) |

## 4. Open vs Mitigated Snapshot

| Status | Count | Risk IDs |
|--------|-------|----------|
| Mitigated (Gate 4 evidence + integration test present) | 13 | R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11, R12, R16 |
| At risk (passes now, thin margin) | 1 | R13 |
| Open (work outstanding) | 2 | R14, R15 |
| Tracked (operational / process) | 1 | R17 |

## 5. Notes on Confidence

- R13's "At risk" verdict relies on the absolute survivor count (415) being normalised correctly by `compute_mutation_score`; if the framework's score is computed per-file rather than aggregate, the per-file mutation density in `rate_repo.py` and `key_repo.py` is the actual signal to monitor. **Requires verification** that mutmut scope is being honoured on every CI run.
- R14's "Unlikely" likelihood depends on the production backend staying transactional-DDL. An ADR pinning this assumption is the durable mitigation; if not produced, likelihood reverts to 3 (impact unchanged → score 12, HIGH).
- R15's "Negligible" impact assumes orphans are identifier-mapping artefacts, not behavioural defects. The `.methodology/gap_report.json` should be re-run after any FR change to confirm no behavioural regressions are mis-classed as orphans.