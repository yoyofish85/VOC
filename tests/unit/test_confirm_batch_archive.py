# -*- coding: utf-8 -*-
"""问题2回归：确认复核即写年度 CSV（部分复核也存档）+ 整批确认全部行 + 整批完成升级 reflow_synced=2。"""
from __future__ import annotations

import csv
import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"


@pytest.fixture()
def archive_client(tmp_path, monkeypatch):
    fd, db_path = tempfile.mkstemp(suffix=".db", dir=str(tmp_path))
    os.close(fd)
    annual_dir = tmp_path / "annual"
    annual_dir.mkdir()
    # 用 monkeypatch.setenv：函数级自动还原，避免污染后续测试（如规则匹配类）。
    monkeypatch.setenv("VOC_DB_PATH", db_path)
    monkeypatch.setenv("VOC_ANNUAL_DIR", str(annual_dir))
    monkeypatch.setenv("VOC_TEST_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("VOC_REFLOW_MERGE_GOLD", "0")
    monkeypatch.setenv("VOC_REFLOW_KEYWORD_BOOST", "0")
    monkeypatch.setenv("VOC_PENDING_REFLOW_INTERVAL", "3600")
    monkeypatch.setenv("VOC_DISABLE_RATE_LIMIT", "1")
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    for name in list(sys.modules):
        if name in ("main", "reflow_service") or name.startswith(("main.", "reflow_service.")):
            del sys.modules[name]
    main_mod = importlib.import_module("main")
    importlib.reload(main_mod)
    # config.paths.ANNUAL_DIR 是导入期常量（不随 reload 重算），逐测试覆盖到本用例 tmp 目录。
    main_mod.ANNUAL_DIR = annual_dir
    with TestClient(main_mod.app) as client:
        yield client, main_mod, annual_dir, db_path


def _seed_batch(main_mod, batch: str, n: int, *, with_v3: bool = True) -> None:
    rows = []
    for i in range(n):
        v3 = json.dumps({"l1": "产品质量类", "l2": "车机", "confidence": 0.85}) if with_v3 else ""
        rows.append(
            [
                f"{batch}_{i:03d}",
                f"车机黑屏第{i}条",
                "2025-10-01",
                batch,
                0,
                v3,
            ]
        )
    main_mod.execute_db_many(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch, review_status, v3_label_meta
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )


def _annual_total_rows(annual_dir: Path) -> int:
    p = annual_dir / "年度数据总和.CSV"
    if not p.is_file():
        return -1
    with p.open(encoding="utf-8-sig") as f:
        return sum(1 for _ in csv.DictReader(f))


def _annual_rows(annual_dir: Path) -> list[dict]:
    p = annual_dir / "年度数据总和.CSV"
    if not p.is_file():
        return []
    with p.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _wait_until(pred, timeout: float = 20.0, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def _reflow_synced(main_mod, oid: str) -> int:
    row = main_mod.query_db(
        "SELECT reflow_synced FROM opinion WHERE opinion_id = ?", [oid], fetch_all=False
    )
    return int((row or {}).get("reflow_synced") or 0)


def test_partial_confirm_writes_annual_csv(archive_client) -> None:
    """只确认整批中的 1 条，也应即时写入年度 CSV；该批未完成 → reflow_synced 停留在 1。"""
    client, main_mod, annual_dir, _ = archive_client
    _seed_batch(main_mod, "BATCH_P", 3)

    r = client.post(
        "/confirm_review",
        json={
            "reviews": [{"opinion_id": "BATCH_P_000", "review_l1": "产品质量类", "review_l2": "车机"}],
            "reviewer": "pytest",
            "with_reflow": True,
            "also_yearly": True,
        },
    )
    assert r.status_code == 200 and r.json().get("code") == 200

    assert _wait_until(lambda: _annual_total_rows(annual_dir) == 1), (
        f"年度 CSV 行数={_annual_total_rows(annual_dir)}，预期 1（部分复核也应存档）"
    )
    assert _wait_until(lambda: _reflow_synced(main_mod, "BATCH_P_000") == 1)
    # 批次未全部复核 → 不应升级为 2
    assert _reflow_synced(main_mod, "BATCH_P_000") == 1


def test_confirm_batch_confirms_all_rows_beyond_page(archive_client) -> None:
    """整批确认应处理全部行（25 > 单页 20），全部写入年度 CSV，整批完成升级为 reflow_synced=2。"""
    client, main_mod, annual_dir, _ = archive_client
    n = 25
    _seed_batch(main_mod, "BATCH_ALL", n)

    r = client.post(
        "/confirm_review_batch",
        json={
            "upload_batch": "BATCH_ALL",
            "reviewer": "pytest",
            "with_reflow": True,
            "also_yearly": True,
        },
    )
    body = r.json()
    assert r.status_code == 200 and body.get("code") == 200, body
    assert body.get("confirmed") == n, body

    # 全部已复核
    reviewed = main_mod.query_db(
        "SELECT COUNT(*) AS cnt FROM opinion WHERE upload_batch = ? AND review_status = 1",
        ["BATCH_ALL"],
        fetch_all=False,
    )
    assert int(reviewed["cnt"]) == n

    assert _wait_until(lambda: _annual_total_rows(annual_dir) == n), (
        f"年度 CSV 行数={_annual_total_rows(annual_dir)}，预期 {n}"
    )
    # 整批完成 → 全部升级 reflow_synced=2
    assert _wait_until(
        lambda: _reflow_synced(main_mod, "BATCH_ALL_000") == 2
        and _reflow_synced(main_mod, "BATCH_ALL_024") == 2
    ), "整批完成后应升级 reflow_synced=2"


def test_confirm_batch_blocked_when_unreviewed(archive_client) -> None:
    """存在未就绪行时整批确认应返回 409，且不确认任何行。"""
    client, main_mod, annual_dir, _ = archive_client
    _seed_batch(main_mod, "BATCH_MIX", 2, with_v3=True)
    _seed_batch(main_mod, "BATCH_MIX2", 1, with_v3=False)
    main_mod.execute_db(
        "UPDATE opinion SET upload_batch = 'BATCH_MIX' WHERE upload_batch = 'BATCH_MIX2'"
    )

    preview = client.get("/confirm_review_batch/preview", params={"upload_batch": "BATCH_MIX"})
    pv = preview.json()
    assert preview.status_code == 200 and pv.get("code") == 200
    assert pv.get("unreviewed_count") == 1
    assert pv.get("confirmable_count") == 2
    assert len(pv.get("unreviewed") or []) == 1
    assert pv["unreviewed"][0]["opinion_id"] == "BATCH_MIX2_000"
    assert pv["unreviewed"][0]["reason"] == "no_l1"

    r = client.post(
        "/confirm_review_batch",
        json={"upload_batch": "BATCH_MIX", "reviewer": "pytest", "with_reflow": True},
    )
    body = r.json()
    assert body.get("code") == 409, body
    assert body.get("confirmed") == 0
    assert body.get("unreviewed_count") == 1

    reviewed = main_mod.query_db(
        "SELECT COUNT(*) AS cnt FROM opinion WHERE upload_batch = ? AND review_status = 1",
        ["BATCH_MIX"],
        fetch_all=False,
    )
    assert int(reviewed["cnt"]) == 0
    assert _annual_total_rows(annual_dir) == -1


def test_preview_and_list_filter_for_unreviewed(archive_client) -> None:
    """预览返回未就绪 ID 后，列表可用 opinionIds 筛选仅显示这些行。"""
    client, main_mod, _, _ = archive_client
    _seed_batch(main_mod, "BATCH_FLT", 2, with_v3=True)
    main_mod.execute_db(
        "UPDATE opinion SET v3_label_meta = '' WHERE opinion_id = 'BATCH_FLT_001'"
    )

    pv = client.get("/confirm_review_batch/preview", params={"upload_batch": "BATCH_FLT"}).json()
    assert pv.get("unreviewed_count") == 1
    bad_id = pv["unreviewed"][0]["opinion_id"]

    lst = client.get(
        "/get_review_list",
        params={"uploadBatch": "BATCH_FLT", "opinionIds": bad_id, "size": 20},
    ).json()
    assert lst.get("code") == 200
    assert lst.get("total") == 1
    assert lst["data"][0]["opinion_id"] == bad_id


def test_confirm_batch_after_fixing_unreviewed(archive_client) -> None:
    """补全未就绪标签后，整批确认应成功。"""
    client, main_mod, annual_dir, _ = archive_client
    _seed_batch(main_mod, "BATCH_FIX", 2, with_v3=True)
    main_mod.execute_db(
        "UPDATE opinion SET v3_label_meta = '' WHERE opinion_id = 'BATCH_FIX_001'"
    )
    blocked = client.post(
        "/confirm_review_batch",
        json={"upload_batch": "BATCH_FIX", "reviewer": "pytest", "with_reflow": True},
    ).json()
    assert blocked.get("code") == 409

    main_mod.execute_db(
        "UPDATE opinion SET v3_label_meta = ? WHERE opinion_id = ?",
        [json.dumps({"l1": "产品质量类", "l2": "车机"}), "BATCH_FIX_001"],
    )
    ok = client.post(
        "/confirm_review_batch",
        json={"upload_batch": "BATCH_FIX", "reviewer": "pytest", "with_reflow": True},
    ).json()
    assert ok.get("code") == 200
    assert ok.get("confirmed") == 2
    assert _wait_until(lambda: _annual_total_rows(annual_dir) == 2)


def test_annual_csv_sorted_oldest_to_newest(archive_client) -> None:
    """年度 CSV 应按舆情时间从早到晚保存，而不是倒序。"""
    client, main_mod, annual_dir, _ = archive_client
    batch = "BATCH_SORT"
    rows = [
        ["SORT_2", "第二条", "2025-03-02 10:00:00", batch, 0, json.dumps({"l1": "非问题", "l2": ""})],
        ["SORT_1", "第一条", "2025-03-01 09:00:00", batch, 0, json.dumps({"l1": "非问题", "l2": ""})],
        ["SORT_3", "第三条", "2025-03-03 11:00:00", batch, 0, json.dumps({"l1": "非问题", "l2": ""})],
    ]
    main_mod.execute_db_many(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch, review_status, v3_label_meta
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )

    out = client.post(
        "/confirm_review_batch",
        json={"upload_batch": batch, "reviewer": "pytest", "with_reflow": True},
    ).json()
    assert out.get("code") == 200, out
    assert _wait_until(lambda: _annual_total_rows(annual_dir) == 3)
    assert [r["舆情编号"] for r in _annual_rows(annual_dir)] == ["SORT_1", "SORT_2", "SORT_3"]


def _upload_csv(client: TestClient, rows: list[dict]) -> dict:
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    resp = client.post(
        "/upload_csv",
        files={"file": ("case.csv", buf.getvalue().encode("utf-8-sig"), "text/csv")},
    )
    assert resp.status_code == 200
    return resp.json()


def test_upload_alias_vin_model_written_to_annual_csv(archive_client) -> None:
    """导入应兼容 VIN/车型别名列，并最终写入年度 CSV。"""
    client, _main_mod, annual_dir, _ = archive_client
    body = _upload_csv(
        client,
        [
            {
                "舆情编号": "ALIAS_001",
                "舆情中文": "用户反馈车辆车机黑屏。",
                "舆情时间": "2025-04-01 09:00:00",
                "渠道": "测试",
                "一级标签": "非问题",
                "车辆VIN": "LJUBMSA13SK002038",
                "车型名称": "Eletre S",
            }
        ],
    )
    assert body.get("code") == 200, body
    batch = body.get("batch_id")
    out = client.post(
        "/confirm_review_batch",
        json={"upload_batch": batch, "reviewer": "pytest", "with_reflow": True},
    ).json()
    assert out.get("code") == 200, out
    assert _wait_until(lambda: _annual_total_rows(annual_dir) == 1)
    row = _annual_rows(annual_dir)[0]
    assert row["VIN"] == "LJUBMSA13SK002038"
    assert row["车型"] == "Eletre S"


def test_upload_extracts_vin_model_from_text_when_columns_missing(archive_client) -> None:
    """没有 VIN/车型列时，从原文中兜底提取车辆信息。"""
    client, _main_mod, annual_dir, _ = archive_client
    body = _upload_csv(
        client,
        [
            {
                "舆情编号": "TEXT_001",
                "舆情中文": "用户反馈：车型：Emira First Edition，VIN：SCCLEKAX8PHN12654，空调异味。",
                "舆情时间": "2025-05-01 09:00:00",
                "渠道": "测试",
                "一级标签": "非问题",
            }
        ],
    )
    assert body.get("code") == 200, body
    out = client.post(
        "/confirm_review_batch",
        json={"upload_batch": body.get("batch_id"), "reviewer": "pytest", "with_reflow": True},
    ).json()
    assert out.get("code") == 200, out
    assert _wait_until(lambda: _annual_total_rows(annual_dir) == 1)
    row = _annual_rows(annual_dir)[0]
    assert row["VIN"] == "SCCLEKAX8PHN12654"
    assert row["车型"] == "Emira First Edition"


def test_review_list_skip_total_keeps_last_pages_fast(archive_client) -> None:
    """翻到后续页时可跳过重复 COUNT，避免最后几页被 COUNT + OFFSET 拖超时。"""
    client, main_mod, _annual_dir, _ = archive_client
    rows = []
    for i in range(25):
        rows.append(
            [
                f"PAGE_{i:03d}",
                f"第 {i} 条人工复核分页数据",
                f"2025-06-{(i % 28) + 1:02d} 09:00:00",
                "BATCH_PAGE",
                0,
                json.dumps({"l1": "产品质量类", "l2": "车机问题", "confidence": 0.88}),
            ]
        )
    main_mod.execute_db_many(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch, review_status, v3_label_meta
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )

    first = client.get(
        "/get_review_list",
        params={"page": 1, "size": 20, "uploadBatch": "BATCH_PAGE"},
    ).json()
    assert first.get("code") == 200, first
    assert first.get("total") == 25

    second = client.get(
        "/get_review_list",
        params={"page": 2, "size": 20, "uploadBatch": "BATCH_PAGE", "skipTotal": True},
    ).json()
    assert second.get("code") == 200, second
    assert second.get("total") == -1
    assert len(second.get("data") or []) == 5
