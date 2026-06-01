# -*- coding: utf-8 -*-
"""
将 data/labeled/data_clear.csv（或旧版 data_clean_project）中已人工复核的清洗数据融入 label_hierarchy_final.json：
- 对每条有效记录用 LabelMatcher 从「原文」推断三级标签并累加频次
- 不删除原有条目，仅合并频次与可选补充关键词
"""

import json
import os
import shutil
import sys
from datetime import datetime
from typing import Optional

import pandas as pd

_BASE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_BASE, ".."))
DEFAULT_CLEAN = os.path.join(_ROOT, "data", "labeled", "data_clear.csv")
FALLBACK_LEGACY = os.path.join(_ROOT, "data_clean_project", "data_clear.csv")
FALLBACK_CLEAN = os.path.join(_ROOT, "data_clean_project", "data_clear_20260304_154149.csv")
HIERARCHY_PATH = os.path.join(_BASE, "label_hierarchy_final.json")

# 供 LabelMatcher 读取的可选别名（与 offline_validate 内建规则互补）
KEYWORD_ALIASES = {
    "产品质量类": {
        "座舱问题": {
            "HUD屏异常问题": ["hud", "抬头显示", "ar模式", "卡帧", "掉帧", "帧率", "简洁模式"],
            "CSD/HUD屏异常问题": ["hud", "抬头显示", "csd"],
            "蓝牙异常": ["蓝牙", "蓝牙断连", "蓝牙断开"],
        },
        "OTA更新问题": {
            "OTA后蓝牙电话故障": ["蓝牙", "ota", "ota更新", "ota升级"],
        },
        "车端充电问题": {
            "无法充电": ["无法充电", "充不进", "充电失败"],
            "车端充电-速度慢": ["充电慢", "充电功率"],
            "充电未按设定时间点开始或停止": ["预约充电", "定时充电", "未按设定"],
        },
        "LFC问题": {
            "闪充站问题": ["闪充", "超充", "地锁", "充电站"],
            "家充桩问题": ["家充", "家用桩"],
        },
    }
}


# 延迟导入避免循环
sys.path.insert(0, _BASE)
from offline_validate import LabelMatcher  # noqa: E402


def normalize_l1(s: str) -> Optional[str]:
    if pd.isna(s):
        return None
    t = str(s).replace(" ", "").strip()
    if t in ("非问题", "咨询类"):
        return None
    if t in ("产品质量类", "服务类", "体验需求类"):
        return t
    return None


def ensure_data_clear_file():
    if os.path.isfile(DEFAULT_CLEAN):
        return DEFAULT_CLEAN
    os.makedirs(os.path.dirname(DEFAULT_CLEAN), exist_ok=True)
    if os.path.isfile(FALLBACK_LEGACY):
        shutil.copy2(FALLBACK_LEGACY, DEFAULT_CLEAN)
        print(f"✅ 已复制 {FALLBACK_LEGACY} → {DEFAULT_CLEAN}")
        return DEFAULT_CLEAN
    if os.path.isfile(FALLBACK_CLEAN):
        shutil.copy2(FALLBACK_CLEAN, DEFAULT_CLEAN)
        print(f"✅ 已复制 {FALLBACK_CLEAN} → {DEFAULT_CLEAN}")
        return DEFAULT_CLEAN
    raise FileNotFoundError("未找到 data_clear.csv 或备用清洗库文件")


def merge():
    csv_path = ensure_data_clear_file()
    print(f"📂 读取: {csv_path}")
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    print(f"   行数: {len(df)}")

    with open(HIERARCHY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["_keyword_aliases"] = KEYWORD_ALIASES
    with open(HIERARCHY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    matcher = LabelMatcher(HIERARCHY_PATH)
    merged = 0
    skipped = 0

    for _, row in df.iterrows():
        l1m = normalize_l1(row.get("人工复核结果", row.get("人工分类", "")))
        if l1m is None:
            skipped += 1
            continue
        text = str(row.get("原文", "") or "")
        if not text.strip():
            skipped += 1
            continue
        m = matcher.match(text, None)
        if not m:
            skipped += 1
            continue
        l1, l2, l3 = m["level1"], m["level2"], m["level3"]
        if l1 not in data or l2 not in data[l1]:
            skipped += 1
            continue
        block = data[l1][l2]
        if not isinstance(block, dict):
            skipped += 1
            continue
        tags = block.setdefault("标签列表", [])
        detail = block.setdefault("详情", {})
        if l3 not in tags:
            tags.append(l3)
        detail[l3] = int(detail.get(l3, 0)) + 1
        block["频次"] = int(block.get("频次", 0)) + 1
        merged += 1

    # 元数据
    data["_清洗数据融合"] = {
        "融合时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "源文件": os.path.basename(csv_path),
        "成功融合条数": merged,
        "跳过条数": skipped,
    }

    # 刷新 _统计信息
    stats = {}
    for l1, l2d in data.items():
        if str(l1).startswith("_") or not isinstance(l2d, dict):
            continue
        n2 = sum(1 for k in l2d if not str(k).startswith("_") and isinstance(l2d[k], dict))
        n3 = 0
        for l2, b in l2d.items():
            if isinstance(b, dict) and "标签列表" in b:
                n3 += len(b.get("标签列表", []))
        stats[l1] = {"二级标签数": n2, "三级标签数": n3}
    data["_统计信息"] = stats

    with open(HIERARCHY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 已写回: {HIERARCHY_PATH}（融合 {merged} 条）")


if __name__ == "__main__":
    merge()
