# VOC_V1.5 · Design

> **最后更新**：2026-06-04  
> **规则版本**：`v12_m4b_r4`（缓存键 `classify_v12_m4b_r4`）  
> **设计原则**：14B 出标 + 可离线重放的确定性规则链；评估与写库分离；patch 幂等

---

## 1. 系统架构

```
┌─────────────┐     ┌──────────────────────────────────────────┐
│ Vue 前端     │────▶│ FastAPI (main.py)                        │
│ OpinionReview│     │  upload / save_review / 报表 / 仪表盘      │
└─────────────┘     └──────────────────┬───────────────────────┘
                                       │
                    ┌──────────────────▼───────────────────────┐
                    │ voc_classifier_service.py                  │
                    │  LabelMatcher → enforce guard → v3 写库   │
                    └──────────────────┬───────────────────────┘
                                       │
         ┌─────────────────────────────┼─────────────────────────────┐
         ▼                             ▼                             ▼
┌─────────────────┐          ┌─────────────────┐          ┌─────────────────┐
│ label_project/   │          │ qwen_ollama.py   │          │ opinion_review   │
│ LabelMatcher     │          │ 14B + 规则链      │          │ .db (SQLite)     │
│ 金标/关键词/LLM  │          │ Ollama 本地推理   │          │                  │
└─────────────────┘          └─────────────────┘          └─────────────────┘
                                       │
                    ┌──────────────────▼───────────────────────┐
                    │ performance_evaluation/（只读评估 + patch） │
                    └──────────────────────────────────────────┘
```

### 1.1 环境

| 项 | 值 |
|----|-----|
| 开发机 | `/Users/yuchao/Documents/AI Agent/VOC_V1.5` |
| 服务器 | `/Users/chaoyu/Desktop/VOC_AI agent/VOC_V1.5` |
| LLM | Ollama `qwen2.5:14b-instruct-q4_K_M` |
| DB | `src/backend/opinion_review.db` |

---

## 2. 分类流水线

### 2.1 在线路径（batch_classify / 单条 classify）

```
原文 text
  → LabelMatcher.match()
       ├─ gold_review（MD5 命中已复核）→ confidence 0.99，跳过 LLM/联防
       ├─ 关键词 / clean_l1
       └─ LLM（14B）→ JSON {l1,l2,...}
  → enforce_l2_whitelist_and_guard()
  → apply_classification_post_rules()   # 与离线重放同一函数
  → detect_rule_conflict()（可选仲裁）
  → 写 v3_label_meta + v3_l1/l2 物化列
  → review_status：低置信 → 2，已复核 → 保持 1
```

### 2.2 规则链顺序（v12）

文件：`src/backend/qwen_ollama.py` → `apply_classification_post_rules()`

| 序号 | 函数 | 作用 |
|------|------|------|
| 1 | `apply_non_issue_guardrail` | 带诉求/异常的「非问题」→ 拉回业务类 |
| 2 | `consultation_praise_misclass` | L2=咨询与表扬 + 硬负面 → 重定位 |
| 3 | `apply_charging_domain_guard` | LFC/家充域 → 产品质量类 |
| 4 | `apply_service_quality_guard` | 试驾差评信号 → 服务类 |
| 5 | `apply_l1_category_rebalance` | 服务↔产品↔体验跨类 tilt（M4-B2） |
| 6 | `apply_positive_consult_capture` | 好评/咨询/弱正向 → 非问题（L2 空） |
| — | `strip_non_issue_l2` | 非问题清空 L2 |

**回归保护**：`charging_guard` 已触发且为 LFC 咨询时，不再 capture 回非问题。

### 2.3 v12 M4-B 扩展要点

