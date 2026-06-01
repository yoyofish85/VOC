# -*- coding: utf-8 -*-
"""CSV 上传：正常、空文件、格式错误、去重、大样本。"""
from __future__ import annotations

import io

import httpx
import pytest

from tests.conftest import assert_ok_json, build_csv_bytes, get_list, upload_csv


def test_upload_single_row_ok(api_client: httpx.Client, unique_suffix: str) -> None:
    oid = f"UP1_{unique_suffix}"
    raw = build_csv_bytes(
        [
            {
                "舆情编号": oid,
                "舆情中文": "充电口接触不良，无法充电。",
                "舆情时间": "2025-01-01",
                "一级标签": "产品质量类",
                "渠道": "测试",
            }
        ]
    )
    body = upload_csv(api_client, raw)
    assert_ok_json(body)
    assert body.get("imported_count", 0) >= 1
    assert body.get("batch_id")
    lst = get_list(api_client, upload_batch=body["batch_id"], size=10)
    assert_ok_json(lst)
    ids = [r.get("opinion_id") for r in lst.get("data", [])]
    assert oid in ids


def test_upload_duplicate_skipped(api_client: httpx.Client, unique_suffix: str) -> None:
    oid = f"DUP_{unique_suffix}"
    row = {
        "舆情编号": oid,
        "舆情中文": "重复原文用于 MD5 去重测试",
        "舆情时间": "2025-01-02",
        "一级标签": "咨询",
        "渠道": "测试",
    }
    b1 = upload_csv(api_client, build_csv_bytes([row]))
    assert_ok_json(b1)
    b2 = upload_csv(api_client, build_csv_bytes([row]))
    assert_ok_json(b2)
    assert (b2.get("skipped_duplicates") or 0) >= 1
    assert b2.get("imported_count", 0) == 0


def test_upload_empty_file_rejected(api_client: httpx.Client) -> None:
    files = {"file": ("empty.csv", io.BytesIO(b""), "text/csv")}
    r = api_client.post("/upload_csv", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") in (400, 500)


def test_upload_non_csv_rejected(api_client: httpx.Client) -> None:
    files = {"file": ("x.txt", io.BytesIO(b"a,b"), "text/plain")}
    r = api_client.post("/upload_csv", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 400


def test_upload_missing_required_column(api_client: httpx.Client) -> None:
    raw = build_csv_bytes([{"舆情中文": "无编号", "一级标签": "x"}], columns=["舆情中文", "一级标签"])
    body = upload_csv(api_client, raw, filename="bad.csv")
    assert body.get("code") == 400


@pytest.mark.slow
def test_upload_5628_rows(api_client: httpx.Client, unique_suffix: str) -> None:
    """大文件：仅验证上传与计数，不对全量做分类（避免 CI 超时）。"""
    rows = []
    for i in range(5628):
        rows.append(
            {
                "舆情编号": f"BIG_{unique_suffix}_{i:05d}",
                "舆情中文": f"批量性能测试文本{i}，包含充电与续航关键词。",
                "舆情时间": "2025-03-01",
                "一级标签": "体验需求类",
                "渠道": "压测",
            }
        )
    raw = build_csv_bytes(rows)
    body = upload_csv(api_client, raw, filename="big.csv")
    assert_ok_json(body)
    assert body.get("imported_count") == 5628
    assert body.get("total_rows") == 5628
    assert body.get("skipped_duplicates", 0) == 0
    batch = body.get("batch_id")
    lst = get_list(api_client, upload_batch=batch, page=1, size=1)
    assert_ok_json(lst)
    assert lst.get("total", 0) >= 5628
