# Implementation Plan：MLX 准确率评估与进化路线

> **创建日期**：2026-07-08  
> **最后更新**：2026-07-09  
> **状态**：执行中（Track B 已完成；当前停在 Step E0）  
> **关联**：`performance_evaluation/state/evolution_loop_mvp_plan_20260604.md`、`docs/VOC_V1.5_开发记录.md` R22–R25

---

## 目标锁定（2026-07-09 决策）

| 项 | 决议 |
|----|------|
| **上线门槛（MVP）** | L1 **90–93%**，L2 **82–88%** |
| **长期 95%** | **暂不作为本阶段决策依据**；单靠当前 MLX+LoRA **不能**到 95% |
| **系统状态** | 尚未完全上线 → **可全力配合测试**（允许切模型、重训、大批量导入） |
| **执行方式** | 分步门禁；**每步达标后由 Agent 提示再进入下一步**，禁止跳步 |

### 关于「能否到 95%」的裁决（已确认）

> 当前 adapter + 失衡训练数据下，MLX+LoRA **不能**将 L1 提升到 95%。  
> Track B 正式 L1 **82.64%**，低于全库基线 **86.98%**；训练集非问题 **63.5%**，错误以「业务类→非问题」为主。  
> 本阶段只冲 **90–93%**；95% 留待 MVP 达标后的第 4–6 月系统工程。

---

## Overview

切换 MLX+LoRA 的目标是把 L1 从当前实测拉到 **MVP 90–93%**（全库基线曾为 **86.98%**）。基础设施已就绪；Track B 已量化生产表现。

| 轨道 | 名称 | 状态 |
|------|------|------|
| **Track B** | 生产批次实证（288 条） | ✅ 已完成 |
| **Track A** | holdout 三方 A/B | ⏳ 进行中（holdout 已初始化；MLX candidate 待用 venv 跑完） |

---

## 架构决策

| 决策 | 理由 |
|------|------|
| MVP 门槛锁定 90–93% | 上线标准；95% 暂不作为本阶段目标 |
| Holdout A/B 为换模型门禁 | 固定 seed holdout 防过拟合；`verify_lora.sh` 门槛 Δ≥2pp |
| 生产批次为运营门禁 | 反映最新分布、完整导入→分类→复核链路 |
| 全库 KPI 与 Holdout/批次分离 | 口径不同，不可混为一谈 |
| 规则链与 MLX 并存 | MLX 只替换推理后端；L2 仍依赖规则与白名单 |
| 效率优化后置 | 准确率门禁通过后再做延迟优化 |
| Ollama 保留为回滚路径 | `./start_ollama.sh` 即回滚 |
| **分步门禁执行** | 每步达标后由 Agent 提示下一步；未达标不跳步 |

---

## 当前能力 vs 缺口

| 已有 | 缺口 |
|------|------|
| `verify_lora.sh`：smoke + holdout A/B | 须用 `.venv`（`VOC_PYTHON`）；系统 `python3` 缺 `mlx_lm` |
| `ab_compare_models.py`：L1 holdout 对比 | L2 未纳入 holdout 门禁 |
| `evaluate_accuracy.py`：全库只读 KPI | 无 `--mlx-shadow-limit`；无 `--upload-batch` 切片 |
| `POST /api/batch_status` | 仅 L1，且用 `v3_l1` 字符串比较（未 canonicalize）→ **以正式脚本为准** |
| `export_l1_errors.py --since 7d` | ✅ 服务器已可用；无 MLX vs Ollama 错误 diff |

---

# Track B 实测结果（2026-07-09，已归档）

| 批次 | 条数 | L1（正式） | L2（正式） |
|------|------|-----------|-----------|
| `20260709_1_105` | 105 | 84.76% (89/105) | 81.82% (36/44) |
| `20260708_2_174` | 174 | 81.03% (141/174) | 88.64% (39/44) |
| `20260708_1_9` | 9 | 88.89% (8/9) | 100% (1/1) |
| **合计** | **288** | **82.64%** (238/288) | **85.39%** (76/89) |

