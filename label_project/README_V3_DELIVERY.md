# V3 标签体系交付说明（label_project）

## 路径

| 内容 | 位置 |
|------|------|
| 离线验证 | `offline_validate.py` |
| 金标白名单/聚类构建 | `build_gold_taxonomy_v3.py` → `gold_l2_whitelist_v3.json`、`gold_l3_clusters_v3.json` |
| 标注用标准表导出 | `export_standard_label_system.py` → `../output/standard_label_system_for_annotators.xlsx` |
| Mac 一键更新 | `update_clean_data.command`（双击）、`run_label_update.sh` |
| 压测 | `run_benchmark_v3.py` |

## 依赖

- Python 3.9+
- `pip install pandas openpyxl`（导出 Excel 需要 openpyxl）
- 全量 LLM：`ollama pull qwen2.5:7b-instruct-q4_K_M`

## 数据文件约定

- 金标全量：`../testdata3.csv`（或环境变量 `LABEL_GOLD_CSV`）
- 清洗库：`../data_clean_project/data_clear.csv`
- 映射：`label_hierarchy_final.json`
