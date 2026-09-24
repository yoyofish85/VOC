# -*- coding: utf-8 -*-
"""S7：跨记录洞察与证据链单测。"""
from __future__ import annotations

from datetime import datetime, timedelta

from label_project.car_model_normalize import normalize_car_model, normalize_car_models
from label_project.cross_record_insights import (
    build_evidence_chain,
    choose_entity_key,
    compute_resolution,
    reconcile_evidence,
    run_cross_record_insights,
)
from label_project.theme_lookup import resolve_theme


def test_car_model_normalize():
    assert normalize_car_model("ELETRE") == "Eletre"
    assert normalize_car_model("emeya") == "EMEYA"
    assert normalize_car_model("EMEYA, Eletre") == "EMEYA"
    assert normalize_car_models("Emira, Eletre") == ["Emira", "Eletre"]


def test_theme_resolve_known_path():
    tid, name = resolve_theme("产品质量类", "车端充电问题", "无法充电")
    assert tid == "T-Q-02"
    assert "充电" in name


def _row(**kw):
    base = {
        "opinion_id": "1",
        "original_text": "车辆无法充电",
        "v3_l1": "产品质量类",
        "v3_l2": "车端充电问题",
        "v3_l3": "无法充电",
        "review_status": 0,
        "reflow_synced": 0,
        "vin": "VIN001",
        "phone": "13800000001",
        "car_model": "eletre",
        "country": "CN",
        "create_time": "2026-09-20 10:00:00",
        "v3_label_meta": "",
    }
    base.update(kw)
    return base


def test_evidence_chain_reconcile():
    rows = [
        _row(opinion_id="a", review_status=1, review_l1="产品质量类", review_l2="车端充电问题", review_l3="无法充电"),
        _row(opinion_id="b", v3_l3="充电中断"),
        _row(
            opinion_id="c",
            v3_l1="产品质量类",
            v3_l2="LFC问题",
            v3_l3="闪充站问题",
            original_text="闪充站故障",
        ),
        _row(opinion_id="a", original_text="重复 id 应去重"),  # duplicate id
    ]
    result = run_cross_record_insights(rows, previous_rows=[], window_days=30)
    assert result["gate_ok"] is True
    recon = result["reconcile"]
    assert recon["theme_l3_match"] and recon["theme_oid_match"]
    # 已复核 / 仅模型
    themes = {t["theme_id"]: t for t in result["evidence_chain"]}
    assert themes["T-Q-02"]["reviewed_count"] == 1
    assert themes["T-Q-02"]["model_only_count"] == 1
    assert themes["T-Q-02"]["count"] == 2
    assert len(themes["T-Q-02"]["opinion_ids"]) == 2


def test_resolution_repeat_by_vin():
    day0 = datetime(2026, 9, 1)
    rows = [
        _row(opinion_id="1", vin="V1", create_time=(day0).strftime("%Y-%m-%d %H:%M:%S")),
        _row(opinion_id="2", vin="V1", create_time=(day0 + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")),
        _row(opinion_id="3", vin="V2", create_time=(day0 + timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")),
    ]
    result = run_cross_record_insights(rows)
    assert result["meta"]["coverage"]["entity_key"] == "vin"
    assert result["resolution"]["repeat_entities"] == 1
    assert result["resolution"]["eligible_entities"] == 2
    assert result["resolution"]["repeat_rate"] == 0.5


def test_choose_entity_falls_back():
    rows = [{"vin": "", "phone": ""} for _ in range(10)]
    assert choose_entity_key(rows) == "text_repeat"


def test_meta_version_and_insights_shape():
    rows = [_row(opinion_id=str(i), vin=f"V{i}") for i in range(5)]
    result = run_cross_record_insights(rows, query={"source": "unit"})
    assert result["meta"]["theme_mapping_version"]
    assert "coverage" in result["meta"]
    assert "query" in result["meta"]
    for ins in result["insights"]:
        assert "template_id" in ins
        assert "evidence_sql" in ins
        assert "coverage" in ins


def test_parse_slash_datetime():
    from label_project.cross_record_insights import _parse_day

    d = _parse_day("2026/1/27 8:14")
    assert d is not None
    assert d.year == 2026 and d.month == 1 and d.day == 27