| 对照 | 数值 |
|------|------|
| 全库基线 L1 | 86.98% |
| Track B vs 基线 | **−4.3pp** |
| MVP 门槛 | 90–93% |
| 近 7 天 L1 错误主因 | **75%** 为「业务类→非问题」 |
| 训练集非问题占比 | **63.5%** (2522/3968) |

**Track B 结论**：当前 MLX+LoRA **未达**上线门槛，且低于基线；主因非问题偏向。进入执行门禁 **Step E0 → E1**。

---

# Track A：Phase 0 — 离线对照基线

### Phase 0.0：初始化 holdout — ✅ 已完成（2026-07-09）

```
复核样本: 4270 | holdout: 302 | 训练集: 3968
holdout IDs: data/holdout_opinion_ids.json
baseline（库内 v3）: 89.74% (271/302)
```

### Task 0.1：三方 Holdout 对比 — ⏳ 待完成

须用 venv：

```bash
cd "/Users/chaoyu/Desktop/VOC_AI agent/VOC_V1.5"
export VOC_PYTHON="$(pwd)/.venv/bin/python"
export VOC_MLX_ADAPTER="$HOME/lora_adapter_14b_v2"

# 若缺 mlx_lm
bash scripts/ensure_mlx_deps.sh

unset VOC_USE_MLX
$VOC_PYTHON performance_evaluation/ab_compare_models.py --baseline
$VOC_PYTHON performance_evaluation/ab_compare_models.py --candidate "qwen2.5:14b-instruct-q4_K_M"
$VOC_PYTHON performance_evaluation/ab_compare_models.py --compare

export VOC_USE_MLX=1
$VOC_PYTHON performance_evaluation/ab_compare_models.py \
  --candidate "mlx-community/Qwen2.5-14B-Instruct-4bit+lora"
$VOC_PYTHON performance_evaluation/ab_compare_models.py --compare

# 或
VOC_PYTHON=.venv/bin/python bash scripts/verify_lora.sh
```

归档 → `performance_evaluation/state/mlx_baseline_YYYYMMDD.json`

### Checkpoint 0（holdout Δ 出来后）

| Holdout Δpp（MLX vs Ollama） | 建议 |
|------------------------------|------|
| ≥ +2pp | 继续 MLX 实验路径；仍须补训冲 90% |
| 0 ~ +2pp | MLX 仅离线实验；生产可切 Ollama |
| < 0 | 生产切 Ollama；MLX 离线 `balance → train → verify` |

---

# 分步执行门禁（当前主流程）

> **规则**：只执行「当前步骤」；达标后把结果贴给 Agent，由 Agent 确认并提示下一步命令。  
> **未达标**：按该步「失败分支」处理，不进入下一步。

## 进度总览

| Step | 名称 | 达标条件 | 状态 |
|------|------|----------|------|
| **E0** | 环境就绪 | `.venv` 可 `import mlx_lm`；adapter 路径确认 | ⏳ **当前** |
| **E1** | Holdout 三方 A/B | Ollama + MLX candidate 均有 L1%；报告已写 | ⬜ |
| **E2** | Checkpoint 决策 | 按 Δpp 选定：生产路径 + 是否重训 | ⬜ |
| **E3** | 数据均衡 | 非问题 ≤30%；`mlx_finetune_balanced/` 产出 | ⬜ |
| **E4** | 重训 LoRA | 新 adapter（建议 `lora_adapter_14b_v3`）训练完成 | ⬜ |
| **E5** | 门禁验证 | smoke ≥3/5；holdout Δ ≥ **+2pp** vs 基线/Ollama | ⬜ |
| **E6** | 验证批次 | 新导入 ≥200 条，正式 L1 ≥ **90%**，L2 ≥ **82%** | ⬜ |
| **E7** | 上线门禁 | 连续两批或全库切片 L1 ∈ **90–93%** | ⬜ |
| **E8** | 效率优化 | 仅 E7 通过后；warmup / 缓存 | ⬜ 冻结 |

