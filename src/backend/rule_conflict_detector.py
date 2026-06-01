# -*- coding: utf-8 -*-
"""规则路径双重特征冲突检测：词库外置 JSON + 热加载。

命中后返回 C1/C2/C3，由 voc_classifier_service 标记 arbitration_required。
不修改 LabelMatcher 规则、联防或 14B 提示词。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.paths import LABEL_PROJECT_DIR

logger = logging.getLogger("voc.rule_conflict")

DEFAULT_CONFIG_PATH = Path(
    os.environ.get(
        "VOC_RULE_CONFLICT_JSON",
        str(LABEL_PROJECT_DIR / "rule_conflict_keywords_v1.json"),
    )
).resolve()

# 内置兜底（JSON 缺失或损坏时使用，与 v1 文件内容一致）
_BUILTIN_DEFAULT: Dict[str, Any] = {
    "version": 1,
    "conflicts": [
        {"id": "C1", "when_l1": "非问题", "keyword_group": "negative"},
        {"id": "C2", "when_l1": "服务类", "keyword_group": "quality"},
        {"id": "C3", "when_l1": "体验需求类", "keyword_group": "quality"},
    ],
    "keyword_groups": {
        "negative": [
            "投诉", "赔偿", "退款", "售后", "故障", "维修",
            "不能用", "无法使用", "无法启动", "无法开机",
            "坏了", "死机", "黑屏", "起火", "自燃", "爆炸", "漏液",
            "断货", "无货", "缺陷", "质量问题", "召回",
            "危险", "不安全", "换车", "退车",
            "不满意", "失望", "愤怒", "气愤",
            "耽误", "延迟", "超时", "拖延",
            "敷衍", "忽悠", "欺骗", "套路", "霸王", "刁难", "傲慢", "推诿",
            "忽视", "无视", "不理", "不管",
            "不解决", "不处理", "不回应", "不答复",
            "严重", "强烈", "抗议", "要求",
        ],
        "quality": [
            "电池", "续航", "掉电", "断电", "动力", "电机",
            "刹车", "方向盘", "轮胎", "底盘",
            "车机", "中控", "大屏", "仪表", "空调", "座椅",
            "车门", "车窗", "后备箱", "尾门",
            "引擎", "发动机", "变速", "变速箱", "齿轮",
            "传感器", "雷达", "摄像头",
            "碰撞", "追尾", "侧滑", "失控", "抖动", "异响",
            "漏水", "漏油", "渗水", "漏气", "起火", "自燃",
            "趴窝", "抛锚", "无法充电", "充不进",
            "充电桩", "充电口", "快充", "慢充", "超充", "换电站",
            "电池包", "电芯", "电控", "电压",
            "续航缩水", "续航虚标",
            "严重故障", "质量缺陷", "工艺缺陷", "安全隐患",
            "卡死", "花屏", "失灵", "失效", "失控",
        ],
    },
}

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "path": str(DEFAULT_CONFIG_PATH),
    "mtime": 0.0,
    "config": None,
    "l1_to_rules": {},
    "group_patterns": {},
    "loaded_at": "",
}


def _compile_keyword_group(keywords: List[Any]) -> re.Pattern:
    parts: List[str] = []
    seen = set()
    for raw in keywords or []:
        kw = str(raw or "").strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        parts.append(re.escape(kw))
    if not parts:
        return re.compile(r"(?!x)")
    return re.compile("|".join(parts))


def _normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = deepcopy(raw if isinstance(raw, dict) else _BUILTIN_DEFAULT)
    groups = cfg.get("keyword_groups")
    if not isinstance(groups, dict):
        groups = {}
    norm_groups: Dict[str, List[str]] = {}
    for gname, kws in groups.items():
        g = str(gname or "").strip()
        if not g:
            continue
        if not isinstance(kws, list):
            continue
        norm_groups[g] = [str(x or "").strip() for x in kws if str(x or "").strip()]
    cfg["keyword_groups"] = norm_groups

    conflicts = cfg.get("conflicts")
    if not isinstance(conflicts, list):
        conflicts = []
    norm_conflicts: List[Dict[str, str]] = []
    for item in conflicts:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        l1 = str(item.get("when_l1") or "").strip()
        grp = str(item.get("keyword_group") or "").strip()
        if cid and l1 and grp:
            norm_conflicts.append({"id": cid, "when_l1": l1, "keyword_group": grp})
    cfg["conflicts"] = norm_conflicts
    cfg.setdefault("version", 1)
    return cfg


def _build_runtime(cfg: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, str]]], Dict[str, re.Pattern]]:
    groups = cfg.get("keyword_groups") or {}
    group_patterns = {
        gname: _compile_keyword_group(kws)
        for gname, kws in groups.items()
        if isinstance(kws, list)
    }
    l1_to_rules: Dict[str, List[Dict[str, str]]] = {}
    for rule in cfg.get("conflicts") or []:
        l1 = rule.get("when_l1") or ""
        grp = rule.get("keyword_group") or ""
        if not l1 or not grp or grp not in group_patterns:
            continue
        l1_to_rules.setdefault(l1, []).append(rule)
    return l1_to_rules, group_patterns


def _read_config_file(path: Path) -> Dict[str, Any]:
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return _normalize_config(raw)
        except Exception as exc:
            logger.warning("读取冲突词库失败，使用内置兜底: %s (%s)", path, exc)
    return _normalize_config(_BUILTIN_DEFAULT)


def _apply_config(cfg: Dict[str, Any], path: Path, mtime: float) -> None:
    l1_to_rules, group_patterns = _build_runtime(cfg)
    from datetime import datetime

    _STATE["path"] = str(path)
    _STATE["mtime"] = mtime
    _STATE["config"] = cfg
    _STATE["l1_to_rules"] = l1_to_rules
    _STATE["group_patterns"] = group_patterns
    _STATE["loaded_at"] = datetime.now().isoformat(timespec="seconds")


def load_rule_conflict_config(*, force: bool = False) -> Dict[str, Any]:
    """加载或热加载冲突词库；文件 mtime 变化时自动重载。"""
    path = DEFAULT_CONFIG_PATH
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    with _LOCK:
        if not force and _STATE.get("config") is not None and _STATE.get("mtime") == mtime:
            return dict(_STATE["config"])
        cfg = _read_config_file(path)
        _apply_config(cfg, path, mtime)
        logger.info(
            "规则冲突词库已加载: path=%s conflicts=%d groups=%d",
            path,
            len(cfg.get("conflicts") or []),
            len(cfg.get("keyword_groups") or {}),
        )
        return dict(cfg)


def refresh_rule_conflict_keywords() -> Dict[str, Any]:
    """强制从磁盘重载词库（供 API / 运维调用）。"""
    cfg = load_rule_conflict_config(force=True)
    groups = cfg.get("keyword_groups") or {}
    kw_total = sum(len(v) for v in groups.values() if isinstance(v, list))
    return {
        "path": _STATE.get("path"),
        "loaded_at": _STATE.get("loaded_at"),
        "conflict_rules": len(cfg.get("conflicts") or []),
        "keyword_groups": len(groups),
        "keyword_total": kw_total,
        "version": cfg.get("version", 1),
    }


def get_rule_conflict_config_public() -> Dict[str, Any]:
    """返回当前内存中的词库配置（供 GET API）。"""
    load_rule_conflict_config()
    cfg = _STATE.get("config") or _normalize_config(_BUILTIN_DEFAULT)
    out = deepcopy(cfg)
    out["_meta"] = {
        "path": _STATE.get("path"),
        "loaded_at": _STATE.get("loaded_at"),
        "mtime": _STATE.get("mtime"),
    }
    return out


def save_rule_conflict_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """写入 JSON 并立即热加载。"""
    cfg = _normalize_config(data)
    if not cfg.get("conflicts"):
        raise ValueError("conflicts 不能为空")
    if not cfg.get("keyword_groups"):
        raise ValueError("keyword_groups 不能为空")
    path = DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return refresh_rule_conflict_keywords()


def detect_rule_conflict(meta: Dict[str, Any], text: str) -> str:
    """规则命中后做毫秒级冲突检测，返回 C1/C2/C3 或空字符串。"""
    if not text:
        return ""
    l1 = (meta.get("l1") or "").strip()
    if not l1:
        return ""
    load_rule_conflict_config()
    rules = (_STATE.get("l1_to_rules") or {}).get(l1) or []
    if not rules:
        return ""
    patterns = _STATE.get("group_patterns") or {}
    for rule in rules:
        pat = patterns.get(rule.get("keyword_group") or "")
        if pat and pat.search(text):
            return str(rule.get("id") or "")
    return ""


# 模块导入时预加载一次
try:
    load_rule_conflict_config()
except Exception:
    logger.exception("规则冲突词库初始加载失败")
