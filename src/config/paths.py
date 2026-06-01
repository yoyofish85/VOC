# -*- coding: utf-8 -*-
"""项目根与各数据目录（相对路径，支持 VOC_PROJECT_ROOT 覆盖）。"""
from __future__ import annotations

import os
from pathlib import Path


def _resolved_project_root() -> Path:
    explicit = os.environ.get("VOC_PROJECT_ROOT", "").strip()
    if explicit:
        return Path(explicit).resolve()
    # src/config/paths.py -> parent config -> parent src -> parent = 项目根
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _resolved_project_root()
SRC_DIR = PROJECT_ROOT / "src"
BACKEND_DIR = SRC_DIR / "backend"
DATA_DIR = PROJECT_ROOT / "data"
ANNUAL_DIR = Path(os.environ.get("VOC_ANNUAL_DIR", str(DATA_DIR / "annual"))).resolve()
LABELED_DIR = Path(os.environ.get("VOC_LABELED_DIR", str(DATA_DIR / "labeled"))).resolve()
UPLOADED_DIR = DATA_DIR / "uploaded"
TEMP_DATA_DIR = DATA_DIR / "temp"
LABEL_PROJECT_DIR = PROJECT_ROOT / "label_project"


def ensure_runtime_dirs() -> None:
    """确保数据子目录存在。"""
    for d in (ANNUAL_DIR, LABELED_DIR, UPLOADED_DIR, TEMP_DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)


# 旧版清洗库路径（兼容：若仅存在旧目录则仍可读）
LEGACY_DATA_CLEAN_DIR = PROJECT_ROOT / "data_clean_project"
LEGACY_DATA_CLEAR = LEGACY_DATA_CLEAN_DIR / "data_clear.csv"


def resolve_data_clear_csv() -> Path:
    """优先 data/labeled/data_clear.csv，否则回退旧路径。"""
    preferred = LABELED_DIR / "data_clear.csv"
    if preferred.is_file():
        return preferred
    if LEGACY_DATA_CLEAR.is_file():
        return LEGACY_DATA_CLEAR
    return preferred
