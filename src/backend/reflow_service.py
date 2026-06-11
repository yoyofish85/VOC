# -*- coding: utf-8 -*-
"""复核回流：清洗库 upsert、审计、详细日志、关键词与金标。"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BACKEND_DIR = Path(__file__).resolve().parent
SRC_DIR = BACKEND_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
from config.paths import LABELED_DIR as _LAB_DIR

# 自动化测试：VOC_TEST_ARTIFACTS_DIR 下隔离清洗库与审计日志，不写生产 data_clean.csv
_test_art = os.environ.get("VOC_TEST_ARTIFACTS_DIR")
if _test_art:
    _td = Path(_test_art)
    DATA_CLEAR_PATH = _td / "data_clear.csv"
    AUDIT_LOG_PATH = _td / "review_reflow_audit.jsonl"
    REFLOW_DETAIL_LOG_PATH = _td / "reflow_detail.jsonl"
else:
    DATA_CLEAR_PATH = _LAB_DIR / "data_clear.csv"
    AUDIT_LOG_PATH = BACKEND_DIR / "review_reflow_audit.jsonl"
    REFLOW_DETAIL_LOG_PATH = BACKEND_DIR / "reflow_detail.jsonl"

FIELDNAMES = [
    "舆情编号",
    "原文",
    "日期",
    "模型分类结果",
    "置信度",
    "人工分类",
    "筛选原因",
    "人工复核结果",
    "修正原因",
]


def _parse_v3(meta_raw: Any) -> Dict[str, Any]:
    if not meta_raw:
        return {}
    if isinstance(meta_raw, dict):
        return meta_raw
    try:
        return json.loads(str(meta_raw))
    except Exception:
        return {}


def append_audit(entry: Dict[str, Any]) -> None:
    entry["ts"] = datetime.now().isoformat()
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_detail_log(entry: Dict[str, Any]) -> None:
    """详细回流日志：原文、修正前后、时间（供人工核验）。"""
    entry["reflow_time"] = datetime.now().isoformat(timespec="seconds")
    REFLOW_DETAIL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REFLOW_DETAIL_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_data_clear_index() -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """读取清洗库为 id->行；保留首次出现顺序。"""
    by_id: Dict[str, Dict[str, str]] = {}
    order: List[str] = []
    if not DATA_CLEAR_PATH.is_file():
        return by_id, order
    with open(DATA_CLEAR_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            for fn in FIELDNAMES:
                if fn not in reader.fieldnames:
                    pass
        for r in reader:
            oid = (r.get("舆情编号") or "").strip()
            if not oid:
                continue
            row = {k: (r.get(k) or "") for k in FIELDNAMES}
            if oid not in by_id:
                order.append(oid)
            by_id[oid] = row
    return by_id, order


def _write_data_clear_full(by_id: Dict[str, Dict[str, str]], order: List[str]) -> None:
    DATA_CLEAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_CLEAR_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        seen = set()
        for oid in order:
            if oid in by_id:
                w.writerow(by_id[oid])
                seen.add(oid)
        for oid, row in by_id.items():
            if oid not in seen:
                w.writerow(row)


def _build_clear_row(
    opinion_id: str,
    original_text: str,
    create_time: str,
    model_l1: str,
    conf: str,
    import_l1: str,
    filter_reason: str,
    human_final_l1: str,
    fix_reason: str,
) -> Dict[str, str]:
    return {
        "舆情编号": opinion_id,
        "原文": original_text or "",
        "日期": create_time or "",
        "模型分类结果": model_l1 or "",
        "置信度": conf if conf != "" else "0",
        "人工分类": import_l1 or "",
        "筛选原因": filter_reason or "VOC工作台",
        "人工复核结果": human_final_l1 or "",
        "修正原因": fix_reason or "",
    }


def upsert_data_clear_row(
    opinion_id: str,
    original_text: str,
    create_time: str,
    model_l1: str,
    conf: str,
    import_l1: str,
    filter_reason: str,
    human_final_l1: str,
    fix_reason: str,
) -> bool:
    """
    按舆情编号 upsert清洗库一行。
    Returns True若该编号此前已存在（覆盖），False 为新建。
    """
    by_id, order = _load_data_clear_index()
    oid = opinion_id.strip()
    existed = oid in by_id
    row = _build_clear_row(
        opinion_id,
        original_text,
        create_time,
        model_l1,
        conf,
        import_l1,
        filter_reason,
        human_final_l1,
        fix_reason,
    )
    if not existed:
        order.append(oid)
    by_id[oid] = row
    _write_data_clear_full(by_id, order)
    return existed


def reflow_batch_rows(
    rows: List[Dict[str, Any]],
    keyword_extractor: Any,
    reviewer: str = "",
    *,
    keyword_boost: bool = True,
    merge_gold: bool = True,
    trigger: str = "confirm",
    skip_keyword_gold_for_ids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """
    批量回流：清洗库 upsert（同编号覆盖）；关键词/金标可单独关闭（如归档时仅补写库）。
    skip_keyword_gold_for_ids：已在确认阶段完成关键词/金标的舆情编号，归档时跳过重复加权。
    返回 reflowed / new_in_clear / overwritten_in_clear
    """
    from voc_classifier_service import merge_gold_feedback

    if os.environ.get("VOC_REFLOW_MERGE_GOLD") == "0":
        merge_gold = False
    if os.environ.get("VOC_REFLOW_KEYWORD_BOOST") == "0":
        keyword_boost = False

    n_ok = 0
    new_c = 0
    over_c = 0
    errs: List[str] = []
    gold_items: List[Dict[str, Any]] = []
    by_id, order = _load_data_clear_index()

    for row in rows:
        oid = (row.get("opinion_id") or "").strip()
        text = (row.get("original_text") or "").strip()
        human_l1 = (row.get("review_l1") or "").strip()
        human_l2 = (row.get("review_l2") or "").strip()
        if not oid:
            errs.append("缺少舆情编号")
            continue
        if not human_l1:
            errs.append(f"{oid}: 缺少人工一级")
            continue

        v3 = _parse_v3(row.get("v3_label_meta"))
        auto_l1 = str(v3.get("l1") or "")
        auto_l2 = str(v3.get("l2") or "")
        conf = v3.get("confidence")
        conf_s = str(conf) if conf is not None else ""

        kws = keyword_extractor.extract_keywords(text)[:30]
        extracted = ",".join(kws)

        fix_reason = (
            f"VOC回流|L1:{auto_l1}→{human_l1};L2:{auto_l2}→{human_l2};"
            f"三级关键词:{extracted}|复核人:{reviewer or '—'}|{datetime.now().isoformat(timespec='seconds')}"
        )

        was_overwrite = oid in by_id
        clear_row = _build_clear_row(
            oid,
            text,
            row.get("create_time") or "",
            auto_l1,
            conf_s,
            row.get("model_class") or "",
            "VOC复核",
            human_l1,
            fix_reason,
        )
        if was_overwrite:
            over_c += 1
        else:
            new_c += 1
            order.append(oid)
        by_id[oid] = clear_row

        skip_kw = bool(skip_keyword_gold_for_ids and oid in skip_keyword_gold_for_ids)

        if keyword_boost and not skip_kw:
            keyword_extractor.apply_review_reflow_boost(human_l1, kws)

        if merge_gold and not skip_kw:
            gold_items.append(
                {
                    "l1": human_l1,
                    "l2": human_l2 or auto_l2,
                    "l3": "",
                    "text_snippet": text[:120],
                }
            )

        audit = {
            "opinion_id": oid,
            "reviewer": reviewer or None,
            "before": {"l1": auto_l1, "l2": auto_l2, "v3_l3": str(v3.get("l3") or "")},
            "after": {"l1": human_l1, "l2": human_l2, "keywords_l3": extracted},
            "upload_batch": row.get("upload_batch"),
            "clear_row_action": "overwrite" if was_overwrite else "insert",
            "trigger": trigger,
        }
        append_audit(audit)

        text_for_log = text[:4000] if len(text) > 4000 else text
        append_detail_log(
            {
                "opinion_id": oid,
                "original_text": text_for_log,
                "before_l1": auto_l1,
                "before_l2": auto_l2,
                "after_l1": human_l1,
                "after_l2": human_l2,
                "keywords_l3": extracted,
                "reviewer": reviewer or None,
                "trigger": trigger,
                "data_clear_action": "overwrite" if was_overwrite else "insert",
            }
        )
        n_ok += 1

    if n_ok > 0:
        _write_data_clear_full(by_id, order)

    if merge_gold and gold_items:
        gold_res = merge_gold_feedback(gold_items)
        if int(gold_res.get("code") or 0) != 200:
            raise RuntimeError(str(gold_res.get("msg") or "merge_gold_feedback failed"))

    if rows and n_ok == 0:
        raise RuntimeError(errs[0] if errs else "reflow_batch_rows: no rows reflowed")

    return {
        "code": 200,
        "reflowed": n_ok,
        "new_in_clear": new_c,
        "overwritten_in_clear": over_c,
        "errors": errs[:40],
    }
