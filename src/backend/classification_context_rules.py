# -*- coding: utf-8 -*-
"""O1–O4 上下文分类规则（纯函数，不读库、不调模型）。"""
from __future__ import annotations

import re

_ASSIST_DOMAIN = re.compile(
    r"爆胎|扎钉|补胎|瘪胎|换胎|轮胎没气|胎压|交通事故|发生事故|道路救援|拖车|保养|"
    r"到店检测|到店检查|预约检测|预约检查|有什么问题|怎么回事|"
    r"积分|贴膜|车膜|ETC|代驾|清洗|补能|客档|权益|商城|赠品|邮寄|"
    r"维修单|移动上门|对接|活动|看一看"
)
_ASSIST_INTENT = re.compile(
    r"咨询|询问|需要预约|需要协助|需要安排|需要移动|需要到店|"
    r"需要救援|需要对接|需要联系|需要做个保养|需要拖车|需要补胎|"
    r"协助|安排|预约|上门|如何处理|怎么办|求助|售后支持|"
    r"催促|是否可以|能否|什么时候|改到"
)
_ROADSIDE_ASSIST = re.compile(
    r"爆胎|扎钉|补胎|瘪胎|换胎|轮胎没气|胎压|道路救援|拖车|需要救援|需要拖车"
)
_MAINTENANCE_APPT = re.compile(
    r"预约保养|预约.{0,8}保养|需要.{0,6}保养|需要做个保养|常规保养"
)
_AFTERSALES_COORD = re.compile(
    r"售后.{0,12}(?:对接|支持|联系|协助)|"
    r"维修需求.{0,16}(?:对接|联系|协助)|"
    r"需要.{0,8}售后.{0,12}(?:支持|对接|联系|协助)"
)
_ROADSIDE_BLOCK = re.compile(
    r"投诉|不满|态度差|无人响应|无法联系|未处理|乱收费|收费不合理|"
    r"维修失败|保养后|故障|异常|无法启动|黑屏|失灵|质量问题|受伤|"
    r"报价|定损|索赔|为什么还没|过了这么久|没人接听|无人接听|"
    r"没人联系|无人联系|空号|显示空号|联系不上|"
    r"三角钥匙|钥匙无法|钥匙坏|再次致电"
)
# O1 v4：服务投诉/未解决/回访复合故障 — 保持服务类（broken 门禁收窄）
_O1_SERVICE_COMPLAINT_BLOCK = re.compile(
    r"还没人添加|还没.{0,10}添加微信|目前还没人|"
    r"为其反馈催促|"
    r"异响|还没解决|代步车|滴滴打车|"
    r"检查\d次|上门检查\d次|"
    r"售后回访.{0,400}(?:掉线|不显示|有问题|检测|仪表|更换|额外)|"
    r"提示.{0,12}保养|保养.{0,12}(?:提示|是什么情况|什么情况)|"
    r"弄脏|翻在|风油精|"
    r"之前有反馈|此前有反馈|之前.{0,8}反馈过|"
    r"烧蚀|钥匙失效|数字钥匙失效|感应不上|卡片钥匙|"
    r"安排取车|取车保养"
)
_SERVICE_COORD = re.compile(
    r"联系对接|门店对接|需要对接|需要联系|需要门店|"
    r"催促.{0,10}确认|"
    r"是否可以|能否|"
    r"询问.{0,12}(?:积分提醒|有多少数额|大概有多少)|"
    r"什么时候.{0,15}(?:进店|贴膜|安装)|"
    r"改到.{0,10}(?:上门|服务|星期)|"
    r"需要.{0,6}(?:救援|做个保养)|"
    r"协助安装|"
    r"(?:活动|服务活动).{0,40}(?:到位|耐心解答|耐心沟通)|"
    r"还是挺到位|"
    r"看一看(?:FORME|forme)|"
    r"前往.{0,8}门店.{0,12}(?:清洗|补能|看看)"
)
_COORD_BLOCK = re.compile(
    r"投诉|不满|态度差|无人响应|无法联系|未处理|乱收费|收费不合理|"
    r"保养后|故障|异常|无法启动|黑屏|失灵|质量问题|受伤|"
    r"为什么还没|过了这么久|这么久|为什么还|为什么不|"
    r"索赔|路政|胜诉|"
    r"要求安排上门|上门处理|再次致电|移动上门|"
    r"三角钥匙|钥匙无法|钥匙坏|"
    r"没人联系|无人联系|空号|显示空号|联系不上|"
    r"被扣除|前后说法|不一致|核实下|"
    r"组织外部|IT安全|没人接听|无人接听|"
    r"异响|还没解决|代步车|滴滴打车|"
    r"还没人添加|还没.{0,10}添加微信|目前还没人"
)
_EXPLICIT_ISSUE = re.compile(
    r"投诉|不满|态度差|无人响应|无法联系|未处理|乱收费|收费不合理|"
    r"维修失败|保养后|故障|异常|无法启动|黑屏|失灵|质量问题|受伤|"
    r"报价|定损|剐蹭|损伤|维修|套餐|门店表示|去了门店|"
    r"没人联系|无人联系|没人接听|无人接听|空号|显示空号|联系不上|"
    r"为什么还没|过了这么久|这么久|为什么还|为什么不|"
    r"索赔|路政|胜诉|"
    r"无法使用|不能用|坏了|"
    r"要求安排上门|上门处理|"
    r"付费吗|会收费|为什么不显示|"
    r"三角钥匙|钥匙无法|钥匙坏|"
    r"再次致电|移动上门|"
    r"被扣除|前后说法|不一致|核实下|什么情况"
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
_EXPERIENCE_L2 = frozenset(
    {"车机智能化", "充电建议", "OTA建议", "智驾建议", "智驾系统", "智驾功能"}
)
_SERVICE_REAL_REQUEST = re.compile(
    r"人过来取车|上门取车|取车吗|取送车|安排取车|取车保养"
)
_CAR_SIDE_DOMINANT = re.compile(
    r"担心车辆|车辆问题|到售后|售后检查|充一会|停止充电|再次启动|几分钟后"
)


def service_coordination_should_be_non_issue(text: str) -> bool:
    """O1b：门店对接/预约协调/活动好评等 → 非问题（无投诉/故障）。"""
    t = (text or "").strip()
    if _O1_SERVICE_COMPLAINT_BLOCK.search(t):
        return False
    if re.search(r"充电站|闪充站|充电桩|换桩", t) and re.search(
        r"催促|上线|什么时候|联系我", t
    ):
        return False
    return bool(_SERVICE_COORD.search(t) and not _COORD_BLOCK.search(t))


def assistance_should_be_non_issue(text: str) -> bool:
    """O1：道路协助/保养预约/补胎拖车等协助请求 → 非问题（非投诉/故障）。"""
    t = (text or "").strip()
    if _O1_SERVICE_COMPLAINT_BLOCK.search(t):
        return False
    if _AFTERSALES_COORD.search(t) and not re.search(
        r"投诉|不满|没人接听|无人接听|不接听|收费不合理|无人响应", t
    ):
        return True
    if (_ROADSIDE_ASSIST.search(t) or _MAINTENANCE_APPT.search(t)) and not _ROADSIDE_BLOCK.search(
        t
    ):
        return True
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
    t = (text or "").strip()
    if _O1_SERVICE_COMPLAINT_BLOCK.search(t):
        return False
    coord = service_coordination_should_be_non_issue(text)
    assist = assistance_should_be_non_issue(text)
    app = app_narrative_should_be_non_issue(text, source=source)
    if not (coord or assist or app):
        return False
    l1s = (l1 or "").strip()
    l2s = (l2 or "").strip()
    t = text or ""

    if l1s == "体验需求类" and l2s:
        if l2s in _EXPERIENCE_L2:
            return False
        if re.search(r"【\d+\.\d+|OTA|哨兵模式|时光引擎|车道级导航", t, re.I):
            return False

    if l1s == "体验需求类" and l2s == "充电建议":
        return False

    if l1s == "产品质量类" and l2s == "LFC问题":
        if re.search(r"充电桩|充电站|闪充站|换桩|什么时候", t):
            return False

    if l1s == "产品质量类" and l2s == "故障告警":
        return False

    if l1s == "服务类" and l2s in _SERVICE_L2:
        if re.search(r"没人接听|无人接听|不接听", t):
            return False
        if _SERVICE_REAL_REQUEST.search(t):
            return False

    if l1s == "服务类" and l2s == "钥匙问题":
        return False

    if l1s == "产品质量类":
        if not l2s:
            return True
        target = charging_l2_target(text)
        if target and target == l2s:
            return False
        if l2s == "Emira问题" and vehicle_family(vin) == "emira":
            return False
        return True

    return True
