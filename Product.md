# VOC_V1.5 · Product

> **最后更新**：2026-06-04  
> **产品阶段**：M4-B r4 已交付 → 进化闭环 MVP 第 1 月  
> **权威 KPI 快照**：L1 **86.98%**，L2 **76.90%**（服务器，2304 / 671 分母）

---

## 1. 产品定位

**VOC_V1.5** 是路特斯（Lotus）车载用户舆情（Voice of Customer）的 **自动分类 + 人工复核** 系统。

目标：将分散的用户反馈（APP、专属群、回访、工单 relay 等）稳定映射到统一的 **一级 / 二级标签体系**，支撑质量、服务、体验三类业务决策，并可通过 KPI 量化模型识别能力。

| 维度 | 说明 |
|------|------|
| 用户 | 舆情运营、质量/服务/体验分析、复核员 |
| 场景 | 批次入库 → 自动打标 → 低置信/冲突推人工 → 金标沉淀 → 报表与评估 |
| 部署 | 离线服务器（M3 Max + 本地 Ollama Qwen2.5-14B），无公网依赖 |

---

## 2. 标签体系与 KPI 口径

### 2.1 一级标签（四类）

| 一级 | 业务含义 |
|------|----------|
| 产品质量类 | 车辆硬件、软件、充电、智驾等产品故障/缺陷 |
| 服务类 | 销售、交付、售后、门店等服务问题 |
| 体验需求类 | 建议、希望、功能诉求（非明确故障） |
| 非问题 | 好评、纯咨询、品牌动态、协调 relay 等 |

### 2.2 KPI 定义（当前阶段）

| 指标 | 目标 | 当前（2026-06-04） | 统计口径 |
|------|------|---------------------|----------|
| **L1 准确率** | **≥ 95%**（长期） | **86.98%**（2004/2304） | 已复核且有模型 L1；人机均规范到四类后比较 |
| **L2 准确率** | **≥ 90%**（长期） | **76.90%**（516/671） | 仅 **业务三类** 且 **L1 一致** 的样本；有人工 L2 才计入 |
| 非问题 L2 | 不要求 | — | 非问题 **不计 L2 KPI**；复核页 L2 可选/清空 |

**MVP 3 个月承诺**（进化闭环）：L1 **90–93%**，L2 **82–88%**（非最终 95/90）。

### 2.3 距目标缺口

| 目标 | 还需（约） |
|------|------------|
| L1 90% | +70 条正确 |
| L1 95% | +185 条正确 |
| L2 90% | +88 条正确（分母 671） |

---

## 3. 核心用户旅程

```
上传批次 CSV/接口入库
    → 自动分类（金标 / 关键词 / 14B + 规则后处理）
    → review_status=0 待复核 | =2 低置信待复核
    → 复核员在 Vue 页确认或修正 review_l1 / review_l2
    → review_status=1 已复核（金标）
    → 报表 / 月度趋势 / 准确率评估
```

### 3.1 复核员

- 看待复核队列（低置信、冲突、仲裁优先）
- 修正 L1/L2；非问题可不填 L2
- 复核结果写入 `opinion.review_l1/l2`，**不覆盖**模型侧 `v3_label_meta`（除非运营 patch）

### 3.2 算法 / 运营

- 用 `evaluate_accuracy.py` 看全库 KPI
- 用 `eval_p1_subset.py` 秒级验证规则改动
- 用 CSV 幂等 patch 写回 v3（**禁止**全库非幂等重跑 post_rules）
- 每周五跑进化闭环脚本（见 §6）

---

## 4. 功能清单

### 4.1 已上线

| 模块 | 能力 |
|------|------|
| 数据入库 | 批次上传 → SQLite `opinion` |
| 自动分类 | LabelMatcher 金标 → 关键词 → Qwen2.5-14B |
| 规则后处理 | v12 `apply_classification_post_rules()`（守卫 + 正向捕获 + 跨类 tilt） |
| 人工复核 | Vue `OpinionReview.vue`；FastAPI `save_review` |
| 金标命中 | 相同原文 hash → `gold_review`，置信度 0.99 |
| 低置信路由 | `confidence < 0.52` 或 `needs_review` → `review_status=2` |
| 报表 | `report_aggregator.py`、数据汇报页 |
| 准确率评估 | `evaluate_accuracy.py`（只读） |
| P1 子集评估 | `eval_p1_subset.py`（离线规则重放，秒级） |
| L1 错误导出 | `export_l1_errors.py`（含 `--since 7d` 周切片） |
| 幂等 patch | `patch_p1_regression_v3.py`、`patch_p1_other_l1_v3.py`、`patch_v3_l2_replay.py` |
| 进化周报告 | `weekly_evolution_report.py`（P0 ✅） |
| 离线部署 | `code_deploy/` 打包 `src/` |

### 4.2 规划中（进化闭环 MVP）

