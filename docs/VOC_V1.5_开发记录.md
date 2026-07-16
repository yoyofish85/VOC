# VOC V1.5 开发记录

> 本文档记录 V1.5 迭代中的**问题背景、改动内容、涉及文件与验证要点**，便于每次发版前核对「这次到底修了什么、没动什么」。  
> 架构级 V3 标签体系说明见 [`VOC_V1.5_优化记录_v3.0.md`](./VOC_V1.5_优化记录_v3.0.md)。

**最后更新**：2026-07-08  
**适用分支**：VOC_V1.5 当前主开发线（`main`，含双启动脚本与服务器 MLX 验证）

---

## 一、变更总览（快速索引）

| 批次 | 主题 | 核心问题 | 状态 |
|------|------|----------|------|
| R1 | 性能与稳定性 | 14B 批量分类卡死、数据汇报页自动加载卡死、畸形 JSON 导致 SQL/解析失败 | ✅ 已落地 |
| R2 | 准确率提升 | 实测约 50%，需在不改规则核心的前提下提升路由质量 | ✅ 已落地 |
| R3 | 方案 A | 人工复核结论应作为金标优先命中 | ✅ 已落地 |
| R4 | 方案 C | 规则双特征冲突需标记并交 14B 仲裁 | ✅ 已落地 |
| R5 | 方案 D | 冲突词库硬编码不便维护 | ✅ 已落地 |
| R6 | 方案 E | 分类路由缺少可观测指标 | ✅ 已落地 |
| R7 | 方案 X1 | 14B 易误对整批运行 | ✅ 已落地 |
| R8 | P1 数据汇报 | 日常加载带 nocache、批次未绑定导致慢查 | ✅ 已落地 |
| R9 | P2 列表与缓存 | 列表 SELECT 过重、L2 白名单重复读盘 | ✅ 已落地 |
| R10 | P3 物化列 | v3 筛选走 JSON UDF，无法走索引 | ✅ 已落地 |
| R11 | 慢路径优化 | 报表 O(n²)、批量保存逐条 UPDATE、历史样例单字 LIKE | ✅ 已落地 |
| R12 | 部署启动修复 | 服务器「后端服务启动失败」 | ✅ 已落地 |
| R13 | 测试期缺陷 | 列表空数据 F1、暂存 500 F4 | ✅ 已落地 |
| R14 | 六任务基础加固（T1–T6） | 部署脚本、WAL、回流失败、L2 后备、延迟回流、健康端点 | ✅ 已落地 |
| R15 | 盲区 1–4 | 回流测试、WAL 压测、L2 API 回归、微调导出/A/B | ✅ 已落地 |
| R16 | 14B 批量稳定性 | >150 条分类 database is locked、整批确认仅写 20 条 | ✅ 已落地 |
| R17 | 汇报文案四维度 | 数据汇报「生成汇报文案」无反馈、summary 缺趋势/原文 | ✅ 已落地 |
| R18 | 年度 CSV 归档修复 | 排序倒序、VIN/车型缺失、所选确认误写 CSV | ✅ 已落地 |
| R19 | MLX 可选推理后端 | `VOC_USE_MLX=1` 时走 MLX，与 Ollama 共享后处理 | ✅ 已落地 |
| R20 | 交互优化 + 确认归档拆分 | 按钮语义、match_type 展示、确认归档与批次状态分离 | ✅ 已落地 |
| R21 | 综合平台扩展 | 实时看板 KPI/异常/趋势、周报、L3 短语、问题状态 | ✅ 已落地 |
| R22 | LoRA 路径 A 重训工具链 | 数据均衡 → 14B 训练 → smoke + holdout A/B 验证 | ✅ 已落地 |
| R23 | 双启动脚本 + 服务器环境修复 | Ollama/MLX 一键切换；Python 3.12 venv；依赖版本锁定 | ✅ 已落地 |
| R24 | MLX 准确率评估计划 | Track B 生产批次 + Track A holdout 对照；进化路线 | 📋 计划中 |
| R25 | MVP 90–93% + 分步门禁 | 目标锁定；E0–E7 门禁；Track B 82.64% 已归档 | ⏳ 执行中 |
| R26 | Ollama 测试运营 + 优化待办 | 每日 ~200 条；R1/R2 完成；三类边界问题记录 | ⏳ 测试中 |

---

## 二、禁改区（历次迭代均未修改）

以下区域在性能/准确率/部署优化中**刻意不动**，发版核对时请确认 diff 未触及：

- `LabelMatcher` 核心匹配规则与标签层级语义
- 联防逻辑（`enforce_l2_whitelist_and_guard` 等行为定义，除 gold_review 豁免外）
- 14B 分类提示词正文
- 咨询黑名单匹配逻辑
- `performance_evaluation/` 下评估脚本逻辑
- 人工复核页**布局与按钮顺序**（仅增加交互，不改结构）

---

## 三、分批次详细记录

### R1 · 性能与稳定性（2026-05 初）

#### 背景

- 批量 14B 分类长时间无响应，前端像「卡死」。
- 数据汇报页进入后自动触发重计算，页面阻塞。
- 部分历史数据的 `v3_label_meta` 为畸形 JSON，导致 dashboard / 列表 / 报表 SQL 或 `json.loads` 失败。

#### 改动摘要

| 项 | 修复内容 |
|----|----------|
| 异步分类 Job | `POST /api/batch_classify` 立即返回 `job_id`，后台线程执行；前端轮询 `/api/batch_classify/status/{job_id}` |
| 14B 超时 | `qwen_ollama.py` 分类请求 60s 超时；外层重试 |
| 数据汇报 | 移除进入页自动 AI 汇报；图表由 `scheduleLoad` 加载；AI 文案仅手动点「生成汇报文案」 |
| SQLite | WAL 模式、复核相关索引、`voc_json_extract` UDF |
| Dashboard | 统计改 Python 侧解析 v3，避免畸形 JSON 拖垮 SQL |
| 复核列表分页 | `TABLE_PAGE_CAP = 20`，默认页大小上限 20 |
| 报表聚合 | `report_aggregator.py` 轻量 fetch + Python 解析 v3 |

#### 主要文件

- `src/backend/main.py`
- `src/backend/voc_classifier_service.py`
- `src/backend/qwen_ollama.py`
- `src/backend/report_aggregator.py`
- `src/frontend/src/components/OpinionReview.vue`
- `src/frontend/src/views/DataReport.vue`

#### 验证要点

