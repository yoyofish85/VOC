# -*- coding: utf-8 -*-
"""apply_classification_post_rules 重放兼容层（支持未同步 source/vin 的旧版 backend）。"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Tuple

_compat_warned = False


def apply_post_rules_with_context(
    text: str,
    l1: str,
    l2: str,
    l2_map: Dict[str, List[str]],
    *,
    source: str = "",
    vin: str = "",
) -> Tuple[str, str, Dict[str, bool]]:
    """调用 post-rule；旧版 qwen_ollama 无 source/vin 时自动降级。"""
    global _compat_warned
    from qwen_ollama import apply_classification_post_rules  # noqa: WPS433

    try:
        return apply_classification_post_rules(
            text, l1, l2, l2_map, source=source, vin=vin
        )
    except TypeError as exc:
        if "source" not in str(exc) and "vin" not in str(exc):
            raise
        if not _compat_warned:
            print(
                "[warn] qwen_ollama 未支持 source/vin；O2/O3 上下文规则不会生效。"
                "请同步 src/backend/qwen_ollama.py 与 classification_context_rules.py",
                file=sys.stderr,
            )
            _compat_warned = True
        return apply_classification_post_rules(text, l1, l2, l2_map)
