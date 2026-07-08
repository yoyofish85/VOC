# VOC_V1.5 项目交接摘要（AI Agent 上下文）

> **最后更新**：2026-06-04  
> **当前 KPI（服务器验收后）**：L1 **86.98%**（2004/2304），L2 **76.90%**（516/671）  
> **规则版本**：`v12_m4b_r4`（缓存键 `classify_v12_m4b_r4`）— **M4-B 规则轨已见底**（fixable=0）
> **文档体系**： [Product.md](./Product.md)（产品/KPI）· [Design.md](./Design.md)（架构/规则）· [Agent.md](./Agent.md)（Agent 协作）  
> **用途**：在新 Cursor Agent 对话中快速恢复本项目的业务目标、代码状态与待办。  
> **开发机路径**：`/Users/yuchao/Documents/AI Agent/VOC_V1.5`  
> **生产/服务器路径**：`/Users/chaoyu/Desktop/VOC_AI agent/VOC_V1.5`（M3 Max，离线部署）

---

## 1. 项目定位与核心功能

**VOC_V1.5** 是车载用户舆情（VOC）的 **分类 + 人工复核** 系统。

| 能力 | 说明 |
|------|------|
| 数据入库 | 上传批次舆情 → SQLite `opinion` 表 |
| 自动分类 | 金标命中 → 规则/冲突检测 → **本地 Qwen2.5-14B（Ollama）** 结构化输出 |
| 后处理规则 | 14B 输出后经 `apply_classification_post_rules()` 守卫链修正（v9.x 重点） |
| 人工复核 | Vue 前端复核页写 `review_l1` / `review_l2` |
| 报表 | 月度趋势、数据汇报页（`report_aggregator.py`） |
| 评估 | 只读脚本对比「模型 v3 vs 人工金标」 |

**业务 KPI（当前阶段）**：

- **L1 准确率 ≥ 95%**（长期）
- **L2 准确率 ≥ 90%**（仅统计人工 L2 属于 `{产品质量类, 服务类, 体验需求类}` 且 L1 一致样本）
- **「非问题」不要求二级**；非问题 L2 **不计入 L2 KPI**

**数据规模（2026-05~06 快照）**：

- 全库约 **29,056** 条；`review_status=1` 已复核 **2,362** 条
- 评估分母（有人工+模型 L1）：**2,304** 条（2026-06-04）
- 全量 `batch_classify` / 14B 重分类约 **2 小时**（2362 条已复核）

---

## 2. 分类规则演进（v9 → v9.3）

核心文件：`src/backend/qwen_ollama.py`

| 版本 | 缓存键 | 要点 |
|------|--------|------|
| v9 | `classify_v9_nonissue_no_l2` | 非问题不写 L2；口径对齐 `taxonomy_normalize` |
| v9.1 | `classify_v9_1_praise_capture` | 销售/试驾/售后 **正向捕获** → 非问题 |
| v9.2 | `classify_v9_2_lfc_soft` | LFC 咨询/投诉细分；硬负面拆分；弱正向词表；**charging_guard 回归保护** |
| **v9.3** | `classify_v9_3_neutral_stmt` | LFC consult **eligible bugfix**；中性陈述/购车意向词表；硬负面再收窄 |
| **v12 M4-B r4** | `classify_v12_m4b_r4` | neutral_query 拉回 + 跨类 `l1_category_rebalance`；**2026-06-04 服务器交付 +14 L1** |

**规则链顺序**（`apply_classification_post_rules`，v12）：

1. `non_issue_guardrail` — 带诉求/异常的非问题 → 业务类  
2. `consultation_praise_misclass` — L2=咨询与表扬 + 硬负面 → 重定位  
3. `charging_domain_guard` — 非问题 + LFC 域 → 产品质量类  
4. `service_quality_guard` — 非问题 + 试驾差评信号 → 服务类  
5. `l1_category_rebalance` — 业务三类跨类 tilt（M4-B2）  
6. `positive_consult_capture` — 好评/咨询/弱正向 → **非问题**（l2 空）  
   - **回归保护**：若 `charging_guard` 已触发且为 LFC 咨询文本，**不再**捕获回非问题

---

## 3. 已完成工作（本对话周期）

### 3.1 口径与产品（步骤 1，已部署/开发机均有）

