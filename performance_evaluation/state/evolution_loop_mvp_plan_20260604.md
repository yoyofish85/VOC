# VOC 最小进化闭环 MVP — 3 个月落地计划

> **状态**：M4-B r4 已交付；**Week 0 基线已锁定**，待执行 MVP 第 1 月  
> **记录日期**：2026-06-04  
> **Week 0 基线**：L1 **86.98%**（2004/2304），L2 **76.90%**（516/671）  
> **前置**：v12 r4 写库 +14 L1；`analyze_m4b` 三轨 fixable=0（规则轨见底）  
> **长期 KPI**：L1 ≥ 95%，L2 ≥ 90%（业务三类且 L1 一致）  
> **运营假设**：上线后约 **200 条/天** 入库 + 人工复核

---

## 1. 目标与边界

### 3 个月 MVP 要达成

| 维度 | MVP 内承诺 | MVP 后（4–6 月） |
|------|------------|------------------|
| L1 | **90–93%** | 冲 **95%** |
| L2 | **82–88%** | 冲 **90%** |
| 流程 | 周报告 + 微调样本池 + 门禁化 patch + 可选模型灰度 | 第 2 轮微调 + L2 专项 |

### 设计原则

- **自动发现、人工决策**：系统推荐动作，不自动改规则/写库/换模型。
- **禁止**全库 `apply_post_rules_to_v3.py --write`（非幂等，曾导致 L1 回退）。
- **只用** CSV 幂等 patch（`patch_p1_*`、`patch_v3_l2_replay`）。

### 当前「自我进化」现状（记录用）

| 已有 | 未有 |
|------|------|
| `gold_review` 金标 hash 命中 | 自动从复核错误挖掘规则 |
| `historical_examples`（4 条 few-shot） | 14B 定期微调流水线 |
| `refresh_matcher_gold_cache` | Prompt 自动优化 |
| 人工规则迭代（M3→M4-B r4） | 错误聚类 → 规则 PR 自动化 |

纯运营（不改规则/模型）预期长期平台：**L1 ~88–89%，L2 ~80–85%**，难以自然到 95/90。

---

## 2. 闭环架构

```
每日入库 ~200条 → 14B + 规则分类 → 人工复核 → opinion DB 金标
    → 周度进化报告 → 决策（规则可修 / 微调样本 / 暂缓）
    → eval 重放 + patch 或 微调 A/B → 门禁验收 → 写库/换模型
    → evaluate_accuracy KPI → 下一周
```

---

## 3. 分月交付

### 第 1 月（Week 1–4）：可观测 + 周节奏

| # | 交付 | 类型 | 验收 |
|---|------|------|------|
| 1.1 | `weekly_evolution_report.py` | **新建 P0** ✅ | 一条命令出周报告 |
| 1.2 | `evolution_baseline.json`（扩展 last_eval） | 新建 | 可追踪周增量 |
| 1.3 | `export_l1_errors.py --since 7d` | **新建 P1** ✅ | 仅本周新错 |
| 1.4 | 固定周会 SOP（周五 30min） | 流程 | 连续 4 期报告 |

**第 1 月末 KPI**：L1 **≥ 88%**；新增复核 **≥ 5000 条**；周报告 **4 期**。

**每周五命令（服务器）**：

```bash
python3 performance_evaluation/evaluate_accuracy.py
python3 performance_evaluation/export_l1_errors.py --since 7d
python3 performance_evaluation/eval_p1_subset.py --compare \
  --export-regression-fixable auto --export-other-fixable auto
python3 performance_evaluation/weekly_evolution_report.py
```

**周报告必含**：

1. L1 / L2 + 较上周 Δpp  
2. 本周新增已复核条数  
3. 本周新增 L1 错误 Top5（模型→人工）  
4. 三池：回归 / 主靶 / 跨类  
5. 规则可修估算（regression_fixable + other_fixable）  
6. 微调触发进度（距阈值 Δreview）  
7. 建议动作各 1 条（规则 / 微调 / 暂缓）

---

### 第 2 月（Week 5–8）：微调样本池 + 离线 A/B

| # | 交付 | 验收 |
|---|------|------|
| 2.1 | `export_finetune_from_db.py`（**P0**） | SQLite → Ollama JSONL |
| 2.2 | `data/holdout_opinion_ids.json`（10% 固定 seed） | holdout 不变 |
| 2.3 | `ab_compare_models.py`（**P1**） | holdout 上 L1/L2 Δpp |
| 2.4 | `state/finetune_trigger.json` | 触发条件可查询 |

