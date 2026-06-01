# -*- coding: utf-8 -*-
"""v9.1 正向捕获（销售/试驾/售后好评 → 非问题）单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend"
LABEL = Path(__file__).resolve().parents[2] / "label_project"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(LABEL))

from qwen_ollama import (  # noqa: E402
    _eligible_for_positive_capture,
    apply_charging_domain_guard,
    apply_positive_consult_capture,
    apply_service_quality_guard,
    load_l2_whitelist,
)


def _capture(text: str, l1: str = "服务类", l2: str = "销售服务问题"):
    l2_map = load_l2_whitelist()
    nl1, nl2, ok = apply_positive_consult_capture(text, l1, l2, l2_map)
    return ok, nl1, nl2


def test_sales_praise_from_service():
    ok, l1, l2 = _capture("销售马女士热情有耐心,讲解很透彻到位")
    assert ok and l1 == "非问题" and l2 == ""


def test_testdrive_praise_from_product():
    ok, l1, l2 = _capture("试驾人员费经理很专业,服务很好", "产品质量类", "故障-通用")
    assert ok and l1 == "非问题" and l2 == ""


def test_aftersale_praise():
    ok, l1, l2 = _capture("保养体验很好,售后很专业", "服务类", "售后服务问题")
    assert ok and l1 == "非问题" and l2 == ""


def test_neutral_price_query():
    ok, l1, l2 = _capture("用户咨询是否有价格表", "服务类", "销售服务问题")
    assert ok and l1 == "非问题"


def test_short_benign():
    assert _eligible_for_positive_capture("很好")
    ok, l1, _ = _capture("很好", "产品质量类", "故障-通用")
    assert ok and l1 == "非问题"


def test_lfc_consult_allows_capture_v92():
    """v9.2：家充咨询 → 非问题（不再被 LFC 域一票否决）。"""
    assert _eligible_for_positive_capture("用户咨询家充桩什么时候到货")
    ok, l1, l2 = _capture("用户咨询家充桩什么时候到货", "服务类", "销售服务问题")
    assert ok and l1 == "非问题" and l2 == ""


def test_lfc_complaint_still_blocks_capture():
    assert not _eligible_for_positive_capture("占位费不认可,充电跳枪无法完成")
    ok, l1, _ = _capture("占位费不认可,充电跳枪无法完成", "产品质量类", "LFC问题")
    assert not ok and l1 == "产品质量类"


def test_complaint_blocks_capture():
    assert not _eligible_for_positive_capture("售后多次反映没解决，态度差，要求退款")
    ok, _, _ = _capture("售后多次反映没解决，态度差，要求退款")
    assert not ok


def test_service_quality_blocks_capture():
    assert not _eligible_for_positive_capture("试驾回访,讲解少,还没客户自己了解的多")
    ok, _, _ = _capture("试驾回访,讲解少,还没客户自己了解的多")
    assert not ok


def test_negation_blocks_capture():
    assert not _eligible_for_positive_capture("不是很好,服务一般")
    ok, _, _ = _capture("不是很好,服务一般")
    assert not ok


def test_already_nonissue_empty_unchanged():
    ok, l1, l2 = _capture("销售很好", "非问题", "")
    assert not ok and l1 == "非问题" and l2 == ""


def test_charging_guard_after_nonissue():
    l2_map = load_l2_whitelist()
    l1, l2, hit = apply_charging_domain_guard(
        "用户咨询家充桩什么时候到货", "非问题", "", l2_map
    )
    assert hit and l1 == "产品质量类"


def test_service_guard_after_nonissue():
    l2_map = load_l2_whitelist()
    l1, _, hit = apply_service_quality_guard(
        "试驾回访,讲解少,还没客户自己了解的多", "非问题", "", l2_map
    )
    assert hit and l1 == "服务类"


def test_guard_order_praise_wins_over_wrong_service():
    """14B=服务类 + 销售好评 → 最终非问题（正向捕获在守卫之后）。"""
    text = "销售顾问小李热情专业,客户很满意"
    l2_map = load_l2_whitelist()
    l1, l2 = "服务类", "销售服务问题"
    _, _, sg = apply_service_quality_guard(text, l1, l2, l2_map)
    assert not sg
    nl1, nl2, ok = apply_positive_consult_capture(text, l1, l2, l2_map)
    assert ok and nl1 == "非问题" and nl2 == ""


def test_announcement_capture():
    ok, l1, _ = _capture("FOR ME 正式发布开启预售", "产品质量类", "故障-通用")
    assert ok and l1 == "非问题"