- `label_project/taxonomy_normalize.py`：`strip_non_issue_l2`、`L2_EVAL_L1`、`NON_ISSUE_L1`
- `src/backend/main.py`、`voc_classifier_service.py`：写库/仪表盘与非问题 L2 口径统一
- `src/frontend/.../OpinionReview.vue`：非问题二级可选/清空
- `performance_evaluation/evaluate_accuracy.py`：新版 L1/L2 KPI
- `performance_evaluation/import_label_audit.py`：非问题导入可不填 L2
- 单元测试：`tests/unit/test_taxonomy_nonissue_l2.py`

### 3.2 L1 边界规则迭代（Phase 0~1 + v9.2/v9.3）

- **Phase 0**：`performance_evaluation/export_l1_errors.py` — L1 错误切片 CSV + baseline snapshot
- **Phase 1~3 规则**：`qwen_ollama.py` 正向捕获与守卫（v9.1 → v9.3）
- **子集评估**：`performance_evaluation/eval_p1_subset.py`
  - 离线重放 `apply_classification_post_rules()`，**秒级**验证，避免每次 2h 全量重分
  - `--compare`、`--save-baseline`、`--export-ids`、`--export-unfixed`（含捕获诊断）
  - `_local_explain_positive_capture_block` 兼容旧版无 `explain_positive_capture_block` 的服务器
- **单元测试**：
  - `tests/unit/test_v9_1_praise_capture.py`
  - `tests/unit/test_v9_2_lfc_soft_capture.py`
  - `tests/unit/test_v9_3_neutral_capture.py`
  - `tests/unit/test_post_rules_replay.py`

### 3.3 全量 14B 重分类（服务器历史，2026-05-28）

- 脚本：`performance_evaluation/reclassify_all_with_14b.py`（**在服务器上存在，开发机仓库可能未纳入 git**）
- 2362 条已复核重跑约 **155 分钟**；人工标签不被覆盖，只更新 `v3_label_meta`
- 重跑后全库 L1 约 **70.27%** → 后续评估基线约 **77.66%**（口径更新后）

---

## 4. 评估结果时间线（子集 / 全库）

### 4.1 全库基线（`evaluate_accuracy.py`，约 2026-05-29）

| 指标 | 数值 |
|------|------|
| L1 | **77.66%**（1769/2278） |
| L2 | **78.41%**（534/681，业务三类且 L1 一致） |

**L1 错误 Top4**（约 509 条）：

1. 产品质量类 → 非问题：**152**
2. 服务类 → 非问题：**109**
3. 非问题 → 产品质量类：**67**
4. 非问题 → 服务类：**46**

### 4.3 M4 全库 KPI 时间线（服务器，2026-06-04）

| 阶段 | L1 | L2（业务三类） | 说明 |
|------|-----|----------------|------|
| M0 基线 | 80.68% | — | |
| M3 写库 | 83.20% | 75.00% | +14 主靶 |
| M4-A | 84.11% | 75.54% | +21 回归 |
| M4-B r1–r3 | **86.37%** | 77.63% | +52 回归/守卫 |
| **M4-B r4 写库** | **86.98%** | **76.90%** | +14（1 回归 +13 跨类） |

**r4 写库明细**：`patch_p1_regression` 1 条 + `patch_p1_other_l1` 13 条；`patch_v3_l2_replay` 0 条。  
**交付报告**：`performance_evaluation/state/m4b_r4_delivery_20260604.json`

**r4 后错误池**：

| 池 | 数量 |
|----|------|
| P1 回归（人工=业务，v3=非问题） | **164** |
| P1 主靶 | **57** |
| 其他 L1 错 | **79** |
| **L1 错合计** | **300** |

**回归 164 未拉回 Top 诊断**：`eligible:neutral_query` 36，`eligible:praise` 21，`eligible:soft_hope_suggest` 16，`blocked:v12_regression_pullback` 14。

**analyze_m4b（r4 后）**：三轨 fixable 均为 **0** → 规则迭代 ROI 见底。

**距目标**：L1 90% 还需 **~70 条**；L1 95% 还需 **~185 条**；L2 90% 还需 **~88 条**（分母 671）。

**累计 L1 patch（M3→r4）**：**+101 条**。

---

### 4.2 P1 子集规则重放（293 主靶 + 122 回归）

| 版本 | 主靶修复 | 全库 L1 估算 | 回归 broken |
|------|----------|--------------|-------------|
| v9.1 | 11/293 | +0.48pp | 0 |
| **v9.2** | **97/293** | **+4.26pp** | 0 |
| v9.3 | **待服务器验证** | 目标 +6~8pp | ≤5 |

