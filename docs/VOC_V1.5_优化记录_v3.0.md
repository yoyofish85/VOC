# VOC 舆情复核系统 · 优化记录 v3.0

## 一、优化目标

- **一级标签**：规则 + 清洗数据 MD5 锁定，**不经过 LLM 修改**，目标精确率稳定 ≥95%（依赖金标与规则维护）。
- **二级标签**：基于 5628 金标生成 **白名单**，规则优先，不足时 **Qwen 单行精排**，目标精确率 ≥85%。
- **三级标签**：**原体系规则** → **金标聚类** → **通用** → **LLM 在「体系标签 ∪ 聚类 canonical」中选一行**。
- **环境**：`qwen2.5:7b-instruct-q4_K_M`，Ollama 自动检测/拉起，模型未拉取时安全兜底不崩溃。
- **推理**：严格 **纯原文**（舆情中文+舆情原文），不把问题简述/问题细分作为匹配输入。

## 二、架构变更

| 模块 | 说明 |
|------|------|
| `label_project/offline_validate.py` | 离线全量验证、与金标对比 |
| `label_project/build_gold_taxonomy_v3.py` | 从 `testdata3.csv` 重建 `gold_l2_whitelist_v3.json`、`gold_l3_clusters_v3.json` |
| `label_project/export_standard_label_system.py` | 导出标注用标准表（Excel+CSV）到 `output/` |
| `label_project/run_label_update.sh` / `update_clean_data.command` | 一键备份并重建白名单/聚类/标准表 |
| `backend/main.py` | 表 `opinion` 增加 `v3_label_meta`（JSON 文本），上传 CSV 可选列 `v3_label_meta` 或 `标签体系JSON` |
| `frontend/.../OpinionReview.vue` | 有 V3 元数据时展示一级绿/二级蓝/三级聚类高亮与匹配类型 |

## 三、准确率与数据

- 离线指标以 `offline_validate.py` 输出 `validation_result.csv` 及控制台汇总为准。
- 线上展示依赖导入时是否写入 **v3_label_meta**；旧数据无该列时界面自动回退为「分类 + 关键词」旧版。

## 四、使用说明（简要）

1. **更新金标白名单与聚类**（Mac）：双击 `label_project/update_clean_data.command`，或终端执行 `./run_label_update.sh`（需 `testdata3.csv` 与 `label_hierarchy_final.json` 路径正确）。
2. **导出标注用标准表**：`python3 label_project/export_standard_label_system.py`，输出见 `output/standard_label_system_for_annotators.xlsx`。
3. **离线全量验证**：`python3 label_project/offline_validate.py -i testdata3.csv -n 0`（需 Ollama 与模型时去掉 `--no-llm`）。
4. **后端**：首次启动自动 `ALTER TABLE` 增加 `v3_label_meta`；上传 CSV 若含 `v3_label_meta` 列则写入 JSON。

## 五、兼容性

- 未改 VOC 线上业务主流程语义；旧 CSV 无 `v3_label_meta` 时行为与 v1.2 一致。
- 前端在无 V3 数据时保持原列表展示。
