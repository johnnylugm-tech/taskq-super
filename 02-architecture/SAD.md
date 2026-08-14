# Software Architecture Document (SAD) — taskq-api

> taskq-api: Python 3.11 / FastAPI / SQLAlchemy / Alembic — harness-methodology 漸進式驗證測床第 2 輪
> 文件版本: v1.0.0 | 對應 SPEC.md v1.0.0 (10 FR / 12 NFR / 12 env)
> 編製日期: 2026-08-14

## 1. Overview

### 1.1 目標

`taskq-api` 是 taskq 第 1 輪(CLI)的 HTTP 服務化本體:以 REST API 提交、查詢、執行任務;資料持久化於關聯式資料庫;schema 隨版本演進;具備認證、授權、流量控制、錯誤契約和 graceful shutdown。

第 2 輪相對第 1 輪新增的關鍵維度:

- **HTTP 層**:FastAPI + ASGI;authn/authz/輸入邊界均有真實觸發點
- **資料庫**:SQLAlchemy 2.x ORM + 明確交易邊界 + N+1 防護斷言
- **Schema migration**:Alembic v1→v2→v3 真實演進(v3 含資料搬遷 + 可逆 downgrade)
- **Async**:`async def` 端點 + `asyncio.TaskGroup` 背景執行器 + `CancelledError` 正確語意

### 1.2 形態

- **進程**:ASGI 服務,`uvicorn taskq_api.app:app` 啟動
- **管理入口**:`python -m taskq_api`(migrate / seed / healthcheck / key create)
- **資料庫**:SQLite(開發/測試)、PostgreSQL(生產)同 ORM 模型
- **驗證關卡**:`make verify-system` 串接 alembic upgrade → 測試 → 服務冒煙 → alembic downgrade → upgrade head(NFR-12)

### 1.3 系統驗證目標

> **Gate 2 必要條件**:harness 執行 `make verify-system`,exit 0 + stdout 出現 `verify-system: PASS` 為通過。

`Makefile` 中的 `verify-system` target 串接(SPEC §5.3 / NFR-12):

1. `alembic upgrade head`
2. 全套測試(`pytest 03-development/tests -q`,skipped 計數為 0)
3. 服務啟動 + `/healthz`、`/readyz` 冒煙
4. `alembic downgrade base` 後再 `upgrade head`(往返驗證 — FR-07 的 v3 資料搬遷可逆)

### 1.4 設計原則(本輪新增)

1. **分層禁止迴圈**:`api > service > repository > models`;`config`/`errors` 為 independence。
2. **ORM 洩漏禁止**:`sqlalchemy` 僅 `repository/` 可 import(NFR-06 forbidden contract)。
3. **無副作用 pytest**:`FR-07` 的三步 migration 以真實 SQLite 檔案測試,不得跳過(NFR-09)。
4. **錯誤契約單一**:`/v1/*` 全部非 2xx 為 `application/problem+json`,`detail` 不得含堆疊/SQL/路徑(FR-10)。

---

## 2. Module Design

### 2.1 分層架構

```
                ┌─────────────────────────────────────────────┐
   HTTP 客戶端  │  FastAPI app  (uvicorn taskq_api.app:app)   │
   (外部)       │  ASGI / 路由 / 中介層 dependency            │
                └────────────────┬────────────────────────────┘
                                 │  ↓ 全部 /v1/* 經 api/deps.py 單一授權點
                ┌────────────────▼────────────────────────────┐
   L4 API 層    │  api/ — deps / tasks / health                │
                │  (HTTP 邊界:序列化、認證、scope 強制)        │
                └────────────────┬────────────────────────────┘
                                 │  ↓ 業務邏輯
                ┌────────────────▼────────────────────────────┐
   L3 服務層    │  service/ — tasks / runner / auth / ratelimit│
                │  (規則、狀態機、async 執行器)               │
                │  禁止 import sqlalchemy(import-linter 把關)  │
                └────────────────┬────────────────────────────┘
                                 │  ↓ 唯一可接觸 ORM 的層
                ┌────────────────▼────────────────────────────┐
   L2 倉儲層    │  repository/ — session / task_repo /        │
                │              key_repo / rate_repo           │
                │  (交易邊界、預載防 N+1、row-level lock)      │
                └────────────────┬────────────────────────────┘
                                 │
                ┌────────────────▼────────────────────────────┐
   L1 模型層    │  models/ — orm / schemas                     │
                │  (SQLAlchemy declarative + pydantic)         │
                └─────────────────────────────────────────────┘

   獨立模組:config.py(taskq_* env)、errors.py(RFC 7807 problem+json)
   外部:migrations/versions/(FR-07)
```