**v9.2 未修复 196 条诊断分布**：

- `no_capture_pattern`: 87  
- `blocked:hard_negative`: 56  
- `blocked:lfc_charging_domain`: 29  
- `eligible:lfc_consult`: 12（v9.3 已修 eligible/捕获不一致 bug）  
- 其他：negation 7，srv_quality 3，lfc_complaint 2  

**P1 子集验收参考线**（全量重分前）：

- 主靶修复 **≥ 150** / 293  
- 全库估算 **≥ +8pp**  
- 回归 **broken ≤ 5**

---

## 5. 当前待办 / 下一步

### 优先级 P0（规则轨已见底 → 转 MVP）

1. **进化闭环 MVP Week 0** — 基线已锁定（L1 86.98% / L2 76.90%）  
   - 计划：`performance_evaluation/state/evolution_loop_mvp_plan_20260604.md`  
   - 编码起点：`weekly_evolution_report.py`（用户说「开工 P0」）

2. **14B 微调样本池**（规则 fixable=0 后的主路径）  
   - 新建 `export_finetune_from_db.py`；holdout 10%  
   - 触发：新增复核 ≥3000 或连续 2 周 fixable < 5（**已满足后者**）

3. **禁止**全库 `apply_post_rules_to_v3.py --write`（非幂等）

### 优先级 P1（可选，ROI 低）

- **praise / neutral_query 边界**（164 回归 Top：36+21）— 易回退，需逐条门禁  
- **L2 专项**：Top `车端充电→LFC`、`故障-通用→故障告警`（当前 l2 replay 0 可写）

### 数据治理（非阻塞）

- `reviewer` 字段历史全空  
- 部分已复核未归档到年度表（`reflow_synced`）  
- 35 条缺 `review_l1` / 96 条缺 `review_l2`（评估脚本已跳过）

---

## 6. 关键文件 / 目录

```
VOC_V1.5/
├── app_launcher.py              # 一键启停后端+前端
├── Product.md                   # 产品：KPI、路线图、运营 SOP
├── Design.md                    # 设计：架构、规则链、评估/patch
├── Agent.md                     # Agent 协作规范与命令
├── AI_CONTEXT.md                # 本交接文档（精简版）
├── code_deploy/                 # 离线打包/更新（仅 src/）
│   ├── package_code.sh          # 开发机：npm build + zip
│   ├── update_server.sh         # 服务器：备份+覆盖 src/
│   └── rollback_server.sh
├── label_project/               # ⚠️ 不在 update.zip 内，需单独同步
│   ├── taxonomy_normalize.py    # 四类规范、L2 KPI 口径
│   └── gold_l2_whitelist_v3.json
├── performance_evaluation/      # ⚠️ 不在 update.zip 内
│   ├── evaluate_accuracy.py     # 全库金标评估（只读）
│   ├── eval_p1_subset.py        # P1 子集规则重放（秒级）
│   ├── export_l1_errors.py      # L1 错误 CSV 导出
│   ├── import_label_audit.py    # ambiguous 复核结果导入
│   ├── patch_p1_regression_v3.py / patch_p1_other_l1_v3.py  # 幂等 L1 patch
│   ├── weekly_evolution_report.py  # MVP P0 周报告
│   ├── patch_csv_utils.py       # --csv glob 多文件取最新
│   ├── analyze_m4b.py           # 两轨 ROI 对比
│   ├── state/m4b_r4_delivery_20260604.json  # r4 交付报告
│   └── state/evolution_loop_mvp_plan_20260604.md  # 3 月闭环计划
├── src/backend/
│   ├── main.py                  # FastAPI 入口
│   ├── qwen_ollama.py           # ★ 14B + v12 规则链
│   ├── voc_classifier_service.py # batch_classify 写 v3
│   └── opinion_review.db        # 生产库（打包/更新脚本排除）
├── src/frontend/                # Vue 复核 UI
└── tests/unit/test_v9_*.py      # 规则回归测试
```

---

## 7. 部署与评估操作速查

### 7.1 code_deploy（只更 src/）

```bash
# 开发机
./code_deploy/package_code.sh
# 拷 update.zip → 服务器
./code_deploy/update_server.sh
```

