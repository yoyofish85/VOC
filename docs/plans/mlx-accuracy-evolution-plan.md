# Implementation Plan：MLX 准确率评估与进化路线

> **创建日期**：2026-07-08  
> **状态**：草案，待 Phase 0 / 生产批次实证后更新  
> **关联**：`performance_evaluation/state/evolution_loop_mvp_plan_20260604.md`、`docs/VOC_V1.5_开发记录.md` R22/R23

---

## Overview

切换 MLX+LoRA 的目标是**提升 L1/L2 准确率**（当前全库基线 L1 **86.98%**，MVP 目标 **90–93%**）。基础设施（`start_mlx.sh`、`verify_lora.sh`、`ab_compare_models.py`）已就绪，但生产侧 `match_type=mlx_*` 记录仍接近 0，**MLX 真实准确率尚未量化**。

本计划包含两条评估轨道，最终汇合为换模型/继续微调/回滚 Ollama 的决策：

| 轨道 | 名称 | 核心问题 |
|------|------|----------|
| **Track A** | Phase 0 — 离线对照基线 | 同批文本上，MLX+LoRA 是否优于 Ollama / 库内 v3？ |
| **Track B** | 生产批次实证（300–400 条） | 真实入库链路上，MLX+LoRA 的 L1/L2 能否支撑日常运营？ |

---

## 架构决策

| 决策 | 理由 |
|------|------|
| Holdout A/B 为换模型门禁 | 固定 seed holdout 防过拟合；`verify_lora.sh` 门槛 Δ≥2pp |
| 生产批次为运营门禁 | 反映最新分布、完整导入→分类→复核链路 |
| 全库 KPI 与 Holdout/批次分离 | 口径不同，不可混为一谈 |
| 规则链与 MLX 并存 | MLX 只替换推理后端；L2 仍依赖规则与白名单 |
| 效率优化后置 | 准确率门禁通过后再做延迟优化 |
| Ollama 保留为回滚路径 | `./start_ollama.sh` 即回滚 |

---

## 当前能力 vs 缺口

| 已有 | 缺口 |
|------|------|
| `verify_lora.sh`：smoke + holdout A/B | `data/holdout_opinion_ids.json` **当前为空** `[]`，holdout A/B 暂不可用 |
| `ab_compare_models.py`：L1 holdout 对比 | L2 未纳入 holdout 门禁 |
| `evaluate_accuracy.py`：全库只读 KPI | 无 `--mlx-shadow-limit`；无 `--upload-batch` 切片 |
| `POST /api/batch_status` | 仅 L1，且用 `v3_l1` 字符串比较（未 canonicalize） |
| `export_l1_errors.py` | 无 MLX vs Ollama 错误 diff |

---

# Track B：生产批次实证（2026-07-08/09，300–400 条）

> **计划**：今明两天投喂 **300–400 条**新数据，**全部使用 MLX+LoRA** 完成分类，经人工复核后评估 L1/L2 准确率。

### 执行 SOP

#### 1. 启动与环境

```bash
cd "/Users/chaoyu/Desktop/VOC_AI agent/VOC_V1.5"   # 服务器路径
./start_mlx.sh
# 确认 adapter：echo $VOC_MLX_ADAPTER
```

#### 2. 批次标识（强烈建议）

导入时使用**独立 `upload_batch`**，便于事后切片，例如：

```
mlx_eval_20260709_01
```

可在 CSV 导入或上传接口中指定；系统按 `YYYYMMDD_seq_N` 亦会自动分批，但专用命名更利于报告归档。

#### 3. 分类与复核

- 新数据全部走 MLX+LoRA（`match_type` 应为 `mlx_14b_lora`）
- 人工按规范完成 **L1 必填**；业务三类 **L2 尽量填**（非问题 L2 可空）
- 目标：**本批 300–400 条全部 `review_status=1`** 后再算准确率

#### 4. 评估命令

**快速 L1（复核页同款口径，仅 L1）：**

```bash
curl -s -X POST http://localhost:8000/api/batch_status \
  -H 'Content-Type: application/json' \
  -d '{"upload_batch":"mlx_eval_20260709_01"}' | python3 -m json.tool
```

**正式 L1+L2（与 KPI 报表一致，推荐）：**

```bash
# 当前需按批次手动筛选或扩展 evaluate_accuracy.py --upload-batch
python3 performance_evaluation/evaluate_accuracy.py
# 复核完成后对照 reports/ 中全库 KPI；批次达标后 Task 1.4 增加批次切片
```

**错误归因：**

```bash
python3 performance_evaluation/export_l1_errors.py --since 7d
# 人工过滤本批 upload_batch
```

#### 5. 建议记录的指标

| 指标 | 口径 | 目标参考 |
|------|------|----------|
| L1 准确率 | 已复核 + canonicalize 四类 | MVP ≥ **90%**；当前全库 86.98% |
| L2 准确率 | 业务三类且 L1 一致 + 有人工 L2 | MVP **82–88%**；当前全库 76.90% |
| 非问题偏向率 | MLX 预测非问题 / 本批总数 | 相对训练前不应恶化 |
| 单条 P50 延迟 | 首次 vs 缓存后 | 记录即可，暂不优化 |
| match_type 占比 | `mlx_14b_lora` 行数 / 本批 | 应 ≈ 100% |