- [ ] 快速规则分类后，14B 任务可后台跑完且前端可继续操作
- [ ] 数据汇报改日期/区域不会自动调 AI；点「刷新」才带 `nocache`
- [ ] 含畸形 JSON 的批次 dashboard 仍能打开

---

### R2 · 准确率分析与策略（2026-05）

#### 背景

- 全量实测准确率约 **50%**；硬件为 M3 Max，14B 不宜作为默认全量路径。
- 目标策略：**约 90% 规则 + 约 10% 14B**（低置信、冲突、必要的 L2/L3 精排）。

#### 结论（文档化，非代码）

- 优先提高规则命中率与金标覆盖，而非扩大 14B 默认范围。
- 后续 R3–R7 为可落地的具体措施。

---

### R3 · 方案 A：复核金标覆盖层

#### 问题

人工在复核页确认的标签，再次分类时仍可能被规则或联防改写。

#### 修复

1. `offline_validate.py`：`load_review_gold_db()`，从 DB 读取已复核记录构建金标索引。
2. `LabelMatcher.match()`：首行检查 `gold_review` 命中，`confidence=0.99`。
3. `voc_classifier_service.py`：`match_type=gold_review` **跳过** `enforce_l2_whitelist_and_guard`。
4. `main.py`：draft/confirm/yearly reflow 后调用 `_refresh_label_matcher_gold_cache()` 刷新内存金标。

#### 主要文件

- `label_project/offline_validate.py`
- `src/backend/voc_classifier_service.py`
- `src/backend/main.py`

#### 验证要点

- [ ] 已复核条目再次「快速规则分类」结果与人工 L1/L2 一致
- [ ] reflow 后金标仍生效（无需重启服务）

---

### R4 · 方案 C：规则冲突 → 14B 仲裁

#### 问题

同一条文本在规则路径上同时命中互斥特征（如负面 vs 质量、多 L2 信号），静默选一条导致错分。

#### 修复

- 规则分类完成后调用 `detect_rule_conflict(meta, text)`。
- 命中冲突：`arbitration_required=True`，`conflict_kind` 为 C1/C2/C3，`confidence` 降至 **0.40**，交由后续 14B 路径处理。

#### 主要文件

- `src/backend/voc_classifier_service.py`
- `src/backend/rule_conflict_detector.py`（R5 外置词库后）

#### 验证要点

- [ ] 冲突样本能打出 `conflict_kind` 且进入低置信/待 14B 队列
- [ ] 非冲突样本不受影响

---

### R5 · 方案 D：冲突词库外置 + 热加载

#### 问题

冲突关键词写在代码里，调整需改 Python 并发版。

#### 修复

- 词库文件：`label_project/rule_conflict_keywords_v1.json`（negative/quality 词组 + C1/C2/C3 规则）。
- `rule_conflict_detector.py`：按文件 mtime 热加载，JSON 解析失败有兜底。
- API：
  - `GET /api/rule_conflict_keywords`
  - `POST /api/rule_conflict_keywords/reload`
  - `POST /api/rule_conflict_keywords/update`

#### 主要文件

- `label_project/rule_conflict_keywords_v1.json`
- `src/backend/rule_conflict_detector.py`
- `src/backend/main.py`

#### 验证要点

- [ ] 修改 JSON 后 reload 生效，无需重启
- [ ] 部署包**必须包含**该 JSON 文件

---

### R6 · 方案 E：分类路由指标

#### 问题

无法量化 gold 命中、冲突、14B 仲裁各占多少。

#### 修复

- `classify_metrics_logger.py`：单次 batch 收集 `gold_hit`、`conflict`、`llm_arbitrated` 等。
- 追加写入 `classify_route_metrics.jsonl`。
- `GET /api/classification/route_metrics?limit=50` 供排查。

#### 主要文件

- `src/backend/classify_metrics_logger.py`
- `src/backend/voc_classifier_service.py`
- `src/backend/main.py`

---

### R7 · 方案 X1：14B 范围确认弹窗

#### 问题

用户勾选少量条目却误点 14B，导致对整批（如 159 条）跑模型。

#### 修复

- `OpinionReview.vue`：`submitBatchClassify` 统一入口。
- 当 **use_llm=true 且已有勾选** 时弹窗：
  - 「仅所选 N 条」
  - 「整批运行」（显式二次确认）

#### 主要文件

- `src/frontend/src/components/OpinionReview.vue`

#### 验证要点

- [ ] 有勾选 + 14B → 必弹窗
- [ ] 无勾选 → 仍按批次全量（与原有整批语义一致）

#### 推荐操作习惯（给用户）

1. 先「快速规则分类」
2. 筛选「待复核 / 低置信」
3. 勾选目标行
4. 顶部 14B 选「仅所选 N 条」；或表格内「批量重新分类」

---

### R8 · P1：数据汇报加载策略

#### 问题

- 日常 `scheduleLoad` 若带 `nocache` 会反复击穿缓存。
- 复核页复杂筛选未绑 `upload_batch` 时扫全库。

#### 修复

| 位置 | 改动 |
|------|------|
| `DataReport.vue` | `scheduleLoad` → `loadAll()` **不带** nocache；仅「刷新 / Refresh」按钮 `loadAll(true)` |
| `OpinionReview.vue` | `pickDefaultBatchIfNeeded()`、`ensureBatchScopeForQuery()`：无批次时自动选最新批次 |

#### 验证要点

- [ ] 改日期后图表加载明显快于点「刷新」
- [ ] 打开复核页默认有批次 scope

---

### R9 · P2：列表窄列 + L2 白名单缓存

#### 问题

- 复核列表 SELECT 含全文 `original_text`，行宽大、传输慢。
- `load_l2_whitelist()` 每次分类都读 JSON。

#### 修复

- `REVIEW_LIST_SELECT_BODY`：列表只带原文 preview（约 420 字）。
- 新增 `GET /api/opinion_detail?opinion_id=` 按需拉全文。
- `qwen_ollama.py`：`load_l2_whitelist()` 按 mtime 内存缓存；`invalidate_l2_whitelist_cache()` 供金标更新后调用。

#### 主要文件

- `src/backend/main.py`
- `src/backend/qwen_ollama.py`

---

### R10 · P3：v3 物化列与索引

#### 问题

列表筛选 `v3_l1 / v3_match_type / confidence` 依赖 JSON UDF，无法稳定走索引。

#### 修复

