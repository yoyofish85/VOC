# VOC V1.5 开发记录

> 本文档记录 V1.5 迭代中的**问题背景、改动内容、涉及文件与验证要点**，便于每次发版前核对「这次到底修了什么、没动什么」。  
> 架构级 V3 标签体系说明见 [`VOC_V1.5_优化记录_v3.0.md`](./VOC_V1.5_优化记录_v3.0.md)。

**最后更新**：2026-05-25  
**适用分支**：VOC_V1.5 当前主开发线

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

---

## 五、部署文件清单（发版核对）

以下文件为近期迭代**新增或强依赖**，漏传会导致 `ImportError` 或功能缺失：

```
src/backend/v3_materialized.py
src/backend/rule_conflict_detector.py
src/backend/classify_metrics_logger.py
label_project/rule_conflict_keywords_v1.json
```

同时确保更新：

```
src/backend/main.py
src/backend/voc_classifier_service.py
src/backend/report_aggregator.py
src/backend/qwen_ollama.py
label_project/offline_validate.py
src/frontend/src/components/OpinionReview.vue
src/frontend/src/views/DataReport.vue
app_launcher.py
```

**服务器手动排查：**

```bash
cd src/backend
python3 main.py
# 若失败，根据 traceback 补依赖或修权限
```

---

## 六、修订历史

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-05-25 | v1.0 | 首版：汇总 R1–R12 性能、准确率、P1–P3、部署启动修复 |
| 2026-05-25 | v1.1 | 新增 R13：F1 列表参数顺序、F4 暂存绑定、F5 测试断言 |

---

## 七、后续记录模板（复制使用）

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