#### 6. 统计显著性（心里有数）

n≈350、真实 L1≈90% 时，95% 置信区间约 **±3.2pp**。  
单批结果 **90% vs 87%** 差距可能在抽样误差内；需结合 Track A 或多样本周趋势判断。

#### 7. 可选增强：同批 Ollama 影子对照

若有余力，对本批 `opinion_id` 在**不写库**前提下用 Ollama 重跑 `classify_text`，可得到 **paired Δpp**（同文 MLX vs Ollama）。  
这是 Track A 与 Track B 的桥接，工作量中等，但能回答「MLX 到底比 Ollama 好多少」。

---

# Track A：Phase 0 — 离线对照基线

### Phase 0.0：初始化 holdout（当前阻塞项）

`data/holdout_opinion_ids.json` 为空时，`verify_lora.sh` 会跳过 holdout A/B。需先：

```bash
python3 performance_evaluation/export_finetune_from_db.py
# 首次运行会写入 holdout（约 10% 固定 seed）
```

### Task 0.1：三方 Holdout 对比

在固定 holdout 上记录：库内 v3 baseline、Ollama 14B、MLX+LoRA 的 L1 Δpp。

```bash
unset VOC_USE_MLX
python3 performance_evaluation/ab_compare_models.py --baseline
python3 performance_evaluation/ab_compare_models.py --candidate "qwen2.5:14b-instruct-q4_K_M"
python3 performance_evaluation/ab_compare_models.py --compare

export VOC_USE_MLX=1
export VOC_MLX_ADAPTER=~/lora_adapter_14b   # 实际路径
python3 performance_evaluation/ab_compare_models.py --candidate "mlx-community/Qwen2.5-14B-Instruct-4bit+lora"
python3 performance_evaluation/ab_compare_models.py --compare
```

归档 → `performance_evaluation/state/mlx_baseline_YYYYMMDD.json`

### Task 0.2：全库影子评估（Ollama vs MLX，N=50–100）

对**最近已复核**样本实时重推理，不写库。  
`evaluate_accuracy.py` 已有 `--qwen-shadow-limit`；**待实现** `--mlx-shadow-limit`。

### Task 0.3：错误归因切片

从 holdout A/B `details` 导出「Ollama 对 / MLX 错」「MLX 对 / Ollama 错」样例。

### Checkpoint 0

| Holdout Δpp（MLX vs Ollama） | 建议 |
|------------------------------|------|
| ≥ +2pp | 继续 MLX；Track B 批次作运营确认 |
| 0 ~ +2pp | MLX 灰度；加强微调 |
| < 0 | 暂停 MLX 生产；走 Phase 2B |

---

# Track A vs Track B：公正对比

| 维度 | Track A（Phase 0） | Track B（300–400 生产批次） | 判断 |
|------|-------------------|---------------------------|------|
| **当前可执行性** | holdout 为空，需先 `export_finetune` | 今明两天即可开始 | **B 更优先** |
| **评估 L2** | `ab_compare` 偏重 L1；shadow 可补但待开发 | 复核后自然得到 L2 KPI | **B 更适合** |
| **生产真实性** | Shadow/holdout 不走完整导入链路 | 完整：导入→MLX 分类→复核 | **B 更真实** |
| **对照实验** | 同文 Ollama vs MLX 易做 | 默认只有 MLX，无对照 | **A 更强** |
| **出结果速度** | holdout 就绪后数小时内 | 依赖复核完成（通常 1–3 天+） | **A 更快** |
| **分布代表性** | 历史已复核数据 | 最新入库分布 | **各有所长** |
| **样本量** | holdout ~230（10% of 2304） | 300–400 | **量级相当** |
| **决策风险** | 只读，零生产负担 | 模型差则增加 300–400 条复核纠错成本 | **A 更安全** |
| **与 MVP 目标关系** | 验证「模型能力」 | 验证「运营能不能用」 | **B 更贴近上线** |

### 综合结论（公正判断）

**不是二选一，而是串联；但对「当前这一步」有明确优先级：**

1. **Track B（300–400 MLX 批次）更适合作为当前主评估方案**  
   - 你的直接目标是「MLX+LoRA 准不准、L1/L2 能不能用」——这必须在**真实生产链路**上测。  
   - holdout 尚未初始化，Phase 0 的 A/B 门禁**今天跑不起来**（除非先 export）。  
   - 全库 `evaluate_accuracy.py` 统计的主要是历史 Ollama/v3 写库结果，**不能代表 MLX**。

2. **Track A 不可替代 Track B，但是重要补充**  
   - Track B 告诉你「新数据上表现如何」；Track A 告诉你「相对 Ollama 提升了多少、是否过拟合 holdout」。  
   - 仅有 Track B 时，可能出现：批次 92% 但 Ollama 同批也能 91%（提升来自分布而非模型）——**需要 paired 对照才能下结论**。  
   - 仅有 Track A 时，可能出现：holdout +3pp 但新渠道分布偏移导致生产批次下滑——**需要 Track B 验证**。

