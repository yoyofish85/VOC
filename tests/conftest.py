# -*- coding: utf-8 -*-
"""
共享夹具：HTTP 客户端、CSV 构造、与运行中的后端通信。

后端须由 app_launcher.py --verify 或 CI 预先启动，并设置：
  VOC_DB_PATH / VOC_TEST_ARTIFACTS_DIR / VOC_KEYWORD_FILE / VOC_DISABLE_RATE_LIMIT
"""
from __future__ import annotations

import io
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest

BASE_URL = os.environ.get("VOC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _skip_api_healthcheck(pytestconfig: pytest.Config) -> bool:
    """tests/unit 离线单测无需启动 FastAPI；也可用 VOC_UNIT_ONLY=1 显式跳过。"""
    if os.environ.get("VOC_UNIT_ONLY") == "1":
        return True
    args = pytestconfig.args
    if not args:
        return False
    for arg in args:
        norm = str(arg).replace("\\", "/")
        if norm.startswith("tests/unit") or "/tests/unit/" in norm:
            continue
        if norm.startswith("tests/test_deploy") or "/tests/test_deploy" in norm:
            continue
        if norm.startswith("unit/"):
            continue
        return False
    return True


@pytest.fixture(scope="session")
def api_base() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def api_client(api_base: str) -> httpx.Client:
    """长超时：大文件上传 / 分类。"""
    with httpx.Client(
        base_url=api_base,
        timeout=httpx.Timeout(360.0, connect=30.0),
        headers={"Accept": "application/json"},
    ) as c:
        yield c


@pytest.fixture
def unique_suffix() -> str:
    return uuid.uuid4().hex[:10]


def assert_ok_json(data: Dict[str, Any], *, allow_codes: Optional[set] = None) -> None:
    allow = allow_codes or {200}
    code = data.get("code")
    assert code in allow, f"unexpected code={code} msg={data.get('msg')!r}"


def build_csv_bytes(rows: List[Dict[str, str]], columns: Optional[List[str]] = None) -> bytes:
    """构造 UTF-8 CSV（舆情编号、舆情中文 必填）。"""
    if not rows:
        return b""
    cols = columns or list(rows[0].keys())
    lines = [",".join(cols)]
    for r in rows:
        cells = []
        for k in cols:
            v = str(r.get(k, "")).replace('"', '""')
            if "," in v or "\n" in v or '"' in v:
                cells.append(f'"{v}"')
            else:
                cells.append(v)
        lines.append(",".join(cells))
    return ("\n".join(lines) + "\n").encode("utf-8")


def upload_csv(client: httpx.Client, content: bytes, filename: str = "upload.csv") -> Dict[str, Any]:
    files = {"file": (filename, io.BytesIO(content), "text/csv")}
    r = client.post("/upload_csv", files=files)
    r.raise_for_status()
    return r.json()


def get_list(
    client: httpx.Client,
    *,
    page: int = 1,
    size: int = 20,
    upload_batch: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"page": page, "size": size, **kwargs}
    if upload_batch:
        params["uploadBatch"] = upload_batch
    r = client.get("/get_review_list", params=params)
    r.raise_for_status()
    return r.json()


@pytest.fixture(scope="session", autouse=True)
def _session_healthcheck(api_client: httpx.Client, pytestconfig: pytest.Config) -> None:
    if _skip_api_healthcheck(pytestconfig):
        return
    r = api_client.get("/health")
    assert r.status_code == 200, f"后端未就绪: {r.text}"
    body = r.json()
    assert body.get("code") == 200, body
