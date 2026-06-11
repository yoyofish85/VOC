#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署后健康自检：行数、WAL、import、前端 dist、/api/health。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "src" / "backend"
FRONTEND_DIST = ROOT / "src" / "frontend" / "dist" / "index.html"
MAIN_PY = BACKEND / "main.py"
REFLOW_PY = BACKEND / "reflow_service.py"

MAIN_LINE_RANGE = (2800, 4500)
REFLOW_LINE_RANGE = (150, 500)

BASE_URL = os.environ.get("VOC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DB_PATH = os.path.abspath(
    os.environ.get("VOC_DB_PATH", str(BACKEND / "opinion_review.db"))
)


def _line_count(path: Path) -> int:
    with path.open(encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def check_main_line_count() -> Tuple[bool, str]:
    if not MAIN_PY.is_file():
        return False, f"缺少 {MAIN_PY}"
    n = _line_count(MAIN_PY)
    lo, hi = MAIN_LINE_RANGE
    if lo <= n <= hi:
        return True, f"main.py 行数 {n}（预期 {lo}-{hi}）"
    return False, f"main.py 行数 {n} 超出预期 {lo}-{hi}"


def check_reflow_line_count() -> Tuple[bool, str]:
    if not REFLOW_PY.is_file():
        return False, f"缺少 {REFLOW_PY}"
    n = _line_count(REFLOW_PY)
    lo, hi = REFLOW_LINE_RANGE
    if lo <= n <= hi:
        return True, f"reflow_service.py 行数 {n}（预期 {lo}-{hi}）"
    return False, f"reflow_service.py 行数 {n} 超出预期 {lo}-{hi}"


def check_wal_mode() -> Tuple[bool, str]:
    if not os.path.isfile(DB_PATH):
        return False, f"数据库不存在: {DB_PATH}"
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    if str(mode).lower() == "wal":
        return True, f"journal_mode={mode} ({DB_PATH})"
    return False, f"journal_mode={mode}，期望 wal ({DB_PATH})"


def check_reflow_import() -> Tuple[bool, str]:
    sys.path.insert(0, str(BACKEND))
    try:
        from reflow_service import reflow_batch_rows  # noqa: F401

        return True, "from reflow_service import reflow_batch_rows OK"
    except Exception as e:
        return False, f"reflow_service import 失败: {e}"
    finally:
        if str(BACKEND) in sys.path:
            sys.path.remove(str(BACKEND))


def check_frontend_dist() -> Tuple[bool, str]:
    if FRONTEND_DIST.is_file():
        return True, f"存在 {FRONTEND_DIST}"
    return False, f"缺少 {FRONTEND_DIST}"


def _http_get_json(path: str) -> Tuple[int, dict]:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(body) if body.strip() else {}


def check_health_endpoint() -> Tuple[bool, str]:
    try:
        status, body = _http_get_json("/api/health")
        if status == 200 and body.get("code") == 200:
            return True, f"/api/health → {status}"
        return False, f"/api/health 异常: status={status} body={body!r}"
    except urllib.error.URLError as e:
        return False, f"/api/health 不可达 ({BASE_URL}): {e}"


def check_health_detail_endpoint() -> Tuple[bool, str]:
    try:
        status, body = _http_get_json("/api/health/detail")
        if status == 200:
            return True, f"/api/health/detail → {status}"
        return False, f"/api/health/detail 异常: status={status}"
    except urllib.error.URLError as e:
        return False, f"/api/health/detail 不可达 ({BASE_URL}): {e}"


CHECKS: List[Tuple[str, Callable[[], Tuple[bool, str]]]] = [
    ("main.py 行数", check_main_line_count),
    ("reflow_service.py 行数", check_reflow_line_count),
    ("SQLite WAL", check_wal_mode),
    ("reflow_service import", check_reflow_import),
    ("前端 dist/index.html", check_frontend_dist),
    ("/api/health", check_health_endpoint),
    ("/api/health/detail", check_health_detail_endpoint),
]


def main() -> int:
    print("=" * 60)
    print(" VOC_V1.5 · 部署健康自检")
    print("=" * 60)
    print(f"项目根: {ROOT}")
    print(f"API   : {BASE_URL}")
    print("")

    failed = 0
    for name, fn in CHECKS:
        ok, msg = fn()
        mark = "✓" if ok else "✗"
        print(f"  [{mark}] {name}: {msg}")
        if not ok:
            failed += 1

    print("")
    if failed:
        print(f"自检未通过: {failed}/{len(CHECKS)} 项失败")
        return 1
    print(f"自检全部通过 ({len(CHECKS)}/{len(CHECKS)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