### 2.2 FR → Module 對照表

| FR | 標題 | 主要模組 | 輔助模組 |
|----|------|----------|----------|
| **FR-01** | 任務資源 CRUD API | `api/tasks.py`、`service/tasks.py` | `repository/task_repo.py`、`models/orm.py`、`models/schemas.py` |
| **FR-02** | 任務執行端點 | `api/tasks.py`、`service/runner.py` | `repository/task_repo.py`(寫入 `task_results`) |
| **FR-03** | API Key 認證 | `api/deps.py`、`service/auth.py` | `repository/key_repo.py`、`models/orm.py`(`api_keys` 表) |
| **FR-04** | Scope 授權 | `api/deps.py`、`service/auth.py` | — |
| **FR-05** | 流量控制 | `api/deps.py`、`service/ratelimit.py` | `repository/rate_repo.py`(row-level lock) |
| **FR-06** | 持久化層與交易邊界 | `repository/session.py` | 全 `repository/` 子模組 |
| **FR-07** | Schema Migration(Alembic 三步) | `migrations/versions/v1_initial.py` | `migrations/versions/v2_tags.py`、`migrations/versions/v3_split_results.py` |
| **FR-08** | 非同步執行器 | `service/runner.py`、`app.py`(lifespan) | — |
| **FR-09** | 健康檢查與可觀測性 | `api/health.py`、`__main__.py`(CLI 健康檢查) | `service/runner.py`(metrics) |
| **FR-10** | 錯誤契約(RFC 7807) | `errors.py` | `app.py`(exception handler 掛載) |

每一條 FR 均對應 ≥1 模組;FR-02/03/05/06/08 同步觸及多層,符合分層契約。

### 2.3 模組清單與職責

| 模組 | 層級 | 職責 | 檔案 |
|------|------|------|------|
| `taskq_api.app` | L4 根 | FastAPI app 組裝、middleware 掛載、exception handler 註冊、lifespan(graceful drain) | `app.py` |
| `taskq_api.__main__` | L4 根 | 管理入口(`migrate`、`seed`、`key create`、`healthcheck`) | `__main__.py` |
| `taskq_api.config` | independence | `TASKQ_*` 環境變數讀取與驗證(`pydantic-settings`) | `config.py` |
| `taskq_api.errors` | independence | RFC 7807 `problem+json` 模型、敏感資料遮蔽工具 | `errors.py` |
| `taskq_api.api.deps` | L4 | `X-API-Key` 認證、`scope` 授權、rate limit 檢查的 **單一 dependency 判定點**(FR-04 強制) | `api/deps.py` |
| `taskq_api.api.tasks` | L4 | `/v1/tasks` CRUD、`/v1/tasks/{id}/run`、`/v1/tasks/{id}/runs` 路由 | `api/tasks.py` |
| `taskq_api.api.health` | L4 | `/healthz`、`/readyz`、`/v1/metrics` 路由 | `api/health.py` |
| `taskq_api.service.tasks` | L3 | 任務資源 CRUD 業務規則、狀態機轉換、cursor 分頁 | `service/tasks.py` |
| `taskq_api.service.runner` | L3 | async 子進程執行器、TaskGroup 管理、graceful drain、metrics 收集 | `service/runner.py` |
| `taskq_api.service.auth` | L3 | key 雜湊驗證、scope 階層判定(`read < write < admin`) | `service/auth.py` |
| `taskq_api.service.ratelimit` | L3 | 令牌桶計算、與 `rate_repo` 互動 | `service/ratelimit.py` |
| `taskq_api.repository.session` | L2 | Session 生命週期、交易 context manager(FR-06) | `repository/session.py` |
| `taskq_api.repository.task_repo` | L2 | `tasks`、`task_results`、`task_tags` CRUD、顯式 `selectinload` | `repository/task_repo.py` |
| `taskq_api.repository.key_repo` | L2 | `api_keys` 查詢(hash 比對)、`revoked_at` 過濾 | `repository/key_repo.py` |
| `taskq_api.repository.rate_repo` | L2 | `rate_buckets` 更新(row-level lock) | `repository/rate_repo.py` |
| `taskq_api.models` | L1 | SQLAlchemy declarative + pydantic request/response schema | `models/orm.py`、`models/schemas.py` |

#### 2.3.1 分層約束(`.importlinter` 規格)