---

### Step E0：环境就绪（当前）

**你要做的：**

```bash
cd "/Users/chaoyu/Desktop/VOC_AI agent/VOC_V1.5"
bash scripts/setup_server_venv.sh          # 若尚无可用 .venv
bash scripts/ensure_mlx_deps.sh
.venv/bin/python -c "import mlx_lm; print('ok')"
ls "$HOME/lora_adapter_14b_v2"             # 确认 adapter 存在
```

**达标：**
- [ ] `import mlx_lm` 成功
- [ ] adapter 目录存在（`adapters.safetensors` 或 `adapter_config.json`）

**失败：** 按 `scripts/setup_server_venv.sh` / `transformers<5.13` 修复，不进入 E1。

**达标后回复 Agent：**「E0 完成」+ `mlx_lm` 版本一行输出。

---

### Step E1：Holdout 三方 A/B

**你要做的：**（见上方 Task 0.1 命令块）

**达标：**
- [ ] baseline L1 已记录（已有 89.74%，可复用）
- [ ] Ollama candidate L1% 已记录
- [ ] MLX+LoRA candidate L1% 已记录
- [ ] `ab_comparison_report.json` 含 `comparison.delta_pp`

**失败：** MLX 加载失败 → 回 E0；Ollama 不可用 → 先 `ollama serve` + pull 模型。

**达标后回复 Agent：** 三方 L1% + `delta_pp`（可贴 JSON 摘要）。

---

### Step E2：Checkpoint 决策

**由 Agent 根据 E1 数字给出明确指令**（生产用 Ollama 还是 MLX；是否立刻 E3 重训）。

**达标：**
- [ ] 书面决策已写入本计划「决策日志」
- [ ] 用户确认执行

**默认倾向（可被 E1 数据推翻）：**  
Track B 已显示 MLX L1 偏低 → **生产倾向切 Ollama**；MLX 走离线重训（E3–E5）。系统未上线，测试可全力配合。

---

### Step E3：数据均衡

```bash
python3 scripts/balance_finetune_data.py
# 确认输出目录非问题占比 ≤30%
```

**达标：**
- [ ] `mlx_finetune_balanced/{train,valid,test}.jsonl` 存在
- [ ] 训练集非问题占比 ≤ 30%

**达标后回复 Agent：** 均衡后 L1 分布数字。

---

### Step E4：重训 LoRA

```bash
# 建议输出到新路径，勿覆盖 v2，直至 E5 PASS
export VOC_MLX_ADAPTER="$HOME/lora_adapter_14b_v3"
bash scripts/train_lora_14b.sh
```

**达标：**
- [ ] 新 adapter 文件齐全
- [ ] 训练日志无异常中断

**达标后回复 Agent：** adapter 路径 + 训练结束确认。

---

### Step E5：门禁验证

```bash
export VOC_PYTHON="$(pwd)/.venv/bin/python"
export VOC_MLX_ADAPTER="$HOME/lora_adapter_14b_v3"
bash scripts/verify_lora.sh
```

**达标：**
- [ ] smoke ≥ 3/5
- [ ] holdout Δ ≥ **+2.0pp**（相对约定基线）
- [ ] 结论为 PASS（非 FAIL）

**失败：** Δ < 0 → 调参/再均衡，**禁止**用新 adapter 灌生产；连续 3 轮 FAIL → 评估「Ollama+规则」为主路径。

**达标后回复 Agent：** verify 结论全文。

---

### Step E6：验证批次（冲 90%）

用 **E5 PASS 的 adapter** 导入 ≥200 条新数据，全链路复核。

**达标：**
- [ ] 正式口径 L1 ≥ **90%**
- [ ] 正式口径 L2 ≥ **82%**（分母足够时）
- [ ] 「业务类→非问题」错误占比显著下降（相对 Track B 的 75%）

