# -*- coding: utf-8 -*-
"""集成与性能：并发 batch_classify、报表 SLA。"""
from __future__ import annotations

import concurrent.futures
import time

import httpx
import pytest

from tests.conftest import assert_ok_json, build_csv_bytes, upload_csv

pytestmark = pytest.mark.perf


def _wait_job(client: httpx.Client, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = client.get(f"/api/batch_classify/status/{job_id}")
        r.raise_for_status()
        last = r.json().get("data") or r.json()
        st = str(last.get("status") or "").lower()
        if st in ("completed", "failed", "error"):
            return last
        time.sleep(0.3)
    return last


@pytest.fixture(scope="module")
def perf_batch(api_client: httpx.Client) -> str:
    rows = [
        {
            "舆情编号": f"PERF_{i:03d}",
            "舆情中文": f"测试并发分类第{i}条，车机卡顿。",
            "舆情时间": "2025-10-15",
            "一级标签": "",
            "渠道": "perf",
        }
        for i in range(12)
    ]
    body = upload_csv(api_client, build_csv_bytes(rows), filename="perf_batch.csv")
    assert_ok_json(body)
    return body["batch_id"]


def test_concurrent_batch_classify_jobs(api_client: httpx.Client, perf_batch: str) -> None:
    """10 个 async batch_classify 同时提交，不应卡死 health。"""
    job_ids = []

    def submit_one(_: int) -> str:
        r = api_client.post(
            "/api/batch_classify",
            json={"upload_batch": perf_batch, "use_llm": False, "async": True},
        )
        r.raise_for_status()
        data = r.json()
        assert data.get("code") in (200, 202), data
        jid = (data.get("data") or {}).get("job_id") or data.get("job_id")
        assert jid, data
        return jid

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        job_ids = list(pool.map(submit_one, range(10)))

    assert len(job_ids) == 10

    hr = api_client.get("/health")
    assert hr.status_code == 200
    assert hr.json().get("code") == 200

    completed = 0
    for jid in job_ids:
        final = _wait_job(api_client, jid, timeout=90.0)
        if str(final.get("status", "")).lower() == "completed":
            completed += 1
    assert completed >= 8, f"仅 {completed}/10 任务 completed: {job_ids}"


def test_llm_async_does_not_block_health(api_client: httpx.Client, perf_batch: str) -> None:
    """14B 异步提交应立即返回；health 仍可访问（Ollama 缺失时 job 可能 failed）。"""
    t0 = time.time()
    r = api_client.post(
        "/api/batch_classify",
        json={
            "upload_batch": perf_batch,
            "use_llm": True,
            "async": True,
            "opinion_ids": ["PERF_000", "PERF_001"],
        },
    )
    elapsed = time.time() - t0
    assert r.status_code == 200
    assert elapsed < 5.0, f"async 提交耗时应 <5s，实际 {elapsed:.2f}s"
    data = r.json()
    jid = (data.get("data") or {}).get("job_id") or data.get("job_id")
    assert jid

    hr = api_client.get("/health")
    assert hr.status_code == 200

    _wait_job(api_client, jid, timeout=120.0)


def test_monthly_overview_october_under_2s(api_client: httpx.Client) -> None:
    params = {"date_from": "2025-10-01", "date_to": "2025-10-31", "region": "all"}
    t0 = time.time()
    r = api_client.get("/api/get_monthly_overview", params=params)
    elapsed = time.time() - t0
    assert r.status_code == 200
    assert r.json().get("code") == 200
    assert elapsed < 2.0, f"get_monthly_overview 耗时 {elapsed:.3f}s > 2s SLA"


def test_monthly_subtag_trend_october_under_2s(api_client: httpx.Client) -> None:
    params = {
        "date_from": "2025-10-01",
        "date_to": "2025-10-31",
        "region": "all",
        "top_secondary": 1,
    }
    t0 = time.time()
    r = api_client.get("/api/get_monthly_subtag_trend", params=params)
    elapsed = time.time() - t0
    assert r.status_code == 200
    assert r.json().get("code") == 200
    assert elapsed < 2.0, f"get_monthly_subtag_trend 耗时 {elapsed:.3f}s > 2s SLA"
