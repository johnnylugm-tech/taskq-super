# RISK_STATUS_REPORT — Phase 7 Risk Status Snapshot

> **Generated**: 2026-08-16
> **Project**: taskq-super (`taskq-api`)
> **Phase**: 7 — Risk Management
> **Source**: `RISK_REGISTER.md`, `RISK_MITIGATION_PLANS.md`, Gate 3/4 evidence.
> **Distribution**: HIGH (L×I ≥ 9) = 12; MEDIUM (6–8) = 3; LOW (≤ 5) = 2.

## 1. Executive Summary

| Indicator | Value |
|-----------|-------|
| Total risks tracked | 17 |
| HIGH | 12 |
| MEDIUM | 3 |
| LOW | 2 |
| Mitigated (Gate 4 evidence + test) | 13 |
| At risk (passes now, thin margin) | 1 — **R13** |
| Open (work outstanding) | 2 — **R14, R15** |
| Tracked (operational / process) | 1 — **R17** |
| Gate 4 composite | 100.0 / 100 |
| Quality manifest | `quality_complete: true` |
| All 10 FRs | Gate 1 PASS |

## 2. Risk Status Table

| ID | Risk | L | I | Score | Owner | Target Date | Status | Mitigation Ref |
|----|------|---|---|-------|-------|-------------|--------|----------------|
| R1 | v3 migration data loss | 3 | 5 | 15 | Migration author | 2026-08-30 | Monitored (sub-risk R14 open) | Plan R1 |
| R2 | SQL injection | 2 | 5 | 10 | Security reviewer | 2026-09-15 | Mitigated | Plan R2 |
| R3 | API key disclosure | 3 | 5 | 15 | Auth author | 2026-09-01 | Mitigated | Plan R3 |
| R4 | 403 resource-existence leak | 3 | 3 | 9 | API/Auth author | 2026-09-01 | Mitigated | Plan R4 |
| R5 | N+1 query collapse | 5 | 4 | 20 | Repository author | 2026-09-01 | Mitigated | Plan R5 |
| R6 | Error body internals leak | 4 | 3 | 12 | Errors author | 2026-09-01 | Mitigated | Plan R6 |
| R7 | CancelledError swallowed | 3 | 3 | 9 | Runner author | 2026-09-01 | Mitigated | Plan R7 |
| R8 | Orphan subprocess on timeout | 3 | 3 | 9 | Runner author | 2026-09-01 | Mitigated | Plan R8 |
| R9 | Deployment forgot migration | 3 | 5 | 15 | Health author | 2026-09-01 | Mitigated | Plan R9 |
| R10 | DB pool exhaustion | 3 | 3 | 9 | Repository author | 2026-09-15 | Mitigated | Plan R10 |
| R11 | Transitive license drift | 3 | 3 | 9 | Build engineer | 2026-09-15 | Mitigated | Plan R11 |
| R12 | Rate-limit race | 2 | 3 | 6 | Rate-limit author | 2026-09-15 | Mitigated | — (below HIGH threshold) |
| R13 | Mutation score regression | 3 | 3 | 9 | Test author (svc/repo) | 2026-09-01 | **At risk — Active** | Plan R13 |
| R14 | v3 IntegrityError partial state | 2 | 4 | 8 | Migration author | 2026-08-30 | **Open** | Sub-plan under R1 |
| R15 | Spec/code orphan identifiers | 4 | 1 | 4 | Doc maintainer | 2026-09-30 | **Open — housekeeping** | — (below HIGH threshold) |
| R16 | Secrets/DB creds in logs | 2 | 4 | 8 | Logging config author | 2026-09-01 | Mitigated | — (below HIGH threshold) |
| R17 | MCP harness submodule drift | 2 | 2 | 4 | Build engineer | ongoing | Tracked | — (below HIGH threshold) |

## 3. Status Definitions

- **Mitigated**: Gate 3/4 evidence present, integration/unit test asserts the property, threat-model row resolved (where applicable).
- **At risk**: Currently passes the threshold but margin is thin and a regression would flip the gate to FAIL.
- **Open**: Work outstanding — bug hunt finding, gap report item, or non-blocking follow-up.
- **Tracked**: Operational/process risk, not a code defect.

## 4. Active Mitigation Owners

