# -*- coding: utf-8 -*-
"""confirm_review 回流一致性 + 失败可观测性回归。"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"


@pytest.fixture()
def reflow_client(tmp_path):
    fd, db_path = tempfile.mkstemp(suffix=".db", dir=str(tmp_path))
    os.close(fd)
    failures_path = tmp_path / "reflow_failures.jsonl"
    os.environ["VOC_DB_PATH"] = db_path
    os.environ.setdefault("VOC_DISABLE_RATE_LIMIT", "1")
    os.environ["VOC_TEST_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    for name in list(sys.modules):
        if name == "main" or name.startswith("main."):
            del sys.modules[name]
    main_mod = importlib.import_module("main")
    importlib.reload(main_mod)
    main_mod.REFLOW_FAILURES_PATH = failures_path
    from main import execute_db

    execute_db(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch,
            review_status, review_l1, review_l2, reflow_synced
        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
        ["RF_T001", "回流韧性测试原文", "2025-10-01", "BATCH_RF_T", "产品质量类", "车机", 1],
    )
    execute_db(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch,
            review_status, review_l1, review_l2, reflow_synced
        ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)""",
        ["RF_T002", "标签变更测试", "2025-10-02", "BATCH_RF_T", "服务类", "销售", 1],
    )
    with TestClient(main_mod.app) as client:
        yield client, main_mod, failures_path, db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _confirm(client: TestClient, opinion_id: str, l1: str, l2: str, *, with_reflow: bool = True):
    return client.post(
        "/confirm_review",
        json={
            "reviews": [{"opinion_id": opinion_id, "review_l1": l1, "review_l2": l2}],
            "reviewer": "pytest",
            "with_reflow": with_reflow,
        },
    )


def _reflow_synced(main_mod, oid: str) -> int:
    row = main_mod.query_db(
        "SELECT reflow_synced, reflow_sync_reason FROM opinion WHERE opinion_id = ?",
        [oid],
        fetch_all=False,
    )
    assert row is not None
    return int(row.get("reflow_synced") or 0), str(row.get("reflow_sync_reason") or "")


def test_confirm_unchanged_labels_preserves_reflow_synced(reflow_client) -> None:
    client, main_mod, _, _ = reflow_client
    r = _confirm(client, "RF_T001", "产品质量类", "车机")
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200, body.get("msg")
    synced, _ = _reflow_synced(main_mod, "RF_T001")
    assert synced == 1


def test_confirm_changed_labels_resets_reflow_synced(reflow_client) -> None:
    client, main_mod, _, _ = reflow_client
    r = _confirm(client, "RF_T002", "体验需求类", "内饰", with_reflow=False)
    assert r.status_code == 200
    synced, _ = _reflow_synced(main_mod, "RF_T002")
    assert synced == 0


def test_background_reflow_failure_marks_row_and_jsonl(reflow_client) -> None:
    client, main_mod, failures_path, _ = reflow_client

    class _InlineThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, **kw):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    boom = RuntimeError("simulated data_clear write failure")

    with patch.object(threading, "Thread", _InlineThread):
        with patch("reflow_service.reflow_batch_rows", side_effect=boom):
            r = _confirm(client, "RF_T002", "体验需求类", "内饰")
            assert r.status_code == 200

    synced, reason = _reflow_synced(main_mod, "RF_T002")
    assert synced == -1
    assert "simulated data_clear write failure" in reason

    assert failures_path.is_file()
    lines = [ln for ln in failures_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines
    last = json.loads(lines[-1])
    assert last.get("opinion_id") == "RF_T002"
    assert "simulated data_clear write failure" in (last.get("reason") or "")
    assert last.get("ts")


def test_get_reflow_failures_api(reflow_client) -> None:
    client, main_mod, failures_path, _ = reflow_client
    main_mod._append_reflow_failure_jsonl("RF_T001", "pytest failure sample")

    r = client.get("/api/reflow_failures", params={"limit": 50})
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    data = body.get("data") or []
    assert len(data) >= 1
    assert data[0].get("opinion_id") == "RF_T001"
    assert "pytest failure sample" in (data[0].get("reason") or "")


def test_reflow_failure_sets_synced_minus_one_or_success(reflow_client) -> None:
    """确认复核后后台回流完成：reflow_synced 应为 1（成功）或 -1（失败），不应卡在 0。"""
    client, main_mod, _, _ = reflow_client

    class _InlineThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, **kw):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    with patch.object(threading, "Thread", _InlineThread):
        r = _confirm(client, "RF_T002", "体验需求类", "内饰")
        assert r.status_code == 200

    synced, _ = _reflow_synced(main_mod, "RF_T002")
    assert synced in (1, -1)


def test_reflow_failure_endpoint_returns_failures(reflow_client) -> None:
    client, main_mod, failures_path, _ = reflow_client
    main_mod._append_reflow_failure_jsonl("RF_T002", "endpoint mock failure")

    r = client.get("/api/reflow_failures", params={"limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    ids = {item.get("opinion_id") for item in (body.get("data") or [])}
    assert "RF_T002" in ids
    assert failures_path.is_file()


def test_reflow_failure_multiple_rows(reflow_client) -> None:
    client, main_mod, _, _ = reflow_client
    from main import execute_db

    execute_db(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch,
            review_status, review_l1, review_l2, reflow_synced
        ) VALUES (?, ?, ?, ?, 0, ?, ?, 0)""",
        ["RF_T003", "第三条", "2025-10-03", "BATCH_RF_T", "服务类", "销售"],
    )

    class _InlineThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, **kw):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    with patch.object(threading, "Thread", _InlineThread):
        r = client.post(
            "/confirm_review",
            json={
                "reviews": [
                    {"opinion_id": "RF_T002", "review_l1": "体验需求类", "review_l2": "内饰"},
                    {"opinion_id": "RF_T003", "review_l1": "产品质量类", "review_l2": "车机"},
                ],
                "reviewer": "pytest",
                "with_reflow": True,
            },
        )
        assert r.status_code == 200

    for oid in ("RF_T002", "RF_T003"):
        synced, _ = _reflow_synced(main_mod, oid)
        assert synced in (1, -1), f"{oid} reflow_synced={synced}"
