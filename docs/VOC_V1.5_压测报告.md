# VOC V1.5 · 压测报告（v3 离线）

## 1. 环境

- 项目：`label_project/offline_validate.py`
- 默认数据：`testdata3.csv`（5628 条）
- **默认压测命令（无 LLM，测规则+聚类吞吐）**：

```bash
cd label_project
python3 run_benchmark_v3.py
```

- **含 LLM**（需本机 Ollama 已拉取模型）：

```bash
python3 run_benchmark_v3.py --with-llm
```

## 2. 指标说明

| 指标 | 含义 |
|------|------|
| 总耗时 | 子进程跑 `offline_validate.py` 全程 wall time |
| 均摊 ms/条 | 总耗时 / 样本数 |
| 结果文件 | `output/benchmark_run_result.csv` |

## 3. 本次记录（模板）

> 以下为模板占位，请在本机执行 `run_benchmark_v3.py` 后填入实测值。

| 项目 | 值 |
|------|-----|
| 数据文件 | `testdata3.csv` |
| 样本数 | 5628 |
| LLM | 关闭（--no-llm） 或 开启 |
| 总耗时(s) | （实测） |
| 均摊(ms/条) | （实测） |

## 4. 内存占用

- Python 进程峰值：建议用 macOS「活动监视器」在跑全量时观察；本脚本未内嵌 `psutil` 采样。
- 5628 条 pandas 读入 + 匹配器常驻，**建议** 8GB+ 内存环境。

## 5. Ollama / LLM

- 开启 LLM 时，耗时随 GPU/CPU、模型加载、并发 **强相关**；**MacBook Air M4** 建议以本机 `--with-llm` 实测为准。
- 模型未拉取时：离线脚本会 **提示并跳过 LLM**，不崩溃。

## 6. 结论

- **无 LLM**：全量用于回归规则与聚类路径，适合 CI/每日构建。
- **有 LLM**：用于抽样或夜间批跑，避免与线上高峰抢同一 Ollama 实例。
