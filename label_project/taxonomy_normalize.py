# -*- coding: utf-8 -*-
"""V1.5 一级四类 canonical 与历史别名（与 gold / hierarchy 键一致）。"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Set, Tuple

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore

# 固定四类（展示与下拉顺序）
CANONICAL_L1_LABELS = ("产品质量类", "体验需求类", "服务类", "非问题")

NON_ISSUE_L1 = "非问题"
# 二级准确率仅统计以下三类；「非问题」一级正确即视为识别成功
L2_EVAL_L1 = frozenset({"产品质量类", "服务类", "体验需求类"})

_L1_ALIAS_TO_CANON = {
    "产品质量类": "产品质量类",
    "体验需求类": "体验需求类",
    "服务类": "服务类",
    "销售服务类": "服务类",
    "非问题": "非问题",
    "质量问题": "产品质量类",
    "产品质量": "产品质量类",
    "质量类": "产品质量类",
    "产品问题": "产品质量类",
    "体验需求": "体验需求类",
    "需求类": "体验需求类",
    "营销服务": "服务类",
    "服务问题": "服务类",
    "咨询类": "非问题",
    "咨询": "非问题",
    "其他非问题": "非问题",
}


def try_canonicalize_l1(s: Any) -> Optional[str]:
    """若可识别为历史/标准写法则返回四类之一，否则 None（用于清洗表等避免误映射）。"""
    if s is None:
        return None
    if pd is not None and isinstance(s, float) and pd.isna(s):
        return None
    t = re.sub(r"\s+", "", str(s).strip())
    if not t:
        return None
    if t in _L1_ALIAS_TO_CANON:
        return _L1_ALIAS_TO_CANON[t]
    if t in CANONICAL_L1_LABELS:
        return t
    if "质量" in t and ("产品" in t or t == "质量问题"):
        return "产品质量类"
    if t == "体验" or ("体验" in t and "需求" in t):
        return "体验需求类"
    if "营销" in t and "服务" in t:
        return "服务类"
    if t in ("售后", "销售服务", "销售服务类"):
        return "服务类"
    if "非问题" in t or t.endswith("咨询"):
        return "非问题"
    return None


def canonicalize_l1_label(s: Any) -> str:
    """任意来源一级统一为四类之一；无法识别时默认「服务类」（与旧版非法一级兜底一致）。"""
    r = try_canonicalize_l1(s)
    if r:
        return r
    if s is None or s == "":
        return "服务类"
    if pd is not None and isinstance(s, float) and pd.isna(s):
        return "服务类"
    t = re.sub(r"\s+", "", str(s).strip())
    if not t:
        return "服务类"
    return "服务类"


def l1_values_for_filter(canon: str) -> List[str]:
    """筛选 SQL：标准名 + 已知历史别名。"""
    c = canonicalize_l1_label(canon)
    out: Set[str] = {c}
    for alias, co in _L1_ALIAS_TO_CANON.items():
        if co == c:
            out.add(alias)
    return sorted(out)


def is_non_issue_l1(l1: Any) -> bool:
    return canonicalize_l1_label(l1) == NON_ISSUE_L1


def l2_required_for_l1(l1: Any) -> bool:
    return canonicalize_l1_label(l1) in L2_EVAL_L1


def strip_non_issue_l2(l1: Any, l2: Any) -> Tuple[str, str]:
    """复核/模型落库前：一级为「非问题」时不保留二级标签。"""
    l1_raw = "" if l1 is None else str(l1).strip()
    if not l1_raw:
        l2s = "" if l2 is None else str(l2).strip()
        return "", l2s
    l1c = canonicalize_l1_label(l1_raw)
    l2s = "" if l2 is None else str(l2).strip()
    if l1c == NON_ISSUE_L1:
        return l1c, ""
    return l1c, l2s