| 组件 | 说明 |
|------|------|
| `_KZ_V12_*_PULLBACK` | 回归池 guard 拉回（服务/产品/体验诉求） |
| `_v12_false_capture_pullback_needed` | 回访差评、混合 praise、咨询误捕获 |
| `_KZ_V12_NEUTRAL_QUERY_PULLBACK` | 咨询+投诉/交付不满（窄口径，避免纯咨询误拉） |
| `_v12_benign_neutral_query_only` | 纯咨询保护，不走 false_capture |
| `apply_l1_category_rebalance` | 跨类 92 主战场 tilt |
| `_pick_non_issue_guard_l1` | guard 拉回时 L1 选择（产品故障优先于体验） |
| `_pick_service_l2_from_text` | 回访类型 → 销售/交付/售后 L2 |

缓存键：`classify_v12_m4b_r4`（改规则须 bump 键，避免旧 inference cache）。

---

## 3. 数据模型（核心）

### 3.1 `opinion` 表（摘录）

| 字段 | 用途 |
|------|------|
| `original_text` | 舆情原文 |
| `review_status` | 0 待复核 / 1 已复核 / 2 低置信待复核 |
| `review_l1`, `review_l2` | **人工金标** |
| `v3_label_meta` | JSON：模型 l1/l2、confidence、match_type、flags |
| `v3_l1`, `v3_l2`, … | 物化列（报表/评估优先读） |
| `model_class`, `model_keyword` |  legacy 导入字段 |
| `reviewed_at`, `create_time` | 复核/创建时间（周切片 `--since 7d`） |

### 3.2 标签规范

`label_project/taxonomy_normalize.py`：

- `canonicalize_l1_label()` → 四类规范名
- `L2_EVAL_L1` = {产品质量类, 服务类, 体验需求类}
- `NON_ISSUE_L1` = 非问题
- `strip_non_issue_l2()` — 非问题不写 L2

### 3.3 v3_label_meta 示例字段

```json
{
  "l1": "服务类",
  "l2": "交付问题",
  "confidence": 0.85,
  "match_type": "llm",
  "post_rules_flags": { "non_issue_guard": true },
  "patch_p1_regression_at": "2026-06-04T16:09:48"
}
```

---

## 4. 评估体系设计

### 4.1 两套评估分工

| 脚本 | 输入 | 速度 | 何时用 |
|------|------|------|--------|
| `eval_p1_subset.py --compare` | 库内 v3 + **离线重放规则** | 秒级 | 改 `qwen_ollama.py` 后迭代 |
| `evaluate_accuracy.py` | 库内 **已写回 v3** | 秒级 | patch / reclassify 后正式 KPI |

> 只部署新规则、不重写 v3 → `evaluate_accuracy` 数字不变是 **预期**。

### 4.2 P1 错误池定义

| 池 | 条件 |
|----|------|
| 主靶 | 人工=非问题，模型∈业务三类 |
| 回归 | 人工∈业务三类，模型=非问题 |
| 其他 | 其余 L1 不一致 |

### 4.3 进化闭环（MVP）

```
evaluate_accuracy → export_l1_errors --since 7d
    → eval_p1_subset (fixable 估算)
    → weekly_evolution_report
    → 人工决策 → patch / 微调 / 暂缓
    → 门禁 → 写库 → 再 evaluate
```

**状态文件**：

| 路径 | 用途 |
|------|------|
| `state/evolution_baseline.json` | Week0 KPI 基线 |
| `state/evolution_weekly_last.json` | 上周快照（Δpp） |
| `state/last_eval.json` | evaluate 上次对比 |
| `state/finetune_trigger.json` | 微调基准（待建） |

---

## 5. Patch 写库设计（幂等）

| 脚本 | 方向 | 说明 |
|------|------|------|
| `patch_p1_regression_v3.py` | 非问题 → 业务 | 回归 fixable |
| `patch_p1_other_l1_v3.py` | 跨类 L1 | tilt fixable |
| `patch_p1_fixable_v3.py` | 业务 → 非问题 | 主靶 fixable |
| `patch_v3_l2_replay.py` | L1 已对，修 L2 | `_pick_l2_after_remap` |

**共用**：`patch_csv_utils.resolve_patch_csv()` — shell glob 多文件时取 **mtime 最新**。