| 層 | 可 import | 不得 import |
|----|-----------|-------------|
| `api` | `service`、`errors`、`config` | `repository`、`models`、`sqlalchemy` |
| `service` | `repository`(經由 `models` 介面)、`errors`、`config` | `sqlalchemy`(禁止 contract) |
| `repository` | `models`、`sqlalchemy`、`errors`、`config` | `api`、`service` |
| `models` | `sqlalchemy`、`pydantic`、`config` | `api`、`service`、`repository` |
| `config` / `errors` | 標準庫、`pydantic` | `sqlalchemy`、`api`、`service`、`repository`、`models` |

`import-linter` forbidden contract 額外規定:`repository` 以外的任何層 import `sqlalchemy.orm.Session` / `sqlalchemy.orm.Query` 一律擋下;`models` 層僅允許 `sqlalchemy.orm.declarative_base` / `Mapped` / `mapped_column` 等宣告型 API(NFR-06)。

### 2.4 檔案/目錄大小預算

| 目錄 | 規劃檔案數 | 上限 | 狀態 |
|------|------------|------|------|
| `taskq_api/`(根) | 5(`__init__`、`__main__`、`app`、`config`、`errors`) | 15 | ✅ |
| `taskq_api/api/` | 4(`__init__`、`deps`、`tasks`、`health`) | 15 | ✅ |
| `taskq_api/service/` | 5(`__init__`、`tasks`、`runner`、`auth`、`ratelimit`) | 15 | ✅ |
| `taskq_api/repository/` | 5(`__init__`、`session`、`task_repo`、`key_repo`、`rate_repo`) | 15 | ✅ |
| `taskq_api/models/` | 3(`__init__`、`orm`、`schemas`) | 15 | ✅ |
| `migrations/versions/` | 3(`v1_initial`、`v2_tags`、`v3_split_results`) | 15 | ✅ |

無 god-module;每個模組功能單一。

### 2.5 循環依賴檢查

依賴方向單向:`api → service → repository → models`、`config`/`errors` 為 independence。**確認無循環依賴** —— 由 `import-linter` 在 CI 層級把關。

---

## 3. Interfaces & Data Flows

### 3.1 介面總覽

#### 3.1.1 HTTP API(全部 `/v1/*` 走 `application/problem+json` 錯誤契約)

| 方法 | 路徑 | scope | 對應模組 | FR |
|------|------|-------|----------|----|
| `POST` | `/v1/tasks` | `write` | `api.tasks.create_task` | FR-01 |
| `GET` | `/v1/tasks/{id}` | `read` | `api.tasks.get_task` | FR-01 |
| `GET` | `/v1/tasks` | `read` | `api.tasks.list_tasks` | FR-01 |
| `DELETE` | `/v1/tasks/{id}` | `admin` | `api.tasks.delete_task` | FR-01 |
| `POST` | `/v1/tasks/{id}/run` | `write` | `api.tasks.run_task` | FR-02 |
| `GET` | `/v1/tasks/{id}/runs` | `read` | `api.tasks.list_runs` | FR-02 |
| `GET` | `/v1/metrics` | `admin` | `api.health.metrics` | FR-09 |
| `GET` | `/healthz` | — | `api.health.liveness` | FR-09 |
| `GET` | `/readyz` | — | `api.health.readiness` | FR-09 |

#### 3.1.2 管理 CLI(`python -m taskq_api`)

| 子命令 | 對應模組 | 用途 |
|--------|----------|------|
| `migrate` | `__main__.migrate` | 呼叫 `alembic upgrade head`(FR-07) |
| `seed` | `__main__.seed` | 寫入測試 fixtures(僅測試/dev) |
| `key create --scope <scope>` | `__main__.key_create` | 產生 API key,明文僅印一次(FR-03) |
| `healthcheck` | `__main__.healthcheck` | CLI 端的 `/healthz` 探測 |

### 3.2 端到端資料流

#### 3.2.1 建立任務並執行(`POST /v1/tasks` → `POST /v1/tasks/{id}/run`)

```
[Client]
  │  POST /v1/tasks  + X-API-Key  body=TaskCreate
  ▼
[api.tasks.create_task]
  │  1. api.deps.require_api_key(headers) ─────────┐
  │     → service.auth.verify_key(hash, scope)     │  FR-03/04
  │     → service.ratelimit.check_and_consume()    │  FR-05
  │  2. pydantic TaskCreate validation              │  FR-01
  ▼
[service.tasks.create_task]
  │  呼叫 repository.task_repo
  │  取得 Session(每請求一條,context manager 保證 commit/rollback)
  ▼
[repository.task_repo.insert]
  │  ORM INSERT into tasks(name 唯一約束檢查)
  │  衝突 → 409 problem+json
  ▼
[Response 201 + {"id": "{uuid}"}]

──────────────────分時──────────────────

[Client]
  │  POST /v1/tasks/{id}/run  X-API-Key scope=write
  ▼
[api.tasks.run_task]
  │  api.deps.require_scope("write")  → 202
  ▼
[service.tasks.run_task]
  │  寫入 tasks.status = running      (transaction 1)
  │  提交後立刻回 202 + run_id
  ▼
[service.runner.spawn]  (background asyncio.Task)
  │  asyncio.create_subprocess_exec(*shlex.split(command))
  │  timeout = TASKQ_TASK_TIMEOUT
  │  等候 stdout/stderr → task_results(FR-02)
  │  CancelledError 向上傳播(FR-08 / NFR-03)
```

