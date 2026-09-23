# -*- coding: utf-8 -*-
"""定位 L3 标准层解析（产品质量类 / 服务类）。

功能开关：VOC_L3_STANDARD_V1=1 时启用。
- 从 gold_l3_standard_v1 + l3_alias_v1 取候选
- 命中则返回具体 L3
- 未命中写「其他-待归类」，不再写「通用」
- 不改变 L1/L2
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

LABEL_DIR = Path(__file__).resolve().parent
DEFAULT_STANDARD = LABEL_DIR / "gold_l3_standard_v1.json"
DEFAULT_ALIAS = LABEL_DIR / "l3_alias_v1.json"
PENDING_L3 = "其他-待归类"
FOCUS_L1 = frozenset({"产品质量类", "服务类"})
FOCUS_L2_FOR_GENERIC = frozenset(
    {"销售服务问题", "售后服务问题", "座舱问题", "APP问题"}
)


def feature_enabled(env: Optional[dict] = None) -> bool:
    src = env if env is not None else os.environ
    return str(src.get("VOC_L3_STANDARD_V1", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def load_standard_index(
    standard_path: str = str(DEFAULT_STANDARD),
    alias_path: str = str(DEFAULT_ALIAS),
) -> Dict[str, object]:
    standard = _load_json(Path(standard_path))
    alias_doc = _load_json(Path(alias_path)) if Path(alias_path).is_file() else {}

    by_l1_l2: Dict[str, Dict[str, List[str]]] = {}
    excluded = set()
    for item in (standard.get("_meta") or {}).get("excluded") or []:
        excluded.add((item.get("l1"), item.get("l2"), item.get("l3")))

    for l1, l2m in (standard.get("by_l1_l2") or {}).items():
        by_l1_l2.setdefault(l1, {})
        for l2, items in (l2m or {}).items():
            by_l1_l2[l1][l2] = [str(x).strip() for x in items if str(x).strip()]

    # alias → target within same L2
    alias_to_target: Dict[Tuple[str, str, str], str] = {}
    for row in alias_doc.get("aliases") or []:
        l1, l2, l3 = row.get("l1"), row.get("l2"), row.get("l3")
        status = row.get("status")
        if status == "excluded":
            excluded.add((l1, l2, l3))
            continue
        target = row.get("target_l3")
        if status == "mapped" and target:
            alias_to_target[(l1, l2, l3)] = str(target)

    l2_aliases = standard.get("l2_aliases") or {}
    return {
        "by_l1_l2": by_l1_l2,
        "excluded": frozenset(excluded),
        "alias_to_target": alias_to_target,
        "l2_aliases": l2_aliases,
    }


def candidates_for(l1: str, l2: str, index: Optional[dict] = None) -> List[str]:
    idx = index or load_standard_index()
    l1 = (l1 or "").strip()
    l2 = (l2 or "").strip()
    if l1 not in FOCUS_L1:
        return []
    by = idx["by_l1_l2"].get(l1) or {}
    return list(by.get(l2) or [])


def resolve_alias(l1: str, l2: str, l3: str, index: Optional[dict] = None) -> Optional[str]:
    idx = index or load_standard_index()
    key = ((l1 or "").strip(), (l2 or "").strip(), (l3 or "").strip())
    if key in idx["excluded"]:
        return None
    return idx["alias_to_target"].get(key)


def is_excluded(l1: str, l2: str, l3: str, index: Optional[dict] = None) -> bool:
    idx = index or load_standard_index()
    return ((l1 or "").strip(), (l2 or "").strip(), (l3 or "").strip()) in idx["excluded"]


def _best_substring_hit(text: str, candidates: Sequence[str]) -> Optional[str]:
    t = text or ""
    if not t or not candidates:
        return None
    hits = [c for c in candidates if len(c) >= 2 and c in t]
    if hits:
        hits.sort(key=lambda x: (-len(x), x))
        return hits[0]
    return None


def _bigram_score(text: str, cand: str) -> int:
    """粗粒度中文重合分：候选串的 2-gram 命中数。"""
    if not text or len(cand) < 2:
        return 0
    score = 0
    for i in range(len(cand) - 1):
        bg = cand[i : i + 2]
        if bg in text:
            score += 1
    return score


def _best_fuzzy_hit(text: str, candidates: Sequence[str], *, min_score: int = 2) -> Optional[str]:
    t = text or ""
    if not t or not candidates:
        return None
    exact = _best_substring_hit(t, candidates)
    if exact:
        return exact
    scored = []
    for c in candidates:
        if len(c) < 2:
            continue
        s = _bigram_score(t, c)
        # 至少命中 2 个 bigram，或短标签几乎全覆盖
        need = min_score if len(c) >= 4 else max(1, len(c) - 1)
        if s >= need:
            scored.append((s, len(c), c))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return scored[0][2]


def pick_standard_l3(
    text: str,
    l1: str,
    l2: str,
    *,
    index: Optional[dict] = None,
) -> Optional[str]:
    """从标准/别名候选中选 L3；排除项永不返回。"""
    idx = index or load_standard_index()
    l1 = (l1 or "").strip()
    l2 = (l2 or "").strip()
    if l1 not in FOCUS_L1:
        return None

    cands = candidates_for(l1, l2, idx)
    hit = _best_fuzzy_hit(text, cands)
    if hit and not is_excluded(l1, l2, hit, idx):
        return hit

    # try alias phrases as extra keywords → map to target
    alias_phrases = [
        src
        for (a, b, src), tgt in idx["alias_to_target"].items()
        if a == l1 and b == l2 and tgt in cands
    ]
    alias_hit = _best_fuzzy_hit(text, alias_phrases)
    if alias_hit:
        mapped = resolve_alias(l1, l2, alias_hit, idx)
        if mapped and mapped in cands and not is_excluded(l1, l2, mapped, idx):
            return mapped
    return None


def resolve_l3_with_standard(
    text: str,
    l1: str,
    l2: str,
    *,
    enabled: Optional[bool] = None,
    index: Optional[dict] = None,
) -> Optional[dict]:
    """启用标准层时：命中具体 L3，或返回其他-待归类；未启用返回 None。"""
    if enabled is None:
        enabled = feature_enabled()
    if not enabled:
        return None
    l1 = (l1 or "").strip()
    l2 = (l2 or "").strip()
    if l1 not in FOCUS_L1:
        return None

    # only apply pending fallback when this L1/L2 has standard candidates
    # OR is one of the generic hotspots (even if sheet L2 alias)
    cands = candidates_for(l1, l2, index)
    if not cands and l2 not in FOCUS_L2_FOR_GENERIC:
        return None

    picked = pick_standard_l3(text, l1, l2, index=index)
    if picked:
        return {
            "level1": l1,
            "level2": l2,
            "level3": picked,
            "confidence": 0.58,
            "match_type": "standard_l3",
        }
    return {
        "level1": l1,
        "level2": l2,
        "level3": PENDING_L3,
        "confidence": 0.40,
        "match_type": "l3_pending",
    }
