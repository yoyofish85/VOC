#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运维诊断：调用 GET /api/health/detail，输出可读健康报告。

用法：
  python3 performance_evaluation/health_check.py
  python3 performance_evaluation/health_check.py --base-url http://127.0.0.1:8000
  VOC_BASE_URL=http://127.0.0.1:8000 python3 performance_evaluation/health_check.py --json

依赖：后端已启动（app_launcher.py 或 uvicorn main:app）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

try:
    import httpx
except ImportError:
    print("缺少 httpx，请执行: pip install httpx", file=sys.stderr)
    sys.exit(2)

DEFAULT_BASE = os.environ.get("VOC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def _section(title: str) -> str:
    line = "─" * 56
    return f"\n{line}\n  {title}\n{line}"


def format_report(data: Dict[str, Any]) -> str:
    lines: list[str] = []
    status = data.get("status", "unknown")
    icon = "✓" if status == "healthy" else "!"
    lines.append(f"{'=' * 56}")
    lines.append(f"  VOC 系统健康诊断  [{icon} {status.upper()}]")
    lines.append(f"{'=' * 56}")

    db = data.get("db") or {}
    lines.append(_section("数据库"))
    if db.get("error"):
        lines.append(f"  错误: {db['error']}")
    else:
        lines.append(f"  路径:           {db.get('path', '—')}")
        lines.append(f"  大小:           {_fmt_bytes(int(db.get('size_bytes') or 0))}")
        lines.append(f"  integrity_check: {db.get('integrity_check', '—')}")
        lines.append(f"  journal_mode:   {db.get('journal_mode', '—')}")

    stale = data.get("stale_reflow")
    lines.append(_section("回流积压（>24h 未同步）"))
    if isinstance(stale, dict) and stale.get("error"):
        lines.append(f"  错误: {stale['error']}")
    else:
        cnt = int(stale or 0)
        flag = "需关注" if cnt > 0 else "正常"
        lines.append(f"  积压行数: {cnt}  [{flag}]")

    lines.append(_section("清洗库与失败审计"))
    clear_rows = data.get("data_clear_rows", -1)
    fail_cnt = data.get("reflow_failures_count", -1)
    lines.append(f"  data_clear.csv 数据行: {clear_rows if clear_rows >= 0 else '读取失败'}")
    lines.append(f"  reflow_failures.jsonl 记录数: {fail_cnt if fail_cnt >= 0 else '读取失败'}")

    annual_n = data.get("annual_csv_files")
    lines.append(_section("年度 CSV"))
    if isinstance(data.get("annual_csv"), dict) and data["annual_csv"].get("error"):
        lines.append(f"  错误: {data['annual_csv']['error']}")
    else:
        lines.append(f"  文件数: {annual_n if annual_n is not None else 0}")
        for p in data.get("annual_csv_paths") or []:
            lines.append(f"    · {p}")

    stats = data.get("stats") or {}
    lines.append(_section("舆情库统计"))
    if stats.get("error"):
        lines.append(f"  错误: {stats['error']}")
    else:
        lines.append(f"  总行数:              {stats.get('total_rows', 0)}")
        lines.append(f"  已复核:              {stats.get('reviewed', 0)}")
        lines.append(f"  待回流 (synced=0):   {stats.get('not_reflowed_yet', 0)}")
        lines.append(f"  已回流 (synced=1):   {stats.get('reflow_synced_1', 0)}")
        lines.append(f"  已归档 (synced=2):   {stats.get('reflow_synced_2_archived', 0)}")
        failed = stats.get("reflow_synced_minus1_failed")
        if failed is not None:
            lines.append(f"  回流失败 (synced=-1): {failed}")

    if status == "degraded":
        lines.append(_section("建议"))
        if isinstance(stale, int) and stale > 0:
            lines.append(f"  · 有 {stale} 条复核超过 24h 仍未回流，检查后台线程与 data_clear.csv 写权限")
        if fail_cnt and isinstance(fail_cnt, int) and fail_cnt > 0:
            lines.append(f"  · 查看失败明细: GET /api/reflow_failures 或 {DEFAULT_BASE}/api/reflow_failures")
        if db.get("error") or db.get("integrity_check") not in (None, "ok"):
            lines.append("  · 数据库完整性异常，考虑备份后执行 PRAGMA integrity_check 全量修复")

    lines.append("")
    return "\n".join(lines)


def fetch_detail(base_url: str, timeout: float) -> Dict[str, Any]:
    url = f"{base_url}/api/health/detail"
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        r = client.get(url)
        r.raise_for_status()
        body = r.json()
    if body.get("code") != 200:
        raise RuntimeError(f"API 返回异常: code={body.get('code')} msg={body.get('msg')}")
    return body.get("data") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="VOC 系统健康诊断（/api/health/detail）")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="后端根 URL")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP 超时秒数")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON 而非可读报告")
    args = parser.parse_args()
    base = str(args.base_url).rstrip("/")

    try:
        data = fetch_detail(base, args.timeout)
    except httpx.ConnectError:
        print(f"无法连接后端: {base}", file=sys.stderr)
        print("请先启动服务，例如: python3 app_launcher.py", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"健康检查失败: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_report(data))

    return 0 if data.get("status") == "healthy" else 2


if __name__ == "__main__":
    sys.exit(main())
