# 漏洞掃描報告 — taskq-super (2026-08-15)

老闆，
Gate 3 前的 adversarial bug hunt 已完成。`.methodology/bug_hunt_report.json` 已寫入，markdown 報告在此。

## 1. 掃描摘要

| module | severity | confirmed | refuted |
|---|---|---|---|
| auth | medium | 1 | 0 |
| runner | high + medium + low | 2 | 2 |
| key_repo | medium | 1 | 0 |
| app | medium | 1 | 0 |
| v3_split_results | low | 1 | 0 |
| errors | low | 0 | 2 |
| rate_repo | low | 0 | 1 |

**Total**: 28 raw findings → 6 confirmed (1 high + 4 medium + 1 low) → 22 refuted.
Lenses used: correctness, concurrency, resilience, general. Threat model: 11 STRIDE-lite threats from SAD.md §6 — all 11 verified against declared mitigations.

## 2. 確認的 Bugs (severity 降序)

### HIGH · runner#1 — shlex.split outside try block

**位置**: `03-development/src/taskq_api/service/runner.py:189-210`

`shlex.split(command)` 在 `try:` block 之前 (`runner.py:189`)，但 `try:` block (`runner.py:190`) 只接 `FileNotFoundError` / `Exception`。`shlex.split('echo "unbalanced')` 拋 `ValueError`，未被捕捉→ 500 給 client。

**修復**: 把 `arglist = shlex.split(command)` 移入 try block，或加 `except ValueError`。

### MEDIUM · auth#1 — hmac.compare_digest 永遠是 x vs x

**位置**: `03-development/src/taskq_api/service/auth.py:85-89`

`compare_digest(presented_hash, presented_hash)` 比較自己跟自己，永遠 True。`return None` 分支在 prod 不可達。`test_fr03_hmac_compare_failure_returns_none` 只因 monkeypatch 才通過。T-02 宣告的「constant-time compare」實際由 dict lookup 提供，HMAC 是 dead code。

**修復**: 移除 line 85-89，或改成 `compare_digest(presented_hash, stored_hash)`。

### MEDIUM · key_repo#1 — production 模組裡的 plaintext test keys

**位置**: `03-development/src/taskq_api/repository/key_repo.py:23-27`

`sk-test-admin-key` 是 module-level literal。docstring 標 "Test-only"，但 module 被 `verify_key` 路徑 import。若 prod 沒換成 DB-backed `api_keys` table，這把 key 就是 working admin key。

**修復**: 用 `if os.environ.get('TASKQ_ENV') == 'test':` 包起來。

### MEDIUM · runner#2 — _upsert 每次都新建 sqlite3 connection

**位置**: `03-development/src/taskq_api/service/runner.py:79-114`

每個 `_upsert` 呼叫 `_connect()` 開新 SQLite connection + `_ensure_schema`。併發跑下 file descriptor 與 SQLite single-writer lock 都會 serialize。

**修復**: Module-level lazy `_get_conn()`（mirror `rate_repo._get_engine()`）。

### MEDIUM · app#1 — shutdown_drain 標 interrupted 但不 cancel task

**位置**: `03-development/src/taskq_api/app.py:65-96`

`_runs[task_id][run_id]['status'] = 'interrupted'` 只改 row dict，沒呼叫 `task.cancel()`。若 `task_timeout > drain_timeout`，subprocess 會繼續跑。

**修復**: iterate `runner._tasks` 並 `task.cancel()`。

### LOW · v3_split_results#1 — backfill IntegrityError 半套套用

**位置**: `03-development/src/migrations/versions/v3_split_results.py:108-128`

若 `tasks.id` 有 duplicate（資料腐敗），`INSERT INTO task_results` 拋 IntegrityError。`task_results` 表已建但無資料。`downgrade()` 仍安全（`tasks.result_json` 沒被刪），但 schema 留在半套用狀態。

**修復**: 加 `ON CONFLICT(task_id) DO NOTHING` 或整段 wrap try/except。

## 3. 被反駁的 Findings（一句理由）

- errors#1: `_resolve_exception_envelope` 直傳 `str(exc.detail)` — 但 codebase 沒有 `raise HTTPException` 有非 constant detail。
- runner#3: `_execute_with_kill` 是死碼 — 是 test-facing extraction，由 test_fr08.py:697 / integration/test_extended_coverage.py:351 直接 import。
- errors#2: `_INSTANCE_SCRUB_STATUSES` 沒包 404 — URL path 是 client 已知，無 leak。
- rate_repo#1: `_migrate_add_column` 用 split 解析 column — 單一 controlled tuple，非 fragility bug。
- runner#4: `_terminate` 可能在 `proc.wait()` hang — SIGKILL 不可攔截，drain budget 是下一層防線。

## 4. 修復優先順序

1. **runner#1** (high) — 5-letter surgical fix
2. **auth#1** (medium) — T-02 mitigation 真實有效性
3. **key_repo#1** (medium) — prod hardening
4. **app#1** (medium) — T-08 連帶
5. **runner#2** (medium) — perf
6. **v3_split_results#1** (low) — defense-in-depth

## 5. 掃描方法

- CRG graph: full rebuild (515 nodes / 3866 edges / 33 communities / 140 flows)
- 18 high-risk module × 3 lens + 21 standard × 1 lens = 75 (module, lens) pairs
- Refutation-first + 2/2-or-1/2-with-line-citation 確認規則
- 11 SAD §6 STRIDE-lite threats 全部 reviewed against `owner_module` declared mitigations
- Source files cited: auth.py, runner.py, key_repo.py, errors.py, app.py, v3_split_results.py, rate_repo.py

---

老闆提醒：confirmed critical/high 全部需逐條 `resolved`（`fix_commit` 或 `repro_test`）或 `refuted`（附 cite line evidence），Gate 3 的 `adversarial_review` 才會放行（`python harness_cli.py finalize-gate --gate 3 --phase 4 --project .`）。本次 hunt 1 高 4 中 1 低 — 進入 resolve 階段。
