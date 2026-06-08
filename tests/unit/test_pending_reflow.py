# -*- coding: utf-8 -*-
"""save_review / batch_save_review 延迟回流队列回归。"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"


@pytest.fixture()
def pending_reflow_client(tmp_path):
    fd, db_path = tempfile.mkstemp(suffix=".db", dir=str(tmp_path))
    os.close(fd)
    pending_path = tmp_path / "pending_reflow.jsonl"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    os.environ["VOC_DB_PATH"] = db_path
    os.environ.setdefault("VOC_DISABLE_RATE_LIMIT", "1")
    os.environ["VOC_TEST_ARTIFACTS_DIR"] = str(artifacts)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    for name in list(sys.modules):
        if name == "main" or name.startswith("main."):
            del sys.modules[name]
    main_mod = importlib.import_module("main")
    importlib.reload(main_mod)
    main_mod.PENDING_REFLOW_PATH = pending_path
    from main import execute_db

    execute_db(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch,
            review_status, review_l1, review_l2, reflow_synced
        ) VALUES (?, ?, ?, ?, 0, '', '', 0)""",
        ["PR_T001", "延迟回流测试原文", "2025-10-01", "BATCH_PR_T"],
    )
    with TestClient(main_mod.app) as client:
        yield client, main_mod, pending_path, db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_save_review_queues_pending_reflow(pending_reflow_client) -> None:
    client, main_mod, pending_path, _ = pending_reflow_client
    r = client.post(
        "/save_review",
        json={
            "opinion_id": "PR_T001",
            "review_status": 1,
            "review_l1": "产品质量类",
            "review_l2": "车机问题",
            "reviewer": "pytest",
            "reviewed_at": "2025-10-01T12:00:00",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    assert body.get("queued_reflow") is True
    assert pending_path.is_file()
    lines = [ln for ln in pending_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry.get("opinion_id") == "PR_T001"
    assert entry.get("upload_batch") == "BATCH_PR_T"


def test_flush_pending_reflow_sets_synced_and_clear(pending_reflow_client) -> None:
    client, main_mod, pending_path, _ = pending_reflow_client
    r = client.post(
        "/save_review",
        json={
            "opinion_id": "PR_T001",
            "review_status": 1,
            "review_l1": "产品质量类",
            "review_l2": "车机问题",
            "reviewer": "pytest",
            "reviewed_at": "2025-10-01T12:00:00",
        },
    )
    assert r.status_code == 200

    with patch("reflow_service.reflow_batch_rows") as mock_reflow:
        mock_reflow.return_value = {"reflowed": 1, "new_in_clear": 1, "overwritten_in_clear": 0}
        main_mod._flush_pending_reflow_once()

    row = main_mod.query_db(
        "SELECT reflow_synced FROM opinion WHERE opinion_id = ?",
        ["PR_T001"],
        fetch_all=False,
    )
    assert row is not None
    assert int(row.get("reflow_synced") or 0) == 1
    assert not pending_path.is_file() or pending_path.read_text(encoding="utf-8").strip() == ""
    mock_reflow.assert_called_once()


def test_batch_save_review_queues_confirmed_rows(pending_reflow_client) -> None:
    client, _, pending_path, _ = pending_reflow_client
    r = client.post(
        "/batch_save_review",
        json={
            "reviews": [
                {
                    "opinion_id": "PR_T001",
                    "review_status": 1,
                    "review_l1": "产品质量类",
                    "review_l2": "车机问题",
                    "reviewer": "pytest",
                    "reviewed_at": "2025-10-01T12:00:00",
                }
            ]
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("queued_reflow") == 1
    assert pending_path.is_file()
