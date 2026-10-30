# S6 洞察字段人工标注规范（200 条一致率门禁）

> 目的：为 S6 洞察字段（纯规则）建人工金标，验证一致率 ≥85%。
> 产物：`performance_evaluation/exports/insight_fields_dryrun_*.csv` 的 `human_ok` 列。

## 1. 字段定义（8 个）

| 字段 | 取值 | 判读要点 |
|------|------|----------|
| `intent` 意图 | 报障 / 求助 / 咨询 / 建议 / 投诉升级 / 表扬 / 告知 | 用户这句话的**主要诉求类型** |
| `expected_action` 期望动作 | 维修 / 上门 / 退款赔偿 / 解释说明 / 功能实现 / 无需动作 | 用户**想要我们做什么** |
| `severity` 严重度 | 高 / 中 / 低 | 安全或车辆不可用 = 高 |
| `urgency` 紧迫度 | 紧急 / 一般 | 含催 / 多次 / 仍未解决 = 紧急 |
| `repeat_signal` 复发 | true / false | "第N次 / 之前反馈过 / 又出现" 等 |
| `root_cause_hint` 根因提示 | 短语 | L3 或 L2 或原文截断 |
| `complaint_gist` 抱怨要点 | 短语(≤22字) | 抽取最突出的故障/不满短语 |
| `urgency_score` 紧迫度分 | 0–5 | severity(高2/中1)+投诉升级1+复发1+催促词1 |

## 2. 标注规则

- **`human_ok = 1`**：该行**所有 8 个字段**与人工判读一致。
- **`human_ok = 0`**：**至少一个字段**判错（建议在旁备注哪个字段错）。
- **留空**：未标注，不计入分母。

## 3. 评分与门禁

```bash
# 标注完成后评分（一致率 ≥85% 通过）
python3 performance_evaluation/score_insight_annotation.py \
  performance_evaluation/exports/insight_fields_dryrun_<时间戳>.csv
```

输出 `agreement_rate` 与 `gate_pass_85`；未通过则回查 `0` 行，优先修 `intent / severity`（对下游情绪升级、severity 加权影响最大）。

## 4. 生成标注 CSV（在服务器权威库上跑）

```bash
python3 performance_evaluation/dry_run_insight_fields.py --limit 200 --db src/backend/opinion_review.db
```
