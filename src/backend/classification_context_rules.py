# -*- coding: utf-8 -*-
"""O1–O4 上下文分类规则（纯函数，不读库、不调模型）。"""
from __future__ import annotations

import re

_ASSIST_DOMAIN = re.compile(
    r"爆胎|扎钉|轮胎没气|交通事故|发生事故|道路救援|拖车|保养|"
    r"到店检测|到店检查|预约检测|预约检查|有什么问题|怎么回事"
)
_ASSIST_INTENT = re.compile(
    r"咨询|询问|需要预约|需要协助|需要安排|需要移动|需要到店|协助|安排|预约|上门|如何处理|怎么办|求助"
)
_EXPLICIT_ISSUE = re.compile(
    r"投诉|不满|态度差|无人响应|无法联系|未处理|乱收费|收费不合理|"
    r"维修失败|保养后|故障|异常|无法启动|黑屏|失灵|质量问题|受伤|"
    r"报价|定损|剐蹭|损伤|维修|套餐|门店表示|去了门店|"
    r"没人联系|无人联系|空号|显示空号|联系不上|"
    r"为什么还没|过了这么久|这么久|为什么还|为什么不|"
    r"索赔|路政|胜诉|"
    r"无法使用|不能用|坏了|"
    r"要求安排上门|上门处理|"
    r"付费吗|会收费|为什么不显示|"
    r"三角钥匙|钥匙无法|钥匙坏|"
    r"再次致电|移动上门"
)
_APP_SOURCE = re.compile(r"app|应用", re.I)
_NARRATIVE = re.compile(r"分享|自驾|游记|旅途|旅程|一路|风景|日常用车|提车日记|打卡")
_DEMAND_OR_FAULT = re.compile(
    r"投诉|建议|希望|要求|故障|异常|无法|不能|黑屏|死机|异响|报错|失灵|维修|"
    r"后备箱|收纳|储物|配件|改装|安装|套餐"
)
_LFC_SUPPLIER = re.compile(r"蔚来|浩瀚|第三方桩|供应商|桩下线")
_LFC_STATION_FAULT = re.compile(
    r"(?:充电桩|充电站|闪充站|超充站|极充站).{0,12}(?:下线|故障|损坏|不可用|不能使用|无法使用)|"
    r"占位费|地锁"
)
_STATION_STATUS_INQUIRY = re.compile(
    r"(?:询问|咨询|修好了|什么时候|维修完成|需要回复|催促|告知|回复)"
)
_CAR_CHARGING = re.compile(
    r"充电功率低|功率只有|功率过低|预约充电.{0,10}(不充电|未充电|没有开始)|"
    r"车辆.{0,8}(充不进|无法充电)|车端充电"
)
_STATION_WEAK = re.compile(r"闪充站|超充站|极充站|充电站")
_STATION_STRONG_FAULT = re.compile(r"下线|故障|损坏|占位费|地锁")
_SERVICE_L2 = frozenset(
    {"交付问题", "售后服务问题", "销售服务问题", "试驾体验不良"}
)
_SERVICE_REAL_REQUEST = re.compile(
    r"取车|拖车|爆胎|贴膜|常规保养|前挡玻璃|到店.{0,8}保养"
)
_CAR_SIDE_DOMINANT = re.compile(
    r"担心车辆|车辆问题|到售后|售后检查|充一会|停止充电|再次启动|几分钟后"
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


def _inquiry_only_station(text: str) -> bool:
    """带回复/催促等咨询语气、且无供应商/强故障信号 → 不强行归 LFC。"""
    t = (text or "").strip()
    if not _STATION_STATUS_INQUIRY.search(t):
        return False
    if _LFC_SUPPLIER.search(t):
        return False
    if _STATION_STRONG_FAULT.search(t):
        return False
    return True


def station_status_inquiry_preserves_lfc(text: str) -> bool:
    """闪充站修好了没/什么时候上线等状态咨询：保留 LFC 二级，禁止 pos_capture 抹掉。"""
    t = (text or "").strip()
    return bool(_STATION_STATUS_INQUIRY.search(t) and _STATION_WEAK.search(t))


def charging_l2_target(text: str) -> str:
    """O4：充电域 L2 唯一决策。桩/供应商 → LFC；车端功率/预约 → 车端充电。"""
    t = (text or "").strip()
    if _CAR_CHARGING.search(t):
        return "车端充电问题"
    if _LFC_SUPPLIER.search(t):
        return "LFC问题"
    if _inquiry_only_station(t):
        return ""
    if _LFC_STATION_FAULT.search(t):
        return "LFC问题"
    if _STATION_WEAK.search(t):
        return "LFC问题"
    return ""


def should_skip_charging_l2_flip(text: str, current_l2: str, target_l2: str) -> bool:
    """已正确的充电 L2 不被 O4 误翻（Release B broken 门禁）。"""
    t = (text or "").strip()
    if current_l2 == "车端充电问题" and target_l2 == "LFC问题":
        if _inquiry_only_station(t):
            return True
        if _LFC_SUPPLIER.search(t) and _CAR_SIDE_DOMINANT.search(t):
            return True
        if _CAR_SIDE_DOMINANT.search(t) and not _LFC_SUPPLIER.search(t):
            return True
        return False
    if current_l2 == "LFC问题" and target_l2 == "车端充电问题":
        if _LFC_SUPPLIER.search(t) or _LFC_STATION_FAULT.search(t) or _STATION_WEAK.search(t):
            return True
        if _inquiry_only_station(t):
            return True
    return False


def should_apply_context_non_issue(
    text: str, l1: str, l2: str, *, source: str = "", vin: str = ""
) -> bool:
    """O1/O3 是否覆盖当前标签；模型充电/Emira L2 已与文本一致时不覆盖。"""
    capture = assistance_should_be_non_issue(text) or app_narrative_should_be_non_issue(
        text, source=source
    )
    if not capture:
        return False
    l1s = (l1 or "").strip()
    l2s = (l2 or "").strip()
    if l1s == "服务类" and l2s in _SERVICE_L2:
        if assistance_should_be_non_issue(text) and _SERVICE_REAL_REQUEST.search(text):
            return False
    if l1s != "产品质量类":
        return True
    if not l2s:
        return True
    target = charging_l2_target(text)
    if target and target == l2s:
        return False
    if l2s == "Emira问题" and vehicle_family(vin) == "emira":
        return False
    return True
