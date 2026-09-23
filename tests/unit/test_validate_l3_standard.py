# -*- coding: utf-8 -*-
"""S3：定位 L3 标准层校验。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from label_project.validate_l3_standard import (
    EXPECTED_STANDARD_COUNT,
    build_validation_report,
    validate_l3_standard,
)

ROOT = Path(__file__).resolve().parents[2]
LABEL = ROOT / "label_project"
STANDARD_PATH = LABEL / "gold_l3_standard_v1.json"
ALIAS_PATH = LABEL / "l3_alias_v1.json"
WHITELIST_PATH = LABEL / "gold_l2_whitelist_v3.json"

EXCLUDED = {
    ("产品质量类", "座舱问题", "CSD/HUD屏异常问题"),
    ("产品质量类", "钥匙问题", "数字钥匙不能启动车辆"),
    ("产品质量类", "钥匙问题", "无法解锁，打不开车门"),
}


@pytest.fixture(scope="module")
def standard():
    assert STANDARD_PATH.is_file(), f"missing {STANDARD_PATH}"
    return json.loads(STANDARD_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def alias():
    assert ALIAS_PATH.is_file(), f"missing {ALIAS_PATH}"
    return json.loads(ALIAS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def whitelist():
    return json.loads(WHITELIST_PATH.read_text(encoding="utf-8"))


def test_standard_has_exact_214_unique_paths(standard):
    report = validate_l3_standard(standard)
    assert report["path_count"] == EXPECTED_STANDARD_COUNT
    assert report["duplicate_paths"] == []
    assert report["ok"] is True


def test_each_path_belongs_to_single_l1_l2(standard):
    report = validate_l3_standard(standard)
    assert report["multi_parent_l3"] == []


def test_scope_is_product_and_service_only(standard):
    l1s = set(standard["by_l1_l2"].keys())
    assert l1s == {"产品质量类", "服务类"}
    svc = sum(len(v) for v in standard["by_l1_l2"]["服务类"].values())
    prd = sum(len(v) for v in standard["by_l1_l2"]["产品质量类"].values())
    assert svc == 85
    assert prd == 129


def test_excluded_tags_not_in_standard(standard):
    paths = set()
    for l1, l2m in standard["by_l1_l2"].items():
        for l2, items in l2m.items():
            for l3 in items:
                paths.add((l1, l2, l3))
    for triple in EXCLUDED:
        assert triple not in paths, f"excluded tag still in standard: {triple}"


def test_l2_maps_to_whitelist_or_explicit_alias(standard, whitelist):
    report = validate_l3_standard(standard, whitelist=whitelist)
    assert report["unmapped_l2"] == []
    # known sheet-only L2 must appear in l2_aliases
    aliases = standard.get("l2_aliases") or {}
    for l2 in ["外饰问题", "故障告警", "灯类故障", "销售承诺未能兑现"]:
        assert l2 in aliases, f"missing l2 alias for {l2}"


def test_alias_file_covers_non_standard_candidates(standard, alias):
    std_paths = set()
    for l1, l2m in standard["by_l1_l2"].items():
        for l2, items in l2m.items():
            for l3 in items:
                std_paths.add(f"{l1}/{l2}/{l3}")
    entries = alias.get("aliases") or []
    assert len(entries) >= 300
    for e in entries:
        path = f"{e['l1']}/{e['l2']}/{e['l3']}"
        assert path not in std_paths
        assert e.get("status") in {"mapped", "candidate", "excluded"}


def test_build_validation_report_is_machine_readable(standard, whitelist):
    report = build_validation_report(standard, whitelist=whitelist)
    assert report["ok"] is True
    assert report["path_count"] == 214
    assert "l1_counts" in report
    assert "l2_alias_count" in report
