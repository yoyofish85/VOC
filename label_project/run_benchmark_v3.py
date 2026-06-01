# -*- coding: utf-8 -*-
"""
离线压测：5628 全量（默认 --no-llm 测吞吐；加 --with-llm 需 Ollama）
输出 Markdown 片段到 stdout，可重定向到 docs/VOC_V1.5_压测报告.md
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

_BASE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_BASE, ".."))
DEFAULT_CSV = os.path.join(_ROOT, "testdata3.csv")
OUT_CSV = os.path.join(_ROOT, "output", "benchmark_run_result.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", default=DEFAULT_CSV)
    ap.add_argument("--with-llm", action="store_true", help="启用 LLM（需 Ollama）；默认关闭以测吞吐")
    args = ap.parse_args()
    use_llm = bool(args.with_llm)

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    py = os.path.join(_BASE, "offline_validate.py")
    cmd = [
        sys.executable,
        py,
        "-i",
        args.input,
        "-n",
        "0",
        "-o",
        OUT_CSV,
    ]
    if not use_llm:
        cmd.append("--no-llm")

    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=_BASE)
    elapsed = time.perf_counter() - t0

    n = 0
    try:
        import pandas as pd

        df = pd.read_csv(OUT_CSV, encoding="utf-8-sig")
        n = len(df)
    except Exception:
        pass

    per = (elapsed / n * 1000) if n else 0
    print(f"""## 自动化压测（run_benchmark_v3.py）

| 项目 | 值 |
|------|-----|
| 数据文件 | `{args.input}` |
| 样本数 | {n} |
| LLM | {'开启' if use_llm else '关闭（--no-llm）'} |
| 总耗时(s) | {elapsed:.2f} |
| 均摊(ms/条) | {per:.2f} |
| 结果 CSV | `{OUT_CSV}` |

> 说明：MacBook Air M4 上请以本机实测为准；开启 LLM 时耗时随 Ollama/模型负载变化。

进程退出码: {r.returncode}
""")


if __name__ == "__main__":
    main()