- 新模块 `v3_materialized.py`：列 `v3_l1, v3_l2, v3_l3, v3_confidence, v3_match_type`。
- 启动时 `ensure_v3_materialized_schema` + 复合索引。
- 分类 UPDATE 时同步写物化列（`voc_classifier_service.py`）。
- `get_review_list` WHERE 改查物化列；dashboard 优先读物化列。
- `report_aggregator.py` SELECT 含物化列。

#### 主要文件

- `src/backend/v3_materialized.py`（**部署必带**）
- `src/backend/main.py`
- `src/backend/voc_classifier_service.py`
- `src/backend/report_aggregator.py`

#### 验证要点

- [ ] 按 L1 / match_type / 低置信筛选响应时间下降
- [ ] 新分类结果物化列与 `v3_label_meta` 一致

---

### R11 · 慢路径代码优化（code-simplify 轮）

#### 问题

性能审查发现的 hot path：报表双循环、批量保存 N 次 connect、MD5/关键词回填逐条 UPDATE、历史样例单字符 LIKE。

#### 修复

| 模块 | 优化 |
|------|------|
| `report_aggregator.py` | `_build_report_index()` 单次扫描替代 O(n²) 聚合 |
| `main.py` | `execute_db_many()`；`batch_save_review` / `draft_save_reviews` 批量 IN + executemany；`_backfill_keywords_for_opinion_ids` 批量查改 |
| `qwen_ollama.py` | `historical_examples` 改为 ≥2 字 token；缓存每 8 次写盘；90s 历史样例内存缓存 |

#### 验证要点

- [ ] 大批量 draft 保存耗时明显下降
- [ ] 报表多月 TOP 子标签接口稳定返回

---

### R12 · 部署启动修复（2026-05-25）

#### 问题

代码传到服务器后，`app_launcher.py` 报 **「后端服务启动失败」**。

#### 根因

1. **启动阶段同步回填**：import `main.py` 时执行 v3 物化列全量回填 + `original_text_md5` 逐条 UPDATE，大数据库可能 **>15s**，端口 8000 尚未监听即被判定失败。
2. **uvicorn 配置**：`reload=True` 与 `workers=2` 同开，生产环境不稳定。
3. **启动器**：子进程 stdout PIPE 未读取可能死锁；失败时不打印后端日志。

#### 修复

| 文件 | 改动 |
|------|------|
| `main.py` | 启动仅 DDL；`@app.on_event("startup")` 后台线程回填 v3 物化列与 MD5；默认 `reload=False, workers=1`（`VOC_UVICORN_RELOAD=1` 可开热重载） |
| `app_launcher.py` | 后台 drain 日志；等待默认 **60s**（`VOC_BACKEND_START_WAIT`）；失败打印最近 40 行 |

#### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `VOC_UVICORN_RELOAD` | 关 | `1/true/yes` 开启开发热重载 |
| `VOC_UVICORN_WORKERS` | `1` | 生产 worker 数 |
| `VOC_BACKEND_START_WAIT` | `120` | 启动器等待次数×0.5s |
| `VOC_DB_PATH` | `src/backend/opinion_review.db` | 数据库路径，目录需可写 |

#### 验证要点

- [ ] 本地 `python3 main.py` 约 1s 内 8000 端口 LISTEN
- [ ] 启动器失败时能看到 Python traceback
- [ ] 后台回填日志：`v3 物化列后台回填完成`、`original_text_md5 后台回填完成`

---

### R13 · 测试期缺陷修复（2026-05-25）

#### F1 · 复核列表 `data` 为空

| 项 | 内容 |
|----|------|
| 现象 | `GET /get_review_list` 返回 `total>0` 但 `data=[]`，复核页表格空白 |
| 根因 | `REVIEW_LIST_SELECT_BODY` 的 `?` 在 SQL 中先于 `WHERE`，参数却把 batch 条件放在最前 |
| 修复 | `list_params = [preview_n, preview_n, *params, size, offset]` |
| 文件 | `src/backend/main.py` |

#### F4 · 暂存复核 `draft_save_reviews` 500

| 项 | 内容 |
|----|------|
| 现象 | `Incorrect number of bindings supplied... uses 7... 8 supplied` |
| 根因 | SQL 已写死 `review_status=1`，`draft_updates` 仍 append 多余的 `1` |
| 修复 | `draft_updates.append([l1, l2, note, extracted, reviewer or None, now, oid])` |
| 文件 | `src/backend/main.py` |
| 回归 | `tests/unit/test_draft_save_bindings.py` |

#### F5 · 并发分类集成测试断言

- `tests/test_perf_integration.py`：异步 `batch_classify` 接受 `code in (200, 202)`。

#### 验证要点

- [ ] `curl POST /draft_save_reviews` 返回 `code:200, saved>=1`
- [ ] `pytest tests/unit/test_draft_save_bindings.py -v` 通过

---

### R14 · 六任务基础加固（T1–T6）（2026-06-12）

#### 背景

生产测试前需完成 6 项基础设施加固：部署可校验、SQLite 并发、回流可观测、L2 下拉不空、健康探针完善。交叉引用验证 54/54 PASS，基础测试 18/18 PASS。

#### 六任务清单

| 任务 | 主题 | 核心改动 |
|------|------|----------|
| **T1** | 部署脚本 | `package_code.sh` MD5 校验 + git tag；`update_server.sh` `--check-only`；`deploy_checklist.py` 自检 7 项 |
| **T2** | 回流失败机制 | `_append_reflow_failure_jsonl` / `_mark_reflow_row_failed` / `GET /api/reflow_failures` |
| **T3** | L2 后备列表 | 前端 `_FALLBACK_L2_LIST` / `_FALLBACK_L2_BY_L1` / `_FALLBACK_L2_FOR_L1`；后端 `merged_l2_whitelist_for_l1` / `flatten_all_hierarchy_l2` |
| **T4** | SQLite WAL | `_ensure_db_wal_mode()`（WAL / busy_timeout 60s / 64MB cache）；`query_db` 只读 `PRAGMA query_only=ON` |
| **T5** | 延迟回流队列 | `PENDING_REFLOW_PATH` + `_enqueue_pending_reflow` + daemon 每 60s flush |
| **T6** | 健康端点 | `GET /api/health/detail`（integrity / journal_mode / stale_reflow / stats） |

#### 同期修复的 9 个缺陷

