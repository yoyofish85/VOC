# -*- coding: utf-8 -*-
"""SQLite WAL 模式并发压力测试（仅开发机，@pytest.mark.perf）。"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 离线 DB 压测，无需启动 FastAPI
os.environ.setdefault("VOC_UNIT_ONLY", "1")

BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "backend"

pytestmark = pytest.mark.perf


@pytest.fixture()
def main_mod(tmp_path, monkeypatch):
    """隔离 DB + artifacts，重载 main 模块。"""
    fd, db_path = tempfile.mkstemp(suffix=".db", dir=str(tmp_path))
    os.close(fd)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setenv("VOC_DB_PATH", db_path)
    monkeypatch.setenv("VOC_TEST_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("VOC_REFLOW_MERGE_GOLD", "0")
    monkeypatch.setenv("VOC_REFLOW_KEYWORD_BOOST", "0")
    monkeypatch.setenv("VOC_DISABLE_RATE_LIMIT", "1")
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    for name in list(sys.modules):
        if name in ("main", "reflow_service") or name.startswith(("main.", "reflow_service.")):
            del sys.modules[name]
    mod = importlib.import_module("main")
    importlib.reload(mod)
    yield mod, db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _seed_opinion_rows(
    main_mod,
    n: int,
    *,
    review_status: int = 0,
    reflow_synced: int = 0,
    prefix: str = "CC",
) -> None:
    rows = [
        [
            f"{prefix}_{i:04d}",
            f"并发测试原文{i}",
            "2025-10-01",
            "BATCH_CC",
            review_status,
            "产品质量类",
            "车机",
            reflow_synced,
        ]
        for i in range(n)
    ]
    main_mod.execute_db_many(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch,
            review_status, review_l1, review_l2, reflow_synced
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def test_concurrent_write_and_read(main_mod) -> None:
    """10 写 + 10 读并发，30 秒内无错误完成。"""
    mod, _ = main_mod
    _seed_opinion_rows(mod, 200)

    errors: list[str] = []
    lock = threading.Lock()
    deadline = time.time() + 30.0

    def writer(tid: int) -> None:
        for j in range(5):
            oid = f"CC_{(tid * 5 + j) % 200:04d}"
            try:
                mod.execute_db(
                    "UPDATE opinion SET review_status = 1, review_l1 = ? WHERE opinion_id = ?",
                    ["体验需求类", oid],
                )
            except Exception as exc:
                with lock:
                    errors.append(f"writer{tid}[{j}]: {exc}")

    def reader(rid: int) -> None:
        for _ in range(10):
            try:
                mod.query_db("SELECT COUNT(*) AS cnt FROM opinion", fetch_all=False)
            except Exception as exc:
                with lock:
                    errors.append(f"reader{rid}: {exc}")

    threads: list[threading.Thread] = []
    for i in range(10):
        threads.append(threading.Thread(target=writer, args=(i,)))
    for i in range(10):
        threads.append(threading.Thread(target=reader, args=(i,)))

    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        remaining = deadline - time.time()
        t.join(timeout=max(0.0, remaining))

    elapsed = time.time() - t0
    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"线程未在 30s 内结束: {alive}"
    assert elapsed < 30.0, f"并发未在 30 秒内完成: {elapsed:.1f}s"
    assert not errors, errors

    row = mod.query_db(
        "SELECT COUNT(*) AS cnt FROM opinion WHERE review_status = 1",
        fetch_all=False,
    )
    assert row is not None
    assert int(row["cnt"]) >= 5


def test_wal_mode_persists_across_restarts(main_mod) -> None:
    """WAL 模式在连接关闭重开后仍保持。"""
    _, db_path = main_mod

    conn = sqlite3.connect(db_path, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()

    conn2 = sqlite3.connect(db_path, timeout=20)
    try:
        mode = conn2.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn2.close()

    assert str(mode).lower() == "wal"


def test_concurrent_reflow_and_query(main_mod) -> None:
    """回流写入与 COUNT 查询并发，15 秒内无错误完成。"""
    mod, _ = main_mod
    _seed_opinion_rows(mod, 100, review_status=1, reflow_synced=1)

    from reflow_service import reflow_batch_rows

    errors: list[str] = []
    lock = threading.Lock()
    deadline = time.time() + 15.0

    def reflow_worker() -> None:
        try:
            rows = mod.query_db(
                """SELECT opinion_id, original_text, create_time, review_l1, review_l2,
                          model_class, v3_label_meta
                   FROM opinion LIMIT 50"""
            )
            kw = MagicMock()
            kw.extract_keywords.return_value = ["测试"]
            reflow_batch_rows(rows, kw, reviewer="pytest", trigger="concurrent_test")
        except Exception as exc:
            with lock:
                errors.append(f"reflow: {exc}")

    def query_worker() -> None:
        for _ in range(20):
            try:
                mod.query_db("SELECT COUNT(*) AS cnt FROM opinion", fetch_all=False)
            except Exception as exc:
                with lock:
                    errors.append(f"query: {exc}")

    t_reflow = threading.Thread(target=reflow_worker, name="reflow")
    t_query = threading.Thread(target=query_worker, name="query")

    t0 = time.time()
    t_reflow.start()
    t_query.start()
    for t in (t_reflow, t_query):
        remaining = deadline - time.time()
        t.join(timeout=max(0.0, remaining))

    elapsed = time.time() - t0
    alive = [t.name for t in (t_reflow, t_query) if t.is_alive()]
    assert not alive, f"线程未在 15s 内结束: {alive}"
    assert elapsed < 15.0, f"并发未在 15 秒内完成: {elapsed:.1f}s"
    assert not errors, errors
