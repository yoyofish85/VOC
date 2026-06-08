# -*- coding: utf-8 -*-
"""main.py 健康检查接口 — TestClient 单元测试（独立测试库）。"""
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
def health_client():
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
    with TestClient(main_mod.app) as client:
        yield client
    try:
        os.unlink(db_path)
    except OSError:
        pass


def test_health_root(health_client: TestClient) -> None:
    r = health_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    assert body.get("data", {}).get("status") == "healthy"


def test_health_api_path(health_client: TestClient) -> None:
    r = health_client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("code") == 200


def test_health_database_connected(health_client: TestClient) -> None:
    r = health_client.get("/api/health")
    data = r.json().get("data") or {}
    assert data.get("database") == "connected"


def test_health_detail_api(health_client: TestClient) -> None:
    r = health_client.get("/api/health/detail")
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    data = body.get("data") or {}
    assert data.get("status") in ("healthy", "degraded")
    assert "db" in data
    assert "stats" in data
    assert data["stats"].get("total_rows", -1) >= 0