| # | 问题 | 修复 |
|---|------|------|
| 1 | `draft_save_reviews` 无条件写 `reflow_synced=0` | 增加 `labels_changed` 判断 |
| 2 | L2 下拉高负载返回空 | 后备列表 + 空缓存即时 fallback |
| 3 | 仪表盘与列表并发超时 | WAL + query_only |
| 4 | 部署无 MD5 | package/update 脚本增强 |
| 5 | `_verify_and_migrate_db` 缺失 | 统一模块 init 入口 |
| 6 | `fallbackL2ForL1` 命名不一致 | 重命名为 `_FALLBACK_L2_FOR_L1` |
| 7 | `deploy_checklist` 行数范围偏窄 | `MAIN_LINE_RANGE = (2800, 4500)` |
| 8 | `merge_gold_feedback` 返回 400 仍标 `reflow_synced=1` | `reflow_batch_rows` 检查 code 并 raise |
| 9 | 前端忽略 `queued_reflow` | `execRowAutoSave` 增加后台队列提示 |

#### 主要文件

- `src/backend/main.py`
- `src/backend/reflow_service.py`
- `src/frontend/src/components/OpinionReview.vue`
- `code_deploy/package_code.sh` / `update_server.sh` / `deploy_checklist.py`

#### 验证要点

- [ ] `python3 code_deploy/deploy_checklist.py` → 7/7
- [ ] `PRAGMA journal_mode` → `wal`
- [ ] `pytest tests/unit/test_reflow_resilience.py tests/unit/test_pending_reflow.py tests/unit/test_main_health.py tests/test_deploy.py -v` → 18 passed

---

### R15 · 盲区 1–4 测试与工具链（2026-06-12 ~ 06-17）

#### 盲区 1 · 回流管线可靠性

| 新增 | 说明 |
|------|------|
| `tests/unit/test_reflow_service.py` | reflow_service 核心 11 项单元测试 |
| `performance_evaluation/check_reflow_health.py` | CI/部署后回流健康检查（**需单独 rsync 到服务器**） |
| `tests/unit/test_check_reflow_health.py` | 健康检查脚本测试 |
| `tests/unit/test_reflow_resilience.py` | 追加 3 个 resilience 回归 |

Commit：`241b91b`

#### 盲区 2 · SQLite WAL 并发压测

| 新增 | 说明 |
|------|------|
| `tests/test_concurrent_db.py` | 3 个 `@pytest.mark.perf` 测试：10 写 + 10 读、WAL 重启持久、回流+查询并发 |

仅开发机验证，不改生产代码。Commit：`618e9e7`

#### 盲区 3 · L2 后备 API 回归

| 新增 | 说明 |
|------|------|
| `tests/unit/test_taxonomy_fallback.py` | 4 项：`get_l2_by_l1`、未知 L1、批量 L1、空 L1 |

Commit：`c248d16`

#### 盲区 4 · 微调技术风险前置

| 新增 | 说明 |
|------|------|
| `performance_evaluation/export_finetune_from_db.py` | 复核数据 → JSONL；seed=42 锁定 10% holdout |
| `performance_evaluation/ab_compare_models.py` | baseline / candidate / compare holdout A/B |
| `tests/unit/test_export_finetune.py` | 4 项导出测试 |
| `tests/unit/test_ab_compare.py` | 2 项 A/B 测试 |
| `data/holdout_opinion_ids.json` | holdout 锁定集（首次导出写入） |

开发机 DB 当前 0 条可导出复核样本；服务器有数据后需先跑正式导出。Commit：`81dc57e`

#### 验证要点

- [ ] `pytest tests/unit/test_reflow_service.py tests/unit/test_check_reflow_health.py -v` → 通过
- [ ] `pytest tests/test_concurrent_db.py -m perf -v` → 3 passed
- [ ] `pytest tests/unit/test_taxonomy_fallback.py -v` → 4 passed
- [ ] `pytest tests/unit/test_export_finetune.py tests/unit/test_ab_compare.py -v` → 6 passed

---

### R16 · 14B 批量分类稳定性 + 整批确认归档（2026-06-17）

#### 问题 1：14B 批量 >150 条崩溃

| 项 | 内容 |
|----|------|
| 现象 | `database is locked`，分类任务中断 |
| 根因 | `run_batch_classify` 全程持有一个 SQLite 写连接，批次结束才 commit |
| 修复 | 读连接加载后立即关闭；结果缓冲每 `VOC_QWEN_COMMIT_EVERY`（默认 10）条短事务 flush；周期性 `PRAGMA wal_checkpoint(PASSIVE)` |

#### 问题 2：年度 CSV 仅保存约 20 条

| 项 | 内容 |
|----|------|
| 现象 | 导入 200+ 条，年度 CSV 只有当前页勾选行 |
| 根因 | 「确认复核」只提交 `selectedRows`（每页最多 20 条）；年度 CSV 仅在整批完成时写入 |
| 修复 | 新增 `POST /confirm_review_batch` 整批确认；`GET /confirm_review_batch/preview` 有未就绪行时 409；每次确认即 `_write_yearly_csv_to_disk`；`_YEARLY_CSV_LOCK` 防并发写 |

#### 主要文件

- `src/backend/voc_classifier_service.py`
- `src/backend/main.py`
- `src/frontend/src/api/review.js`
- `src/frontend/src/components/OpinionReview.vue`
- `tests/test_classify_stress.py`（300 行 + 并发写）
- `tests/unit/test_confirm_batch_archive.py`（5 项）

Commit：`903deea`

#### 验证要点

- [ ] `pytest tests/test_classify_stress.py -v` → 2 passed（无 database is locked）
- [ ] `pytest tests/unit/test_confirm_batch_archive.py -v` → 5 passed
- [ ] 复核页主按钮为「确认整批 + 归档年度数据」

---

### R17 · 汇报文案四维度增强（2026-06-18）

#### 问题

数据汇报页「生成汇报文案」按钮点击后无可见反馈；`summarize_opinions` 输出缺少数量对比、趋势、代表性原文。

#### 后端改动（`qwen_ollama.py`）

- 新参数：`prev_period_stats`、`prev_period_rows`
- 辅助函数：`_pick_representative_quotes`、`_build_volume_stats`、`_compute_trends_from_l1`
- 四维度 prompt：数量 / 趋势 / 问题表象 / 代表性原文（脱敏）
- **保留**原字段：`summary`、`top_issues`、`risks`、`actions`、`ppt_text`、`total`、`period`、`region`、`cache_hit`
- **新增**字段：`volume`（含 `this_period`/`prev_period`）、`trends`、`representative_quotes`；`top_issues[]` 增加 `trend`/`trend_detail`
- `ppt_text` 过短时程序组装降级版本；缓存 key 纳入上期统计摘要