**不包含**：`label_project/`、`performance_evaluation/`、`data/`、`*.db`、`app_launcher.py`（无变更可不动）

### 7.2 两种评估分工

| 脚本 | 读什么 | 何时用 |
|------|--------|--------|
| `eval_p1_subset.py --compare` | 库内 v3 + **离线重放规则** | 改规则后秒级迭代 |
| `evaluate_accuracy.py` | 库内 **v3 写回结果** | 全量 reclassify 后正式 KPI |

> 只部署新规则、不重写 v3 → 跑 `evaluate_accuracy.py` **数字不变**是预期行为。

### 7.3 推理环境（服务器）

**Ollama（`./start_ollama.sh`）：**

```bash
ollama pull qwen2.5:14b-instruct-q4_K_M
export VOC_QWEN_MODEL=qwen2.5:14b-instruct-q4_K_M
export OLLAMA_HOST=http://127.0.0.1:11434
```

**MLX + LoRA（`./start_mlx.sh`）：**

```bash
bash scripts/setup_server_venv.sh          # 首次：Python 3.11–3.13 venv
export VOC_USE_MLX=1
export VOC_MLX_MODEL=mlx-community/Qwen2.5-14B-Instruct-4bit
export VOC_MLX_ADAPTER=~/lora_adapter_14b_v2   # 或实际 adapter 路径
```

约束：`transformers<5.13`；须 `python-multipart`；勿用 Python 3.14 建 venv。

---

## 8. 注意事项

1. **全量重分约 2h**：规则开发阶段优先 `eval_p1_subset.py`，达标后再 `reclassify_all_with_14b.py`。  
2. **batch_classify 不覆盖人工标签**：只更新 `v3_label_meta` 等模型侧字段。  
3. **v9 部署 ≠ L1 提升**：v9 主要是非问题 L2 口径；L1 边界靠 v9.1+ 规则 + 全量重分写库。  
4. **P0 CSV（export_l1_errors）**：供规则开发/回归，**不是**默认人工复核任务。  
5. **SQLite 只读评估**：`evaluate_accuracy.py` / `eval_p1_subset.py` 使用 `mode=ro`。  
6. **服务器脚本单行启动重分**（勿拼接引号导致 zsh 卡住）：  
   `nohup python3 performance_evaluation/reclassify_all_with_14b.py > performance_evaluation/state/reclassify_console.log 2>&1 &`  
7. **git / 忽略**：`code_deploy/update.zip`、`.db`、`qwen_inference_cache.json`、评估 `reports/`/`state/` 产物勿提交；`.cursorignore` 可能限制 agent 读库。  
8. **回复语言**：用户偏好 **简体中文**。

---

## 9. 给新 Agent 的第一条命令建议

```bash
# 1) 确认规则版本
grep -n "classify_v9" src/backend/qwen_ollama.py

# 2) 子集快验（服务器，需 DB 可读）
python3 performance_evaluation/eval_p1_subset.py --compare --export-unfixed

# 3) 全库 KPI（仅 reclassify 后有意义）
python3 performance_evaluation/evaluate_accuracy.py

# 4) 单元测试
python3 -m pytest tests/unit/test_v9_1_praise_capture.py \
  tests/unit/test_v9_2_lfc_soft_capture.py \
  tests/unit/test_v9_3_neutral_capture.py \
  tests/unit/test_post_rules_replay.py -q
```

---

## 10. 下一步计划（已确认 2026-06-04）

**M4-B r4 已交付**（2026-06-04）：L1 **86.98%**，规则轨 fixable=0。  
**最小进化闭环 MVP（3 个月）** — 完整清单见：

`performance_evaluation/state/evolution_loop_mvp_plan_20260604.md`

**Week 0 基线**（已锁定）：L1 **86.98%**（2004/2304），L2 **76.90%**（516/671）。  
**P0 已交付**：`performance_evaluation/weekly_evolution_report.py`（周报告 + `evolution_weekly_last.json`）。  
要点：周度进化报告 → 微调样本池 → 门禁化 patch/灰度；MVP 目标 L1 **90–93%**（3 月内）。

---

## 11. 相关对话

本摘要源自 Cursor Agent 会话（P1 子集评估、v9.1~v9.3 规则迭代、code_deploy 与全量重分流程）。完整 transcript 可在 agent-transcripts 中按项目检索 `eval_p1_subset`、`v9.2`、`reclassify_all_with_14b` 等关键词。