| 模块 | 计划时间 | 说明 |
|------|----------|------|
| 微调样本池 | MVP 第 2 月 | `export_finetune_from_db.py` |
| Holdout A/B | MVP 第 2 月 | `ab_compare_models.py` |
| 模型灰度 | MVP 第 3 月 | 新模型先试低置信 / 新批次 |
| 规则自动挖掘 | 未规划 | 当前人工 + Agent 改规则 |
| **L3 定位与汇报主题体系** | 规划中 | 产品质量+服务类先定版约 214 个定位 L3，再唯一映射至约 30–45 个汇报主题；体验需求暂缓，L3 暂不进 KPI |
| **汇报洞察字段** | 规划中 | `intent` / `expected_action` / `severity` / `urgency` / `repeat_signal` → 故障生命周期 + 跨 L2 抱怨聚集 |
| **32B 模型灰度** | 规划中 | M5 Max 128GB 到位后，经 A/B + dry-run 门禁再决定换默认模型 |

以上三项详细方案见 `docs/plans/2026-09-12-l3-insight-hardware-roadmap.md`。

---

## 5. 里程碑与 KPI 时间线

| 阶段 | L1 | L2 | 关键交付 |
|------|-----|-----|----------|
| M0 基线 | 80.68% | — | 14B 全量重分后口径 |
| M3 | 83.20% | 75.00% | business_guard + 14 主靶 patch |
| M4-A | 84.11% | 75.54% | 回归 guard +21 |
| M4-B r1–r3 | 86.37% | 77.63% | v12 回归/回访/L2 映射 +52 |
| **M4-B r4** | **86.98%** | **76.90%** | neutral_query + 跨类 tilt +14 patch |
| MVP M1 目标 | ≥ 88% | — | 周报告 ×4、复核增量 |
| MVP M2 目标 | ≥ 90% | — | 微调首包 + A/B |
| MVP M3 目标 | 90–93% | 82–88% | 门禁 patch + 可选灰度 |
| 长期 | **95%** | **90%** | 规则 + 14B 微调多轮 |

**规则轨状态（r4 后）**：离线 fixable = **0**，纯规则迭代 ROI 见底；下一主路径为 **微调 + 运营复核积累**。

---

## 6. 当前错误结构（产品视角）

全库 L1 错误 **300 条**，构成：

| 池 | 数量 | 含义 | 产品策略 |
|----|------|------|----------|
| P1 回归 | 164 | 人工=业务，模型=非问题 | Top：neutral_query(36)、praise(21)；攻边界 ROI 低 |
| P1 主靶 | 57 | 人工=非问题，模型=业务 | hard_negative 为主，规则难修 |
| 其他跨类 | 79 | L1 三类之间误分 | r4 tilt 已修 13，剩余需模型/细规则 |

L2 Top 错误：`车端充电→LFC`、`故障-通用→故障告警`、`智能驾驶→AD4`（L1 对齐后映射偏差）。

---

## 7. 运营假设与预期

| 假设 | 数值 |
|------|------|
| 上线后入库 | ~**200 条/天** |
| 复核 | 尽量与入库同步 |
| 纯运营不改规则/模型 | L1 平台期 ~**88–89%**，难以自然到 95% |
| 200 条/天 + 规则/微调闭环 | 3 月 MVP L1 **90–93%**；95% 需第 4–6 月 + 微调 |

---

## 8. 每周运营 SOP（产品/运营）

| 日 | 动作 |
|----|------|
| 周一 | 看入库量、复核 backlog |
| 周三 | 抽 20 条低置信 + 人机不一致做归因 |
| **周五** | 跑评估 + 周报告，定 1 个主攻池 |
| 周五+ | 若 fixable > 0：patch dry-run → 门禁 → 写库 |

```bash
python3 performance_evaluation/evaluate_accuracy.py
python3 performance_evaluation/export_l1_errors.py --since 7d
python3 performance_evaluation/eval_p1_subset.py --compare \
  --export-regression-fixable auto --export-other-fixable auto
python3 performance_evaluation/weekly_evolution_report.py
```

---

## 9. 风险与约束（产品级）

| 风险 | 影响 | 对策 |
|------|------|------|
| 全库 `apply_post_rules_to_v3 --write` | L1 回退 | **禁止**；仅 CSV patch |
| praise/neutral_query 规则扩写 | 回归 broken | 门禁：主靶 broken ≤2，回归 broken ≤8 |
| 只改规则不重写 v3 | KPI 不变 | 预期行为；需 patch 或全量 reclassify |
| 复核字段缺失 | 评估分母缩小 | 35 无 review_l1；治理中 |

---

## 10. 相关文档

| 文档 | 用途 |
|------|------|
| [Design.md](./Design.md) | 架构、规则链、数据模型、部署 |
| [Agent.md](./Agent.md) | Cursor Agent 协作规范 |
| [AI_CONTEXT.md](./AI_CONTEXT.md) | 精简交接摘要 |
| `performance_evaluation/state/evolution_loop_mvp_plan_20260604.md` | 3 月闭环详细计划 |
| `performance_evaluation/state/m4b_r4_delivery_20260604.json` | r4 交付 KPI 与 patch 明细 |