#### 3.2.2 認證 + scope 授權 + rate limit(api.deps 單一判定點)

```
api.deps.require_api_key(request):
  1. 讀取 X-API-Key header
  2. SHA-256(key) → repository.key_repo.find_by_hash()
     → 找不到或 revoked_at 非空 → 401
  3. hmac.compare_digest(hash, stored)  ← 常數時間
  4. 把 ApiKey 物件注入 request.state
  5. 呼叫 service.ratelimit.check_and_consume(key_id, bucket_cfg)
     → repository.rate_repo.update_atomic(row-level lock)
     → 桶空 → 429 + Retry-After
  6. 視端點註解(Depends(require_scope("admin")))決定後續
     → service.auth.has_scope(api_key.scope, required) 
     → 不足 → 403 + 不可洩漏資源存在性
```

#### 3.2.3 Schema migration 往返(FR-07)

```
[migrations/versions/v1_initial.py]
   tasks / api_keys / rate_buckets 三表
   ↓
[migrations/versions/v2_tags.py]
   + tags / task_tags + tasks.name 唯一索引
   ↓
[migrations/versions/v3_split_results.py]
   + task_results 表
   + 把既有 tasks.result_json 內容逐列搬入 task_results
   - drop tasks.result_json
   ↓
[alembic downgrade -1]
   反向搬遷回 tasks.result_json
   drop task_results
   ← 樣本資料逐欄相同(SPEC §8 #12)
```

---

## 4. NFR Handling

> 每一條 NFR 的 `dimension` 與 SPEC.md 第 4 節逐字對齊;處理方式落地到具體模組/測試。

### 4.1 NFR-01 效能與查詢效率 — `dimension: performance`

| 設計落地 | 位置 |
|---------|------|
| 列表端點(cursor-based)預載 task_tags、task_results | `repository/task_repo.list_paginated` 使用 `selectinload` |
| SQL 陳述計數斷言(SQLAlchemy event listener) | `03-development/tests/integration/test_perf_sql_count.py` |
| p95 計測(< 30ms / < 80ms) | `03-development/tests/perf/test_bench.py`(`pytest-benchmark`) |
| 10k 筆 fixture | `03-development/tests/integration/conftest.py` |

**驗證**:SPEC §8 #14、#15。

### 4.2 NFR-02 HTTP 與資料層安全 — `dimension: security`

| 設計落地 | 位置 |
|---------|------|
| `shlex.split` + `shell=False`(預設) | `service/runner.spawn` |
| 禁字串拼接 SQL — ORM/參數化一致 | `repository/*`(全面 ORM);`grep -rn '%.*SELECT\|f".*SELECT\|+.*SELECT' 03-development/src/` CI gate |
| key hash:`hashlib.sha256(key.encode()).hexdigest()` | `service/auth.hash_key` |
| `hmac.compare_digest` | `service/auth.verify_key` |
| 403 不洩漏資源存在性 | `api.deps.require_scope` 在 resource 查詢前判定 |
| 錯誤 body 不含堆疊/SQL/路徑 | `errors.to_problem_json` 強制白名單欄位 |
| CORS 全拒(預設) | `app.py` 啟動時讀 `TASKQ_CORS_ORIGINS`;空字串 → 拒絕所有 |
| bandit 0 HIGH / 0 MEDIUM | CI:`bandit -r 03-development/src/` |

**驗證**:SPEC §8 #5–#7、#16、#17、#19、#21、#23。

### 4.3 NFR-03 錯誤處理、交易與非同步正確性 — `dimension: error_handling`

| 設計落地 | 位置 |
|---------|------|
| 交易 context manager | `repository/session.transactional()` |
| 禁裸 `except:` / `except Exception: pass` | `service/*`、`api/*` 全部顯式處理;CI gate 掃(框架 `ast-error-handling`) |
| `asyncio.CancelledError` 必須 re-raise | `service/runner` 內 `try/except CancelledError: raise`(允許頂層記錄後 re-raise) |
| DB 連線失敗 → `/readyz` 503 | `api.health.readiness` 嘗試 `SELECT 1`;失敗回 503 + detail |
| 子進程 timeout → kill + wait | `service/runner`:`process.kill()` 後 `await process.wait()`,記錄 `interrupted` |
| Alembic 失敗 → 交易 rollback | `migrations/versions/v3_split_results.py` 在 `upgrade()` 用 transaction context |

