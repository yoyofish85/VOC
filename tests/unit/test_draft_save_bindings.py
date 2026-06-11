# -*- coding: utf-8 -*-
"""draft_save_reviews SQL 绑定回归（F4）。"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"


@pytest.fixture(scope="module")
def draft_client():
    fd, db_path = tempfile.mkstemp(suffix=".db")
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
        """INSERT INTO opinion (opinion_id, original_text, create_time, upload_batch, review_status)
           VALUES (?, ?, ?, ?, 0)""",
        ["DRAFT_T001", "测试暂存绑定修复", "2025-10-01", "BATCH_DRAFT_T"],
    )
    with TestClient(main_mod.app) as client:
        yield client
    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_draft_save_reviews_bindings(draft_client: TestClient) -> None:
    r = draft_client.post(
        "/draft_save_reviews",
        json={
            "reviews": [
                {
                    "opinion_id": "DRAFT_T001",
                    "review_l1": "产品质量类",
                    "review_l2": "车机",
                }
            ],
            "reviewer": "pytest",
            "with_reflow": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200, body.get("msg")
    assert body.get("saved") == 1

    lst = draft_client.get("/get_review_list", params={"page": 1, "size": 5, "uploadBatch": "BATCH_DRAFT_T"})
    assert lst.status_code == 200
    data = lst.json()
    assert data.get("total") == 1
    rows = data.get("data") or []
    assert len(rows) == 1
    assert rows[0].get("review_status") == 1
    assert rows[0].get("review_l1") == "产品质量类"


def test_draft_save_unchanged_labels_preserves_reflow_synced(draft_client: TestClient) -> None:
    """标签未变时保留 reflow_synced，避免多余回流 IO（与 confirm_review 一致）。"""
    import importlib
    import sys
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parents[2] / "src" / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    main_mod = importlib.import_module("main")
    from main import execute_db

    execute_db(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch,
            review_status, review_l1, review_l2, reflow_synced
        ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)""",
        ["DRAFT_T002", "标签未变暂存", "2025-10-02", "BATCH_DRAFT_T", "产品质量类", "车机", 1],
    )
    r = draft_client.post(
        "/draft_save_reviews",
        json={
            "reviews": [
                {
                    "opinion_id": "DRAFT_T002",
                    "review_l1": "产品质量类",
                    "review_l2": "车机",
                }
            ],
            "reviewer": "pytest",
            "with_reflow": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200, body.get("msg")
    row = main_mod.query_db(
        "SELECT reflow_synced FROM opinion WHERE opinion_id = ?",
        ["DRAFT_T002"],
        fetch_all=False,
    )
    assert int(row.get("reflow_synced") or 0) == 1
