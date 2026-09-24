# S7 抽样评估报告（开发机）

**日期**：2026-09-24  
**范围**：`opinion_review.db` 最近 800 条（只读）  
**映射版本**：`theme_mapping_version=v1` / `insight_version=v1`

## 门禁

| 项 | 结果 |
|----|------|
| 主题汇总 = L3 小计 = 去重 opinion_id | ✅ 800=800=800，`gate_ok` |
| 已复核 / 仅模型分层 | ✅ `reviewed_count` / `model_only_count` |
| 映射版本 + 覆盖率 + 查询条件 | ✅ 写入 `meta` 与每条 `insights[].coverage/evidence_sql` |
| 单测 | ✅ `tests/unit/test_cross_record_insights.py` |
| CSV 证据行数对账 | ✅ `export_detail_rows == opinion_id_total` |

## 覆盖率（n=800）

- VIN 79.6% → 实体键选用 **vin**
- phone 87.3% / car_model 79.5%

## 产物

```bash
python3 performance_evaluation/run_cross_record_insights.py --limit 800 --days 30
```

- JSON：`performance_evaluation/exports/cross_record_insights_*.json`
- 主题×L3：`cross_record_theme_l3_*.csv`
- 明细（脱敏）：`cross_record_evidence_*.csv`

## 说明

- 只读聚合，不写 `opinion` 分类字段。
- 单条洞察字段优先读 `v3_label_meta`，缺失时用 S6 规则即时推导。
- 语义跨 L2 聚集（I7）留待 S8。
