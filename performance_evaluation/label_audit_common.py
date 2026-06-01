#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标注存疑样本：公共逻辑（导出筛选 + 导入写库）。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"

import sys

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
from taxonomy_normalize import canonicalize_l1_label  # noqa: E402

# 与 qwen_ollama v8 充电域检测口径对齐，用于筛「人工 vs 模型」边界样本
CHARGING_DOMAIN = re.compile(
    r"(lfc|极充|超充|闪充|家充|充电站|充站|充电桩|充电枪|占位费|超时占位|"
    r"充电功率|功率只有|跳枪|地锁|异位充电|公共充电|自动闪充|"
    r"家充桩|充桩|电缆米数|充完电|驶离|闪充站|超充站|极充站|轻充站)",
    re.I,
)

CHARGING_L2 = frozenset({"LFC问题", "车端充电问题"})
NONISSUE_L2 = frozenset({"咨询与表扬", "其他非问题"})

PRAISE_HINT = re.compile(
    r"(礼盒|盲盒|表扬|感谢|好看|爱了|专业|耐心|热情|满意|很好|不错|给力|赞)",
    re.I,
)

CONSULT_HINT = re.compile(
    r"(咨询|询问|请问|想了解|怎么预约|如何购买|什么时|收费标准|具体位置|勘测路径)",
    re.I,
)

ISSUE_HINT = re.compile(
    r"(不认可|用户反馈|反馈:|没有下一步|怎么没有|无法|功率只有|收取|投诉|不满|异常|坏了|没显示)",
    re.I,
)

# 存疑原因代码 → 复核说明（写入 CSV 供标注员参考）
REASON_GUIDE: Dict[str, str] = {
    "lfc_consult_vs_lfc": "充电/LFC 域：人工=咨询与表扬，模型=LFC/车端充电。请判定：纯规则咨询→非问题/咨询与表扬；有问题/异常/不满→产品质量类/LFC问题",
    "lfc_lfc_vs_consult": "充电/LFC 域：人工=LFC问题，模型=咨询与表扬。请判定是否为真问题（App无显示/功率低/占位费争议等）",
    "lfc_pure_consult": "充电/LFC 域 + 咨询语气 + 无问题信号：人工与模型一级不一致，需统一口径",
    "lfc_issue_signal": "充电/LFC 域 + 有问题信号：人工标非问题但模型标产品质量，需确认是否应为 LFC",
    "praise_vs_product": "含表扬/礼盒/活动语气，人工=非问题，模型=产品质量类，需确认",
    "sales_praise_boundary": "含销售/试驾好评词，人工=非问题，模型=服务类，需确认",
    "nonissue_l2_split": "一级均为非问题，但二级在「咨询与表扬」与「其他非问题」不一致，需统一 L2 口径",
    "top_l1_product_nonissue": "一级：模型=产品质量类，人工=非问题（Top 错误模式）",
    "top_l1_service_nonissue": "一级：模型=服务类，人工=非问题（Top 错误模式）",
    "top_l1_nonissue_product": "一级：模型=非问题，人工=产品质量类（v8 守卫可能误伤）",
    "top_l1_nonissue_service": "一级：模型=非问题，人工=服务类",
}

# 导出排序：分数越高越优先进入限量 CSV（试点 200 条）
REASON_PRIORITY: Dict[str, int] = {
    "lfc_consult_vs_lfc": 100,
    "lfc_lfc_vs_consult": 98,
    "lfc_pure_consult": 95,
    "lfc_issue_signal": 92,
    "top_l1_nonissue_product": 88,
    "praise_vs_product": 85,
    "sales_praise_boundary": 82,
    "top_l1_product_nonissue": 75,
    "nonissue_l2_split": 70,
    "top_l1_service_nonissue": 65,
    "top_l1_nonissue_service": 60,
}


def ambiguity_priority_score(reasons: List[str]) -> int:
    """单条样本优先级：取最高原因分；附带轻微加分（多原因叠加）。"""
    if not reasons:
        return 0
    base = max(REASON_PRIORITY.get(r, 10) for r in reasons)
    return base + min(5, max(0, len(reasons) - 1))


def _parse_v3(meta: Optional[str]) -> Tuple[str, str]:
    if not meta or not str(meta).strip():
        return "", ""
    try:
        j = json.loads(meta) if isinstance(meta, str) else meta
        if not isinstance(j, dict):
            return "", ""
        return str(j.get("l1") or "").strip(), str(j.get("l2") or "").strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        return "", ""