**微调触发阈值（初值）**：

| 条件 | 阈值 |
|------|------|
| 自上次微调新增复核 | **≥ 3000 条** |
| 其中人机 L1 不一致 | **≥ 800 条** |
| 规则 ROI 见底 | 连续 **2 周** fixable **< 5** |
| Holdout 候选 vs 当前 | L1 **≥ +2pp** 才换模型 |
| 回归 broken | **≤ 8** |

**第 2 月末 KPI**：L1 **≥ 90%**；微调首包 **≥ 4000 条**；完成 **1 次** holdout A/B（可不换模型）。

---

### 第 3 月（Week 9–12）：灰度 + 真闭环

| # | 交付 |
|---|------|
| 3.1 | 规则发布流水线：eval → dry-run → 门禁 → `--write` → 再 eval |
| 3.2 | 模型灰度：`upload_batch` 或 `confidence < 0.52` 先试新模型 |
| 3.3 | 金标刷新监控（`refresh_matcher_gold_cache`） |
| 3.4 | 12 周 evolution 曲线回顾 |

**第 3 月末 KPI**：L1 **90–93%**；L2 **82–88%**；12 周报告无间断；可选 1 次微调灰度。

---

## 4. 统一门禁（patch / 换模型前必过）

```
✅ 主靶 broken ≤ 2
✅ 回归 broken ≤ 8
✅ Holdout L1 不回退（Δ ≥ 0）
✅ Holdout L2 不回退 > 1pp
✅ 禁止 apply_post_rules_to_v3.py --write 全库
```

---

## 5. 每周运营 SOP

| 日 | 动作 | 耗时 |
|----|------|------|
| 周一 | 入库量、复核 backlog | 15min |
| 周三 | 抽 20 条低置信+人机不一致归因 | 1h |
| 周五 | 跑周报告、定 1 个主攻池 | 30min |
| 周五+ | eval 重放 → 改规则 → patch dry-run | 2–4h |
| 双周 | 检查微调触发条件 | 30min |

---

## 6. 待新建脚本（优先级）

| 优先级 | 脚本 | 说明 |
|--------|------|------|
| **P0** | `performance_evaluation/weekly_evolution_report.py` | 聚合 evaluate + export + eval_p1 |
| **P0** | `performance_evaluation/export_finetune_from_db.py` | 复核库 → JSONL，分层采样 |
| **P1** | `export_l1_errors.py --since 7d` | 本周新错切片 |
| **P1** | `performance_evaluation/ab_compare_models.py` | holdout 模型对比 |

**复用现有**：`evaluate_accuracy.py`、`eval_p1_subset.py`、`patch_p1_regression_v3.py`、`patch_p1_other_l1_v3.py`、`patch_v3_l2_replay.py`、`analyze_m4b.py`。

---

## 7. 里程碑时间线

```
Month 1  ── 看得见：周报告 + L1≥88%
Month 2  ── 攒得动：微调数据池 + A/B + L1≥90%
Month 3  ── 发得出去：门禁 patch + 模型灰度 + L1 90–93%
Month 4+ ── 冲 KPI：第 2 轮微调 + L2 映射 → 95/90
```

---

## 8. 人力与风险

| 项 | 估算 |
|----|------|
| 工程 | 0.3–0.5 人月/月 |
| 复核 | 1–2 人（200条/天） |
| 最大风险 | 全库非幂等写回 → 仅 CSV patch |
| 次风险 | 微调过拟合回访模板 → holdout + 分层采样 |

---

## 9. 与当前 M4-B 的衔接

- **M4-B r4 已关闭**（2026-06-04）：交付报告 `state/m4b_r4_delivery_20260604.json`；L1 **86.98%**；patch +14；规则 fixable=0。
- **Week 0 基线**：L1 86.98% / L2 76.90%（`evaluate_accuracy` 2026-06-04T16:10:49）。
- **下一步编码起点**：用户确认后从 **P0 `weekly_evolution_report.py`** 开工。

---

## 10. 相关文件

| 路径 | 用途 |
|------|------|
| `performance_evaluation/state/last_eval.json` | 评估 KPI 快照 |
| `performance_evaluation/state/m4b_r4_20260604.json` | 当前规则迭代状态 |
| `src/backend/qwen_ollama.py` | 规则链（`classify_v12_m4b_r4`） |
| `AI_CONTEXT.md` | 项目交接摘要 |
