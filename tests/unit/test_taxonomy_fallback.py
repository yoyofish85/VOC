# -*- coding: utf-8 -*-
"""L2 taxonomy API 后备行为回归测试（TestClient + 隔离测试库）。"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"


@pytest.fixture()
def taxonomy_client(tmp_path):
    fd, db_path = tempfile.mkstemp(suffix=".db", dir=str(tmp_path))
    os.close(fd)
    os.environ["VOC_DB_PATH"] = db_path
    os.environ.setdefault("VOC_DISABLE_RATE_LIMIT", "1")
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    for name in list(sys.modules):
        if name == "main" or name.startswith("main."):
            del sys.modules[name]
    main_mod = importlib.import_module("main")
    importlib.reload(main_mod)
    from main import execute_db

    execute_db(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch,
            review_status, review_l1, review_l2
        ) VALUES (?, ?, ?, ?, 1, ?, ?)""",
        ["TAX_T001", "taxonomy 后备测试", "2025-10-01", "BATCH_TAX", "产品质量类", "车机"],
    )
    with TestClient(main_mod.app) as client:
        yield client
    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_get_l2_by_l1_product_returns_list(taxonomy_client: TestClient) -> None:
    r = taxonomy_client.get("/api/get_l2_by_l1", params={"l1": "产品质量类"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    l2_list = (body.get("data") or {}).get("l2_whitelist") or []
    assert isinstance(l2_list, list)
    assert len(l2_list) > 0


def test_taxonomy_options_unknown_l1(taxonomy_client: TestClient) -> None:
    r = taxonomy_client.get("/api/v3/taxonomy_options", params={"l1": "未知类别"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    l2_list = (body.get("data") or {}).get("l2_whitelist") or []
    assert isinstance(l2_list, list)
    assert len(l2_list) > 0


def test_taxonomy_options_batch_multi_l1(taxonomy_client: TestClient) -> None:
    l1_list = ["产品质量类", "服务类", "体验需求类"]
    r = taxonomy_client.post("/api/v3/taxonomy_options_batch", json={"l1_list": l1_list})
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    by_l1 = (body.get("data") or {}).get("by_l1") or {}
    for l1 in l1_list:
        lst = by_l1.get(l1) or []
        assert isinstance(lst, list), f"{l1} 应返回列表"
        assert len(lst) > 0, f"{l1} 的 l2_whitelist 不应为空"


def test_taxonomy_options_empty_l1(taxonomy_client: TestClient) -> None:
    r = taxonomy_client.get("/api/v3/taxonomy_options", params={"l1": ""})
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    l2_list = (body.get("data") or {}).get("l2_whitelist")
    assert l2_list == []
