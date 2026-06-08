# VOC_V1.5 模型识别准确率性能评估

本目录提供**只读**评估脚本，用于长期监控「模型预测 vs 人工复核」一致性，**不写入、不删除、不修改**业务数据库及 `data/` 下任何文件。

---

## 一、如何运行

### 前置条件

- Python 3（macOS / Linux 自带或已安装）
- 与正式环境相同的项目目录结构（至少包含 `label_project/taxonomy_normalize.py` 与 `src/backend/opinion_review.db`）

### 默认数据库路径

`{项目根}/src/backend/opinion_review.db`

### 命令

在项目根 `VOC_V1.5/` 下执行：

```bash
python3 performance_evaluation/evaluate_accuracy.py
```

指定数据库路径（例如服务器上副本）：

```bash
python3 performance_evaluation/evaluate_accuracy.py --db /path/to/opinion_review.db
```

只读调用本地 Qwen2.5-14B 对最近 N 条已复核数据做「影子评估」（不写回数据库）：

```bash
python3 performance_evaluation/evaluate_accuracy.py --qwen-shadow-limit 50
```

也可直接赋予可执行权限后运行：

```bash
chmod +x performance_evaluation/evaluate_accuracy.py
./performance_evaluation/evaluate_accuracy.py
```

---

## 二、报告保存在哪里

- 路径：`performance_evaluation/reports/`
- 文件名：`YYYYMMDD_HHMM_report.txt`（例如 `20260508_1530_report.txt`）
- 每次运行**追加**一份新报告，不覆盖历史报告

---

## 三、历史对比（基准）存在哪里

- 路径：`performance_evaluation/state/last_eval.json`
- **首次运行**：生成当前评估摘要，写入该文件，作为下次对比基准  
- **第二次及以后**：运行前读取「上次」结果，在控制台与报告中输出准确率变化、全库已复核条数累计差（近似「期间新增已复核量」）

> 对比中的「变化」为百分点差（本次% − 上次%）。累计条数差 = 本次 `COUNT(review_status=1)` − 上次记录值。

---

## 四、统计口径（重要）

| 项目 | 说明 |
|------|------|
| 有效样本 | `review_status = 1`（已复核） |
| 一级评估 | 人工 `review_l1` 非空，且模型侧能给出一级（V3 `l1` 或 `model_class`） |
| 二级评估 | 在通过一级筛选的集合中，**仅当** 人工 `review_l2` 非空时参与二级准确率 |
| 模型标签 | 与业务报表一致：优先 `v3_label_meta`，否则 `model_class` / `model_keyword`（二级取逗号前一段） |
| 一级比较 | 人机两侧均经 `canonicalize_l1_label` 规范到四类 |
| 二级比较 | 去掉多余空白后的字符串相等 |
| 14B 影子评估 | 可选参数 `--qwen-shadow-limit N`，实时调用本地 Ollama/Qwen2.5 14B 与人工复核比对，不写入 `v3_label_meta` |

未填人工一级、或无任何模型一级来源的行，**不计入**一级分母（报告中会给出跳过数量）。

---

## 五、如何迁移到服务器（离线）

1. 将整个 **`performance_evaluation/`** 文件夹随项目一并拷贝到服务器（或使用现有 `code_deploy` 全量同步后该目录已存在）。  
2. 确保 `label_project/` 仍在项目根下（脚本需导入 `taxonomy_normalize`）。  
3. 在服务器项目根执行：

```bash
python3 performance_evaluation/evaluate_accuracy.py --db "$(pwd)/src/backend/opinion_review.db"
```

**无需联网**，无 pip 额外依赖（仅用标准库 + 项目内 `label_project`）。

---

## 六、注意事项（安全）

1. **只读连接**：使用 SQLite `mode=ro` 打开数据库，不在评估流程中执行任何 `UPDATE/DELETE/INSERT`。  
2. **不修改** `opinion` 表、`data/`、年度 CSV、关键词文件。  
3. 评估结果与 **`state/last_eval.json`** 仅反映评估时快照；业务数据仍以库内为准。  
4. 若数据库路径与默认不一致，务必使用 `--db` 指向**生产库副本**时同样安全；直接在只读挂载卷上运行亦可。  
5. `reports/`、`state/` 可加入备份策略；勿把含敏感舆情的报告提交到公开仓库。

---

## 七、目录结构

```
performance_evaluation/
├── README.md                 # 本说明
├── evaluate_accuracy.py       # 主程序（只读）
├── reports/                   # 文本报告（按时间生成）
│   └── .gitkeep
└── state/
    ├── .gitkeep
    └── last_eval.json         # 运行后生成：上次对比基准（可不入库）
```

---

---

## 八、进化闭环周报告（MVP P0）

在项目根执行（只读 DB）：

```bash
python3 performance_evaluation/weekly_evolution_report.py
python3 performance_evaluation/weekly_evolution_report.py --days 7 --db path/to/opinion_review.db
```

- 报告：`performance_evaluation/reports/YYYYMMDD_HHMM_weekly_evolution.txt`
- 上周快照：`performance_evaluation/state/evolution_weekly_last.json`
- Week0 基线：`performance_evaluation/state/evolution_baseline.json`

建议每周五流程：

```bash
python3 performance_evaluation/evaluate_accuracy.py
python3 performance_evaluation/export_l1_errors.py --since 7d
python3 performance_evaluation/eval_p1_subset.py --compare \
  --export-regression-fixable auto --export-other-fixable auto
python3 performance_evaluation/weekly_evolution_report.py
```

---

**版本**：随 VOC_V1.5 仓库维护。
