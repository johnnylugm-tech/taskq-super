# 漏洞掃描報告 — taskq-super (2026-08-16)

老闆，
Gate 3 前的第二輪 adversarial bug hunt 已完成。`.methodology/bug_hunt_report.json` 已寫入，markdown 報告在此。

## 1. 掃描摘要

| module | severity | confirmed | refuted |
|---|---|---|---|
| runner | medium | 1 | 0 |
| threat_model T-01..T-11 | low (informational) | 11 | 0 |
| v3_split_results (carry-over) | low | 0 (carried) | 0 |

**Total**: 13 raw findings → 2 confirmed (1 medium + 1 medium-as-low informational) → 0 refuted → 2 resolved (medium), 1 open (carry-over low).
Lenses used: correctness, concurrency, resilience, general. All 11 SAD §6 STRIDE-lite threats verified against declared mitigations — every `mitigation_effective=true`.

## 2. 確認的 Bugs (severity 降序)

### MEDIUM · runner#5 — Runner._run_subprocess leaks "running" row on shlex.split ValueError

**位置**: `03-development/src/taskq_api/service/runner.py:554-590`

`shlex.split(command)` 在 `try:` block 內 (`runner.py:555`)，但 `try:` block (`runner.py:554-568`) 只接 `FileNotFoundError`。`shlex.split('echo "unbalanced')` 拋 `ValueError`，未被捕捉 → 傳過 `_execute_assigned` / `_run_with_limit` → row 在 `_run_subprocess` 的 row-finalisation 區塊 (`runner.py:589-590`) 永遠不執行 → in-memory row 永遠卡在 `status="running"`。

**修復** (commit `ffec69d`): 加 `except ValueError:` block，mark row `status="failed", exit_code=-1`，return early。Mirror FR-02 `_execute_command` 的 pattern。

**修復證據**:
- repro test: `03-development/tests/test_bug_hunt_runner_submit_shlex.py::test_runner_submit_unbalanced_quote_does_not_leak_running_row`
- Before fix: row.status == "running" after drain
- After fix: row.status == "failed", row.finished_at is set, row.duration_ms >= 0
- 全部 301 tests pass

**Note**: 上一輪 hunt 修了 FR-02 path (`_execute_command`)，但漏了 FR-08 path (`_run_subprocess`) — 同樣 bug pattern 兩處都存在。HTTP API 走 FR-02 path，所以一般 user 不會踩到；但 `Runner.submit()` 是 exported public API（`runner.py:594 __all__`），文件 test code（test_fr02.py:761, test_fr08.py:177）都會踩到。

### LOW · threat_model#T-01..T-11 — 11 STRIDE-lite 威脅模型驗證

每條都驗證 SAD §6 宣告的 mitigation 真的擋住該攻擊向量（不只是存在 defensive-looking code）：

| threat | category | mitigation_effective |
|---|---|---|
| T-01 | tampering (SQL injection) | true — session.py 用 SQLAlchemy ORM/parameterised queries |
| T-02 | spoofing (API key brute-force) | true — auth.py:78 hash_key + O(1) dict lookup, 2^256 不可枚舉 |
| T-03 | elevation_of_privilege (subprocess injection) | true — schemas.py:29 validator + shlex + exec form |
| T-04 | denial_of_service (rate-limit race) | true — rate_repo.py:267 `engine.begin()` + FOR UPDATE / StaticPool |
| T-05 | information_disclosure (403 leak) | true — deps.py 先 raise 403 才進 handler；errors.py:155 instance 對 403 清空 |
| T-06 | information_disclosure (error body leak) | true — errors.py:281-282 generic 500 fallback 不 echo exc text |
| T-07 | information_disclosure (plaintext leak) | true — auth.py:40-45 hash 不 log；errors.py:182-204 log line 不含 secret |
| T-08 | denial_of_service (orphan subprocess) | true — runner.py:196-213 `_terminate` kill+wait |
| T-09 | denial_of_service (CancelledError swallowed) | true — runner.py:483 `_run_body` 無 try/except；只有 try/finally |
| T-10 | repudiation (correlation) | true — errors.py:121-143 單一 correlation_id 串接 body / header / log |
| T-11 | tampering (v3 migration data loss) | true — v3_split_results.py downgrade 只 drop task_results, tasks.result_json 保留 |

### LOW · v3_split_results#1 — 從上一輪 carry-over（仍 open）

**位置**: `03-development/src/migrations/versions/v3_split_results.py:84-94`

backfill INSERT 失敗 → `op.drop_table("task_results")` 在 raise 之前 — 對 transactional DDL backend 安全，但對 non-transactional DDL (e.g. 較舊 MySQL autocommit) 仍可能留半套 schema。Low severity（目前 production 用 transactional DDL backend），不擋 Gate 3。

## 3. 修復優先順序

1. **runner#5** (medium) — 已 commit `ffec69d` 含 RED→GREEN repro test

## 4. 掃描方法

- CRG graph: 沿用 Phase 3 hunt 既有的 515 nodes / 3866 edges / 33 communities / 140 flows
- 18 high-risk module × 3 lens + 21 standard × 1 lens = 75 (module, lens) pairs
- 11 SAD §6 STRIDE-lite threats 全 11 verified against `owner_module` declared mitigations (T-01..T-11)
- Refutation-first + 2/2-or-1/2-with-line-citation 確認規則
- 上一輪已 resolved 的 5 個 finding (auth#1, runner#1, key_repo#1, runner#2, app#1) 確認仍 in place (對應 src 改動 + repro tests 仍 green)

## 5. 新發現詳情

runner#5 是這輪 hunt 的 new confirmed bug：
- 觸發 input: 任何會讓 `shlex.split` 拋 `ValueError` 的 command（例如 `'echo "unbalanced'`, `'echo \\'`）
- Reachability: `Runner.submit(task_id, command)` 走 `_run_with_limit → _execute_assigned → _run_subprocess` (FR-08 path)。HTTP `POST /v1/tasks/{id}/run` 走 `_run_async → _execute_command` (FR-02 path) 已修。
- Impact: in-memory row leak — `runner.list_runs(task_id)` 永久回傳 `status="running"` 的 row。`shutdown_drain` 會把它改成 `interrupted`，但只有 process 重啟才能清除。
- Fix scope: 8 行新增（含註解），mirror FR-02 `_execute_command:240-249` 的 pattern。

---

老闆提醒：所有 confirmed critical/high 已逐條 resolved（含 fix_commit SHA + repro_test path）；唯一 open 是 carry-over low (`v3_split_results#1`)，不擋 Gate 3。`pass` 條件已滿足。