**禁止**：`apply_post_rules_to_v3.py --write` 全库（非幂等，曾致 L1 回退）。

### 5.1 写库门禁

```
主靶 broken ≤ 2
回归 broken ≤ 8
Holdout L1 不回退（微调路径）
禁止全库 post_rules write
```

---

## 6. 「学习」机制（设计现状）

| 机制 | 类型 | 说明 |
|------|------|------|
| `gold_review` | 精确记忆 | 原文 MD5 命中 → 直接用 review 标签 |
| `historical_examples` | 弱 few-shot | prompt 注入最多 4 条相似已复核样例 |
| `refresh_matcher_gold_cache` | 运行时刷新 | 复核后刷新内存金标 |
| 规则 patch | 人工驱动 | eval → 改 regex → CSV patch |
| 14B 微调 | **未接入生产** | 脚本在 `scripts/`，MVP M2 建池 |

**不是**：自动规则生成、自动 fine-tune、自动全库 rewrite。

---

## 7. 部署设计

### 7.1 code_deploy（仅 `src/`）

```bash
./code_deploy/package_code.sh   # 开发机：npm build + zip
./code_deploy/update_server.sh    # 服务器：覆盖 src/
```

**不包含**：`label_project/`、`performance_evaluation/`、`*.db`、`data/`

### 7.2 需单独 rsync 的目录

- `performance_evaluation/` — 评估、patch、周报告
- `label_project/` — 白名单 JSON、taxonomy
- `AI_CONTEXT.md`、`Product.md`、`Design.md`、`Agent.md`

### 7.3 Ollama

```bash
export VOC_QWEN_MODEL=qwen2.5:14b-instruct-q4_K_M
export OLLAMA_HOST=http://127.0.0.1:11434
```

全量 reclassify ~2362 条 ≈ **155 分钟**。

---

## 8. 关键目录

```
VOC_V1.5/
├── src/backend/
│   ├── main.py                 # FastAPI
│   ├── qwen_ollama.py          # ★ 14B + 规则链
│   ├── voc_classifier_service.py
│   └── classify_metrics_logger.py
├── src/frontend/               # Vue 复核 UI
├── label_project/
│   ├── offline_validate.py     # LabelMatcher
│   └── taxonomy_normalize.py
├── performance_evaluation/
│   ├── evaluate_accuracy.py
│   ├── eval_p1_subset.py
│   ├── export_l1_errors.py     # --since 7d
│   ├── weekly_evolution_report.py
│   ├── patch_p1_*.py / patch_v3_l2_replay.py
│   ├── patch_csv_utils.py / time_utils.py
│   └── state/
└── tests/unit/test_v9_*.py test_v12_m4b.py
```

---

## 9. 测试策略

| 层级 | 位置 | 覆盖 |
|------|------|------|
| 规则单元测试 | `tests/unit/test_v9_*`, `test_v12_m4b.py` | 捕获/guard/tilt 边界 |
| 离线重放 | `eval_p1_subset.py --compare` | 全库 P1 子集 fixable/broken |
| 正式 KPI | `evaluate_accuracy.py` | 写库后验收 |
| 影子 14B | `evaluate_accuracy.py --qwen-shadow-limit N` | 模型变更对比（慢） |

---

## 10. 后续设计项（MVP）

| 项 | 设计要点 |
|----|----------|
| `export_finetune_from_db.py` | 复核 JSONL；分层采样；排除 holdout 10% |
| `ab_compare_models.py` | 固定 holdout；L1/L2 Δpp 报告 |
| 模型灰度 | `confidence < 0.52` 或新 `upload_batch` 走候选模型 |
| L2 映射表 | 充电/LFC/AD4 专项 remap（当前 l2 replay 0 可写） |

---

## 11. 相关文档

- [Product.md](./Product.md) — KPI、路线图、运营 SOP  
- [Agent.md](./Agent.md) — Agent 协作与命令  
- [AI_CONTEXT.md](./AI_CONTEXT.md) — 精简交接