**驗證**:SPEC §8 #25、R7、R8。

### 4.4 NFR-04 敏感資料遮蔽 — `dimension: security`

| 設計落地 | 位置 |
|---------|------|
| 遮蔽 regex — stdout/stderr/錯誤 body 寫入前 | `errors.redact(text)` |
| DB URL 密碼片段遮蔽 | `config.format_db_url()`(logging 之前) |
| `api_keys.key_hash` 為 64 hex | `service/auth.hash_key` 固定長度 |
| 明文 API key 僅 `key create` 印一次 | `__main__.key_create`;後續僅 hash 寫入 DB |

**驗證**:SPEC §8 #18、#20。

### 4.5 NFR-05 文件覆蓋 — `dimension: documentation`

| 設計落地 | 位置 |
|---------|------|
| 100% 公開函式/類別 docstring,含 `[FR-XX]` / `[NFR-XX]` 引用 | `taskq_api/**/*.py` |
| OpenAPI schema 摘要完整 | `api.tasks` / `api.health` 全部 `summary` + `description` |
| 框架掃描 | `harness/toolchains/ast_docstrings` |

**驗證**:SPEC §8 #1(全綠)+ 框架 `ast-docstrings` 100%。

### 4.6 NFR-06 架構分層契約 — `dimension: architecture_constraints`

| 設計落地 | 位置 |
|---------|------|
| `.importlinter` 在 repo 根 | `api > service > repository > models`(合約) |
| forbidden contract | `repository` 以外 import `sqlalchemy.orm.Session` / `Query` 一律違規;`models` 層限用 declarative API(`declarative_base` / `Mapped` / `mapped_column`) |
| `lint-imports` CI gate | exit 0 才允許 merge |

**驗證**:SPEC §8 #21。

### 4.7 NFR-07 依賴與授權合規 — `dimension: license_compliance`

| 設計落地 | 位置 |
|---------|------|
| `requirements.txt` 全部 `==` 釘版 | repo 根 |
| `requirements.lock` 鎖定 transitive | 維護腳本:`pip-compile` 產出 |
| `08-config/SBOM.json` | `name` / `version` / `license` / `direct\|transitive` |
| license allowlist:MIT / BSD-2 / BSD-3 / Apache-2.0 / PSF | CI:`pip-licenses --format=json --with-system` 比對 |

**驗證**:SPEC §8 #22。

### 4.8 NFR-08 變異測試 — `dimension: mutation_testing`

| 設計落地 | 位置 |
|---------|------|
| `.methodology/harness_config.json` | `features.mutation_testing: true` |
| mutmut scope 限定 | `service/` + `repository/`(執行時間預算) |
| mutation score ≥ 70 | `mutmut results` |

**驗證**:SPEC §8 #24。

### 4.9 NFR-09 驗證真實性(零 skip 鐵律) — `dimension: test_assertion_quality`

| 設計落地 | 位置 |
|---------|------|
| 全部測試函式至少一個 `assert` | `zero_assert == 0`(框架 `ast-assertions`) |
| FR-07 三步 migration 真實 SQLite 檔案測試 | `tests/integration/test_migration_roundtrip.py`(非 in-memory mock) |
| 不得用 `--ignore` / `-k` / `collect_ignore` 排除 | CI gate(框架本身) |
| `skipped 計數 == 0` | `pytest 03-development/tests -q` 輸出 |

**驗證**:SPEC §8 #1、#12、#13。

### 4.10 NFR-10 整合覆蓋 — `dimension: integration_coverage`

| 設計落地 | 位置 |
|---------|------|
| `httpx.AsyncClient(transport=ASGITransport(app))` | `tests/integration/conftest.py` |
| 不直接呼叫 handler 函式 | 全部走 HTTP 介面 |
| 錯誤碼全覆蓋 | 401 / 403 / 404 / 409 / 422 / 429 / 503 各一例 |
| integration 行覆蓋 ≥ 80% | `pytest-cov --cov=03-development/src` |

**驗證**:SPEC §8 #3、#5–#10。

### 4.11 NFR-11 可讀性 — `dimension: readability`

