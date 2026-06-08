# VOC_V1.5 · Agent Guide

> **最后更新**：2026-06-04  
> **读者**：Cursor Agent / 自动化 coding agent  
> **语言**：与用户沟通使用 **简体中文**  
> **精简上下文**：可先读 [AI_CONTEXT.md](./AI_CONTEXT.md)；细节见 [Product.md](./Product.md)、[Design.md](./Design.md)

---

## 1. 项目一句话

车载舆情 **L1/L2 分类**：本地 Qwen2.5-14B +  heavy regex 规则后处理 + 人工复核；当前 L1 **86.98%**，规则轨 fixable=**0**，处于 **进化闭环 MVP 第 1 月**。

---

## 2. Agent 当前目标优先级

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | 进化周报告 `weekly_evolution_report.py` | ✅ 已完成 |
| P0 | Week0 基线 `state/evolution_baseline.json` | ✅ 已锁定 |
| P1 | `export_l1_errors.py --since 7d` | ✅ 已完成 |
| P0（下一步） | `export_finetune_from_db.py` | 待做（MVP M2） |
| P1 | `ab_compare_models.py` | 待做 |
| 暂停 | 继续扩 v12 praise/neutral_query 规则 | ROI 低，易回退 |
| 禁止 | 全库 `apply_post_rules_to_v3.py --write` | 非幂等 |

---

## 3. 硬性约束（必须遵守）

### 3.1 写库

```
✅ 允许：patch_p1_*_v3.py --csv ... --write（CSV 幂等）
✅ 允许：patch_v3_l2_replay.py --write
✅ 允许：batch_classify / reclassify（只更新 v3，不覆盖 review_*）

❌ 禁止：apply_post_rules_to_v3.py --write 全库
❌ 禁止：未跑 eval 门禁直接 patch
❌ 禁止：未用户明确要求时 git commit / push
```

### 3.2 评估

```
改规则后快验 → eval_p1_subset.py --compare
写库后 KPI   → evaluate_accuracy.py
两者不可混用解释 KPI 变化
```

### 3.3 部署

```
改 src/backend 或前端 → code_deploy 打包
改 performance_evaluation / label_project → rsync 单独同步，不进 update.zip
```

### 3.4 Patch CSV

```bash
# shell 会展开 glob；脚本取最新文件
python3 performance_evaluation/patch_p1_regression_v3.py \
  --csv performance_evaluation/exports/p1_regression_fixable_*.csv

# 或引号阻止展开（旧版也适用）
--csv 'performance_evaluation/exports/p1_regression_fixable_*.csv'
```

---

## 4. 门禁（patch / 换规则前）

| 检查项 | 阈值 |
|--------|------|
| 主靶 broken | ≤ **2** |
| 回归 broken | ≤ **8** |
| 离线 fixable vs 写库条数 | 应一致 |
| 全库 post_rules write | **禁止** |

---

## 5. 标准工作流

### 5.1 改 L1 规则

```
1. 改 src/backend/qwen_ollama.py
2. bump classify 缓存键（如 classify_v12_m4b_r5）
3. 加/改 tests/unit/test_v12_m4b.py
4. python3 performance_evaluation/eval_p1_subset.py --compare \
     --export-regression-fixable auto --export-other-fixable auto
5. python3 performance_evaluation/analyze_m4b.py
6. dry-run patch → --write → evaluate_accuracy.py
```

### 5.2 每周五（进化闭环）

```bash
python3 performance_evaluation/evaluate_accuracy.py
python3 performance_evaluation/export_l1_errors.py --since 7d
python3 performance_evaluation/eval_p1_subset.py --compare \
  --export-regression-fixable auto --export-other-fixable auto
python3 performance_evaluation/weekly_evolution_report.py
```

### 5.3 新 Agent 会话冷启动

```bash
grep '_cache_key("classify_' src/backend/qwen_ollama.py | tail -3
python3 performance_evaluation/evaluate_accuracy.py --db src/backend/opinion_review.db
python3 performance_evaluation/eval_p1_subset.py --compare
python3 -m pytest tests/unit/test_v12_m4b.py -q
```

