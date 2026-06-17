# -*- coding: utf-8 -*-
"""从复核库批量调用 label_project LabelMatcher 写入 v3_label_meta（stdout 静默）。"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

BACKEND_DIR = Path(__file__).resolve().parent
SRC_DIR = BACKEND_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
from config.paths import LABEL_PROJECT_DIR as LABEL_PROJECT_PATH

LABEL_PROJECT = LABEL_PROJECT_PATH
if str(LABEL_PROJECT) not in sys.path:
    sys.path.insert(0, str(LABEL_PROJECT))

from rule_conflict_detector import detect_rule_conflict
from classify_metrics_logger import ClassifyMetricsCollector, finalize_classify_metrics
from v3_materialized import materialized_update_params
from taxonomy_normalize import CANONICAL_L1_LABELS, canonicalize_l1_label

logger = logging.getLogger("voc.classifier")

# 延迟导入 offline_validate（依赖较重）
_matcher_cache: Dict[Tuple[bool, str], Any] = {}


def _get_matcher(use_llm: bool, ollama_host: str, db_path: Optional[str] = None):
    key = (use_llm, ollama_host, db_path or "")
    if key in _matcher_cache:
        return _matcher_cache[key]
    import offline_validate as ov

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        m = ov.LabelMatcher(
            ov.MAPPING_FILE,
            use_llm=use_llm,
            ollama_host=ollama_host,
            clean_csv_path=ov.DEFAULT_CLEAN_CSV,
            l2_whitelist_json=ov.DEFAULT_L2_WHITELIST_JSON,
            l3_cluster_json=ov.DEFAULT_L3_CLUSTER_JSON,
        )
        if db_path:
            try:
                n_gold = m.load_review_gold_db(db_path)
                logger.info("LabelMatcher 已注入复核金标缓存: %d 条 (db=%s)", n_gold, db_path)
            except Exception:
                logger.exception("注入复核金标缓存失败 (db=%s)", db_path)
    if use_llm:
        m._ollama_model_ok = ov.ollama_model_available(ollama_host, m.ollama_model)
        if not m._ollama_model_ok:
            m._model_warned = True
    _matcher_cache[key] = m
    return m


def refresh_matcher_gold_cache(db_path: str) -> int:
    """在确认/归档完成后调用：让进程内已缓存的 LabelMatcher 重新加载复核金标。

    新复核完的样本会在下一次推理时立即命中金标，无需重启服务。
    返回最新缓存条数（多个 matcher 实例时取最大值）。
    """
    if not db_path:
        return 0
    max_n = 0
    for m in _matcher_cache.values():
        try:
            m._review_gold_by_hash.clear()
            n = m.load_review_gold_db(db_path)
            if n > max_n:
                max_n = n
        except Exception:
            logger.exception("刷新 LabelMatcher 金标缓存失败")
    return max_n


def match_to_v3_meta(mr: Dict[str, Any]) -> Dict[str, Any]:
    mt = str(mr.get("match_type") or "")
    l3_src = ""
    if mt == "cluster_l3":
        l3_src = "cluster"
    elif mt.startswith("llm") or mt == "llm_l3":
        l3_src = "llm"
    l1_raw = (mr.get("level1") or "").strip()
    out = {
        "l1": canonicalize_l1_label(l1_raw) if l1_raw else "",
        "l2": mr.get("level2") or "",
        "l3": mr.get("level3") or "",
        "confidence": float(mr.get("confidence") or 0),
        "match_type": mt,
        "clean_hit": mt == "clean_l1",
        "l3_source": l3_src,
    }
    return out


def enforce_l2_whitelist_and_guard(
    meta: Dict[str, Any], text: str
) -> Dict[str, Any]:
    """
    后端二级标签校验兜底：
      1) 二级必须落在金标白名单内（否则标记 needs_review，等待人工修正）。
      2) 「咨询与表扬」专项驳回：原文存在负面/诉求/服务边界词时，强制 needs_review=True
         并把 L1/L2 重定位到更贴近的业务二级（不强行归类，仍走人工复核）。
    不改写原置信度判定逻辑，但显式置低置信度，触发 review_status=2。
    """
    try:
        from qwen_ollama import (
            apply_positive_consult_capture,
            consultation_l2_relocate,
            consultation_praise_misclass,
            load_l2_whitelist,
        )
        from taxonomy_normalize import is_non_issue_l1, strip_non_issue_l2
    except Exception:
        return meta

    l1 = (meta.get("l1") or "").strip()
    l2 = (meta.get("l2") or "").strip()
    if is_non_issue_l1(l1):
        meta = dict(meta)
        meta["l1"] = canonicalize_l1_label(l1)
        meta["l2"] = ""
        return meta
    if not l1 or not l2:
        return meta

    l2_map = load_l2_whitelist()
    allowed = list(l2_map.get(l1) or [])

    # 1) 二级白名单校验
    if allowed and l2 not in allowed:
        meta = dict(meta)
        meta["raw_model_l2"] = l2[:120]
        meta["l2"] = allowed[0]
        meta["needs_review"] = True
        meta["l2_whitelist_reject"] = True
        meta["confidence"] = min(float(meta.get("confidence") or 0), 0.4)
        l2 = meta["l2"]

    # 2) 咨询与表扬误判驳回（含负面/服务词时强制改判 + needs_review）
    if consultation_praise_misclass(text, l2):
        nl1, nl2 = consultation_l2_relocate(text, l2_map)
        nl_allowed = list(l2_map.get(nl1) or [])
        if nl_allowed and nl2 not in nl_allowed:
            nl2 = nl_allowed[0]
        meta = dict(meta)
        meta.setdefault("raw_model_l1", l1[:120])
        meta.setdefault("raw_model_l2", l2[:120])
        meta["l1"] = nl1
        meta["l2"] = nl2
        meta["needs_review"] = True
        meta["l2_consult_guard"] = True
        meta["confidence"] = min(float(meta.get("confidence") or 0), 0.45)
        l1 = nl1
        l2 = nl2

    # 3) 正向捕获：纯咨询/纯表扬/超短善意 → 强制 ("非问题","咨询与表扬")
    #    与 2) 互斥（含禁止词时正向捕获不会触发），并对规则路径误分类做正向收敛
    nl1c, nl2c, captured = apply_positive_consult_capture(text, l1, l2, l2_map)
    if captured:
        meta = dict(meta)
        meta.setdefault("raw_model_l1", l1[:120])
        meta.setdefault("raw_model_l2", l2[:120])
        meta["l1"] = nl1c
        meta["l2"] = nl2c
        # 正向捕获是确定性结论，不进待复核，直接落库
        meta["needs_review"] = False
        meta["positive_capture"] = True
        meta["confidence"] = max(float(meta.get("confidence") or 0), 0.85)
        # 已确定，清掉之前可能写过的"咨询误判驳回"标记
        meta.pop("l2_consult_guard", None)
        meta.pop("l2_whitelist_reject", None)

    meta = dict(meta)
    l1f, l2f = strip_non_issue_l2(meta.get("l1"), meta.get("l2"))
    if l1f:
        meta["l1"] = l1f
    meta["l2"] = l2f
    return meta


def run_batch_classify(
    db_path: str,
    upload_batch: Optional[str] = None,
    opinion_ids: Optional[List[str]] = None,
    use_llm: bool = False,
    ollama_host: str = "http://127.0.0.1:11434",
    low_confidence_threshold: float = 0.52,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[str, Any]:
    """
    对指定批次或 id 列表跑分类，更新 v3_label_meta；低置信度标为 review_status=2（存疑/待复核）。
    """
    use_qwen14 = use_llm and os.environ.get("VOC_QWEN_DIRECT", "1") != "0"
    matcher = None
    if not use_qwen14:
        try:
            matcher = _get_matcher(use_llm, ollama_host, db_path)
        except Exception as e:
            logger.exception("规则分类器 LabelMatcher 初始化失败（路径/词库/标签 JSON 异常）")
            return {
                "code": 500,
                "msg": f"规则分类器初始化失败: {e}",
                "updated": 0,
                "low_confidence_count": 0,
                "failed_count": 0,
                "errors": [str(e)],
            }
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if opinion_ids is not None:
        if len(opinion_ids) == 0:
            conn.close()
            return {"code": 400, "msg": "opinion_ids 为空", "updated": 0}
        q = f"SELECT id, opinion_id, original_text, country, review_status FROM opinion WHERE opinion_id IN ({','.join(['?']*len(opinion_ids))})"
        c.execute(q, opinion_ids)
    elif upload_batch:
        c.execute(
            "SELECT id, opinion_id, original_text, country, review_status FROM opinion WHERE upload_batch = ?",
            (upload_batch,),
        )
    else:
        conn.close()
        return {"code": 400, "msg": "需要 upload_batch 或 opinion_ids", "updated": 0}
    rows = c.fetchall()
    # 读完即关连接：分类期间（数十分钟）不持有任何连接/写锁，避免阻塞复核确认、回流等并发写。
    conn.close()
    total = len(rows)
    started_at = time.time()
    metrics_collector = ClassifyMetricsCollector()
    updated = 0
    low_n = 0
    failed_n = 0
    conflict_n = 0
    errors: List[str] = []
    # 14B：每批 8 条后批次间休眠 500ms（可环境变量覆盖），降低 Ollama 瞬时负载
    batch_sz = max(1, min(20, int(os.environ.get("VOC_QWEN_BATCH_SIZE", "8"))))
    inter_batch_sleep = float(os.environ.get("VOC_QWEN_INTER_BATCH_SLEEP", "0.5"))

    # 分批短事务提交：分类结果先缓冲，每 commit_every 条用一次性短连接写库并提交，
    # 写锁仅在毫秒级的 executemany 期间持有（不跨越慢速 Ollama 调用），避免长事务饿死其他写操作。
    commit_every = max(1, int(os.environ.get("VOC_QWEN_COMMIT_EVERY", "10")))
    checkpoint_every = max(0, int(os.environ.get("VOC_QWEN_CHECKPOINT_EVERY", "5")))
    pending_writes: List[tuple] = []
    flush_state = {"count": 0}
    update_sql = (
        "UPDATE opinion SET v3_label_meta = ?, match_score = ?, "
        "v3_l1 = ?, v3_l2 = ?, v3_l3 = ?, v3_confidence = ?, v3_match_type = ?, "
        "review_status = CASE WHEN review_status = 1 THEN 1 "
        "ELSE (CASE WHEN ? = 1 THEN 2 WHEN ? < ? THEN 2 ELSE 0 END) END WHERE id = ?"
    )

    def _flush_writes() -> None:
        if not pending_writes:
            return
        last_err: Optional[Exception] = None
        for attempt in range(3):
            wconn = sqlite3.connect(db_path, timeout=60)
            try:
                wconn.execute("PRAGMA busy_timeout=60000")
                wconn.executemany(update_sql, pending_writes)
                wconn.commit()
                flush_state["count"] += 1
                if checkpoint_every and (flush_state["count"] % checkpoint_every == 0):
                    try:
                        wconn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    except Exception:
                        pass
                pending_writes.clear()
                return
            except Exception as e:
                last_err = e
                try:
                    wconn.rollback()
                except Exception:
                    pass
                logger.warning("分类结果落库重试 attempt=%d: %s", attempt + 1, e)
                time.sleep(0.5 * (attempt + 1))
            finally:
                wconn.close()
        assert last_err is not None
        raise last_err

    for idx, row in enumerate(rows, start=1):
        oid = row["opinion_id"]
        if progress_cb:
            progress_cb(idx - 1, total, f"正在分类 {idx}/{total}: {oid}")
        text = (row["original_text"] or "").strip()
        if not text:
            errors.append(f"{oid}: 空原文")
            failed_n += 1
            metrics_collector.record_empty()
            continue
        try:
            if use_qwen14:
                from qwen_ollama import classify_text

                try:
                    q = None
                    row_retry_sleep = float(os.environ.get("VOC_QWEN_ROW_RETRY_SLEEP", "0.45"))
                    for attempt in range(2):
                        try:
                            q = classify_text(
                                text,
                                db_path=db_path,
                                country=row["country"] or "",
                                host=ollama_host,
                            )
                            break
                        except Exception as qtry:
                            if attempt == 0:
                                logger.warning(
                                    "Qwen14B 单条异常将重试 opinion_id=%s: %s", oid, qtry
                                )
                                time.sleep(row_retry_sleep)
                                continue
                            raise qtry
                    meta = {
                        "l1": q.get("l1") or "",
                        "l2": q.get("l2") or "",
                        "l3": q.get("l3") or "",
                        "confidence": float(q.get("confidence") or 0),
                        "match_type": q.get("match_type") or "qwen14b_structured",
                        "clean_hit": False,
                        "l3_source": "llm",
                        "risk_level": q.get("risk_level") or "低",
                        "keywords": q.get("keywords") or [],
                        "model": q.get("model") or os.environ.get("VOC_QWEN_MODEL", "qwen2.5:14b-instruct-q4_K_M"),
                        "cache_hit": bool(q.get("cache_hit")),
                        "needs_review": bool(q.get("needs_review")),
                    }
                    if q.get("error"):
                        meta["error"] = q.get("error")
                    if q.get("raw_model_l1") is not None:
                        meta["raw_model_l1"] = q.get("raw_model_l1")
                    if q.get("raw_model_l2") is not None:
                        meta["raw_model_l2"] = q.get("raw_model_l2")
                except Exception as qerr:
                    # 14B 单条推理失败：标记待人工复核，不降级规则分类，不中断整批
                    logger.warning("Qwen14B 单条失败 opinion_id=%s: %s", oid, qerr)
                    errors.append(f"{oid}: Qwen14B失败: {qerr}")
                    meta = {
                        "l1": "",
                        "l2": "",
                        "l3": "",
                        "confidence": 0.0,
                        "match_type": "qwen_row_failed",
                        "clean_hit": False,
                        "l3_source": "",
                        "needs_review": True,
                        "error": str(qerr)[:300],
                    }
            else:
                assert matcher is not None
                try:
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        mr = matcher.match(text, None)
                    if not mr:
                        meta = {
                            "l1": "",
                            "l2": "",
                            "l3": "",
                            "confidence": 0.0,
                            "match_type": "none",
                            "clean_hit": False,
                            "l3_source": "",
                        }
                    else:
                        meta = match_to_v3_meta(mr)
                except Exception as ferr:
                    logger.exception("规则分类失败 opinion_id=%s", oid)
                    errors.append(f"{oid}: 规则分类失败: {ferr}")
                    meta = {
                        "l1": "",
                        "l2": "",
                        "l3": "",
                        "confidence": 0.0,
                        "match_type": "classify_error",
                        "clean_hit": False,
                        "l3_source": "",
                        "error": str(ferr)[:300],
                    }
                    failed_n += 1
            # 统一的二级白名单 + 咨询与表扬误判兜底（规则/LLM 路径共享）
            # 复核金标命中（match_type=gold_review）是人工最终结论，不再走联防，避免被回写。
            if meta.get("match_type") != "gold_review":
                try:
                    meta = enforce_l2_whitelist_and_guard(meta, text)
                except Exception:
                    logger.exception("enforce_l2_whitelist_and_guard 失败 opinion_id=%s", oid)
            # 双重特征冲突 → 路由到 14B 仲裁：仅作用于规则路径（非 14B 直推、非金标命中、
            # 且联防未已经标记 needs_review 的条目），命中即降低置信度并进待复核。
            was_conflict = False
            if (
                not use_qwen14
                and meta.get("match_type") not in ("gold_review", "classify_error", "none")
                and not meta.get("needs_review")
            ):
                try:
                    ck = detect_rule_conflict(meta, text)
                except Exception:
                    ck = ""
                if ck:
                    was_conflict = True
                    meta["arbitration_required"] = True
                    meta["conflict_kind"] = ck
                    meta["confidence"] = min(float(meta.get("confidence") or 0), 0.40)
                    meta["needs_review"] = True
                    conflict_n += 1
            conf = float(meta.get("confidence") or 0)
            force_pending = bool(meta.get("needs_review")) or meta.get("match_type") in (
                "qwen_parse_failed",
                "qwen_whitelist_reject",
                "qwen_row_failed",
            )
            if force_pending or conf < low_confidence_threshold:
                low_n += 1
            v3_json = json.dumps(meta, ensure_ascii=False)
            force_i = 1 if force_pending else 0
            v3_l1, v3_l2, v3_l3, v3_conf, v3_mt = materialized_update_params(meta)
            pending_writes.append(
                (
                    v3_json,
                    conf,
                    v3_l1,
                    v3_l2,
                    v3_l3,
                    v3_conf,
                    v3_mt,
                    force_i,
                    conf,
                    low_confidence_threshold,
                    row["id"],
                )
            )
            metrics_collector.record_row(
                meta,
                use_qwen14=use_qwen14,
                was_conflict=was_conflict,
                low_confidence_threshold=low_confidence_threshold,
            )
            updated += 1
        except Exception as e:
            logger.exception("分类单条异常 opinion_id=%s", oid)
            errors.append(f"{oid}: {e}")
            failed_n += 1
            metrics_collector.record_error()
        if progress_cb:
            progress_cb(idx, total, f"正在分类 {idx}/{total}")
        # 达到提交阈值即落库（短事务），随后再节流；保证断点结果持久、写锁尽快释放。
        if len(pending_writes) >= commit_every:
            _flush_writes()
        if use_qwen14:
            # 14B 本地推理串行节流；按批稍作停顿，避免 M3 Max 上请求堆积。
            time.sleep(float(os.environ.get("VOC_QWEN_BATCH_SLEEP", "0.15")))
            if idx % batch_sz == 0 and idx < total:
                time.sleep(inter_batch_sleep)
    _flush_writes()
    route_metrics = finalize_classify_metrics(
        metrics_collector,
        upload_batch=upload_batch,
        opinion_ids=opinion_ids,
        use_llm=use_llm,
        total=total,
        updated=updated,
        started_at=started_at,
        low_confidence_count=low_n,
        conflict_count=conflict_n,
        failed_count=failed_n,
    )
    msg_parts = [f"已分类 {updated} 条"]
    if conflict_n:
        msg_parts.append(f"规则冲突待 14B 仲裁 {conflict_n} 条")
    msg_parts.append(f"低置信度待复核 {low_n} 条")
    msg_parts.append(f"异常 {failed_n} 条")
    msg_parts.append(
        f"路由 gold={route_metrics.get('gold_hit', 0)} "
        f"conflict={route_metrics.get('conflict', 0)} "
        f"llm={route_metrics.get('llm_arbitrated', 0)}"
    )
    return {
        "code": 200,
        "msg": "，".join(msg_parts),
        "updated": updated,
        "low_confidence_count": low_n,
        "failed_count": failed_n,
        "conflict_count": conflict_n,
        "metrics": route_metrics,
        "errors": errors[:50],
    }


_TAXONOMY_CACHE_KEY: Optional[float] = None
_TAXONOMY_WL_CACHE: dict = {}
_TAXONOMY_BY_L2_CACHE: dict = {}


_HIST_L2_MTIME: float = -1.0
_HIST_L2_LOADED_AT: float = 0.0
_HIST_L2_BY_L1: Dict[str, List[str]] = {}
_HIST_L2_TTL = float(os.environ.get("VOC_HIST_L2_CACHE_TTL", "120"))


def get_historical_l2_by_l1(db_path: Optional[str] = None) -> Dict[str, List[str]]:
    """从复核库聚合历史二级（按规范一级分组）；TTL 内存缓存，避免每次自动保存改 mtime 后全表重扫。"""
    global _HIST_L2_MTIME, _HIST_L2_LOADED_AT, _HIST_L2_BY_L1
    p = Path(db_path) if db_path else (BACKEND_DIR / "opinion_review.db")
    if not p.is_file():
        return {}
    now = time.time()
    if _HIST_L2_BY_L1 and (now - _HIST_L2_LOADED_AT) < _HIST_L2_TTL:
        return _HIST_L2_BY_L1
    buckets: Dict[str, set] = {x: set() for x in CANONICAL_L1_LABELS}
    conn = sqlite3.connect(str(p))
    c = conn.cursor()
    for r1, r2 in c.execute(
        """
        SELECT review_l1, review_l2 FROM opinion
        WHERE review_l2 IS NOT NULL AND TRIM(review_l2) != ''
        """
    ):
        l1 = canonicalize_l1_label(r1)
        if l1 in buckets:
            buckets[l1].add((r2 or "").strip())
    for mc, mk in c.execute(
        """
        SELECT model_class, model_keyword FROM opinion
        WHERE model_keyword IS NOT NULL AND TRIM(model_keyword) != ''
        """
    ):
        l1 = canonicalize_l1_label(mc)
        part = (mk or "").split(",")[0].strip()
        if l1 in buckets and part:
            buckets[l1].add(part)
    for (raw_meta,) in c.execute(
        """
        SELECT v3_label_meta FROM opinion
        WHERE v3_label_meta IS NOT NULL AND TRIM(v3_label_meta) != ''
        AND v3_label_meta != '{}'
        """
    ):
        try:
            j = json.loads(raw_meta)
            l1 = canonicalize_l1_label(j.get("l1"))
            l2 = (j.get("l2") or "").strip()
            if l1 in buckets and l2:
                buckets[l1].add(l2)
        except Exception:
            pass
    conn.close()
    _HIST_L2_BY_L1 = {k: sorted(v) for k, v in buckets.items() if v}
    _HIST_L2_MTIME = p.stat().st_mtime
    _HIST_L2_LOADED_AT = now
    return _HIST_L2_BY_L1


def invalidate_taxonomy_cache() -> None:
    """金标 JSON 更新后清空缓存。"""
    global _TAXONOMY_CACHE_KEY, _TAXONOMY_WL_CACHE, _TAXONOMY_BY_L2_CACHE
    global _HIST_L2_MTIME, _HIST_L2_LOADED_AT, _HIST_L2_BY_L1
    _TAXONOMY_CACHE_KEY = None
    _TAXONOMY_WL_CACHE = {}
    _TAXONOMY_BY_L2_CACHE = {}
    _HIST_L2_MTIME = -1.0
    _HIST_L2_LOADED_AT = 0.0
    _HIST_L2_BY_L1 = {}


def load_taxonomy_json() -> Tuple[dict, dict]:
    """读取金标白名单与三级聚类；按文件 mtime 内存缓存，避免大数据量下列表反复读盘。"""
    global _TAXONOMY_CACHE_KEY, _TAXONOMY_WL_CACHE, _TAXONOMY_BY_L2_CACHE
    wl_path = LABEL_PROJECT / "gold_l2_whitelist_v3.json"
    cl_path = LABEL_PROJECT / "gold_l3_clusters_v3.json"
    mkey = 0.0
    for p in (wl_path, cl_path):
        if p.is_file():
            mkey += p.stat().st_mtime
    if _TAXONOMY_CACHE_KEY is not None and mkey == _TAXONOMY_CACHE_KEY:
        return _TAXONOMY_WL_CACHE, _TAXONOMY_BY_L2_CACHE
    wl = {}
    cl = {}
    if wl_path.is_file():
        with open(wl_path, "r", encoding="utf-8") as f:
            wl = json.load(f)
    if cl_path.is_file():
        with open(cl_path, "r", encoding="utf-8") as f:
            cl = json.load(f)
    by_l1_l2 = cl.get("by_l1_l2") or {}
    _TAXONOMY_CACHE_KEY = mkey
    _TAXONOMY_WL_CACHE = wl
    _TAXONOMY_BY_L2_CACHE = by_l1_l2
    return wl, by_l1_l2


def l2_whitelist_map_for_l1_list(
    l1_keys: List[str], db_path: Optional[str] = None
) -> Dict[str, List[str]]:
    """批量返回各一级下的二级白名单合并列表（金标优先 ∪ 体系 ∪ 历史库），供复核页一次请求拉齐。"""
    wl, _ = load_taxonomy_json()
    dp = db_path or str(BACKEND_DIR / "opinion_review.db")
    uniq: List[str] = []
    seen: set = set()
    for k in l1_keys:
        k2 = (k or "").strip()
        if k2 and k2 not in seen:
            seen.add(k2)
            uniq.append(k2)
    out: Dict[str, List[str]] = {}
    for l1 in uniq:
        l1c = canonicalize_l1_label(l1)
        lst = merged_l2_whitelist_for_l1(wl, l1c, db_path=dp)
        out[l1] = lst
        if l1c != l1:
            out[l1c] = lst
    return out


_HIERARCHY_JSON: Optional[dict] = None
_HIERARCHY_L2_CACHE: Dict[str, List[str]] = {}


def load_label_hierarchy() -> dict:
    """label_hierarchy_final.json（仅内存缓存）。"""
    global _HIERARCHY_JSON
    if _HIERARCHY_JSON is not None:
        return _HIERARCHY_JSON
    p = LABEL_PROJECT / "label_hierarchy_final.json"
    if not p.is_file():
        _HIERARCHY_JSON = {}
        return _HIERARCHY_JSON
    with open(p, "r", encoding="utf-8") as f:
        _HIERARCHY_JSON = json.load(f)
    return _HIERARCHY_JSON


def hierarchy_l2_keys(l1: str) -> List[str]:
    """某一级下所有二级键名（与体系树一致）。"""
    if l1 in _HIERARCHY_L2_CACHE:
        return _HIERARCHY_L2_CACHE[l1]
    data = load_label_hierarchy()
    block = data.get(l1)
    if not isinstance(block, dict):
        _HIERARCHY_L2_CACHE[l1] = []
        return []
    keys = [k for k in block.keys() if not str(k).startswith("_")]
    _HIERARCHY_L2_CACHE[l1] = keys
    return keys


def flatten_all_hierarchy_l2() -> List[str]:
    """全体系二级并集（接口兜底，避免前端 No data）。"""
    data = load_label_hierarchy()
    seen: set = set()
    out: List[str] = []
    for l1_key, block in data.items():
        if str(l1_key).startswith("_"):
            continue
        if not isinstance(block, dict):
            continue
        for k in block.keys():
            if str(k).startswith("_"):
                continue
            kk = str(k).strip()
            if kk and kk not in seen:
                seen.add(kk)
                out.append(kk)
    out.sort()
    return out


def merged_l2_whitelist_for_l1(
    wl: dict,
    l1: str,
    extra: Optional[List[str]] = None,
    db_path: Optional[str] = None,
) -> List[str]:
    """金标顺序优先 ∪ 体系二级 ∪ 历史库 ∪ 额外项。"""
    l1c = canonicalize_l1_label(l1)
    gold = list(wl.get(l1c) or [])
    hier = hierarchy_l2_keys(l1c)
    hist_vals: List[str] = []
    if db_path:
        hist_vals = list(get_historical_l2_by_l1(db_path).get(l1c) or [])
    s = set(gold) | set(hier) | set(hist_vals)
    if extra:
        for x in extra:
            t = (x or "").strip()
            if t:
                s.add(t)
    seen: set = set()
    out: List[str] = []
    for x in gold:
        if x in s and x not in seen:
            out.append(x)
            seen.add(x)
    out.extend(sorted(s - seen, key=lambda x: x))
    if not out:
        fb = hierarchy_l2_keys(l1c)
        if fb:
            return sorted(set(fb))
        flat = flatten_all_hierarchy_l2()
        if flat:
            return flat
    return out


def merge_gold_feedback(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """将人工复核的 l2/l3 合并进金标 JSON（备份 .bak）。"""
    wl_path = LABEL_PROJECT / "gold_l2_whitelist_v3.json"
    cl_path = LABEL_PROJECT / "gold_l3_clusters_v3.json"
    wl, by_l1_l2 = load_taxonomy_json()
    meta = {}
    if cl_path.is_file():
        try:
            with open(cl_path, "r", encoding="utf-8") as f:
                _raw = json.load(f)
            meta = _raw.get("_meta") or {}
            if not by_l1_l2:
                by_l1_l2 = _raw.get("by_l1_l2") or {}
        except Exception:
            pass
    if not wl and not by_l1_l2:
        return {"code": 400, "msg": "金标文件不存在或为空"}

    for it in items:
        l1 = canonicalize_l1_label(it.get("l1") or "")
        l2 = (it.get("l2") or "").strip()
        l3 = (it.get("l3") or "").strip()
        snippet = (it.get("text_snippet") or "")[:80]
        if not l1 or not l2:
            continue
        if l1 not in wl:
            wl[l1] = []
        if l2 not in wl[l1]:
            wl[l1].append(l2)
        if l1 not in by_l1_l2:
            by_l1_l2[l1] = {}
        if l2 not in by_l1_l2[l1]:
            by_l1_l2[l1][l2] = []
        if l3:
            block = by_l1_l2[l1][l2]
            canon = [x.get("canonical") for x in block if isinstance(x, dict)]
            if l3 not in canon:
                kws = []
                if snippet:
                    for i in range(0, min(len(snippet), 24), 2):
                        kws.append(snippet[i : i + 2])
                block.append(
                    {
                        "canonical": l3,
                        "count": 1,
                        "synonyms": [],
                        "keywords": kws or [l3[:4]],
                    }
                )

    if wl_path.is_file():
        bak = wl_path.with_suffix(".json.bak")
        try:
            import shutil

            shutil.copy2(wl_path, bak)
        except Exception:
            pass
    if cl_path.is_file():
        bak = cl_path.with_suffix(".json.bak")
        try:
            import shutil

            shutil.copy2(cl_path, bak)
        except Exception:
            pass

    with open(wl_path, "w", encoding="utf-8") as f:
        json.dump(wl, f, ensure_ascii=False, indent=2)
    out_clusters = {"_meta": meta, "by_l1_l2": by_l1_l2}
    with open(cl_path, "w", encoding="utf-8") as f:
        json.dump(out_clusters, f, ensure_ascii=False, indent=2)

    # 清缓存以便下次分类重新加载
    _matcher_cache.clear()
    invalidate_taxonomy_cache()
    try:
        from qwen_ollama import invalidate_l2_whitelist_cache

        invalidate_l2_whitelist_cache()
    except Exception:
        pass
    return {"code": 200, "msg": f"已回流 {len(items)} 条至金标文件（已备份 .bak）"}
