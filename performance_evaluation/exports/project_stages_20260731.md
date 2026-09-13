# VOC 各阶段识别准确率（项目汇报）

- 生成时间：2026-07-31 11:12
- 数据库：`/Users/yuchao/Documents/AI Agent/VOC_V1.5/src/backend/opinion_review.db`
- 统计窗口：近 **7** 天已复核
- 全库已复核总数：**34** 条

## A. 全库已复核（当前线上 v3）

- L1：**0/0 = 0.00%**
- L2：**0/0 = 0.00%**
- 口径：人工 review_l1/l2 vs 库内 v3_label_meta（含后规则结果）

## B. 近 7 天已复核（本周实测）

- 样本数：**0** 条（参与 L1 评估 0 条）
- L1：**0/0 = N/A**
- L2：**0/0 = N/A**
- Top L1 错误：

## C. 规则重放 dry-run

- （跳过：近窗口无已复核样本或未启用 --replay）

## D. 近窗口匹配方式分布（v3_match_type）

- （无）

## E. 是否需调整（建议）

- 近窗口仅 0 条已复核，样本偏少，汇报时建议同时给出全库口径。
- 近窗口 L1 <80%，建议导出 L1 错误切片并做 targeted 规则/Prompt 迭代。

## 附：单命令复验

```bash
python3 performance_evaluation/evaluate_accuracy.py
python3 performance_evaluation/eval_batch_accuracy.py --since-days 7
python3 performance_evaluation/weekly_evolution_report.py
python3 performance_evaluation/eval_recent_rule_candidate.py --since-days 7 --max-l1-broken 8 --max-l2-broken 3
```