#### API 改动（`main.py`）

- `_calc_prev_period`、`_fetch_opinion_summary_rows` 查询上期已复核数据
- 无已复核数据时返回明确提示（不再静默空响应）
- LLM 失败时规则降级也包含新字段

#### 前端改动（`DataReport.vue`）

- 缺日期 / 无数据 / 成功 / 错误均有 `ElMessage` 提示
- 展示数量环比、趋势观察、代表性原文

#### 测试

- `tests/unit/test_opinion_summary.py` → 5 passed

Commit：`580c2ed`

#### 验证要点

- [ ] `pytest tests/unit/test_opinion_summary.py -v` → 5 passed
- [ ] `GET /api/get_opinion_summary?date_from=...&date_to=...` 响应含 `volume`、`trends`、`representative_quotes`

---

### R18 · 年度 CSV 归档修复（2026-06-18）

#### 问题

用户实测：仅 20 行入 CSV、时间倒序、缺 VIN/车型；「仅确认所选」误触发年度写入。

#### 修复

| 项 | 改动 |
|----|------|
| 排序 | `_write_yearly_csv_to_disk` 改为 `ORDER BY create_time ASC, opinion_id ASC` |
| 列别名 | `_VIN_COLUMN_ALIASES`、`_CAR_MODEL_COLUMN_ALIASES` 等；上传 CSV 兼容「车辆VIN」「车型名称」 |
| 原文兜底 | `_extract_vin_from_text`、`_extract_car_model_from_text` |
| 标签兜底 | `_effective_review_labels_from_row` 回退 `model_class`/`model_keyword` |
| 所选确认 | `confirmReviewApi` 传 `also_yearly: false, write_yearly: false`；按钮文案「仅确认所选（不写年度CSV）」 |

#### 新增测试（`test_confirm_batch_archive.py`）

- `test_annual_csv_sorted_oldest_to_newest`
- `test_upload_alias_vin_model_written_to_annual_csv`
- `test_upload_extracts_vin_model_from_text_when_columns_missing`

Commit：`c51009d`（含 `580c2ed` 中 `main.py` 年度相关改动）

#### 验证要点

- [ ] `pytest tests/unit/test_confirm_batch_archive.py -v` → 8 passed
- [ ] 年度 CSV 时间从早到晚；VIN/车型列有值

---

### R19 · MLX 可选推理后端（2026-06-24）

#### 背景

Apple Silicon 服务器上 Ollama 14B 推理慢；开发机已完成 7B LoRA 预实验，需在 `classify_text` 增加 MLX 路径且与 Ollama 共享后处理。

#### 改动

| 项 | 内容 |
|----|------|
| 环境变量 | `VOC_USE_MLX=1` 启用；`VOC_MLX_MODEL` 基座；`VOC_MLX_ADAPTER` LoRA 权重目录 |
| 懒加载 | `_mlx_model_load()` / `_mlx_generate()`；加载失败返回 `mlx_load_failed` |
| 后处理 | `_post_process_classify_result()` 与 Ollama 共用；MLX 模式下附加 `l3_phrases` |
| 长文本 | `_compact_classify_text()` 截断，避免 14B 批量超时 |

#### 主要文件

- `src/backend/qwen_ollama.py`
- `tests/unit/test_mlx_backend.py`

Commit：`876c4c3`

---

### R20 · 交互优化 + 确认归档拆分（2026-06-29）

#### 改动摘要

| 项 | 内容 |
|----|------|
| 确认归档 | `POST /api/confirm_and_write_csv` 确认所选并刷新年度 CSV |
| 批次状态 | `POST /api/batch_status` 返回完成度与 L1 准确率 |
| 前端 | 「确认归档」「归档年度数据」分按钮；`match_type` 展示 MLX 标签 |
| 数据汇报 | 挂载时 `nocache` 刷新；汇报文案布局优化 |

Commit：`a7013c8`、`00c321b`

---

### R21 · 综合平台扩展（2026-07-01）

#### 后端

- 新增 `analytics_engine.py`：KPI、异常检测（L2 突增 + L3）、7 日趋势、问题处理状态、周报存取
- 7 个 API：`/api/dashboard/kpi|anomalies|trend`、`/api/report/issue_status|weekly_*|generate_weekly`
- `scripts/backfill_l3_phrases.py` 回填历史 L3 短语

#### 前端

- 新建 `Overview.vue`（异常横幅、7 日 L1 趋势、钻取工作台）
- `DataReport.vue` 增加周报与问题处理状态表

Commit：`7eebfa8`；Deploy tag：`deploy_20260701_0927`

---

### R22 · LoRA 路径 A 重训工具链（2026-07-03）

#### 背景

服务器训练集约 2993 条，「非问题」占 ~62%，模型严重偏向非问题。路径 A：L1 均衡后重训 14B LoRA，holdout A/B 验证通过再部署。

#### 新增脚本（服务器顺序执行）

| 脚本 | 作用 |
|------|------|
| `scripts/balance_finetune_data.py` | 非问题 ≤ 原始 30%；业务三类全保留；体验需求升采样；输出 `mlx_finetune_balanced/{train,valid,test}.jsonl` |
| `scripts/train_lora_14b.sh` | `python3 -m mlx_lm lora`，默认 1000 iters，adapter → `~/lora_adapter_14b_v2` |
| `scripts/verify_lora.sh` | 5 条 smoke + holdout baseline/candidate/compare；Δ≥2pp 为 PASS |

#### 代码优化

| 项 | 内容 |
|----|------|
| `match_type` | 有 `VOC_MLX_ADAPTER` → `mlx_14b_lora`；无 adapter → `mlx_14b_base`（避免误标「微调」） |
| 前端 | `OpinionReview.vue` 区分「MLX·14B微调 / MLX·14B基座」 |

#### 前置与部署

```bash
# 1. 导出（服务器有复核数据后）
python3 performance_evaluation/export_finetune_from_db.py

# 2. 均衡 → 训练 → 验证
python3 scripts/balance_finetune_data.py
bash scripts/train_lora_14b.sh
bash scripts/verify_lora.sh

# 3. 启用 LoRA 推理
export VOC_USE_MLX=1
export VOC_MLX_MODEL=mlx-community/Qwen2.5-14B-Instruct-4bit
export VOC_MLX_ADAPTER=~/lora_adapter_14b_v2
```