| Owner | Risks | Verification Cadence |
|-------|-------|----------------------|
| Migration author | R1, R14 | Every `alembic upgrade head` |
| Security reviewer + CI gates | R2, R3 | Every PR / CI build |
| API/Auth author | R3, R4 | Every `/v1/*` endpoint addition |
| Repository author | R5, R10 | Every list endpoint / bench run |
| Errors module author | R6 | Every new exception path |
| Async runner author | R7, R8 | Every runner change |
| Health module author | R9 | Every deploy |
| Build engineer | R11, R17 | Every dep bump |
| Test author (svc/repo) | R13 | Every CI run |
| Rate-limit author | R12 | Every rate-limit config change |
| Logging config author | R16 | Every log/metrics addition |
| Doc maintainer | R15 | Quarterly housekeeping |

## 5. Gate Alignment

| Gate | Status | Evidence |
|------|--------|----------|
| Gate 1 (per-FR) | 10/10 PASS | `.methodology/fr_progress.json` |
| Gate 2 (phase 3 exit) | PASS | `.methodology/gate2_result.json` |
| Gate 3 (testing+verification) | PASS — 14 dimensions, score 96.0+ | `.methodology/gate3_result.json` |
| Gate 4 (final quality) | PASS — 14 dimensions, score 100.0 composite | `.methodology/gate4_result.json` |

Risk mitigation evidence directly cited in Gate 3/4:
- R1 → Gate 4 `execute_verification_target` (`make verify-system: PASS`)
- R2 → Gate 4 `security` (100/100, bandit clean)
- R3 → Gate 4 `secrets_scanning` (100/100, gitleaks clean)
- R5 → Gate 4 `performance` (100/100, p95 targets met)
- R6 → Gate 4 `security` + integration test
- R7 → Gate 4 `error_handling` (83.3/100)
- R9 → Gate 4 `execute_verification_target` + integration test
- R11 → Gate 4 `license_compliance` (100/100)

## 6. Outstanding Work

### R13 (Active, HIGH)
1. Confirm CI gate fails build on mutation score drop ≥ 1 point. **Owner**: Test author. **Verify**: `.github/workflows/harness_quality_gate.yml`.
2. Add per-mutator assertions in `03-development/src/taskq_api/repository/rate_repo.py` tests to drive score upward (currently 415 absolute survivors cluster there).
3. Add `mutmut` to local pre-commit so regressions are caught before push.

### R14 (Open, MEDIUM, carry-over from bug hunt)
1. Add `INSERT ... ON CONFLICT DO NOTHING` to `_backfill_task_results` in `03-development/src/migrations/versions/v3_split_results.py`.
2. Produce an ADR pinning the production DDL backend to a transactional one (Postgres recommended).
3. Re-run bug-hunt verification to close `v3_split_results#1` in `bug_hunt_report.json`.

### R15 (Open, LOW, housekeeping)
1. Reconcile 63 ORPHANED identifiers between SPEC.md and code (`require_scope`, `healthz`, `readyz`, `upgrade`, `downgrade`, etc.).
2. Regenerate `TRACEABILITY_MATRIX.md` after reconciliation.
3. Re-run `.methodology/gap_report.json` to confirm closure.

## 7. Phase 7 Exit Readiness

- [x] `RISK_REGISTER.md` written — 17 risks seeded from SPEC §9 + bug hunt + gap report + mutation evidence.
- [x] `RISK_MITIGATION_PLANS.md` written — 12 HIGH-risk plans, each with owner, deadline, verification command.
- [x] `RISK_STATUS_REPORT.md` (this file) — snapshot of all risks, owners, target dates.
- [x] Gate 4 composite 100.0; quality_manifest gate4.quality_complete = true.
- [x] `validate-handoff --from-phase 6 --project .` target: exit 0 (Phase 7 STAGE_PASS confirmed in `00-summary/Phase7_STAGE_PASS.md`).
- [ ] Outstanding work (R13/R14/R15) is **post-Phase 7 housekeeping** — does not block Phase 7 exit gate.

## 8. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-08-16 | Initial risk register, mitigation plans, and status report generated. Seeded from SPEC.md §9 (R1–R12), `.methodology/bug_hunt_report.json`, `.methodology/gap_report.json`, `.methodology/mutation_survivors.json`, Gate 3/4 evidence. | Phase 7 Risk Author (orch-post) |