3. **推荐执行顺序**

```
今明两天  Track B：MLX 批次导入 + 分类（主任务）
     ‖
并行可选  Track A-0：export_finetune 初始化 holdout
     ‖
复核完成后  Track B：批次 L1/L2 评估 + export_l1_errors
     ‖
同一周末   Track A-1：holdout 三方 A/B（若 holdout 已就绪）
     ‖
决策点     Checkpoint 0 + 批次结果 → 继续 MLX / 微调 / 回滚
```

**一句话**：对你今明两天的目标，**Track B 更好、更应优先**；Track A 是科学对照与部署门禁，应在 holdout 就绪后**一周内补齐**，避免单次批次偶然性误导决策。

---

# Phase 1：生产可观测（批次之后持续）

## Task 1.1：按 match_type 切片 KPI

扩展 `evaluate_accuracy.py` 支持 `--match-type mlx_14b_lora`。

## Task 1.2：周报告纳入 MLX 列

`weekly_evolution_report.py` 增加 MLX 占比、MLX L1 Δpp。

## Task 1.3：微调触发器对接

`finetune_trigger.json`：新增复核 ≥3000、L1 不一致 ≥800 时提示重训。

## Task 1.4：按 upload_batch 评估（服务 Track B）

扩展 `evaluate_accuracy.py --upload-batch mlx_eval_*`，输出与全库一致的 L1/L2 口径。

**Acceptance criteria：**
- [ ] `batch_status` 或新参数可输出批次 L2
- [ ] 批次报告可归档至 `performance_evaluation/reports/`

---

# Phase 2A：准确率上升 — 加速提升

> **前提**：Track B 批次 L1 ≥ 90% 或较全库基线 ≥ +3pp，且 Track A holdout Δ ≥ 0。

| 优先级 | 方向 | 预期增益 |
|--------|------|----------|
| P0 | 增量微调（每 +3000 复核） | L1 +2~5pp |
| P0 | 错误池定向补训 | L1 +1~3pp |
| P1 | Prompt / few-shot 优化 | +0.5~2pp |
| P1 | 规则互补（fixable 池） | L1 +1~2pp |
| P2 | L2 专项（`patch_v3_l2_replay`） | L2 +3~5pp |

双周节奏：`export → balance → train → verify`；仅 PASS 替换 adapter。

---

# Phase 2B：准确率下降 — 止损优化

> **触发**：批次 L1 < 87% 或 holdout Δ < 0。

| 步骤 | 动作 |
|------|------|
| 1 | `./start_ollama.sh` 回滚 |
| 2 | 导出退化样本，禁止全库 reclassify |
| 3 | 重跑 `balance_finetune_data.py` + 调 iters |
| 4 | 连续 3 轮 holdout 仍负 → 考虑 Ollama+规则为主路径 |

---

# Phase 3：效率优化（准确率门禁通过后）

| 方向 | 手段 |
|------|------|
| P0 | 启动 warmup，消除首条 ~90s |
| P0 | 推理缓存命中率监控 |
| P1 | 批量推理 POC |
| P2 | 7B 预筛 + 14B 复核 |

---

# 统一门禁

```
✅ 主靶 broken ≤ 2
✅ 回归 broken ≤ 8
✅ Holdout L1 Δ ≥ 0（换模型 ≥ +2pp）
✅ Holdout L2 不回退 > 1pp
✅ 生产批次 L1 不低于全库基线 - 2pp（运营底线）
✅ 禁止 apply_post_rules_to_v3.py --write 全库
```

---

# 时间线（更新版）

| 时间 | 动作 | 产出 |
|------|------|------|
| **07-08/09** | Track B：300–400 条 MLX 导入分类 | `mlx_eval_*` 批次 |
| **07-09** | 并行：export_finetune 初始化 holdout | holdout JSON 就绪 |
| **复核完成后** | Track B：批次 L1/L2 评估 | 批次准确率报告 |
| **+3 天内** | Track A：holdout 三方 A/B | `mlx_baseline_*.json` |
| **决策点** | Checkpoint 0 + 批次 | 继续 / 微调 / 回滚 |
| **W5+** | Phase 2A 或 2B | 按结果分支 |

---

# Open Questions

1. 生产 adapter 路径：`~/lora_adapter_14b` 还是 `v2`？评估须与线上一致。  
2. 300–400 条复核预计几天完成？决定 Track B 报告时间点。  
3. 是否在批次文本上补跑 Ollama shadow（paired 对照）？  
4. L2 是否纳入 holdout 门禁（MVP 建议：批次必评 L2，holdout 次周补）。

---

# 相关文档

- [Design.md](../../Design.md) §7 部署与双推理路径  
- [Product.md](../../Product.md) §2 KPI 口径  
- [docs/VOC_V1.5_开发记录.md](../VOC_V1.5_开发记录.md) R22/R23  
- [performance_evaluation/state/evolution_loop_mvp_plan_20260604.md](../../performance_evaluation/state/evolution_loop_mvp_plan_20260604.md)
