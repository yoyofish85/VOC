# S8 开发机聚类抽样说明

**日期**：2026-09-24  
**命令**：`python3 performance_evaluation/run_complaint_clusters.py --force-hashing --limit 400`  
**嵌入**：`hashing_ngram3_d256`（本地无 bge-m3 时的降级路径）

## 结果摘要

| 项 | 值 |
|----|----|
| sample_n | 400 |
| stored_vectors | 400（`opinion_embedding.db`） |
| cluster_n | 7 |
| cross_l2_n | 4 |
| cross_l2_ge3_n | 3 |
| gate_ok | true |

## 注意

- hashing 用于**打通存储/聚类/下钻**，簇纯度以服务器 `bge-m3` + 人工抽检为准。
- 未改写 `opinion` 分类字段。
