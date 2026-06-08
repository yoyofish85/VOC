# -*- coding: utf-8 -*-
"""评估脚本共用：解析 reviewed_at / create_time 与时间窗口。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Optional

_TS_PATTERNS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def parse_timestamp(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip().replace("/", "-").replace(".", "-")
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    if s.endswith("Z"):
        s = s[:-1]
    for fmt in _TS_PATTERNS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    return None


def row_review_time(reviewed_at: Any, create_time: Any) -> Optional[datetime]:
    return parse_timestamp(reviewed_at) or parse_timestamp(create_time)


def parse_since_days(since: str) -> int:
    """解析 --since：7d / 7 / 7days / 7 days。"""
    s = (since or "").strip().lower()
    m = re.match(r"^(\d+)\s*(d|day|days)?$", s)
    if not m:
        raise ValueError(f"无法解析时间窗口: {since!r}（示例: 7d）")
    return max(1, int(m.group(1)))


def cutoff_from_since_days(days: int, *, now: Optional[datetime] = None) -> datetime:
    ref = now or datetime.now()
    return ref - timedelta(days=days)