| 設計落地 | 位置 |
|---------|------|
| 單檔 ≤ 400 行 | 模組職責單一,handler ≤ 40 行 |
| 單一目錄 ≤ 15 檔 | 見 §2.4 預算 |
| handler 業務邏輯下沉 service | `api/*` 僅序列化 + 委派 |
| 函式 CC ≤ 10 | 框架 `readability-v2` |

**驗證**:SPEC §8 + 框架 `readability-v2`。

### 4.12 NFR-12 系統驗證目標 — `dimension: execute_verification_target`

| 設計落地 | 位置 |
|---------|------|
| `Makefile` `verify-system` target | SPEC §8 #27 |
| 含 migration 往返 | `alembic upgrade head` → 測試 → 冒煙 → `downgrade base` → `upgrade head` |

**驗證**:SPEC §8 #27 + Gate 2 觸發。

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int must
> match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-08-14"
  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
  project: "taskq-api"

  layers:
    - name: api
      modules:
        - name: "taskq_api.app"
        - name: "taskq_api.__main__"
        - name: "taskq_api.api.deps"
        - name: "taskq_api.api.tasks"
        - name: "taskq_api.api.health"
      allowed_dependencies: ["service", "independence", "migrations"]
    - name: service
      modules:
        - name: "taskq_api.service.tasks"
        - name: "taskq_api.service.runner"
        - name: "taskq_api.service.auth"
        - name: "taskq_api.service.ratelimit"
      allowed_dependencies: ["repository", "independence"]
    - name: repository
      modules:
        - name: "taskq_api.repository.session"
        - name: "taskq_api.repository.task_repo"
        - name: "taskq_api.repository.key_repo"
        - name: "taskq_api.repository.rate_repo"
      allowed_dependencies: ["models", "independence"]
    - name: models
      modules:
        - name: "taskq_api.models.orm"
        - name: "taskq_api.models.schemas"
      allowed_dependencies: ["independence"]
    - name: independence
      modules:
        - name: "taskq_api.config"
        - name: "taskq_api.errors"
      allowed_dependencies: []
    - name: migrations
      modules:
        - name: "migrations.versions.v1_initial"
        - name: "migrations.versions.v2_tags"
        - name: "migrations.versions.v3_split_results"
      allowed_dependencies: ["models"]

  allowed_dependencies:
    - from: api
      to: service
    - from: api
      to: independence
    - from: api
      to: migrations
    - from: service
      to: repository
    - from: service
      to: independence
    - from: repository
      to: models
    - from: repository
      to: independence
    - from: models
      to: independence

  quality_targets:
    max_complexity: 10          # NFR-11 CC ≤ 10
    min_coverage: 100           # docstring coverage floor per NFR-05 (SRS line 720); integration coverage ≥ 80% lives in NFR-10 traceability target
    max_coupling: 0.3           # CRG community cohesion 門檻

  nfr_dimension_mapping: {}    # auto-derived from nfr_traceability.dimension

  nfr_traceability:
    NFR-01:
      type: performance
      dimension: performance
      target: "p95 < 30ms"
      module: taskq_api.repository.task_repo
    NFR-02:
      type: security
      dimension: security
      target: "bandit 0 HIGH/0 MEDIUM; 0 SQL string concat"
      module: taskq_api.repository.session
    NFR-03:
      type: reliability
      dimension: error_handling
      target: "no silent except; CancelledError always re-raised"
      module: taskq_api.repository.session
    NFR-04:
      type: security
      dimension: security
      target: "0 plaintext api_key / db password in logs"
      module: taskq_api.errors
    NFR-05:
      type: documentation
      dimension: documentation
      target: "100% docstring coverage with [FR-XX]/[NFR-XX]"
      module: taskq_api.api.deps
    NFR-06:
      type: layering
      dimension: architecture_constraints
      target: "api > service > repository > models; 0 sqlalchemy leak"
      module: taskq_api.api.deps
    NFR-07:
      type: licensing
      dimension: license_compliance
      target: "100% deps in allowlist (incl. transitive)"
      module: taskq_api.config
    NFR-08:
      type: mutation
      dimension: mutation_testing
      target: "mutation score >= 70"
      module: taskq_api.service.runner
      scope_layers: ["service", "repository"]
    NFR-09:
      type: testability
      dimension: test_assertion_quality
      target: "skipped == 0; zero_assert == 0"
      module: taskq_api.repository.session
    NFR-10:
      type: integration
      dimension: integration_coverage
      target: "integration coverage >= 80%"
      module: taskq_api.api.tasks
    NFR-11:
      type: maintainability
      dimension: readability
      target: "MI >= 80; CC <= 10; <=400 lines/file; <=15 files/dir"
      module: taskq_api.api.tasks
    NFR-12:
      type: verifiability
      dimension: execute_verification_target
      target: "make verify-system exit 0 with PASS"
      module: taskq_api.app

  advisory_only: []

  gate_score_overrides: {}

  fr_module_traceability:
    FR-01: ["taskq_api.api.tasks", "taskq_api.service.tasks"]
    FR-02: ["taskq_api.api.tasks", "taskq_api.service.runner"]
    FR-03: ["taskq_api.api.deps", "taskq_api.service.auth"]
    FR-04: ["taskq_api.api.deps", "taskq_api.service.auth"]
    FR-05: ["taskq_api.api.deps", "taskq_api.service.ratelimit"]
    FR-06: ["taskq_api.repository.session"]
    FR-07: ["migrations.versions.v1_initial", "migrations.versions.v2_tags", "migrations.versions.v3_split_results"]
    FR-08: ["taskq_api.service.runner", "taskq_api.app"]
    FR-09: ["taskq_api.api.health", "taskq_api.__main__"]
    FR-10: ["taskq_api.errors", "taskq_api.app"]

  architecture_constraints:
    - "no_circular_dependencies"
    - "sqlalchemy_only_in_repository"
    - "no_string_sql_concatenation"
    - "no_shell_true_no_eval_no_exec"

  high_risk_modules:
    - "taskq_api.service.runner"
    - "taskq_api.service.auth"
    - "taskq_api.repository.session"
    - "migrations.versions.v3_split_results"
