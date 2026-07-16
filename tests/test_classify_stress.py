# -*- coding: utf-8 -*-
"""14B 批量分类并发压测：验证分批短事务提交不再长时间持有写锁（@pytest.mark.perf）。

复现并防回归生产问题：单次导入 200-300 条 14B 分类时，旧实现整批共用一个
连接、循环结束才 commit，写事务持有 14-30 分钟，导致复核确认/回流等并发写
报 "database is locked"。本测试在分类进行中并发执行写操作，断言无锁错误。
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("VOC_UNIT_ONLY", "1")

BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "backend"

pytestmark = pytest.mark.perf


@pytest.fixture()
def classify_env(tmp_path, monkeypatch):
    fd, db_path = tempfile.mkstemp(suffix=".db", dir=str(tmp_path))
    os.close(fd)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setenv("VOC_DB_PATH", db_path)
    monkeypatch.setenv("VOC_TEST_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("VOC_DISABLE_RATE_LIMIT", "1")
    # 加速：去除节流；小提交阈值放大并发写窗口
    monkeypatch.setenv("VOC_QWEN_BATCH_SLEEP", "0")
    monkeypatch.setenv("VOC_QWEN_INTER_BATCH_SLEEP", "0")
    monkeypatch.setenv("VOC_QWEN_COMMIT_EVERY", "10")
    monkeypatch.setenv("VOC_QWEN_DIRECT", "1")
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    for name in list(sys.modules):
        if name in ("main", "voc_classifier_service", "qwen_ollama") or name.startswith(
            ("main.", "voc_classifier_service.", "qwen_ollama.")
        ):
            del sys.modules[name]
    main_mod = importlib.import_module("main")
    importlib.reload(main_mod)
    return main_mod, db_path


def _seed(main_mod, n: int) -> None:
    rows = [
        [
            f"CLS_{i:04d}",
            f"车机偶发黑屏卡顿第{i}条，需要排查",
            "2025-10-01",
            "BATCH_CLS",
            0,
        ]
        for i in range(n)
    ]
    main_mod.execute_db_many(
        """INSERT INTO opinion (
            opinion_id, original_text, create_time, upload_batch, review_status
        ) VALUES (?, ?, ?, ?, ?)""",
        rows,
    )


def _fake_classify(text, *, db_path, country="", source="", vin="", model=None, host=None):
    time.sleep(0.003)  # 模拟单条 14B 推理耗时
    return {
        "l1": "产品质量类",
        "l2": "车机",
        "l3": "",
        "confidence": 0.88,
        "match_type": "qwen14b_structured",
        "risk_level": "低",
        "keywords": ["车机", "黑屏"],
        "model": "qwen2.5:14b-instruct-q4_K_M",
    }


def test_classify_300_rows_incremental_commit_no_lock(classify_env, monkeypatch) -> None:
    main_mod, db_path = classify_env
    n = 300
    _seed(main_mod, n)

    import qwen_ollama
    import voc_classifier_service

    monkeypatch.setattr(qwen_ollama, "classify_text", _fake_classify)

    errors: list[str] = []
    stop = threading.Event()

    def concurrent_writer() -> None:
        """分类进行中持续写库，模拟复核确认/回流；旧实现会在此报 database is locked。"""
        i = 0
        while not stop.is_set():
            wconn = sqlite3.connect(db_path, timeout=5)
            try:
                wconn.execute("PRAGMA busy_timeout=5000")
                wconn.execute(
                    "UPDATE opinion SET review_note = ? WHERE opinion_id = ?",
                    [f"note_{i}", f"CLS_{i % n:04d}"],
                )
                wconn.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"writer[{i}]: {exc}")
            finally:
                wconn.close()
            i += 1
            time.sleep(0.005)

    writer = threading.Thread(target=concurrent_writer, name="writer")
    writer.start()

    t0 = time.time()
    try:
        result = voc_classifier_service.run_batch_classify(
            db_path,
            opinion_ids=[f"CLS_{i:04d}" for i in range(n)],
            use_llm=True,
            ollama_host="http://127.0.0.1:11434",
        )
    finally:
        stop.set()
        writer.join(timeout=10)
    elapsed = time.time() - t0

    assert not errors, f"并发写出现锁/错误: {errors[:5]}"
    assert result.get("updated") == n, f"updated={result.get('updated')} 预期 {n}"

    row = main_mod.query_db(
        "SELECT COUNT(*) AS cnt FROM opinion WHERE TRIM(IFNULL(v3_label_meta,'')) != ''",
        fetch_all=False,
    )
    assert int(row["cnt"]) == n, "并非所有行都写入了 v3_label_meta"
    assert elapsed < 30.0, f"300 条分类应在 30s 内完成，实际 {elapsed:.1f}s"


def _slow_classify(text, *, db_path, country="", source="", vin="", model=None, host=None):
    time.sleep(0.006)
    return _fake_classify(
        text,
        db_path=db_path,
        country=country,
        source=source,
        vin=vin,
        model=model,
        host=host,
    )


def test_classify_results_committed_incrementally(classify_env, monkeypatch) -> None:
    """flush 阈值=10 时，分类结果应分多次短事务提交，并能在整批结束前被其他连接读到。"""
    main_mod, db_path = classify_env
    n = 60
    _seed(main_mod, n)

    import qwen_ollama
    import voc_classifier_service

    monkeypatch.setattr(qwen_ollama, "classify_text", _slow_classify)

    saw_partial = {"hit": False}
    stop = threading.Event()

    def watcher() -> None:
        while not stop.is_set():
            rconn = sqlite3.connect(db_path, timeout=5)
            try:
                cnt = rconn.execute(
                    "SELECT COUNT(*) FROM opinion WHERE TRIM(IFNULL(v3_label_meta,'')) != ''"
                ).fetchone()[0]
            finally:
                rconn.close()
            if 0 < int(cnt) < n:
                saw_partial["hit"] = True
            time.sleep(0.003)

    w = threading.Thread(target=watcher, name="watcher")
    w.start()
    try:
        result = voc_classifier_service.run_batch_classify(
            db_path,
            opinion_ids=[f"CLS_{i:04d}" for i in range(n)],
            use_llm=True,
            ollama_host="http://127.0.0.1:11434",
        )
    finally:
        stop.set()
        w.join(timeout=10)

    assert result.get("updated") == n
    # 若是旧的"整批最后一次性提交"，其他连接在结束前只会看到 0 条；分批提交则能看到中间态。
    assert saw_partial["hit"], "未观察到增量提交（其他连接在整批完成前看不到任何结果）"