#### 测试

- `tests/unit/test_balance_finetune_data.py` → 5 passed
- `tests/unit/test_mlx_backend.py` → 8 passed（含 `_mlx_match_type`）

#### LoRA 生效状态（2026-07-03）

| 项 | 状态 |
|----|------|
| 7B 预实验 adapter（开发机 Desktop） | ✅ 训练完成，可手动加载验证 |
| 14B v2 adapter（服务器） | ⏳ 待 `train_lora_14b.sh` 执行 |
| 生产运行时默认 | ❌ 未设 `VOC_USE_MLX`，仍走 Ollama |
| 库内 `mlx_*` match_type 记录 | 0 条（尚未切换 MLX 路径） |

---

### R23 · 双启动脚本 + 服务器 MLX 环境修复（2026-07-08）

#### 背景

服务器首次部署 MLX+LoRA 时出现：后台启动超时、`ModuleNotFoundError: fastapi`、`import mlx_lm` 失败、`python-multipart` 缺失等问题。需固化「Ollama / MLX」切换方式与 venv 安装流程。

#### 新增/更新文件

| 文件 | 作用 |
|------|------|
| `start_ollama.sh` | 清除 MLX 变量，前台启动 Ollama 14B 分类路径 |
| `start_mlx.sh` | 设置 `VOC_USE_MLX=1` + adapter，自动检查/安装 MLX 依赖 |
| `scripts/setup_server_venv.sh` | 一键创建 Python 3.11–3.13 的 `.venv` 并安装全部依赖 |
| `scripts/find_python_for_venv.sh` | 自动选择 `python3.12` 等（排除 3.14） |
| `scripts/resolve_venv_python.sh` | 启动脚本解析 `.venv/bin/python` |
| `scripts/ensure_mlx_deps.sh` | 修复 `mlx_lm` 导入；限制 `transformers<5.13` |
| `requirements-mlx.txt` | MLX 依赖锁定（含 `transformers>=5.0.0,<5.13.0`） |
| `app_launcher.py` | 显式传递 env 给子进程；非 TTY 跳过 `input()`；等待 180s |

#### 关键修复

| 问题 | 修复 |
|------|------|
| Python 3.14 venv | `pydantic` 无轮子；强制 3.11–3.13 |
| `transformers 5.13` + `mlx-lm 0.31` | `import mlx_lm` 报错；锁定 `<5.13` |
| `temperature` 传入 `mlx_lm.generate` | 启动即崩溃；已删除该参数 |
| 系统 `python3` 与 venv 混用 | 启动脚本显式用 `.venv/bin/python` |
| 缺 `python-multipart` | 写入 `requirements.txt`；启动前自动补装 |

#### 服务器日常使用（已验证）

```bash
cd "/Users/chaoyu/Desktop/VOC_AI agent/VOC_V1.5"

# 首次或重建环境
bash scripts/setup_server_venv.sh

# MLX + LoRA（默认 adapter: ~/lora_adapter_14b_v2，可用 VOC_MLX_ADAPTER 覆盖）
./start_mlx.sh

# 切换回 Ollama Qwen 14B（先 Ctrl+C 停掉 MLX 实例）
./start_ollama.sh
```

`source .venv/bin/activate` **可选**；启动脚本会自动解析 venv Python。

#### LoRA 生效状态（2026-07-08 服务器实测）

| 项 | 状态 |
|----|------|
| MLX+LoRA 启动（`./start_mlx.sh`） | ✅ 后端/前端正常 |
| `mlx_lm` + `transformers 5.12.1` | ✅ import 正常 |
| Ollama 切换（`./start_ollama.sh`） | ✅ 待按需切换验证 |
| 首次 MLX 分类加载 | ⚠ 约 90s（仅首次推理，非启动阶段） |

---

### R24 · MLX 准确率评估计划（2026-07-08）

#### 计划文档

`docs/plans/mlx-accuracy-evolution-plan.md`

#### 两条评估轨道

| 轨道 | 内容 | 优先级 |
|------|------|--------|
| **Track B** | 今明两天投喂 **300–400 条**，全 MLX+LoRA 分类 → 人工复核 → 评 L1/L2 | **当前主任务** |
| **Track A** | holdout 三方 A/B + 影子评估（需先 `export_finetune` 初始化 holdout） | 一周内补齐 |

#### 结论（计划内）

- 对「MLX+LoRA 能不能用」：**Track B 更合适**，走真实生产链路且自然覆盖 L2。
- Track A 不可替代 B，但是科学对照；holdout 当前为空，A/B 暂不可直接跑。
- 推荐串联：B 批次进行中 → 并行初始化 holdout → 复核后评批次 → 补跑 A/B。

#### 待实现（评估工具）

- `evaluate_accuracy.py --upload-batch`（批次 L1/L2 正式口径）
- `evaluate_accuracy.py --mlx-shadow-limit`（Ollama vs MLX 影子对照）

---

### R25 · MVP 目标锁定 + 分步门禁（2026-07-09）

#### 决策

| 项 | 决议 |
|----|------|
| 上线门槛 | L1 **90–93%**，L2 **82–88%** |
| 95% | 暂不作为本阶段目标；当前 MLX+LoRA **不能**单独到 95% |
| 系统状态 | 未完全上线 → 可全力测试 |
| 执行 | 分步门禁 E0–E7；每步达标后由 Agent 提示下一步 |

#### Track B 实测（正式口径）

| 合计 | L1 | L2 |
|------|-----|-----|
| 288 条（三批） | **82.64%** | 85.39%（分母 89） |

主因：近 7 天 L1 错误约 **75%** 为「业务类→非问题」；训练集非问题 **63.5%**。

#### Track A 进度

- holdout 已初始化：302 条；库内 v3 baseline **89.74%**
- MLX candidate 待用 `.venv` 跑完（系统 `python3` 缺 `mlx_lm`）

#### 当前步骤

**Step E0（环境就绪）** → 达标后进入 E1（holdout 三方 A/B）

计划全文：`docs/plans/mlx-accuracy-evolution-plan.md`

---

### R26 · Ollama 测试运营与优化待办（2026-07-15）

#### 当前运营状态