```
<!-- SAB:END -->

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full
  justification: ""

  trust_boundaries:
    - id: TB-01
      name: "external HTTP client"
      description: >
        Requests entering from unauthenticated clients into the FastAPI app
        via the public internet (or LAN). All /v1/* endpoints cross this boundary;
        /healthz and /readyz remain open per FR-09.
    - id: TB-02
      name: "API → Service boundary"
      description: >
        Authenticated API layer delegates to service layer. The service layer
        enforces business rules and never touches SQLAlchemy directly
        (forbidden contract — NFR-06).
    - id: TB-03
      name: "Service → Repository → Database"
      description: >
        The repository layer is the only layer that may import SQLAlchemy.
        All ORM operations and parameterised queries cross this boundary;
        DB connection strings are secrets that must not leak into logs.
    - id: TB-04
      name: "Runner → Child Subprocess"
      description: >
        The async task runner spawns subprocesses via
        asyncio.create_subprocess_exec(*shlex.split(command)) — shell=False is
        mandatory. Timeout enforcement must kill the child process to avoid
        orphans (FR-08).
    - id: TB-05
      name: "Error response body surface"
      description: >
        The  application/problem+json body is the only externally observable
        representation of an internal failure. The detail field must be a
        allow-listed value; internal structure (SQL, stack trace, file path)
        must never appear (FR-10 / NFR-02).

  threats:
    - id: T-01
      boundary: TB-03
      category: tampering
      description: "SQL injection via string-concatenated SQL or unparameterised queries"
      mitigation: >
        All queries use SQLAlchemy ORM or bound parameters. CI gate grep
        forbids f-string / % / + composition of SQL (NFR-02).
      owner_module: "taskq_api.repository.session"
      nfr: NFR-02
      verified_by: "test_sec_t01_no_sql_string_concatenation"
    - id: T-02
      boundary: TB-01
      category: spoofing
      description: "API key brute-force / replay against the X-API-Key header"
      mitigation: >
        SHA-256 hash on disk; hmac.compare_digest (constant-time) on lookup;
        revoked_at filter excludes invalid keys; plaintext only printed once
        at key create.
      owner_module: "taskq_api.service.auth"
      nfr: NFR-02
      verified_by: "test_sec_t02_api_key_hash_and_compare_digest"
    - id: T-03
      boundary: TB-04
      category: elevation_of_privilege
      description: "Subprocess command injection via shell metacharacters"
      mitigation: >
        shlex.split() splits argument list; asyncio.create_subprocess_exec
        without shell=True. grep gate 'shell=True' returns 0 hits.
      owner_module: "taskq_api.service.runner"
      nfr: NFR-02
      verified_by: "test_sec_t03_no_shell_true_in_runner"
    - id: T-04
      boundary: TB-02
      category: denial_of_service
      description: "Rate-limit race condition allowing over-admission"
      mitigation: >
        rate_buckets update runs in a single transaction with row-level lock
        (SELECT ... FOR UPDATE). Capacity is TASKQ_RATE_BURST; refill rate is
        TASKQ_RATE_PER_SEC; over-quota returns 429 + Retry-After.
      owner_module: "taskq_api.repository.rate_repo"
      nfr: NFR-01
      verified_by: "test_sec_t04_rate_limit_row_lock"
    - id: T-05
      boundary: TB-01
      category: information_disclosure
      description: "403 response reveals whether a resource exists"
      mitigation: >
        api.deps.require_scope enforces scope before resource lookup; 403
        detail is a constant string with no resource id.
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_sec_t05_403_does_not_leak_existence"
    - id: T-06
      boundary: TB-05
      category: information_disclosure
      description: "Error body leaks stack trace, SQL, or file path"
      mitigation: >
        errors.to_problem_json builds a fixed-shape response with
        type/title/status/detail/instance/correlation_id; detail is a
        allow-listed human string; raw exception is only logged.
      owner_module: "taskq_api.errors"
      nfr: NFR-02
      verified_by: "test_sec_t06_problem_json_shape_whitelist"
    - id: T-07
      boundary: TB-03
      category: information_disclosure
      description: "Plaintext API key or DB password leaks into logs / metrics"
      mitigation: >
        api_keys.key_hash is sha256 hex (64 chars); config.format_db_url
        strips password before logging; errors.redact scrubs sk-* / token /
        postgres:// patterns from stdout/stderr/bodies.
      owner_module: "taskq_api.errors"
      nfr: NFR-04
      verified_by: "test_sec_t07_redact_secrets_in_outputs"
    - id: T-08
      boundary: TB-04
      category: denial_of_service
      description: "Child subprocess becomes orphan on timeout"
      mitigation: >
        asyncio.wait_for + process.kill() + await process.wait() in
        service.runner. Timeout records 'interrupted' status; CI test asserts
        no orphan PID remains.
      owner_module: "taskq_api.service.runner"
      nfr: NFR-03
      verified_by: "test_sec_t08_no_orphan_subprocess"
    - id: T-09
      boundary: TB-02
      category: denial_of_service
      description: "asyncio.CancelledError swallowed on shutdown causes graceful drain to hang"
      mitigation: >
        CancelledError is re-raised after logging; handler chain never wraps
        it in 'except Exception'. AST gate detects silence.
      owner_module: "taskq_api.service.runner"
      nfr: NFR-03
      verified_by: "test_sec_t09_cancelled_error_always_reraised"
    - id: T-10
      boundary: TB-01
      category: repudiation
      description: "Client actions cannot be correlated across logs and responses"
      mitigation: >
        correlation_id (uuid4) is generated per request, set on
        X-Correlation-Id response header, and stamped onto every log record.
      owner_module: "taskq_api.errors"
      nfr: NFR-04
      verified_by: "test_sec_t10_correlation_id_round_trip"
    - id: T-11
      boundary: TB-03
      category: tampering
      description: "v3 migration data loss on downgrade path"
      mitigation: >
        migrations/versions/v3_split_results.py performs row-level copy from
        tasks.result_json to task_results; downgrade reverses the copy and
        restores rows. Integration test asserts sample row columns are
        identical after upgrade → write → downgrade -1 → upgrade cycle.
      owner_module: "migrations.versions.v3_split_results"
      nfr: NFR-03
      verified_by: "test_sec_t11_migration_roundtrip_data_integrity"
```
<!-- SEC:END -->

