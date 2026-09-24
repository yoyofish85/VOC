# S6 抽样评估报告（开发机）

**日期**：2026-09-24  
**范围**：`opinion_review.db` 最近 200 条有 L1 的样本（只读 dry-run）  
**规则版本**：`insight_fields` v1 / `VOC_INSIGHT_FIELDS_V1`（默认关闭）

## 门禁结果

| 项 | 结果 |
|----|------|
| 纯函数可离线重放 | ✅ `extract_insight_fields` |
| 不覆盖 L1/L2/L3/confidence/match_type | ✅ `core_unchanged_rate=1.0` |
| 单测 | ✅ 7 passed（含手标金标 ≥85%） |
| 200 条人工一致率 ≥85% | ⏳ CSV 已导出，待人工在 `human_ok` 列标注 |

## 分布（n=200）

- **intent**：报障 123 / 告知 28 / 投诉升级 15 / 咨询 13 / 表扬 11 / 建议 9 / 求助 1
- **expected_action**：维修 115 / 解释说明 63 / 无需动作 9 / 功能实现 8 / 上门 3 / 退款赔偿 2
- **severity**：中 121 / 低 61 / 高 18
- **urgency**：一般 170 / 紧急 30
- **repeat_signal**：1.0%

## 产物

- 明细 CSV：`performance_evaluation/exports/insight_fields_dryrun_*.csv`
- 摘要 JSON：同目录 `insight_fields_dryrun_*.json`
- 复跑：`python3 performance_evaluation/dry_run_insight_fields.py --limit 200`

## 生产说明

- 新批次灰度：仅当 `VOC_INSIGHT_FIELDS_V1=1` 时，分类写库才追加洞察键。
- **不回填全库**；关闭开关时行为与 S5 前一致。
