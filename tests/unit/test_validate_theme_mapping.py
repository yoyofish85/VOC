# -*- coding: utf-8 -*-
"""S4：汇报主题映射校验。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from label_project.validate_theme_mapping import (
    build_theme_l3_index,
    validate_theme_mapping,
)

ROOT = Path(__file__).resolve().parents[2]
LABEL = ROOT / "label_project"
STANDARD_PATH = LABEL / "gold_l3_standard_v1.json"
MAPPING_PATH = LABEL / "l3_report_theme_mapping_v1.json"


@pytest.fixture(scope="module")
def standard():
    return json.loads(STANDARD_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mapping():
    assert MAPPING_PATH.is_file(), f"missing {MAPPING_PATH}"
    return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))


def _standard_paths(standard):
    out = []
    for l1, l2m in standard["by_l1_l2"].items():
        for l2, items in l2m.items():
            for l3 in items:
                out.append(f"{l1}/{l2}/{l3}")
    return out


def test_theme_count_in_30_to_45(mapping):
    themes = {k: v for k, v in (mapping.get("themes") or {}).items() if k != "T99"}
    assert 30 <= len(themes) <= 45


def test_every_standard_path_has_exactly_one_primary(standard, mapping):
    report = validate_theme_mapping(mapping, standard)
    assert report["coverage"] == 1.0
    assert report["missing_paths"] == []
    assert report["duplicate_primary_assignments"] == []
    assert report["invalid_paths"] == []
    assert report["ok"] is True


def test_primary_theme_ids_are_valid(mapping, standard):
    report = validate_theme_mapping(mapping, standard)
    assert report["unknown_primary_theme_ids"] == []
    assert "T99" in (mapping.get("themes") or {})


def test_other_pending_theme_is_isolated(mapping):
    t99 = mapping["themes"]["T99"]
    assert t99["name"] == "其他-待归类"
    assert t99.get("counts_in_formal_total") is False
    # no standard path should use T99 as primary in v1
    for path, row in (mapping.get("l3_mapping") or {}).items():
        assert row["primary_theme_id"] != "T99", path


def test_forward_and_reverse_indexes(standard, mapping):
    paths = _standard_paths(standard)
    index = build_theme_l3_index(mapping)
    # forward
    for path in paths:
        tid = mapping["l3_mapping"][path]["primary_theme_id"]
        assert path in index[tid]
    # reverse: union equals all paths
    reverse_paths = sorted({p for ps in index.values() for p in ps if not p.startswith("T99")})
    # index may include only mapped themes
    all_mapped = sorted(mapping["l3_mapping"].keys())
    assert reverse_paths == all_mapped


def test_theme_definitions_have_boundary_fields(mapping):
    for tid, theme in mapping["themes"].items():
        if tid == "T99":
            continue
        assert theme.get("name")
        assert theme.get("l1") in {"产品质量类", "服务类"}
        assert theme.get("owner")
        assert theme.get("definition")
        assert "includes" in theme
        assert "excludes" in theme
