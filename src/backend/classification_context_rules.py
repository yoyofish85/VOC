# -*- coding: utf-8 -*-
"""O1–O4 上下文分类规则（纯函数，不读库、不调模型）。"""
from __future__ import annotations

import re

_ASSIST_DOMAIN = re.compile(
    r"爆胎|扎钉|轮胎没气|交通事故|发生事故|道路救援|拖车|保养|"
    r"到店检测|到店检查|预约检测|预约检查|有什么问题|怎么回事"
)
_ASSIST_INTENT = re.compile(r"咨询|询问|需要|协助|安排|预约|上门|如何处理|怎么办|求助")
_EXPLICIT_ISSUE = re.compile(
    r"投诉|不满|态度差|无人响应|无法联系|未处理|乱收费|收费不合理|"
    r"维修失败|保养后|故障|异常|无法启动|黑屏|失灵|质量问题|受伤|"
    r"报价|定损|剐蹭|损伤|维修"
)
_APP_SOURCE = re.compile(r"app|应用", re.I)
_NARRATIVE = re.compile(r"分享|自驾|游记|旅途|旅程|一路|风景|日常用车|提车日记|打卡")
_DEMAND_OR_FAULT = re.compile(
    r"投诉|建议|希望|要求|故障|异常|无法|不能|黑屏|死机|异响|报错|失灵|维修"
)
_LFC_STATION = re.compile(
    r"充电桩|充电站|闪充站|超充站|极充站|站点|地锁|占位费|桩下线|"
    r"蔚来|浩瀚|第三方桩|供应商"
)
_CAR_CHARGING = re.compile(
    r"充电功率低|功率只有|功率过低|预约充电.{0,10}(不充电|未充电|没有开始)|"
    r"车辆.{0,8}(充不进|无法充电)|车端充电"
)


def assistance_should_be_non_issue(text: str) -> bool:
    """O1：道路协助/保养预约/到店检测等咨询请求 → 非问题（非投诉/故障）。"""
    t = (text or "").strip()
    return bool(
        _ASSIST_DOMAIN.search(t)
        and _ASSIST_INTENT.search(t)
        and not _EXPLICIT_ISSUE.search(t)
    )


def app_narrative_should_be_non_issue(text: str, *, source: str = "") -> bool:
    """O3：APP 来源的用车分享/游记叙事 → 非问题。"""
    t = (text or "").strip()
    return bool(
        _APP_SOURCE.search(source or "")
        and _NARRATIVE.search(t)
        and not _DEMAND_OR_FAULT.search(t)
    )


def vehicle_family(vin: str) -> str:
    """O2：VIN 前缀 → 车型族。LJU=电车，SCC=Emira。"""
    value = (vin or "").strip().upper()
    if value.startswith("LJU"):
        return "electric"
    if value.startswith("SCC"):
        return "emira"
    return ""


def charging_l2_target(text: str) -> str:
    """O4：充电域 L2 唯一决策。桩/供应商 → LFC；车端功率/预约 → 车端充电。"""
    t = (text or "").strip()
    if _LFC_STATION.search(t):
        return "LFC问题"
    if _CAR_CHARGING.search(t):
        return "车端充电问题"
    return ""
