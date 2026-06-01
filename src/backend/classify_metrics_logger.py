# -*- coding: utf-8 -*-
"""分类路由指标：gold_hit / conflict / llm_arbitrated 等，供迭代调词与评估。"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("voc.classify_metrics")

BACKEND_DIR = Path(__file__).resolve().parent
METRICS_LOG_PATH = Path(
    os.environ.get("VOC_CLASSIFY_METRICS_LOG", str(BACKEND_DIR / "classify_route_metrics.jsonl"))
).resolve()
METRICS_LOG_MAX_LINES = int(os.environ.get("VOC_CLASSIFY_METRICS_MAX", "500"))

_FILE_LOCK = threading.Lock()


class ClassifyMetricsCollector:
    """单次 batch_classify 运行的路由计数器。"""

    def __init__(self) -> None:
        self.gold_hit = 0
        self.conflict = 0
        self.conflict_by_kind: Counter = Counter()
        self.llm_arbitrated = 0
        self.llm_failed = 0
        self.llm_cache_hit = 0
        self.rule_ok = 0
        self.rule_low_conf = 0
        self.guard_triggered = 0
        self.failed = 0
        self.skipped_empty = 0

    def record_empty(self) -> None:
        self.skipped_empty += 1
        self.failed += 1

    def record_error(self) -> None:
        self.failed += 1

    def record_row(
        self,
        meta: Dict[str, Any],
        *,
        use_qwen14: bool,
        was_conflict: bool,
        low_confidence_threshold: float,
    ) -> None:
        mt = str(meta.get("match_type") or "")

        if mt == "gold_review":
            self.gold_hit += 1
            return

        if use_qwen14:
            if mt in ("qwen_row_failed", "classify_error"):
                self.llm_failed += 1
            else:
                self.llm_arbitrated += 1
                if meta.get("cache_hit"):
                    self.llm_cache_hit += 1
            return

        if was_conflict:
            self.conflict += 1
            ck = str(meta.get("conflict_kind") or "").strip()
            if ck:
                self.conflict_by_kind[ck] += 1
            return

        if mt in ("classify_error", "none"):
            self.failed += 1
            return

        if meta.get("positive_capture") or meta.get("l2_whitelist_reject") or meta.get("l2_consult_guard"):
            self.guard_triggered += 1

        conf = float(meta.get("confidence") or 0)
        force_pending = bool(meta.get("needs_review")) or mt in (
            "qwen_parse_failed",
            "qwen_whitelist_reject",
            "qwen_row_failed",
            "classify_error",
            "none",
        )
        if force_pending or conf < low_confidence_threshold:
            self.rule_low_conf += 1
        else:
            self.rule_ok += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gold_hit": self.gold_hit,
            "conflict": self.conflict,
            "conflict_by_kind": dict(self.conflict_by_kind),
            "llm_arbitrated": self.llm_arbitrated,
            "llm_failed": self.llm_failed,
            "llm_cache_hit": self.llm_cache_hit,
            "rule_ok": self.rule_ok,
            "rule_low_conf": self.rule_low_conf,
            "guard_triggered": self.guard_triggered,
            "failed": self.failed,
            "skipped_empty": self.skipped_empty,
        }


def _append_metrics_entry(entry: Dict[str, Any]) -> None:
    line = json.dumps(entry, ensure_ascii=False)
    with _FILE_LOCK:
        METRICS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with METRICS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        try:
            if METRICS_LOG_PATH.is_file():
                lines = METRICS_LOG_PATH.read_text(encoding="utf-8").splitlines()
                if len(lines) > METRICS_LOG_MAX_LINES:
                    keep = lines[-METRICS_LOG_MAX_LINES:]
                    METRICS_LOG_PATH.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except Exception:
            logger.exception("trim classify metrics log failed")


def finalize_classify_metrics(
    collector: ClassifyMetricsCollector,
    *,
    upload_batch: Optional[str],
    opinion_ids: Optional[List[str]],
    use_llm: bool,
    total: int,
    updated: int,
    started_at: float,
    low_confidence_count: int = 0,
    conflict_count: int = 0,
    failed_count: int = 0,
) -> Dict[str, Any]:
    """汇总指标、写 JSONL、打 INFO 日志，返回 metrics 字典。"""
    duration_sec = round(max(0.0, time.time() - started_at), 3)
    counts = collector.to_dict()
    entry: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "upload_batch": upload_batch or "",
        "opinion_ids_count": len(opinion_ids) if opinion_ids else 0,
        "scoped_by_ids": bool(opinion_ids),
        "use_llm": bool(use_llm),
        "total": int(total),
        "updated": int(updated),
        "duration_sec": duration_sec,
        "low_confidence_count": int(low_confidence_count),
        "conflict_count": int(conflict_count),
        "failed_count": int(failed_count),
        **counts,
    }
    try:
        _append_metrics_entry(entry)
    except Exception:
        logger.exception("写入分类路由指标失败")

    c1 = counts["conflict_by_kind"].get("C1", 0)
    c2 = counts["conflict_by_kind"].get("C2", 0)
    c3 = counts["conflict_by_kind"].get("C3", 0)
    logger.info(
        "分类路由统计 batch=%s scoped_ids=%s total=%d updated=%d "
        "gold_hit=%d conflict=%d (C1=%d C2=%d C3=%d) "
        "llm_arbitrated=%d llm_failed=%d rule_ok=%d rule_low_conf=%d "
        "guard=%d failed=%d duration=%.2fs",
        upload_batch or "-",
        bool(opinion_ids),
        total,
        updated,
        counts["gold_hit"],
        counts["conflict"],
        c1,
        c2,
        c3,
        counts["llm_arbitrated"],
        counts["llm_failed"],
        counts["rule_ok"],
        counts["rule_low_conf"],
        counts["guard_triggered"],
        counts["failed"],
        duration_sec,
    )
    return entry


def read_classify_route_metrics(limit: int = 50) -> List[Dict[str, Any]]:
    """读取最近 N 条分类路由指标（JSONL 倒序）。"""
    lim = max(1, min(500, int(limit or 50)))
    if not METRICS_LOG_PATH.is_file():
        return []
    try:
        lines = METRICS_LOG_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
        if len(out) >= lim:
            break
    return out
