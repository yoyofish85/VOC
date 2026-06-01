# -*- coding: utf-8 -*-
"""健康检查、CORS、标签体系、仪表盘等只读/轻量接口。"""
from __future__ import annotations

import httpx

from tests.conftest import assert_ok_json


def test_health_root_alias(api_client: httpx.Client) -> None:
    r = api_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("code") == 200
    assert body.get("data", {}).get("status") == "healthy"


def test_health_api_path(api_client: httpx.Client) -> None:
    r = api_client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("code") == 200


def test_cors_allow_origin_on_response(api_client: httpx.Client) -> None:
    r = api_client.get("/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert r.status_code == 200
    aco = (r.headers.get("access-control-allow-origin") or "").lower()
    assert aco in ("http://127.0.0.1:5173", "*") or "localhost" in aco or "127.0.0.1" in aco


def test_taxonomy_options_get(api_client: httpx.Client) -> None:
    r = api_client.get("/api/v3/taxonomy_options", params={"l1": "非问题"})
    assert r.status_code == 200
    body = r.json()
    assert_ok_json(body)
    assert "l2_whitelist" in body.get("data", {}) or body.get("data", {}).get("l2_whitelist") is not None


def test_taxonomy_options_batch(api_client: httpx.Client) -> None:
    r = api_client.post(
        "/api/v3/taxonomy_options_batch",
        json={"l1_list": ["非问题", "产品质量类", ""]},
    )
    assert r.status_code == 200
    body = r.json()
    assert_ok_json(body)
    by_l1 = body.get("data", {}).get("by_l1") or {}
    assert isinstance(by_l1, dict)
    assert "非问题" in by_l1


def test_get_review_list_empty_ok(api_client: httpx.Client) -> None:
    r = api_client.get("/get_review_list", params={"page": 1, "size": 5})
    assert r.status_code == 200
    body = r.json()
    assert_ok_json(body)
    assert "total" in body
    assert isinstance(body.get("data"), list)


def test_dashboard_stats(api_client: httpx.Client) -> None:
    r = api_client.get("/dashboard_stats")
    assert r.status_code == 200
    body = r.json()
    assert_ok_json(body)
    d = body.get("data") or {}
    assert "total" in d


def test_report_monthly_apis(api_client: httpx.Client) -> None:
    params = {"date_from": "2025-01-01", "date_to": "2025-12-31", "region": "all"}
    r = api_client.get("/api/get_monthly_overview", params=params)
    assert r.status_code == 200
    o = r.json()
    assert o.get("code") == 200
    assert "months" in (o.get("data") or {})
    r2 = api_client.get(
        "/api/get_monthly_subtag_trend",
        params={**params, "l1": "产品质量类", "top_secondary": 1},
    )
    assert r2.status_code == 200
    assert r2.json().get("code") == 200
    r3 = api_client.get("/api/get_top_subtag_monthly", params={**params, "top_n": 5})
    assert r3.status_code == 200
    assert r3.json().get("code") == 200


def test_report_monthly_bad_dates(api_client: httpx.Client) -> None:
    r = api_client.get("/api/get_monthly_overview", params={"date_from": "", "date_to": ""})
    assert r.status_code == 200
    assert r.json().get("code") == 400
