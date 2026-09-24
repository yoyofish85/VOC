# -*- coding: utf-8 -*-
"""汇报主题查找：定位 L3 路径 → primary_theme。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_BASE = Path(__file__).resolve().parent
DEFAULT_MAPPING = _BASE / "l3_report_theme_mapping_v1.json"


def load_theme_mapping(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else DEFAULT_MAPPING
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=2)
def _cached_mapping(path_str: str) -> Dict[str, Any]:
    return load_theme_mapping(Path(path_str))


def mapping_version(mapping: Dict[str, Any]) -> str:
    meta = mapping.get("_meta") or {}
    return str(meta.get("theme_mapping_version") or meta.get("version") or "v1")


def resolve_theme(
    l1: str,
    l2: str,
    l3: str,
    mapping: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """返回 (theme_id, theme_name)；未命中归 T99。"""
    mp = mapping if mapping is not None else _cached_mapping(str(DEFAULT_MAPPING))
    themes = mp.get("themes") or {}
    path = f"{(l1 or '').strip()}/{(l2 or '').strip()}/{(l3 or '').strip()}"
    row = (mp.get("l3_mapping") or {}).get(path) or {}
    tid = str(row.get("primary_theme_id") or "T99")
    name = str((themes.get(tid) or {}).get("name") or ("其他-待归类" if tid == "T99" else tid))
    return tid, name


def theme_path(l1: str, l2: str, l3: str) -> str:
    return f"{(l1 or '').strip()}/{(l2 or '').strip()}/{(l3 or '').strip()}"