---

## 6. 关键文件地图

| 路径 | Agent 何时读/改 |
|------|-----------------|
| `src/backend/qwen_ollama.py` | **规则主战场**；改前读 `apply_classification_post_rules` 链 |
| `label_project/taxonomy_normalize.py` | KPI 口径、L2 范围 |
| `performance_evaluation/eval_p1_subset.py` | P1 池、replay、export fixable |
| `performance_evaluation/evaluate_accuracy.py` | 全库 KPI |
| `performance_evaluation/weekly_evolution_report.py` | 周报告 |
| `performance_evaluation/export_l1_errors.py` | L1 错误切片 `--since 7d` |
| `performance_evaluation/patch_p1_*.py` | 幂等写库 |
| `performance_evaluation/patch_csv_utils.py` | CSV glob 解析 |
| `performance_evaluation/time_utils.py` | 时间窗口 |
| `performance_evaluation/state/evolution_baseline.json` | Week0 |
| `performance_evaluation/state/m4b_r4_delivery_20260604.json` | 最近交付 KPI |
| `tests/unit/test_v12_m4b.py` | 规则回归测试 |

### 6.1 CodeGraph

跨文件影响分析优先用 MCP `codegraph`（`get_callers`、`analyze_impact`），少靠 grep 猜。

---

## 7. 当前 KPI 与错误池（Agent 决策用）

| 指标 | 值 |
|------|-----|
| L1 | **86.98%**（2004/2304） |
| L2 | **76.90%**（516/671） |
| 规则 fixable | **0**（三轨） |
| 回归池 | **164** |
| 主靶池 | **57** |
| 跨类池 | **79** |

**回归未拉回 Top 诊断**：`eligible:neutral_query`(36)、`eligible:praise`(21)、`eligible:soft_hope_suggest`(16)

**Agent 建议**：fixable=0 时勿再大规模加 regex；优先 MVP 微调池或 L2 专项。

---

## 8. 常见误区

| 误区 | 事实 |
|------|------|
| 部署新规则后 evaluate 应涨 | 仅当 v3 已 patch/reclassify 才涨 |
| 复核会自动提升模型 | 仅 gold_review 精确命中 + 弱 few-shot |
| glob CSV 报错 unrecognized arguments | 需 `patch_csv_utils` 或引号包裹 glob |
| 开发机 DB 空 | 权威 KPI 在服务器；开发机做规则/单测 |
| L2 patch 能救 KPI | r4 后 l2 replay **0** 可写；L2 错多在映射语义 |

---

## 9. 与用户协作约定

- 用户说 **「L1」** → 攻一级准确率（回归/跨类/规则），非 L2 专项  
- 用户说 **「开工 P0/P1」** → 指 MVP 计划中的具体脚本项  
- 用户说 **「上线」** → 区分 code_deploy（src）与 performance_evaluation 单独同步  
- 大改前先 **eval 再 patch**；给出 offline 预估与 KPI 对账  
- 回复简洁、中文；代码引用用 `startLine:endLine:path` 格式  

---

## 10. 状态文件索引

| 文件 | 内容 |
|------|------|
| `state/evolution_baseline.json` | Week0：86.98% / 76.90% |
| `state/evolution_weekly_last.json` | 上周 KPI 快照 |
| `state/last_eval.json` | evaluate 对比基准 |
| `state/m4b_r4_delivery_20260604.json` | r4 patch +14 交付报告 |
| `state/evolution_loop_mvp_plan_20260604.md` | 3 月 MVP 全文 |

---

## 11. 相关文档

- [Product.md](./Product.md) — 产品 KPI、路线图、SOP  
- [Design.md](./Design.md) — 架构、规则链、数据模型  
- [AI_CONTEXT.md](./AI_CONTEXT.md) — 一页纸交接  
- [performance_evaluation/README.md](./performance_evaluation/README.md) — 评估脚本说明