| 项 | 状态 |
|----|------|
| 日常分类 | **Ollama**（`./start_ollama.sh`），MLX LoRA 暂停灌库 |
| 投喂节奏 | **~200 条/天** |
| R1 规则评估 | ✅ fixable 14 条（主靶 9 + 回归 3 + 跨类 2），broken 0 |
| R2 patch 写库 | ✅ 14 条已写库（2026-07-13） |
| 全库 KPI（07-13） | L1 **86.03%**，L2 **78.25%**（+1253 条新复核后） |
| R3 验证批次 | ⏳ 进行中；**准确率数据待 07-16 更新** |

#### 测试中发现的问题（后续优化输入）

**O1 · 道路协助类误分为「服务类」**

- **现象**：爆胎、事故、保养协助等用户求助，常被标为「售后服务/服务类」。
- **业务口径**：属于**协助/咨询请求**，非产品故障、非服务流程投诉 → 应倾向 **非问题**（或单独「协助咨询」子类，L1 仍归非问题）。
- **优化方向**：规则捕获（爆胎/扎钉/道路救援/移动服务车上门/保养预约协助）+ 训练硬负样本；与 R2 已 patch 的 9 条主靶（咨询类→非问题）同类。

**O2 · VIN 前缀 → 车型先验**

- **规则**：`LJU` → 电车（Eletre 等）；`SCC` → Emira。
- **用途**：注入分类 prompt 或规则前置，减少 Emira/电车 L2 互串，提升 L1/L2 边界准确率。
- **优化方向**：入库/分类前解析 VIN（若字段已有），写入 `country`/车型 hint 供 `classify_text` 使用。

**O3 · APP 来源的用车分享/游记叙事 → 非问题**

- **现象**：车主日常分享、类似游记的叙事文章，易被标为体验需求或业务类。
- **优化方向**：来源=APP + 叙事/分享特征（无明确投诉/故障/需求动词）→ 非问题 guard；few-shot 补充样例。

**O4 · 「车端充电问题」L2 滥用 vs LFC**

- **现象**：「车端充电问题」被频繁乱用。
- **业务口径**：
  - **车端充电问题** 仅用于：充电功率低、预约充电后不充电等**车辆端充电能力/行为**问题。
  - **LFC 问题**：国内充电桩下线、蔚来/浩瀚等第三方充电桩供应商抱怨、闪充站不可用等**设施/站点**问题。
- **优化方向**：L2 映射 guard + 关键词分流（桩下线、蔚来、浩瀚、闪充站等 → LFC）；可参考 `patch_v3_l2_replay.py`。

#### 待更新（07-16）

- R3 Ollama 近 **4 天**投喂正式 L1/L2（`eval_batch_accuracy.py --since-days 4`）
- 全库 `evaluate_accuracy.py` 对比

---

