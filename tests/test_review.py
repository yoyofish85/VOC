# -*- coding: utf-8 -*-
"""深度分类、人工复核（暂存/确认）、批量接口、年度归档与清洗库回流（测试隔离目录）。"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

from tests.conftest import assert_ok_json, build_csv_bytes, get_list, upload_csv


def _wait_keyword_backfill(api_client: httpx.Client, oid: str, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        lst = get_list(api_client, searchKey=oid, size=5)
        rows = lst.get("data") or []
        if rows and (rows[0].get("extracted_keywords") or ""):
            return
        time.sleep(0.5)


@pytest.fixture
def voc_artifacts_dir() -> Path:
    p = os.environ.get("VOC_TEST_ARTIFACTS_DIR")
    assert p, "VOC_TEST_ARTIFACTS_DIR 未设置（应由 app_launcher --verify 注入）"
    return Path(p)


def test_batch_classify_no_llm_subset(api_client: httpx.Client, unique_suffix: str) -> None:
    oid = f"CLS_{unique_suffix}"
    body = upload_csv(
        api_client,
        build_csv_bytes(
            [
                {
                    "舆情编号": oid,
                    "舆情中文": "车机系统卡顿，导航经常退出。",
                    "舆情时间": "2025-04-01",
                    "一级标签": "",
                    "渠道": "测试",
                }
            ]
        ),
    )
    assert_ok_json(body)
    batch = body["batch_id"]
    r = api_client.post(
        "/batch_classify",
        json={"upload_batch": batch, "use_llm": False, "async": False},
    )
    assert r.status_code == 200
    out = r.json()
    assert out.get("code") == 200
    assert (out.get("updated") or 0) >= 1
    lst = get_list(api_client, upload_batch=batch, size=5)
    row = next(x for x in lst["data"] if x.get("opinion_id") == oid)
    meta = row.get("v3_label_meta") or ""
    assert meta, "分类后应写入 v3_label_meta"
    assert '"l1"' in meta or "'l1'" in meta


def test_draft_save_persists_review_status(api_client: httpx.Client, unique_suffix: str) -> None:
    oid = f"DRF_{unique_suffix}"
    body = upload_csv(
        api_client,
        build_csv_bytes(
            [
                {
                    "舆情编号": oid,
                    "舆情中文": "刹车异响，需要检查制动系统。",
                    "舆情时间": "2025-04-02",
                    "一级标签": "产品质量类",
                    "渠道": "测试",
                }
            ]
        ),
    )
    assert_ok_json(body)
    r = api_client.post(
        "/draft_save_reviews",
        json={
            "reviews": [{"opinion_id": oid, "review_l1": "产品质量类", "review_l2": "", "review_note": ""}],
            "reviewer": "pytest",
            "with_reflow": False,
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert_ok_json(out)
    assert (out.get("saved") or 0) >= 1
    lst = get_list(api_client, searchKey=oid, size=5)
    row = next(x for x in lst["data"] if x.get("opinion_id") == oid)
    assert int(row.get("review_status") or 0) == 1


def test_confirm_review_batch(api_client: httpx.Client, unique_suffix: str) -> None:
    rows_csv = [
        {
            "舆情编号": f"BAT_{unique_suffix}_0",
            "舆情中文": "服务态度很好，点赞。",
            "舆情时间": "2025-04-03",
            "一级标签": "非问题",
            "渠道": "测试",
        },
        {
            "舆情编号": f"BAT_{unique_suffix}_1",
            "舆情中文": "希望增加座椅通风配置。",
            "舆情时间": "2025-04-03",
            "一级标签": "体验需求类",
            "渠道": "测试",
        },
    ]
    body = upload_csv(api_client, build_csv_bytes(rows_csv))
    assert_ok_json(body)
    ids = [r["舆情编号"] for r in rows_csv]
    r = api_client.post(
        "/confirm_review",
        json={
            "reviews": [
                {"opinion_id": ids[0], "review_l1": "非问题", "review_l2": "", "review_note": ""},
                {"opinion_id": ids[1], "review_l1": "体验需求类", "review_l2": "", "review_note": ""},
            ],
            "reviewer": "pytest",
            "with_reflow": False,
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert_ok_json(out)
    for oid in ids:
        lst = get_list(api_client, searchKey=oid, size=5)
        row = next(x for x in lst["data"] if x.get("opinion_id") == oid)
        assert int(row.get("review_status") or 0) == 1


def test_save_to_yearly_and_data_clear(
    api_client: httpx.Client, unique_suffix: str, voc_artifacts_dir: Path
) -> None:
    """全批次已复核后归档；清洗库写入隔离目录；关闭金标合并以免污染 label_project。"""
    assert os.environ.get("VOC_REFLOW_MERGE_GOLD") == "0"
    oid = f"YR_{unique_suffix}"
    body = upload_csv(
        api_client,
        build_csv_bytes(
            [
                {
                    "舆情编号": oid,
                    "舆情中文": "年度归档链路测试文本。",
                    "舆情时间": "2025-05-01",
                    "一级标签": "咨询",
                    "渠道": "测试",
                }
            ]
        ),
    )
    assert_ok_json(body)
    batch = body["batch_id"]
    r = api_client.post(
        "/confirm_review",
        json={
            "reviews": [{"opinion_id": oid, "review_l1": "咨询", "review_l2": "", "review_note": ""}],
            "reviewer": "pytest",
            "with_reflow": True,
        },
    )
    assert r.status_code == 200
    assert_ok_json(r.json())
    _wait_keyword_backfill(api_client, oid)
    r2 = api_client.post(
        "/save_to_yearly_table",
        json={"upload_batch": batch, "reviewer": "pytest"},
    )
    assert r2.status_code == 200
    y = r2.json()
    assert_ok_json(y)
    clear_path = voc_artifacts_dir / "data_clear.csv"
    assert clear_path.is_file(), "应写入隔离目录下的 data_clear.csv"
    text = clear_path.read_text(encoding="utf-8-sig", errors="ignore")
    assert oid in text
