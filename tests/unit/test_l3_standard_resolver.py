# -*- coding: utf-8 -*-
"""S5：定位 L3 标准层解析单元测试。"""
from __future__ import annotations

from label_project.l3_standard_resolver import (
    PENDING_L3,
    candidates_for,
    feature_enabled,
    is_excluded,
    pick_standard_l3,
    resolve_l3_with_standard,
)


def test_feature_flag_default_off(monkeypatch):
    monkeypatch.delenv("VOC_L3_STANDARD_V1", raising=False)
    assert feature_enabled() is False
    monkeypatch.setenv("VOC_L3_STANDARD_V1", "1")
    assert feature_enabled() is True


def test_four_hotspot_l2_have_candidates():
    for l1, l2 in [
        ("服务类", "销售服务问题"),
        ("服务类", "售后服务问题"),
        ("产品质量类", "座舱问题"),
        ("产品质量类", "APP问题"),
    ]:
        cands = candidates_for(l1, l2)
        assert len(cands) >= 10, (l1, l2, len(cands))


def test_excluded_tags_never_picked():
    assert is_excluded("产品质量类", "钥匙问题", "数字钥匙不能启动车辆")
    text = "数字钥匙不能启动车辆，完全打不开"
    # even if phrase in text, excluded tag must not win as itself
    picked = pick_standard_l3(text, "产品质量类", "钥匙问题")
    assert picked != "数字钥匙不能启动车辆"


def test_pick_standard_l3_from_text():
    hit = pick_standard_l3("车机异常黑屏重启", "产品质量类", "座舱问题")
    assert hit == "车机异常"


def test_pick_standard_l3_fuzzy_nav_drift():
    hit = pick_standard_l3("用户反馈车机导航位置漂移", "产品质量类", "座舱问题")
    assert hit == "导航定位漂移"


def test_resolve_returns_pending_when_no_hit():
    out = resolve_l3_with_standard(
        "今天天气不错去兜风",
        "产品质量类",
        "座舱问题",
        enabled=True,
    )
    assert out is not None
    assert out["level3"] == PENDING_L3
    assert out["match_type"] == "l3_pending"
    assert out["level1"] == "产品质量类"
    assert out["level2"] == "座舱问题"


def test_resolve_disabled_returns_none():
    assert (
        resolve_l3_with_standard(
            "车机异常",
            "产品质量类",
            "座舱问题",
            enabled=False,
        )
        is None
    )


def test_resolve_does_not_touch_non_focus_l1():
    out = resolve_l3_with_standard(
        "随便问问",
        "非问题",
        "咨询与表扬",
        enabled=True,
    )
    assert out is None
