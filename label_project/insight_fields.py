# -*- coding: utf-8 -*-
"""S6：单条洞察结构化字段（纯规则，可离线重放）。

写入 v3_label_meta 附加键，不覆盖 l1/l2/l3/confidence/match_type。

字段：
  intent / expected_action / severity / urgency / repeat_signal / root_cause_hint

开关：VOC_INSIGHT_FIELDS_V1=1 启用（默认关闭，避免未验收即写库）。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

INSIGHT_KEYS = (
    "intent",
    "expected_action",
    "severity",
    "urgency",
    "repeat_signal",
    "root_cause_hint",
)

INTENT_VALUES = ("报障", "求助", "咨询", "建议", "投诉升级", "表扬", "告知")
EXPECTED_ACTIONS = ("维修", "上门", "退款赔偿", "解释说明", "功能实现", "无需动作")
SEVERITY_VALUES = ("高", "中", "低")
URGENCY_VALUES = ("紧急", "一般")


def feature_enabled(env: Optional[dict] = None) -> bool:
    src = env if env is not None else os.environ
    return str(src.get("VOC_INSIGHT_FIELDS_V1", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_RE_PRAISE = re.compile(
    r"表扬|感谢|点赞|好评|满意|五星|非常棒|太棒了|很赞|服务很好|态度很好",
    re.I,
)
_RE_ESCALATE = re.compile(
    r"投诉|曝光|媒体|315|消协|律师|告你们|举报|维权|12315|强烈不满|愤怒|忍无可忍",
    re.I,
)
_RE_HELP = re.compile(
    r"求助|怎么办|谁能帮|请协助|请帮忙|希望帮忙|能否帮忙|希望处理|请求处理|帮我看看",
    re.I,
)
_RE_CONSULT = re.compile(
    r"请问|咨询|想问|问一下|了解一下|是否正常|是不是|能不能|可不可以|如何|怎么用",
    re.I,
)
_RE_SUGGEST = re.compile(
    r"建议|希望增加|希望加入|最好能|能不能加|功能实现|期待.*功能|建议优化",
    re.I,
)
_RE_FAULT = re.compile(
    r"故障|异常|失灵|黑屏|死机|报错|告警|报警|无法|打不开|启动不了|充不了|漏油|异响|卡顿|漂移",
    re.I,
)
_RE_INFORM = re.compile(r"告知|通知一下|反馈一下|仅供参考|同步一下", re.I)

_RE_REFUND = re.compile(r"退款|退定|退车|赔偿|赔付|补偿金|索赔", re.I)
_RE_VISIT = re.compile(r"上门|到店|到府|外出救援|道路救援|拖车", re.I)
_RE_REPAIR = re.compile(r"维修|返修|保修|质保|更换|换件|进站|维保|检修", re.I)
_RE_EXPLAIN = re.compile(r"解释|说明|为什么|为何|原因|答复|回复一下", re.I)
_RE_FEATURE = re.compile(r"增加功能|实现功能|加上.*功能|开通|开通权限|开通权益", re.I)

_RE_SEV_HIGH = re.compile(
    r"无法启动|不能启动|无法行驶|动不了|刹车失灵|制动失效|起火|冒烟|自燃|"
    r"漏电|失控|安全气囊|碰撞|漏油严重|动力中断|趴窝|抛锚",
    re.I,
)
_RE_SEV_MID = re.compile(
    r"故障|异常|告警|报警|黑屏|死机|无法充电|充不进|门打不开|钥匙失灵|异响明显",
    re.I,
)

_RE_URGENT = re.compile(
    r"催|加急|尽快|马上|立刻|紧急|今天必须|仍未解决|一直没|多次反馈|第\s*\d+\s*次|一再|反复联系",
    re.I,
)
_RE_REPEAT = re.compile(
    r"第\s*[一二三四五六七八九十\d]+\s*次|之前反馈|上次反馈|再次反馈|反复|又出现|还是没解决|仍未解决|老问题",
    re.I,
)


def _pick_intent(text: str, l1: str) -> str:
    t = text or ""
    if _RE_PRAISE.search(t) and not _RE_FAULT.search(t) and not _RE_ESCALATE.search(t):
        return "表扬"
    if _RE_ESCALATE.search(t):
        return "投诉升级"
    if _RE_HELP.search(t):
        return "求助"
    if _RE_SUGGEST.search(t) and not _RE_FAULT.search(t):
        return "建议"
    if _RE_FAULT.search(t):
        return "报障"
    if _RE_CONSULT.search(t):
        return "咨询"
    if (l1 or "").strip() == "非问题":
        return "告知" if _RE_INFORM.search(t) else "咨询"
    if _RE_INFORM.search(t):
        return "告知"
    return "报障" if (l1 or "").strip() == "产品质量类" else "告知"


def _pick_expected_action(text: str, intent: str) -> str:
    t = text or ""
    if _RE_REFUND.search(t):
        return "退款赔偿"
    if _RE_VISIT.search(t):
        return "上门"
    if _RE_REPAIR.search(t):
        return "维修"
    if _RE_FEATURE.search(t) or intent == "建议":
        return "功能实现"
    if _RE_EXPLAIN.search(t) or intent in ("咨询", "告知"):
        return "解释说明"
    if intent in ("表扬",):
        return "无需动作"
    if intent in ("报障", "求助", "投诉升级"):
        return "维修"
    return "解释说明"


def _pick_severity(text: str, intent: str) -> str:
    t = text or ""
    if _RE_SEV_HIGH.search(t) or intent == "投诉升级":
        return "高"
    if _RE_SEV_MID.search(t) or intent in ("报障", "求助"):
        return "中"
    return "低"


def _pick_urgency(text: str, severity: str) -> str:
    if _RE_URGENT.search(text or "") or severity == "高":
        return "紧急"
    return "一般"


def _pick_repeat(text: str) -> bool:
    return bool(_RE_REPEAT.search(text or ""))


def _pick_root_cause_hint(text: str, l2: str, l3: str) -> str:
    """取短提示：优先 L3，其次 L2，否则原文截断。"""
    l3s = (l3 or "").strip()
    if l3s and l3s not in ("通用", "其他-待归类"):
        return l3s[:40]
    l2s = (l2 or "").strip()
    if l2s:
        return l2s[:40]
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t[:40]


def extract_insight_fields(
    text: str,
    *,
    l1: str = "",
    l2: str = "",
    l3: str = "",
) -> Dict[str, Any]:
    """纯函数：由原文 + 可选分类标签推导洞察字段。"""
    intent = _pick_intent(text, l1)
    expected = _pick_expected_action(text, intent)
    severity = _pick_severity(text, intent)
    urgency = _pick_urgency(text, severity)
    return {
        "intent": intent,
        "expected_action": expected,
        "severity": severity,
        "urgency": urgency,
        "repeat_signal": _pick_repeat(text),
        "root_cause_hint": _pick_root_cause_hint(text, l2, l3),
    }


def attach_insight_fields(
    meta: Dict[str, Any],
    text: str,
    *,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """把洞察字段追加进 meta 副本；永不改写分类核心键。"""
    if enabled is None:
        enabled = feature_enabled()
    if not enabled or not isinstance(meta, dict):
        return meta
    out = dict(meta)
    fields = extract_insight_fields(
        text,
        l1=str(out.get("l1") or ""),
        l2=str(out.get("l2") or ""),
        l3=str(out.get("l3") or ""),
    )
    for k, v in fields.items():
        out[k] = v
    out["insight_version"] = "v1"
    return out


def classification_core_unchanged(
    before: Dict[str, Any], after: Dict[str, Any]
) -> bool:
    keys = ("l1", "l2", "l3", "confidence", "match_type")
    return all((before.get(k) == after.get(k)) for k in keys)