---

## 7. Appendix

### 7.1 模組規模估算(行數上限)

| 模組 | 預估行數 | 上限 |
|------|----------|------|
| `app.py` | 80 | 400 |
| `__main__.py` | 60 | 400 |
| `config.py` | 80 | 400 |
| `errors.py` | 100 | 400 |
| `api/deps.py` | 120 | 400 |
| `api/tasks.py` | 200 | 400 |
| `api/health.py` | 80 | 400 |
| `service/tasks.py` | 150 | 400 |
| `service/runner.py` | 180 | 400 |
| `service/auth.py` | 100 | 400 |
| `service/ratelimit.py` | 80 | 400 |
| `repository/session.py` | 80 | 400 |
| `repository/task_repo.py` | 220 | 400 |
| `repository/key_repo.py` | 80 | 400 |
| `repository/rate_repo.py` | 80 | 400 |
| `models/orm.py` | 200 | 400 |
| `models/schemas.py` | 150 | 400 |
| `migrations/versions/v1_initial.py` | 100 | 400 |
| `migrations/versions/v2_tags.py` | 80 | 400 |
| `migrations/versions/v3_split_results.py` | 150 | 400 |

### 7.2 變更日誌

| 版本 | 日期 | 摘要 |
|------|------|------|
| v1.0.0 | 2026-08-14 | 初版 — 對應 SPEC.md v1.0.0 / 10 FR / 12 NFR |