def model_labels(row: sqlite3.Row) -> Tuple[str, str]:
    l1, l2 = _parse_v3(row["v3_label_meta"] if "v3_label_meta" in row.keys() else None)
    if not l1:
        l1 = str(row["model_class"] or "").strip()
    if not l2:
        mkw = str(row["model_keyword"] or "").strip()
        l2 = mkw.split(",", 1)[0].strip() if mkw else ""
    return l1, l2


def detect_ambiguity(row: sqlite3.Row) -> List[str]:
    """返回存疑原因代码列表；空列表表示不导出。"""
    text = str(row["original_text"] or "")
    if not text.strip():
        return []

    h_l1_raw = str(row["review_l1"] or "").strip()
    h_l2 = str(row["review_l2"] or "").strip()
    m_l1_raw, m_l2 = model_labels(row)
    if not h_l1_raw or not m_l1_raw:
        return []

    h_l1 = canonicalize_l1_label(h_l1_raw)
    m_l1 = canonicalize_l1_label(m_l1_raw)
    reasons: List[str] = []

    in_charging = bool(CHARGING_DOMAIN.search(text))
    has_consult = bool(CONSULT_HINT.search(text))
    has_issue = bool(ISSUE_HINT.search(text))
    has_praise = bool(PRAISE_HINT.search(text))

    if in_charging:
        if h_l2 == "咨询与表扬" and m_l2 in CHARGING_L2:
            reasons.append("lfc_consult_vs_lfc")
        elif h_l2 in CHARGING_L2 and m_l2 == "咨询与表扬":
            reasons.append("lfc_lfc_vs_consult")
        elif h_l1 == "非问题" and m_l1 == "产品质量类":
            if has_consult and not has_issue:
                reasons.append("lfc_pure_consult")
            elif has_issue:
                reasons.append("lfc_issue_signal")
        elif h_l1 != m_l1 and has_consult and not has_issue:
            reasons.append("lfc_pure_consult")

    if h_l1 == "非问题" and m_l1 == "产品质量类" and has_praise:
        reasons.append("praise_vs_product")

    if h_l1 == "非问题" and m_l1 == "服务类" and has_praise:
        reasons.append("sales_praise_boundary")

    # 非问题 L2（咨询与表扬 vs 其他非问题）不再作为存疑导出：业务口径不要求非问题二级准确

    if h_l1 != m_l1:
        pair = f"{m_l1}→{h_l1}"
        top_map = {
            "产品质量类→非问题": "top_l1_product_nonissue",
            "服务类→非问题": "top_l1_service_nonissue",
            "非问题→产品质量类": "top_l1_nonissue_product",
            "非问题→服务类": "top_l1_nonissue_service",
        }
        code = top_map.get(pair)
        if code and code not in reasons:
            reasons.append(code)

    seen = set()
    out: List[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def reason_text(codes: List[str]) -> str:
    parts = [REASON_GUIDE.get(c, c) for c in codes]
    return " | ".join(parts)


def connect_rw(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path.resolve()))
    conn.row_factory = sqlite3.Row
    return conn


def connect_ro(db_path: Path) -> sqlite3.Connection:
    p = db_path.resolve()
    if not p.is_file():
        raise FileNotFoundError(f"数据库不存在: {p}")
    conn = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_reviewed_for_audit(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT opinion_id, original_text, create_time, country, phone, vin,
               review_l1, review_l2, review_note, review_status,
               v3_label_meta, model_class, model_keyword, upload_batch
        FROM opinion
        WHERE review_status = 1
          AND TRIM(IFNULL(review_l1, '')) != ''
        ORDER BY opinion_id
        """
    ).fetchall()


def audit_note_append(old: Optional[str], auditor: str, note: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    who = (auditor or "auditor").strip() or "auditor"
    chunk = f"[label_audit {stamp} {who}]"
    if note.strip():
        chunk += f" {note.strip()}"
    base = str(old or "").strip()
    return f"{base}\n{chunk}".strip() if base else chunk


# CSV 列名（导出/导入共用）
CSV_COLUMNS = [
    "舆情编号",
    "原文",
    "创建时间",
    "批次",
    "当前人工一级",
    "当前人工二级",
    "模型一级",
    "模型二级",
    "优先级",
    "存疑原因代码",
    "复核说明",
    "复核后一级",
    "复核后二级",
    "复核备注",
    "复核人",
]

# 导入时可接受的列名别名
IMPORT_ALIASES = {
    "opinion_id": "舆情编号",
    "audit_l1": "复核后一级",
    "audit_l2": "复核后二级",
    "audit_note": "复核备注",
    "auditor": "复核人",
}