**失败：** L1 < 90% → 错误归因后回 E3/E4，不宣称可上线。

**达标后回复 Agent：** 批次名 + L1/L2 + Top5 错误。

---

### Step E7：上线门禁（MVP）

**达标：**
- [ ] 连续两批验证 **或** 全库近期切片 L1 ∈ **90–93%**
- [ ] L2 ∈ **82–88%**
- [ ] 统一门禁（broken / 禁止全库非幂等写）仍满足

**达标后：** Agent 提示「可进入初步上线」；**仍不冲 95%**，除非另开计划。

---

### Step E8：效率优化（冻结）

仅 E7 通过后解冻：warmup、缓存、批量推理。准确率未达标前禁止。

---

# Phase 2A / 2B（与门禁对应）

| 阶段 | 对应 Step | 说明 |
|------|-----------|------|
| 2B 止损 | E2 若 Δ<0 或 Track B 已证伪当前 adapter | 生产切 Ollama；离线重训 |
| 2A 加速 | E3–E6 | 均衡、错误池补训、规则 fixable 并行 |
| 冲 90–93% | E6–E7 | 上线门槛 |
| 95% | **本计划范围外** | MVP 后再开 |

---

# 统一门禁

```
✅ 主靶 broken ≤ 2
✅ 回归 broken ≤ 8
✅ Holdout L1 Δ ≥ 0（换模型 ≥ +2pp）
✅ Holdout L2 不回退 > 1pp
✅ 验证批次 / 上线：L1 ∈ 90–93%，L2 ∈ 82–88%
✅ 禁止 apply_post_rules_to_v3.py --write 全库
✅ 未过 E5 的 adapter 不得作为生产默认
```

---

# 决策日志

| 日期 | 决策 |
|------|------|
| 2026-07-08 | 优先 Track B 生产批次评估 |
| 2026-07-09 | Track B：L1 82.64%，主因非问题偏向 |
| 2026-07-09 | **目标锁定 MVP L1 90–93%**；95% 暂不作为决策依据 |
| 2026-07-09 | 裁决：当前 MLX+LoRA **不能**单独到 95% |
| 2026-07-09 | 系统未完全上线 → 可全力测试；执行改分步门禁 E0–E7 |
| 2026-07-09 | holdout 初始化完成；baseline 89.74%；**当前停在 E0** |

---

# 时间线（更新版）

| 时间 | 动作 | 状态 |
|------|------|------|
| 07-08/09 | Track B 288 条 MLX 导入复核 | ✅ |
| 07-09 | export_finetune + holdout 302 | ✅ |
| 07-09 | Track B 正式评估 + 错误归因 | ✅ |
| **当前** | **Step E0 → E1** | ⏳ |
| E2 后 | 生产路径决策 + E3 均衡重训 | ⬜ |
| E5 PASS 后 | E6 验证批次冲 90% | ⬜ |
| E7 | MVP 上线门禁 | ⬜ |

---

# Open Questions（已收敛 / 仍开放）

| # | 问题 | 状态 |
|---|------|------|
| 1 | adapter 路径 | **默认 `~/lora_adapter_14b_v2`**；重训用 `v3` |
| 2 | Track B 批次 | ✅ 已完成 |
| 3 | Ollama shadow paired | 可选；E1 三方 A/B 优先 |
| 4 | L2 holdout 门禁 | E6/E7 批次必评；holdout L2 次优先 |
| 5 | 生产是否立刻切 Ollama | **等 E1 数字后在 E2 书面确认**（倾向：切） |

---

# 相关文档

- [Design.md](../../Design.md) §7 部署与双推理路径  
- [Product.md](../../Product.md) §2 KPI 口径  
- [docs/VOC_V1.5_开发记录.md](../VOC_V1.5_开发记录.md) R22–R25  
- [performance_evaluation/state/evolution_loop_mvp_plan_20260604.md](../../performance_evaluation/state/evolution_loop_mvp_plan_20260604.md)