## 四、新增 / 关键 API 一览

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/opinion_detail` | 复核列表按需拉全文 |
| GET | `/api/rule_conflict_keywords` | 读取冲突词库 |
| POST | `/api/rule_conflict_keywords/reload` | 热加载词库 |
| POST | `/api/rule_conflict_keywords/update` | 更新词库 |
| GET | `/api/classification/route_metrics` | 分类路由指标 |
| POST | `/api/batch_classify` | 异步批量分类（返回 job_id） |
| GET | `/api/batch_classify/status/{job_id}` | 分类任务进度 |
| GET | `/api/health`、`/health` | 探活 |
| GET | `/api/health/detail` | 详细健康（WAL / integrity / stale_reflow / stats） |
| GET | `/api/reflow_failures` | 回流失败记录 |
| POST | `/api/confirm_review_batch` | 整批确认 + 即时写年度 CSV |
| GET | `/api/confirm_review_batch/preview` | 整批确认前预览（未就绪行 409） |
| GET | `/api/get_opinion_summary` | AI 汇报文案（四维度：volume/trends/quotes/ppt） |
| POST | `/api/confirm_and_write_csv` | 确认所选并刷新年度 CSV |
| POST | `/api/batch_status` | 批次完成度与 L1 准确率 |
| GET | `/api/dashboard/kpi` | 看板 KPI 快照 |
| GET | `/api/dashboard/anomalies` | L2 异常检测 |
| GET | `/api/dashboard/trend` | 近 N 日 L1 趋势 |
| GET | `/api/report/issue_status` | 按 L2 问题处理状态 |
| GET | `/api/report/weekly_reports` | 周报列表 |
| GET | `/api/report/weekly_report` | 指定周周报 |
| POST | `/api/report/generate_weekly` | 生成本周周报 |

---

## 五、部署文件清单（发版核对）

以下文件为近期迭代**新增或强依赖**，漏传会导致 `ImportError` 或功能缺失：

```
src/backend/v3_materialized.py
src/backend/rule_conflict_detector.py
src/backend/classify_metrics_logger.py
src/backend/reflow_service.py
label_project/rule_conflict_keywords_v1.json
```

同时确保更新：

```
src/backend/main.py
src/backend/voc_classifier_service.py
src/backend/qwen_ollama.py
src/backend/report_aggregator.py
label_project/offline_validate.py
src/frontend/src/components/OpinionReview.vue
src/frontend/src/views/DataReport.vue
src/frontend/dist/（npm run build 后）
app_launcher.py
code_deploy/update.zip + update.zip.md5
```

**需单独拷贝（不在 update.zip 内）：**

```
performance_evaluation/check_reflow_health.py
performance_evaluation/export_finetune_from_db.py
performance_evaluation/ab_compare_models.py
scripts/balance_finetune_data.py
scripts/train_lora_14b.sh
scripts/verify_lora.sh
scripts/setup_server_venv.sh
scripts/ensure_mlx_deps.sh
scripts/find_python_for_venv.sh
scripts/resolve_venv_python.sh
requirements.txt
requirements-mlx.txt
app_launcher.py
start_ollama.sh
start_mlx.sh
```

**服务器手动排查：**

```bash
cd src/backend
python3 main.py
# 若失败，根据 traceback 补依赖或修权限
```

---

## 六、识别率与路由指标（2026-06-18 快照）

### 6.1 正式评估基线（生产库 Week 0，2026-06-04）

来源：`performance_evaluation/state/evolution_baseline.json`（`evaluate_accuracy.py` 在 M4-B r4 patch 后锁定）

| 指标 | 数值 | 样本量 |
|------|------|--------|
| **一级（L1）准确率** | **86.98%** | 2004 / 2304 |
| **二级（L2）准确率** | **76.90%** | 516 / 671（业务三类且 L1 一致） |
| 已复核累计 | 2362 条 | `review_status=1` |
| 规则版本 | v12_m4b_r4 | — |
| M4-B fixable 池 | regression=0, other_l1=0, primary=0 | 规则轨见底 |

**进化闭环 MVP 目标**（`evolution_loop_mvp_plan_20260604.md`）：

| 阶段 | L1 目标 | L2 目标 |
|------|---------|---------|
| 第 1 月末 | ≥ 88% | — |
| 第 2 月末 | ≥ 90% | 微调首包 ≥ 4000 条 |
| 第 3 月末 | 90–93% | 82–88% |
| 长期 KPI | **≥ 95%** | **≥ 90%** |

微调触发条件（尚未满足）：新增复核 ≥ 3000 条 **且** L1 不一致 ≥ 800 条 **且** 连续 2 周 fixable < 5。

### 6.2 LoRA 与 MLX 推理（2026-07-08）

| 项 | 说明 |
|----|------|
| 默认推理（Ollama） | `./start_ollama.sh` → `qwen2.5:14b-instruct-q4_K_M` |
| MLX+LoRA 推理 | `./start_mlx.sh` → `VOC_USE_MLX=1` + `~/lora_adapter_14b_v2` |
| Python 版本 | **3.11–3.13**（勿用 3.14 建 venv） |
| MLX 依赖 | `requirements-mlx.txt`；`transformers<5.13` |
| 环境初始化 | `bash scripts/setup_server_venv.sh` |
| 训练数据均衡 | `scripts/balance_finetune_data.py` |
| 部署 gate | `verify_lora.sh` holdout Δ ≥ 2pp |

### 6.3 历史优化对比（词库/规则迭代，2026-02）

来源：`Project Documentation/优化文档/大模型分类准确率对比数据表.md`

| 指标 | 优化前 | 优化后 | Δ |
|------|--------|--------|---|
| 总体分类准确率 | 65.2% | 78.5% | +13.3pp |
| 关键词匹配率 | 45.2% | 68.7% | +23.5pp |
| 词库规模 | 100 | 287 | +187 |

按 L1 类型（优化后）：质量问题 76.8%、营销服务 81.2%、体验需求 74.3%、咨询 84.6%、非问题 82.1%。

### 6.4 三级标签映射验证（2026-03）

来源：`label_project/validation_report.md`

| 指标 | 数值 |
|------|------|
| 抽样量 | 100 / 645 |
| L3 映射匹配率 | **97.00%**（97/100） |
| 不匹配原因 | 3 条「三级标签不在映射列表中」 |

### 6.5 开发机当前库（2026-06-18 实测）

来源：`evaluate_accuracy.py` 对 `src/backend/opinion_review.db` 只读评估

| 指标 | 数值 | 说明 |
|------|------|------|
| 已复核条数 | 34 | 测试库，非生产快照 |
| L1 可评估条数 | 0 | 无 `review_l1` 或未填模型侧 L1 |
| L1 / L2 准确率 | N/A | 分母为 0，**不可与 Week 0 基线对比** |

> 识别率请以**服务器生产库**运行 `python3 performance_evaluation/evaluate_accuracy.py --db <生产库路径>` 为准。

### 6.6 分类路由指标（classify_route_metrics.jsonl）

近期压测与生产路由摘要（2026-06-16 ~ 06-18）：

| 场景 | 条数 | 耗时 | gold_hit | conflict | llm_arbitrated | 备注 |
|------|------|------|----------|----------|----------------|------|
| 规则分类（小批） | 12 | ~0.01s | 0 | 0 | 0 | `rule_ok=12` |
| 14B 所选（2 条） | 2 | ~0.9s | 0 | 0 | 2 | 低置信走 LLM |
| **压测 300 条 14B** | 300 | **~1.2–1.3s** | 0 | 0 | 300 | `test_classify_stress` mock；无 lock |
| 压测 60 条 14B | 60 | ~0.7s | 0 | 0 | 60 | 增量 commit 验证 |

R2 阶段曾记录全量实测约 **50%**（M3 Max 硬件、14B 不宜默认全量）；经 R3–R7 金标/冲突/路由优化 + M4-B patch 后，Week 0 基线升至 **L1 86.98%**。

### 6.6 识别率评估命令

```bash
# 生产/服务器库（只读）
python3 performance_evaluation/evaluate_accuracy.py --db path/to/opinion_review.db

# 可选：14B 影子评估（最近 N 条，不写库）
python3 performance_evaluation/evaluate_accuracy.py --qwen-shadow-limit 50

# 分类路由日志
curl -s 'http://localhost:8000/api/classification/route_metrics?limit=20' | python3 -m json.tool

# 微调样本导出（服务器有复核数据后）
python3 performance_evaluation/export_finetune_from_db.py
python3 performance_evaluation/ab_compare_models.py --baseline
```

报告输出：`performance_evaluation/reports/YYYYMMDD_HHMM_report.txt`  
对比基准：`performance_evaluation/state/last_eval.json`

### 6.7 2026-06 新增测试覆盖（识别率相关保障）

| 测试文件 | 条数 | 覆盖能力 |
|----------|------|----------|
| `test_classify_stress.py` | 2 | 300 行 14B 无 database is locked |
| `test_confirm_batch_archive.py` | 8 | 整批归档、VIN/车型、排序 |
| `test_opinion_summary.py` | 5 | 汇报 volume/环比/quotes |
| `test_reflow_service.py` + resilience | 14+ | 回流正确性 |
| `test_concurrent_db.py` | 3 | WAL 并发 |
| `test_taxonomy_fallback.py` | 4 | L2 API 后备 |
| `test_export_finetune.py` + `test_ab_compare.py` | 6 | 微调导出与 A/B |
| **合计（本节相关）** | **45 passed** | 2026-06-18 实测 |

---

## 七、修订历史

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-05-25 | v1.0 | 首版：汇总 R1–R12 性能、准确率、P1–P3、部署启动修复 |
| 2026-05-25 | v1.1 | 新增 R13：F1 列表参数顺序、F4 暂存绑定、F5 测试断言 |
| 2026-06-18 | v2.0 | 新增 R14–R18：六任务、盲区 1–4、14B 稳定性、整批归档、汇报四维度、年度 CSV；新增「识别率与路由指标」专章 |

---

## 八、后续记录模板（复制使用）

```markdown
### Rxx · 标题（YYYY-MM-DD）

#### 问题
（用户现象或指标）

#### 修复
（条目列表）

#### 主要文件
- path/to/file

#### 验证要点
- [ ] ...

#### 未改动（禁改区确认）
- [ ] LabelMatcher 规则
- [ ] …
```
