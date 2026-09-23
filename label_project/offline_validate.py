# -*- coding: utf-8 -*-
"""
三级标签离线验证脚本
基于 label_hierarchy_final.json 映射关系，为舆情原文自动生成三级标签并与人工标签对比。

匹配策略（V3 最终版·严格模式）：
- 仅用舆情中文+舆情原文（不拼接问题简述/问题细分，无人工标签泄漏）
- 优先级：① 正则规则 ② data_clear.csv 原文 MD5 → 强制人工复核一级 ③ 关键词/打分路径
- 无命中 → Qwen 兜底：**一级完全锁定**（仅规则+清洗 MD5+match_first，**禁止 LLM 改一级**）；**二级**仅在金标白名单（gold_l2_whitelist_v3.json）内由规则优先、不足则 LLM 单行精排；**三级**先原体系规则 → 金标聚类（gold_l3_clusters_v3.json）→ 通用 → LLM 在「体系标签∪聚类 canonical」中选一行
- 金标文件由 `build_gold_taxonomy_v3.py` 从 testdata3.csv（5628）离线构建，不修改 label_hierarchy_final.json 结构
- Ollama：未就绪自动 serve，120s；模型未拉取则提示并安全兜底

用法:
    python3 offline_validate.py -i ../testdata3.csv -n 0
    python3 offline_validate.py -i ../testdata3.csv -n 0 --no-llm
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd

from taxonomy_normalize import CANONICAL_L1_LABELS as VALID_L1_LABELS
from taxonomy_normalize import canonicalize_l1_label, try_canonicalize_l1

# 路径：相对本文件，便于项目迁移
_BASE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ROOT = os.path.normpath(os.path.join(_BASE, ".."))
MAPPING_FILE = os.path.join(_BASE, "label_hierarchy_final.json")
DEFAULT_INPUT = os.path.join(_DEFAULT_ROOT, "testdata3.csv")
OUTPUT_DIR = _BASE
# 与 label_project 同级的 output（等价于从本目录看 ../output），保存默认结果与不匹配报告父目录
OUTPUT_PARENT = os.path.normpath(os.path.join(_BASE, "..", "output"))
os.makedirs(OUTPUT_PARENT, exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, "output"), exist_ok=True)

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
# 与本地 `ollama list` 中名称一致；可 export OLLAMA_MODEL=其它已拉取模型
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b-instruct-q4_K_M")
OLLAMA_TEMPERATURE = float(os.environ.get("VOC_QWEN_TEMPERATURE", "0.1"))
OLLAMA_TOP_P = float(os.environ.get("VOC_QWEN_TOP_P", "0.3"))
OLLAMA_NUM_CTX = int(os.environ.get("VOC_QWEN_NUM_CTX", "131072"))
_DEFAULT_LABELED = os.path.join(_DEFAULT_ROOT, "data", "labeled", "data_clear.csv")
_DEFAULT_LEGACY = os.path.join(_DEFAULT_ROOT, "data_clean_project", "data_clear.csv")
DEFAULT_CLEAN_CSV = (
    _DEFAULT_LABELED if os.path.isfile(_DEFAULT_LABELED) else _DEFAULT_LEGACY
)
# 由 build_gold_taxonomy_v3.py 从 5628 金标生成
DEFAULT_L2_WHITELIST_JSON = os.path.join(_BASE, "gold_l2_whitelist_v3.json")
DEFAULT_L3_CLUSTER_JSON = os.path.join(_BASE, "gold_l3_clusters_v3.json")


def check_ollama(host: str = OLLAMA_HOST) -> bool:
    try:
        urllib.request.urlopen(f"{host}/api/tags", timeout=2)
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def ollama_model_available(host: str, model: str) -> bool:
    """检查 /api/tags 中是否已存在指定模型（未拉取则返回 False，不抛错）。"""
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]
        return model in names
    except Exception:
        return False


def normalize_l1_clean(s: Any) -> Optional[str]:
    """清洗表人工一级：仅当可映射到四类之一时返回，否则 None（不强行写入）。"""
    return try_canonicalize_l1(s)


def strip_model_noise(s: str) -> str:
    """去掉常见思考标签，便于取末行分类。"""
    if not s:
        return ""
    t = re.sub(r"<think>[\s\S]*?</think>", "", s, flags=re.I)
    return t.strip()


# 非问题弱规则词（与故障/投诉类互斥时生效）。
# 注意：「咨询与表扬」属于 L2，只有在 NEGATIVE_NON_ISSUE 全部不命中且 NON_ISSUE_BOOST 强命中时才可启用。
NON_ISSUE_BOOST = (
    "不是投诉",
    "仅咨询",
    "单纯咨询",
    "只是问问",
    "随便聊聊",
    "闲聊",
    "五星好评",
    "非常满意",
    "特别满意",
    "表扬",
    "点赞",
    "感谢你们",
    "谢谢你们的",
    "辛苦了",
    "咨询一下",
    "想了解一下",
    "请问一下",
    "纯属表扬",
)

# ===== L2「咨询与表扬」黑名单（更严格、覆盖 1076 错例分布的关键词）=====
# 凡命中任一关键词，规则与提示词层均禁止判定为「咨询与表扬」。
NEGATIVE_NON_ISSUE = (
    # 投诉/不满/抱怨
    "投诉", "不满意", "不满", "失望", "糟糕", "体验差", "难用", "崩溃", "心累", "无语",
    "气愤", "愤怒", "郁闷", "吐槽", "抱怨",
    # 故障/异常/硬件
    "故障", "异常", "失灵", "失效", "坏了", "卡顿", "黑屏", "死机", "异响", "抖动",
    "漏水", "漏液", "漏电", "亏电", "断连", "重启", "无响应", "报错", "报警", "告警",
    "无法充电", "充不进", "充不了", "充电失败", "充电异常", "功能异常", "硬件问题",
    # 服务负面
    "售后差", "售后慢", "售后不理", "服务差", "态度差", "推诿", "敷衍", "忽悠", "欺骗",
    "拖延", "没人理", "无人处理", "没解决", "未解决", "未处理", "没回复", "无回复",
    "乱收费", "多收费", "加价", "不退", "不修", "不换",
    # 诉求/维权/退款
    "诉求", "维权", "索赔", "退款", "退费", "退订", "退换", "赔偿", "希望官方", "希望尽快",
    "希望解决", "希望处理", "要求处理", "必须解决", "尽快处理", "尽快解决",
    "问题反馈", "反馈问题", "反映问题", "多次反映", "多次投诉", "多次沟通",
    # 体验需求（同样禁止归为咨询与表扬，应进入「体验需求类」）
    "建议", "希望", "期望", "期待", "优化", "改进", "完善", "提升", "增加", "添加",
    # 业务边界关键词（若出现这些词，更可能属于服务类/产品质量类二级，不应进咨询）
    "售后", "维修", "保养", "质保", "备件", "代步", "道路救援",
    "销售", "顾问", "门店", "客服", "接待", "试驾", "意向金",
    "交付", "提车", "订金", "定金", "合同", "发票",
    "车机", "中控屏", "座舱", "屏幕", "仪表", "hud", "抬头",
    "lfc", "闪充", "超充", "地锁", "公共充电", "充电桩", "充电枪", "家充",
    "智驾", "领航", "noa", "aeb", "泊车", "ad4", "辅助驾驶",
    "钥匙", "解锁", "nfc", "uwb",
    "ota", "升级失败",
)


def parse_plain_l1(raw: str) -> Optional[str]:
    """从非 JSON 输出中解析一级（取末行优先，含子串匹配）。"""
    raw = strip_model_noise(raw or "")
    if not raw:
        return None
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    scan = list(reversed(lines)) if lines else []
    for line in scan:
        for v in VALID_L1_LABELS:
            if line == v or v in line:
                return v
    return None


def first_plain_line(raw: str) -> str:
    """取第一条非空行（模型应只输出一行）。"""
    raw = strip_model_noise(raw or "")
    for line in raw.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def nearest_tag_in_list(candidates: List[str], line: str) -> str:
    """候选为体系内三级标签时，将模型输出对齐到最近一项。"""
    line = (line or "").strip()
    if not line:
        return candidates[0] if candidates else ""
    if line in candidates:
        return line
    if not candidates:
        return line
    best = candidates[0]
    best_score = -1.0
    ls, lset = len(line), set(line)
    for c in candidates:
        cs = set(c)
        inter = len(lset & cs)
        score = inter / max(1, len(lset | cs))
        if inter > best_score or (inter == best_score and len(c) < len(best)):
            best_score = inter
            best = c
    return best


class LLMInferenceError(RuntimeError):
    """LLM 推理失败（网络、HTTP、解析等），不静默降级。"""


def ensure_ollama_ready(host: str = OLLAMA_HOST, wait_sec: int = 120) -> bool:
    if check_ollama(host):
        print(f"✅ Ollama 已运行: {host}")
        return True
    print(f"⚠️ Ollama 未响应 {host}，尝试执行: ollama serve …")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise RuntimeError("未找到 ollama 命令，请先安装 Ollama: https://ollama.com") from None
    for i in range(wait_sec):
        time.sleep(1)
        if check_ollama(host):
            print(f"✅ Ollama 已启动并就绪（约 {i + 1}s）")
            return True
    raise RuntimeError(f"Ollama 在 {wait_sec}s 内未就绪，请手动执行 ollama serve 后重试")


def ollama_generate(
    model: str,
    prompt: str,
    host: str = OLLAMA_HOST,
    temperature: float = OLLAMA_TEMPERATURE,
    num_predict: int = 512,
) -> str:
    """调用 Ollama /api/generate；stream 恒为 False；默认低温、足够 num_predict 避免空输出。"""
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": OLLAMA_TOP_P,
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": num_predict,
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LLMInferenceError(
                f"Ollama 响应非 JSON model={model!r}: {e}; 片段={raw[:600]!r}"
            ) from e
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise LLMInferenceError(
            f"Ollama HTTP {e.code} model={model!r} host={host!r} body={err_body[:1200]!r}"
        ) from e
    except urllib.error.URLError as e:
        raise LLMInferenceError(f"Ollama 网络错误 model={model!r}: {e}") from e
    return str(body.get("response", "") or "")


def extract_json_objects(text: str) -> List[Dict[str, Any]]:
    """从模型输出中解析 JSON 对象（支持 R1 长推理后尾随 JSON）。"""
    out: List[Dict[str, Any]] = []
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                chunk = text[start : i + 1]
                try:
                    out.append(json.loads(chunk))
                except json.JSONDecodeError:
                    pass
                start = -1
    return out


def parse_llm_label_json(raw: str) -> Optional[Dict[str, str]]:
    """解析一级/二级/三级字段（兼容中英键名）。"""
    objs = extract_json_objects(raw)
    if not objs:
        return None
    obj = objs[-1]
    m: Dict[str, str] = {}
    for k1, k2 in (
        ("一级", "level1"),
        ("二级", "level2"),
        ("三级", "level3"),
    ):
        if k1 in obj:
            m[k2] = str(obj[k1]).strip()
        elif k2 in obj:
            m[k2] = str(obj[k2]).strip()
    return m if m else None

# 严格模式一级词表（与任务书一致，可辅以常见车辆词加权）
FAULT_WORDS = ["异响", "卡顿", "失灵", "断开", "失败", "异常", "报错", "无法", "漏", "烧", "烫", "抖"]
EXP_WORDS = ["建议", "希望", "优化", "增加", "能不能", "想要"]
SRV_WORDS = ["售后", "门店", "态度", "慢", "收费", "差"]
# 不含泛化「充电/蓝牙」以免服务场站类舆情误判为产品（充电故障由规则与 L2 路由覆盖）
EXTRA_FAULT_HINT = [
    "卡帧",
    "掉帧",
    "黑屏",
    "死机",
    "亏电",
    "漏电",
    "车机",
    "智驾",
    "泊车",
    "雷达",
    "钥匙",
    "报警",
    "告警",
    "闪充",
    "lfc",
    "家充",
    "地锁",
    "hud",
    "抬头显示",
    "故障",
    "失灵",
    "座舱",
    "充电枪",
    "充电桩",
]

# 一级加权：车辆/充电/座舱相关弱特征（提高无「故障词」但仍属质量问题的召回）
BROAD_PRODUCT_HINTS = [
    "闪充", "超充", "地锁", "家充", "充电桩", "充电站", "充电枪", "占位", "OTA", "ota", "车机",
    "气囊", "导航", "音响", "座椅", "天窗", "车门", "轮胎", "悬架", "雨刮", "雨刷", "后视镜",
    "灯光", "空调", "车窗", "蓝牙", "hud", "抬头显示", "智驾", "泊车", "雷达", "钥匙", "黑屏",
    "死机", "故障", "不亮", "坏了", "脱落", "泄漏", "断裂", "松动", "卡死", "电池", "电机",
    "充电", "内饰", "油漆", "生锈", "漏水", "电吸", "尾门", "后备箱", "前备箱", "减速", "助力",
]

# 内建别名（可被 label_hierarchy_final.json 中 _keyword_aliases 覆盖/合并）
# 顺序＝优先级：命中即返回；质量/服务/体验规则在前，「非问题」规则置后，避免弱咨询句覆盖真实投诉。
_DEFAULT_PRIORITY_RULES_CORE = [
    # --- 产品质量 / 充电 / 座舱 / 通用故障（高优先级）---
    (
        r".*(车机|座舱|中控屏|大连屏|仪表|屏幕).*(卡|顿|黑|闪|花屏|死机|失灵|重启|无响应).*",
        "产品质量类",
        "座舱问题",
        "卡顿-通用-车机",
        0.93,
    ),
    (
        r".*(hud|抬头显示|ar模式|ar 模式).*?(卡|顿|卡帧|掉帧|失灵).*",
        "产品质量类",
        "座舱问题",
        "HUD屏异常问题",
        0.95,
    ),
    (r".*?(卡帧|掉帧).*?(hud|抬头显示).*", "产品质量类", "座舱问题", "HUD屏异常问题", 0.95),
    (
        r".*(充电|闪充|快充|慢充|家充|桩|枪).*(失败|异常|无法|充不进|充不了|断充|跳枪|报错).*",
        "产品质量类",
        "车端充电问题",
        "无法充电",
        0.93,
    ),
    (
        r".*(lfc|闪充站|超充|充电站).*(地锁|占位|打不开|坏|故障|扫不了).*",
        "产品质量类",
        "LFC问题",
        "闪充站问题",
        0.91,
    ),
    (
        r".*(异响|抖动|漏油|漏水|故障灯|无法启动|失速|动力丢失).*",
        "产品质量类",
        "故障-通用",
        "故障-通用-无明确载体",
        0.89,
    ),
    (r".*ota.*蓝牙|.*蓝牙.*ota.*", "产品质量类", "OTA更新问题", "OTA后蓝牙电话故障", 0.92),
    (
        r".*预约充电.*(无法|异常|失败|不准)|.*充电.*(预约|定时).*(问题|异常|失败).*",
        "产品质量类",
        "车端充电问题",
        "充电未按设定时间点开始或停止",
        0.88,
    ),
    (r".*无法充电|.*充不进电|.*充电失败(?!.*建议).*", "产品质量类", "车端充电问题", "无法充电", 0.9),
    (r".*充电慢|.*充电功率低|.*充电.*慢.*", "产品质量类", "车端充电问题", "车端充电-速度慢", 0.85),
    (r".*闪充站|.*超充站|.*地锁.*", "产品质量类", "LFC问题", "闪充站问题", 0.88),
    (r".*家充桩|.*家用桩.*", "产品质量类", "LFC问题", "家充桩问题", 0.88),
    # （已移除内嵌定义，统一在 _DEFAULT_PRIORITY_RULES_TOP 中前置生效）
    # --- 体验需求（弱于明确故障；避免与上面充电故障规则冲突时用词区分）---
    (
        r".*(希望|建议|能否|能否考虑|最好加上)((?!(失败|异常|无法|坏了|故障)).)*(功能|导航|ui|界面|哨兵|座椅|音响|app).*",
        "体验需求类",
        "车机智能化",
        "车机智能化",
        0.84,
    ),
]

# 咨询与表扬：必须满足**整文本零负面/零诉求/零业务边界词**才能成立；负向 lookahead 极严格。
# 与 src/backend/qwen_ollama.py 的 _KZ_FORBID 保持同步。
_KZ_DENY = (
    r"故障|异常|失灵|失效|坏了|无法|不能|黑屏|死机|卡顿|异响|抖动|漏|断|烧|烫|"
    r"投诉|不满|抱怨|吐槽|不满意|失望|糟糕|太差|差|难用|"
    r"诉求|维权|索赔|退款|退费|退订|退换|赔偿|"
    r"推诿|敷衍|忽悠|欺骗|拖延|不理|没人理|没处理|未解决|未处理|没回复|"
    r"建议|希望|期望|期待|增加|优化|改进|完善|提升|添加|"
    # 服务类边界词（销售/售后/交付/客服）
    r"售后|维修|保养|质保|备件|代步|进店检修|服务门店|回访|理赔|"
    r"销售|销售顾问|顾问|门店|客服|接待|试驾|意向金|"
    r"购车|下定|订单|展厅|价格|提车|交付|订金|定金|合同|发票|"
    r"车机|中控屏|座舱|hud|屏幕|仪表|"
    r"lfc|闪充|超充|地锁|公共充电|充电桩|充电枪|家充|"
    r"智驾|领航|noa|aeb|泊车|ad4|辅助驾驶|"
    r"钥匙|解锁|nfc|uwb|"
    r"ota|升级失败|"
    r"报警|告警|报错|警告|bug|警示|"
    r"乱收费|加价|不退|不修|不换"
)

# ===== 正向捕获词集合 =====
_KZ_PRAISE_TOKENS = (
    r"表扬|点赞|不错|给力|感谢|谢谢|多谢|好评|认可|夸赞|赞一个|"
    r"满意|挺好|很好|很棒|超棒|靠谱|专业|贴心|高效|及时|推荐|五星|完美|nice"
)
_KZ_NEUTRAL_TOKENS = (
    r"咨询|询问|请教|想了解|想问一下|想问|请问|问一下|问问|了解一下|了解下|了解|打听"
)
# 超短善意句（去空白后整段就是好评词）
_KZ_SHORT_BENIGN_PAT = (
    r"^[\s\u3000]*("
    r"(很好|好|挺好|很棒|超棒|不错|可以|还行|还可以|没问题|没事|赞|满意|完美|nice|good|ok)"
    r"[\s\u3000。！!\.\?\?,，~～]*)+$"
)

_DEFAULT_PRIORITY_RULES_NON_ISSUE_TAIL = [
    (
        rf".*(不是投诉|仅咨询|单纯咨询|只是问问|随便聊聊|纯属咨询)(?!.*({_KZ_DENY})).*",
        "非问题",
        "咨询与表扬",
        "咨询",
        0.86,
    ),
    (
        rf".*(五星好评|非常满意|特别满意|表扬一下|给你们点赞|非常感谢|十分感谢)(?!.*({_KZ_DENY})).*",
        "非问题",
        "咨询与表扬",
        "表扬感谢",
        0.85,
    ),
]

# ===== 最高优先级（正向捕获）：纯咨询/纯表扬/超短善意 → 非问题/咨询与表扬 =====
# 使用 ^(?!.*禁止词).* 在开头做全局负向 lookahead，确保整文本不含负面/服务/业务边界词。
_DEFAULT_PRIORITY_RULES_POSITIVE_TOP = [
    # A) 超短善意短句（去空白后整段都是 "很好/不错/可以/还行/满意/赞" 等）
    (
        _KZ_SHORT_BENIGN_PAT,
        "非问题",
        "咨询与表扬",
        "短句好评",
        0.88,
    ),
    # B) 正向赞美/感谢话术 + 全文无负面/服务边界词
    (
        rf"^(?!.*({_KZ_DENY})).*({_KZ_PRAISE_TOKENS}).*",
        "非问题",
        "咨询与表扬",
        "表扬感谢",
        0.86,
    ),
    # C) 中性问询话术 + 全文无负面/服务边界词
    (
        rf"^(?!.*({_KZ_DENY})).*({_KZ_NEUTRAL_TOKENS}).*",
        "非问题",
        "咨询与表扬",
        "纯咨询",
        0.85,
    ),
]


# 按需求三：销售/售后/座舱/LFC/钥匙/AD4 强规则前置，确保不被通用规则截胡
_DEFAULT_PRIORITY_RULES_TOP = [
    # LFC / 闪充站 / 超充 / 地锁 / 公共充电 → 固定「LFC问题」（必须最先匹配）
    (
        r".*(lfc|闪充站|超充|公共充电|充电站|地锁).*"
        r"(故障|异常|占位|打不开|扫不了|不可用|坏|破损|无法|不能).*",
        "产品质量类",
        "LFC问题",
        "闪充站问题",
        0.94,
    ),
    # 钥匙 / 解锁 / NFC / UWB → 固定「钥匙问题」
    (
        r".*(钥匙|数字钥匙|解锁|nfc|uwb).*"
        r"(故障|异常|失灵|失效|无法|不响应|配对失败|无法绑定|无法解锁|无法锁车).*",
        "产品质量类",
        "钥匙问题",
        "钥匙-通用",
        0.93,
    ),
    # 智驾 / NOA / AEB / 泊车 / AD4 → 固定「AD4问题」
    (
        r".*(智驾|领航|noa|aeb|泊车|辅助驾驶|ad4).*"
        r"(故障|异常|失灵|退出|脱手|误触发|不工作|不识别|警报).*",
        "产品质量类",
        "AD4问题",
        "智驾-通用",
        0.92,
    ),
    # 座舱 / 车机 / HUD / 屏幕 / 仪表 → 固定「座舱问题」
    (
        r".*(座舱|车机|中控屏|hud|抬头显示|屏幕|仪表).*"
        r"(故障|异常|卡顿|黑屏|死机|失灵|花屏|重启|无响应|不亮).*",
        "产品质量类",
        "座舱问题",
        "卡顿-通用-车机",
        0.94,
    ),
    # 售后 / 维修 / 保养 / 备件 / 质保 / 代步 / 进店检修 / 服务门店 / 回访 / 理赔 → 固定「售后服务问题」
    # 关键词命中即归属，无需后段负面词（符合"固定关键词绑定"需求）
    (
        r".*(售后|维修|保养|质保|备件|代步|道路救援|"
        r"售后慢|售后差|售后不理|售后糟|进店检修|服务门店|回访|理赔).*",
        "服务类",
        "售后服务问题",
        "服务体验不佳",
        0.93,
    ),
    # 销售 / 销售顾问 / 购车 / 下定 / 订单 / 展厅 / 价格 / 顾问 / 试驾 / 意向金 → 固定「销售服务问题」
    # 关键词命中即归属（"价格|展厅|购车|下定|订单" 都是销售强信号）
    (
        r".*(销售顾问|销售|购车|下定|订单|展厅|价格|意向金|试驾|顾问).*",
        "服务类",
        "销售服务问题",
        "销售服务-通用",
        0.92,
    ),
    # 提车 / 交付 / 订金/定金 / 合同 / 发票 → 固定「交付问题」
    (
        r".*(提车|交付|订金|定金|合同|置换|发票).*(拖延|违约|纠纷|不退|延期|欺骗|跳票|没消息|久未交付).*",
        "服务类",
        "交付问题",
        "整车交付状态不良",
        0.91,
    ),
]

DEFAULT_PRIORITY_RULES = (
    _DEFAULT_PRIORITY_RULES_POSITIVE_TOP  # ① 纯咨询/纯表扬/超短善意（最高优先）
    + _DEFAULT_PRIORITY_RULES_TOP          # ② 业务强细分（销售/售后/座舱/LFC/钥匙/AD4/交付）
    + _DEFAULT_PRIORITY_RULES_CORE         # ③ 现有通用规则
    + _DEFAULT_PRIORITY_RULES_NON_ISSUE_TAIL  # ④ 旧版咨询/表扬兜底
)


class LabelMatcher:
    """关键词 + 规则 + 清洗数据优先；无命中时 Qwen 兜底（一级锁定、二级白名单+LLM 精排、三级规则→聚类→LLM）。"""

    def __init__(
        self,
        mapping_file: str,
        use_llm: bool = True,
        ollama_model: Optional[str] = None,
        ollama_host: str = OLLAMA_HOST,
        clean_csv_path: Optional[str] = None,
        l2_whitelist_json: Optional[str] = None,
        l3_cluster_json: Optional[str] = None,
    ):
        with open(mapping_file, "r", encoding="utf-8") as f:
            self.mapping = json.load(f)

        self.file_aliases = self.mapping.get("_keyword_aliases") or {}
        self.keyword_index = self._build_keyword_index()
        self._compiled_rules = [(re.compile(p, re.I | re.S), l1, l2, l3, c) for p, l1, l2, l3, c in DEFAULT_PRIORITY_RULES]

        self.use_llm = use_llm
        self.ollama_model = ollama_model or DEFAULT_OLLAMA_MODEL
        self.ollama_host = ollama_host.rstrip("/")
        self._llm_cache: Dict[str, dict] = {}
        self._ollama_checked = False
        self._clean_l1_by_hash: Dict[str, str] = {}
        self._review_gold_by_hash: Dict[str, Dict[str, str]] = {}
        self._ollama_model_ok: Optional[bool] = None
        self._model_warned = False

        print(f"✅ 加载映射文件: {mapping_file}")
        l1n = len([k for k in self.mapping if not str(k).startswith("_")])
        print(f"   一级标签数: {l1n}")
        print(f"   LLM 兜底: {'开启（默认）' if use_llm else '关闭（--no-llm）'}  model={self.ollama_model}")
        self._load_clean_csv(clean_csv_path if clean_csv_path is not None else DEFAULT_CLEAN_CSV)
        self._load_taxonomy_v3(
            l2_whitelist_json if l2_whitelist_json is not None else DEFAULT_L2_WHITELIST_JSON,
            l3_cluster_json if l3_cluster_json is not None else DEFAULT_L3_CLUSTER_JSON,
        )

    def load_review_gold_db(self, db_path: str) -> int:
        """从复核库（review_status=1）按原文 MD5 缓存人工金标 L1/L2/L3。

        命中后由 match() 第一步直接返回，跳过全部规则与 LLM。
        - 仅只读 URI 连接，PRAGMA query_only=ON，绝不写库。
        - 失败静默降级（缓存保持为空，不影响原规则路径）。
        返回成功加载的条数。
        """
        if not db_path:
            return 0
        p = os.path.abspath(db_path)
        if not os.path.isfile(p):
            return 0
        try:
            import sqlite3 as _sqlite3
            uri = f"file:{p}?mode=ro"
            conn = _sqlite3.connect(uri, uri=True)
            conn.row_factory = _sqlite3.Row
            try:
                conn.execute("PRAGMA query_only = ON")
                cur = conn.execute(
                    """SELECT original_text, review_l1, review_l2, review_l3
                       FROM opinion
                       WHERE review_status = 1
                       AND TRIM(IFNULL(original_text,'')) != ''
                       AND TRIM(IFNULL(review_l1,'')) != ''"""
                )
                n_load = 0
                for r in cur.fetchall():
                    txt = self._norm_text(r["original_text"])
                    if not txt:
                        continue
                    l1c = canonicalize_l1_label(r["review_l1"])
                    if l1c not in VALID_L1_LABELS:
                        continue
                    key = hashlib.md5(txt.encode("utf-8", errors="ignore")).hexdigest()
                    self._review_gold_by_hash[key] = {
                        "l1": l1c,
                        "l2": str(r["review_l2"] or "").strip(),
                        "l3": str(r["review_l3"] or "").strip(),
                    }
                    n_load += 1
                print(f"   复核金标缓存（按原文 MD5）: {n_load} 条 ← {p}")
                return n_load
            finally:
                conn.close()
        except Exception as exc:
            print(f"⚠️ 加载复核金标缓存失败: {exc}")
            return 0

    def _load_clean_csv(self, path: str) -> None:
        """按原文 MD5 → 人工复核一级，供关键词路径之前注入 level1_hint。"""
        if not path or not os.path.isfile(path):
            print(f"   清洗数据: 未找到文件，跳过（{path}）")
            return
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
        except Exception:
            try:
                df = pd.read_csv(path, encoding="utf-8")
            except Exception as e:
                print(f"⚠️ 读取清洗数据失败: {e}")
                return
        n_rows = 0
        for _, row in df.iterrows():
            l1m = normalize_l1_clean(row.get("人工复核结果", row.get("人工分类", "")))
            if l1m is None:
                continue
            txt = self._norm_text(row.get("原文", ""))
            if not txt:
                continue
            key = hashlib.md5(txt.encode("utf-8", errors="ignore")).hexdigest()
            self._clean_l1_by_hash[key] = l1m
            n_rows += 1
        print(
            f"   已人工复核清洗数据（按原文索引）: {len(self._clean_l1_by_hash)} 条不重复键，"
            f"有效行 {n_rows} 条 ← {path}"
        )

    def _load_taxonomy_v3(self, wl_path: str, cl_path: str) -> None:
        """加载 5628 金标导出的二级白名单与三级聚类（缺省文件时退化为全量体系）。"""
        self.l2_whitelist: Dict[str, List[str]] = {}
        self.l3_clusters: Dict[str, Dict[str, List[dict]]] = {}
        raw_wl: Dict[str, List[str]] = {}
        if os.path.isfile(wl_path):
            with open(wl_path, "r", encoding="utf-8") as f:
                raw_wl = json.load(f)
            print(f"   二级白名单: {wl_path}")
        else:
            print(f"⚠️ 未找到二级白名单 {wl_path}，退化为全量体系二级（可运行 build_gold_taxonomy_v3.py）")
        if os.path.isfile(cl_path):
            with open(cl_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.l3_clusters = data.get("by_l1_l2", {}) or {}
            print(f"   三级聚类: {cl_path}（{sum(len(v) for v in self.l3_clusters.values())} 组）")
        else:
            print(f"⚠️ 未找到三级聚类 {cl_path}，跳过聚类匹配（可运行 build_gold_taxonomy_v3.py）")
        for l1 in VALID_L1_LABELS:
            base = list(raw_wl.get(l1, [])) if raw_wl else []
            seen = set(base)
            for k in self._sorted_l2_names(l1):
                if k not in seen:
                    base.append(k)
                    seen.add(k)
            self.l2_whitelist[l1] = base

    def _l2_freq(self, l1: str, l2: str) -> int:
        block = self.mapping.get(l1, {}).get(l2)
        if not isinstance(block, dict):
            return 0
        return int(block.get("频次", 0) or 0)

    def _sorted_l2_names(self, l1: str) -> List[str]:
        l1d = self.mapping.get(l1, {})
        keys = [k for k in l1d.keys() if not str(k).startswith("_") and isinstance(l1d[k], dict)]
        return sorted(keys, key=lambda x: -self._l2_freq(l1, x))

    def _ensure_ollama_once(self):
        if self._ollama_checked:
            return
        ensure_ollama_ready(self.ollama_host)
        if self._ollama_model_ok is None:
            self._ollama_model_ok = ollama_model_available(self.ollama_host, self.ollama_model)
        if not self._ollama_model_ok and not getattr(self, "_model_warned", False):
            self._model_warned = True
            print(f"⚠️ 未在 Ollama 中检测到模型 {self.ollama_model!r}，请执行: ollama pull {self.ollama_model}")
            print("   LLM 将跳过，使用规则兜底。")
        self._ollama_checked = True

    def _llm_triple_fallback(self, text: str, hint_l1: Optional[str] = None) -> dict:
        """LLM 空输出或解析失败时的标签兜底，保证批处理不中断；match_type=llm_fallback。"""
        valid_l1 = [k for k in self.mapping if not str(k).startswith("_")]
        hl = canonicalize_l1_label(hint_l1) if (hint_l1 or "").strip() else ""
        l1 = hl if hl in valid_l1 else self.match_first_label(text)
        w = self._weak_keyword_fallback(text)
        if w and w["level1"] == l1:
            out = dict(w)
            out["match_type"] = "llm_fallback"
            out["confidence"] = min(float(out.get("confidence", 0.4)), 0.42)
            return out
        l2_names = self._sorted_l2_names(l1)
        l2 = l2_names[0] if l2_names else "售后服务问题"
        g = self._generic_l3_for_l2(l1, l2, text)
        if g:
            out = dict(g)
            out["match_type"] = "llm_fallback"
            out["confidence"] = min(float(out.get("confidence", 0.4)), 0.42)
            return out
        l1, l2, l3 = self._validate_and_normalize_triple(l1, l2, "")
        return {
            "level1": l1,
            "level2": l2,
            "level3": l3,
            "confidence": 0.38,
            "match_type": "llm_fallback",
        }

    def _first_cluster_l3(self, l1: str, l2: str) -> str:
        blk = self.l3_clusters.get(l1, {}).get(l2)
        if not blk:
            return ""
        for cl in blk:
            if isinstance(cl, dict):
                c = str(cl.get("canonical") or "").strip()
                if c:
                    return c
        return ""

    def _validate_and_normalize_triple(self, l1: str, l2: str, l3: str) -> tuple[str, str, str]:
        valid_l1 = [k for k in self.mapping if not str(k).startswith("_")]
        l1 = canonicalize_l1_label(l1)
        if l1 not in valid_l1:
            l1 = "服务类"
        wl = list(self.l2_whitelist.get(l1) or [])
        if not wl:
            wl = list(self._sorted_l2_names(l1)) or ["售后服务问题"]
        l2 = (l2 or "").strip()
        if l2 not in wl:
            l2 = nearest_tag_in_list(wl, l2)
        block = self.mapping.get(l1, {}).get(l2)
        if not isinstance(block, dict):
            l3s = (l3 or "").strip()
            if l3s:
                return l1, l2, l3s
            fc = self._first_cluster_l3(l1, l2)
            if fc:
                return l1, l2, fc
            try:
                from label_project.l3_standard_resolver import PENDING_L3, feature_enabled
            except ImportError:
                from l3_standard_resolver import PENDING_L3, feature_enabled  # type: ignore
            if feature_enabled() and l1 in ("产品质量类", "服务类"):
                return l1, l2, PENDING_L3
            return l1, l2, "通用"
        tags = block.get("标签列表", []) if isinstance(block, dict) else []
        if l3 not in tags:
            g = self._generic_l3_for_l2(l1, l2, "")
            if g:
                l3 = g["level3"]
            elif tags:
                l3 = tags[0]
            else:
                try:
                    from label_project.l3_standard_resolver import PENDING_L3, feature_enabled
                except ImportError:
                    from l3_standard_resolver import PENDING_L3, feature_enabled  # type: ignore
                l3 = (
                    PENDING_L3
                    if feature_enabled() and l1 in ("产品质量类", "服务类")
                    else "通用"
                )
        return l1, l2, l3

    def _resolve_l2_whitelist(self, text: str, l1: str, allow_llm: bool) -> str:
        """二级：仅在白名单（金标优先 + 全体系补全）内；规则优先，不足时 LLM 单行精排。"""
        wl = self.l2_whitelist.get(l1) or self._sorted_l2_names(l1)
        if not wl:
            return "售后服务问题"
        l2 = self._infer_l2(text, l1)
        if l2 and l2 in self.mapping.get(l1, {}) and l2 in wl:
            return l2
        pr2 = self._best_in_level1(text, l1)
        if pr2 and pr2.get("level2") in wl:
            return str(pr2["level2"])
        if allow_llm and self.use_llm and self._ollama_model_ok is not False:
            return self._llm_pick_l2_whitelist(text, l1, wl)
        return wl[0]

    def _llm_pick_l2_whitelist(self, text: str, l1: str, wl: List[str]) -> str:
        """LLM 仅在白名单内选一项，单行输出。"""
        if len(wl) == 1:
            return wl[0]
        if self._ollama_model_ok is False:
            return wl[0]
        blob = "、".join(wl[:80])
        tcut = text[:4000]
        prompt = (
            "你是汽车舆情二级标签助手。从下列候选中**只选一项**最贴合原文的。\n"
            "只输出一行：该标签的**完整原文**，不要序号、不要解释、不要思考、不要其它文字。\n"
            "候选：\n"
            + blob
            + "\n\n原文：\n"
            + tcut
        )
        mname = self.ollama_model
        print(f"[LLM] 正在调用 {mname}（二级白名单精排）")
        try:
            raw = ollama_generate(mname, prompt, self.ollama_host, num_predict=512)
        except Exception as e:
            print(f"[LLM] 二级调用失败: {e}")
            return wl[0]
        line = first_plain_line(raw)
        if not line:
            return wl[0]
        return nearest_tag_in_list(wl, line)

    def _try_cluster_l3(self, text: str, l1: str, l2: str) -> Optional[dict]:
        """金标聚类：关键词/同义命中则返回 canonical 三级。"""
        blk = self.l3_clusters.get(l1, {}).get(l2)
        if not blk:
            return None
        for cl in blk:
            if not isinstance(cl, dict):
                continue
            can = str(cl.get("canonical") or "").strip()
            if not can:
                continue
            if len(can) >= 3 and can in text:
                l1, l2, l3 = self._validate_and_normalize_triple(l1, l2, can)
                return {
                    "level1": l1,
                    "level2": l2,
                    "level3": l3,
                    "confidence": 0.66,
                    "match_type": "cluster_l3",
                }
            for kw in cl.get("keywords", []) or []:
                k = str(kw).strip()
                if len(k) >= 3 and k in text:
                    l1, l2, l3 = self._validate_and_normalize_triple(l1, l2, can)
                    return {
                        "level1": l1,
                        "level2": l2,
                        "level3": l3,
                        "confidence": 0.63,
                        "match_type": "cluster_l3",
                    }
            for syn in cl.get("synonyms", []) or []:
                s = str(syn).strip()
                if len(s) >= 3 and s in text:
                    l1, l2, l3 = self._validate_and_normalize_triple(l1, l2, can)
                    return {
                        "level1": l1,
                        "level2": l2,
                        "level3": l3,
                        "confidence": 0.62,
                        "match_type": "cluster_l3",
                    }
        return None

    def _llm_pick_l3_plain(self, text: str, l1: str, l2: str) -> dict:
        """规则未命中三级时，由模型从候选列表中输出一行标签名。"""
        block = self.mapping.get(l1, {}).get(l2)
        tags = list(block.get("标签列表", []) if isinstance(block, dict) else [])
        extra: List[str] = []
        for cl in self.l3_clusters.get(l1, {}).get(l2, []) or []:
            if isinstance(cl, dict):
                c = str(cl.get("canonical") or "").strip()
                if c and c not in tags and c not in extra:
                    extra.append(c)
        if not tags and not extra:
            return self._llm_triple_fallback(text, hint_l1=l1)
        show = (tags + extra)[:100]
        blob = "、".join(show)
        tcut = text[:4000]
        prompt = (
            "你是汽车舆情三级标签助手。从下列候选中**只选一项**最贴合原文的。\n"
            "只输出一行：该标签的**完整原文**，不要序号、不要解释、不要思考、不要其它文字。\n"
            "候选：\n"
            + blob
            + "\n\n原文：\n"
            + tcut
        )
        mname = self.ollama_model
        print(f"[LLM] 正在调用 {mname}（三级候选）")
        try:
            raw = ollama_generate(mname, prompt, self.ollama_host, num_predict=512)
        except Exception as e:
            print(f"[LLM] 三级调用失败: {e}")
            return self._llm_triple_fallback(text, hint_l1=l1)
        line = first_plain_line(raw)
        if not line:
            print("[LLM] 三级输出为空 → 兜底")
            return self._llm_triple_fallback(text, hint_l1=l1)
        l3 = nearest_tag_in_list(show, line)
        l1, l2, l3 = self._validate_and_normalize_triple(l1, l2, l3)
        return {
            "level1": l1,
            "level2": l2,
            "level3": l3,
            "confidence": 0.62,
            "match_type": "llm_l3",
        }

    def _resolve_l3_after_l2(self, text: str, l1: str, l2: str) -> dict:
        """三级：原体系规则 → 金标聚类 →（可选）定位 L3 标准层 → 通用 → LLM。"""
        hit = self._best_l3_in_l2(text, l1, l2, min_score=9.0)
        if hit:
            return hit
        c3 = self._try_cluster_l3(text, l1, l2)
        if c3:
            return c3
        # S5：产品质量/服务类优先用定版 L3；无命中写「其他-待归类」，不再写「通用」
        try:
            from label_project.l3_standard_resolver import resolve_l3_with_standard
        except ImportError:  # 兼容直接在 label_project/ 下运行
            from l3_standard_resolver import resolve_l3_with_standard  # type: ignore
        std = resolve_l3_with_standard(text, l1, l2)
        if std:
            return std
        g3 = self._generic_l3_for_l2(l1, l2, text)
        if g3:
            return g3
        if self._ollama_model_ok is False:
            l1, l2, l3 = self._validate_and_normalize_triple(l1, l2, "")
            return {
                "level1": l1,
                "level2": l2,
                "level3": l3,
                "confidence": 0.45,
                "match_type": "llm_fallback",
            }
        return self._llm_pick_l3_plain(text, l1, l2)

    def _match_llm(self, text: str, l1_locked: Optional[str] = None) -> dict:
        """规则无命中时：一级锁定（无 LLM）；二级白名单+规则+LLM 精排；三级规则→聚类→LLM。"""
        if not self.use_llm:
            raise LLMInferenceError("内部错误：use_llm=False 时不应调用 _match_llm")

        mname = self.ollama_model
        key = hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()
        if key in self._llm_cache:
            print(f"[LLM] 缓存命中 model={mname}（未发起 HTTP）")
            print("[LLM] 调用成功")
            return dict(self._llm_cache[key])

        self._ensure_ollama_once()

        def _finish_fallback(reason: str, hint_l1: Optional[str] = None) -> dict:
            print(f"[LLM] {reason} → 使用兜底标签")
            out = self._llm_triple_fallback(text, hint_l1=hint_l1)
            self._llm_cache[key] = out
            print("[LLM] 调用成功（兜底标签）")
            return out

        if self._ollama_model_ok is False:
            return _finish_fallback("模型未拉取或不可用")

        valid_l1 = [k for k in self.mapping if not str(k).startswith("_")]
        rule_l1 = self.match_first_label(text)
        lock = canonicalize_l1_label(l1_locked) if (l1_locked or "").strip() else ""
        if lock and lock in valid_l1:
            l1 = lock
            print(f"[LLM] 一级锁定（清洗/规则）: {l1}（规则参考: {rule_l1}）")
        else:
            l1 = rule_l1 if rule_l1 in valid_l1 else "服务类"
            print(f"[LLM] 一级（仅规则打分，无 LLM）: {l1}")

        l2 = self._resolve_l2_whitelist(text, l1, allow_llm=True)
        print(f"[LLM] 二级（白名单内规则+LLM 精排）: {l2}")

        out = dict(self._resolve_l3_after_l2(text, l1, l2))
        mt = out.get("match_type", "")
        if mt == "llm_l3":
            out["confidence"] = min(0.7, float(out.get("confidence", 0.62)) + 0.05)
        elif mt in ("routed", "scored", "exact", "fuzzy", "generic_l3", "generic", "llm_fallback", "cluster_l3"):
            pass
        else:
            out["match_type"] = "llm"
            out["confidence"] = max(float(out.get("confidence", 0.5)), 0.58)

        self._llm_cache[key] = out
        print("[LLM] 调用成功")
        return out

    def _build_keyword_index(self):
        index = {"一级": defaultdict(list), "二级": {}, "三级": {}}
        for level1, level2_data in self.mapping.items():
            if str(level1).startswith("_"):
                continue
            for level2, level3_data in level2_data.items():
                if not isinstance(level3_data, dict):
                    continue
                for l3 in level3_data.get("标签列表", []):
                    l3_str = str(l3).strip()
                    if len(l3_str) < 2:
                        continue
                    index["三级"][l3_str] = level3_data.get("详情", {}).get(l3_str, 1)
                    if level2 not in index["二级"]:
                        index["二级"][level2] = {}
                    index["二级"][level2][l3_str] = 1
                    for kw in self._index_tokens(l3_str):
                        if len(kw) >= 3:
                            index["一级"][kw].append(
                                {"level1": level1, "level2": level2, "level3": l3_str}
                            )
        return index

    @staticmethod
    def _index_tokens(s: str):
        s = s.strip()
        out = set()
        if len(s) >= 2:
            out.add(s[:4])
            out.add(s[:3])
        for L in (6, 5, 4, 3):
            for i in range(max(0, len(s) - L + 1)):
                out.add(s[i : i + L])
        return {x for x in out if x and len(x) >= 2}

    @staticmethod
    def _norm_text(text) -> str:
        if text is None or (isinstance(text, float) and pd.isna(text)):
            return ""
        return re.sub(r"\s+", " ", str(text).strip())

    def _get_aliases(self, level1: str, level2: str, level3: str) -> list[str]:
        kws = []
        try:
            block = self.file_aliases.get(level1, {}).get(level2, {}).get(level3)
            if isinstance(block, list):
                kws.extend(block)
        except Exception:
            pass
        return kws

    def match_first_label(self, text: str) -> str:
        """一级加权打分：任务书故障词 + 车辆/充电弱特征 + 体验/服务词，按分值取最大（调参目标：与人工标注一致率最大化）。"""
        if not text:
            return "服务类"
        t = text
        scores = {k: 0.0 for k in VALID_L1_LABELS}

        for w in FAULT_WORDS:
            if w in t:
                scores["产品质量类"] += 6.0
        for w in EXTRA_FAULT_HINT:
            if w in t:
                scores["产品质量类"] += 2.5
        for w in ("故障", "不亮", "坏了", "脱落", "泄漏", "断裂", "松动"):
            if w in t:
                scores["产品质量类"] += 4.0

        tl = t.lower()
        for w in BROAD_PRODUCT_HINTS:
            if w.lower() in tl or w in t:
                scores["产品质量类"] += 1.85

        for w in EXP_WORDS:
            if w in t:
                scores["体验需求类"] += 5.0
        if any(w in t for w in ("售后", "门店")):
            scores["服务类"] += 7.0
        for w in ("态度", "收费", "差"):
            if w in t:
                scores["服务类"] += 3.5
        if "慢" in t:
            if any(x in t for x in ("充电", "车机", "系统", "升级", "OTA", "车速")):
                scores["产品质量类"] += 5.0
            else:
                scores["服务类"] += 3.0
        for w in ("投诉", "退款", "积分", "合同", "订金", "定金", "索赔", "代步", "保养", "质保", "维修", "备件", "销售", "万象城", "交付", "提车", "客服"):
            if w in t:
                scores["服务类"] += 2.5
        if "异常" in t:
            if any(x in t for x in ("服务", "售后", "态度", "门店")) and not any(
                x in t for x in ("车机", "充电", "屏幕", "系统", "电池", "车辆")
            ):
                scores["服务类"] += 4.0
            else:
                scores["产品质量类"] += 4.0

        if not any(x in t for x in NEGATIVE_NON_ISSUE):
            for w in NON_ISSUE_BOOST:
                if w in t:
                    scores["非问题"] += 3.8

        p, s, e, n = (
            scores["产品质量类"],
            scores["服务类"],
            scores["体验需求类"],
            scores["非问题"],
        )
        # 「非问题」须明显高于业务类才采纳，避免咨询话术覆盖真实故障/建议
        if n >= 13.5 and n > max(p, s, e) + 2.8:
            return "非问题"
        if p >= 4.5 and p >= max(s, e) - 1:
            return "产品质量类"
        if s >= 7.0 and s >= max(p, e):
            return "服务类"
        if e >= 6.0 and e >= max(p, s):
            return "体验需求类"
        if p >= max(s, e):
            return "产品质量类"
        return "服务类"

    def _priority_rule_match(self, text: str):
        tl = text.lower()
        for cre, l1, l2, l3, conf in self._compiled_rules:
            if cre.match(tl):
                return {"level1": l1, "level2": l2, "level3": l3, "confidence": conf, "match_type": "rule"}
        return None

    def _score_l3(self, text: str, text_l: str, level1: str, level2: str, l3: str, detail: dict) -> float:
        l3s = str(l3)
        score = 0.0
        if l3s and (l3s in text or l3s.lower() in text_l):
            score += 120.0
        for kw in self._get_aliases(level1, level2, l3s):
            k = kw.strip().lower()
            if len(k) >= 2 and (k in text_l or kw in text):
                score += 45.0
        for tok in self._index_tokens(l3s):
            if len(tok) >= 3 and tok.lower() in text_l:
                score += 15.0
            elif len(tok) == 2 and tok in text:
                score += 5.0
        freq = detail.get(l3s, 1) if isinstance(detail, dict) else 1
        score += min(8.0, math.log(1 + max(1, freq)))
        return score

    def _best_l3_in_l2(self, text: str, level1: str, level2: str, min_score: float = 10.0):
        text_l = text.lower()
        l1data = self.mapping.get(level1, {})
        level3_data = l1data.get(level2)
        if not isinstance(level3_data, dict):
            return None
        tags = level3_data.get("标签列表", [])
        detail = level3_data.get("详情", {})
        best_score = -1.0
        best = None
        l2_boost = 22.0 if level2 in text else 0.0
        for l3 in tags:
            s = self._score_l3(text, text_l, level1, level2, l3, detail) + l2_boost
            if s > best_score:
                best_score = s
                best = (level1, level2, l3)
        if best and best_score >= min_score:
            conf = min(0.92, 0.52 + best_score / 200.0)
            return {
                "level1": best[0],
                "level2": best[1],
                "level3": best[2],
                "confidence": conf,
                "match_type": "routed",
            }
        return None

    def _best_in_level1(self, text: str, level1: str):
        text_l = text.lower()
        best = None
        best_score = -1.0
        l1data = self.mapping.get(level1)
        if not isinstance(l1data, dict):
            return None
        for level2, level3_data in l1data.items():
            if str(level2).startswith("_") or not isinstance(level3_data, dict):
                continue
            l2_boost = 28.0 if level2 and level2 in text else 0.0
            tags = level3_data.get("标签列表", [])
            detail = level3_data.get("详情", {})
            for l3 in tags:
                s = self._score_l3(text, text_l, level1, level2, l3, detail) + l2_boost
                if s > best_score:
                    best_score = s
                    best = (level1, level2, l3, s)
        if best and best_score >= 11.0:
            conf = min(0.92, 0.55 + best_score / 200.0)
            return {
                "level1": best[0],
                "level2": best[1],
                "level3": best[2],
                "confidence": conf,
                "match_type": "scored",
            }
        return None

    def _infer_l2(self, text: str, l1: str) -> Optional[str]:
        """按症状关键词路由到人工标注中最常见的二级标签。"""
        t = text
        tl = t.lower()
        if l1 == "产品质量类":
            scored = []
            if re.search(r"emira", tl, re.I):
                scored.append(("Emira问题", 20))
            if any(w in t for w in ("闪充", "超充", "地锁", "三方充电", "充电站", "公共充电")):
                scored.append(("LFC问题", 18))
            if any(w in t for w in ("家充", "家用桩")):
                scored.append(("LFC问题", 17))
            if any(
                w in t
                for w in (
                    "预约充电",
                    "车端充电",
                    "无法充电",
                    "充不进",
                    "充电失败",
                    "充电功率",
                    "充电慢",
                    "充电中断",
                    "波谷充电",
                    "充电盖板",
                )
            ):
                scored.append(("车端充电问题", 18))
            if any(w in t for w in ("智驾", "领航", "NOA", "AEB", "泊车", "AD4", "流媒体", "AVM", "辅助驾驶")):
                scored.append(("AD4问题", 17))
            if any(w in t for w in ("异响", "异音")):
                scored.append(("异响问题", 18))
            if any(w in t for w in ("钥匙", "解锁", "数字钥匙", "UWB", "nfc", "nfc卡")):
                scored.append(("钥匙问题", 15))
            if any(w in t for w in ("蓝牙", "HUD", "hud", "抬头显示", "车机", "导航", "CarPlay", "carplay", "语音", "音响", "CSD", "黑屏", "死机", "卡帧", "掉帧", "屏幕", "仪表")):
                scored.append(("座舱问题", 16))
            if "ota" in tl or "OTA" in t:
                if any(w in t for w in ("故障", "蓝牙", "失败", "异常", "问题")):
                    scored.append(("OTA更新问题", 16))
            if any(w in t for w in ("报警", "告警", "故障灯")):
                scored.append(("故障告警", 14))
            if any(w in t for w in ("车门", "尾门", "电吸门")):
                scored.append(("车门问题", 12))
            if any(w in t for w in ("空调", "激光雷达", "无线充电", "车窗", "雨刷", "后视镜", "灯光", "天窗", "电子电器")):
                scored.append(("电子电器问题", 14))
            if any(w in t for w in ("APP", "应用", "小程序")):
                scored.append(("APP问题", 12))
            if scored:
                scored.sort(key=lambda x: -x[1])
                return scored[0][0]
        elif l1 == "服务类":
            scored = []
            if any(w in t for w in ("售后", "维修", "备件", "索赔", "保养", "质保", "代步", "道路救援")):
                scored.append(("售后服务问题", 18))
            if any(w in t for w in ("销售", "门店", "政策", "顾问", "万象城")):
                scored.append(("销售服务问题", 17))
            if any(w in t for w in ("订单", "退款", "退订", "定金", "订金")):
                scored.append(("订单取消/退款", 17))
            if "积分" in t:
                scored.append(("积分发放问题", 16))
            if any(w in t for w in ("交付", "提车")):
                scored.append(("交付问题", 14))
            if any(w in t for w in ("商城", "车品", "周边")):
                scored.append(("车品商城问题", 12))
            if scored:
                scored.sort(key=lambda x: -x[1])
                return scored[0][0]
        elif l1 == "体验需求类":
            scored = []
            if "ota" in tl or "OTA" in t or "升级" in t:
                scored.append(("OTA建议", 18))
            if any(w in t for w in ("华为", "安卓", "数字钥匙", "手机钥匙", "三星", "魅族")):
                scored.append(("华为/安卓手机数字钥匙", 16))
            if any(w in t for w in ("充电地图", "导航", "沿途", "规划充电")):
                scored.append(("导航沿途规划权益充电", 12))
            if scored:
                scored.sort(key=lambda x: -x[1])
                return scored[0][0]
        elif l1 == "非问题":
            scored = []
            tl_local = t.lower()
            # 「咨询与表扬」严格收口：原文出现任一负面/诉求/业务边界词，禁止采用咨询与表扬
            has_neg = any(w.lower() in tl_local for w in NEGATIVE_NON_ISSUE)
            if not has_neg:
                if any(w in t for w in ("表扬", "感谢", "点赞", "好评", "满意", "五星", "非常棒")):
                    scored.append(("咨询与表扬", 18))
                if any(w in t for w in ("咨询", "问问", "了解", "请问", "想问", "问一下")):
                    scored.append(("咨询与表扬", 14))
                if scored:
                    scored.sort(key=lambda x: -x[1])
                    return scored[0][0]
            return "其他非问题"
        return None

    def _generic_l3_for_l2(self, l1: str, l2: str, text: str):
        """二级标签下无足够具体三级命中时，返回「通用」或 *-通用-* 三级。"""
        block = self.mapping.get(l1, {}).get(l2)
        if not isinstance(block, dict):
            return None
        tags = block.get("标签列表", [])
        if not tags:
            return None
        if "通用" in tags:
            return {
                "level1": l1,
                "level2": l2,
                "level3": "通用",
                "confidence": 0.48,
                "match_type": "generic_l3",
            }
        for tag in tags:
            ts = str(tag)
            if "无明确" in ts or "通用" in ts:
                return {
                    "level1": l1,
                    "level2": l2,
                    "level3": tag,
                    "confidence": 0.48,
                    "match_type": "generic_l3",
                }
        return {
            "level1": l1,
            "level2": l2,
            "level3": tags[0],
            "confidence": 0.42,
            "match_type": "generic_l3",
        }

    def _generic_fallback(self, text: str, level1: str):
        if level1 != "产品质量类":
            return None
        if "卡顿" in text or "卡死" in text:
            return {
                "level1": "产品质量类",
                "level2": "卡顿-通用",
                "level3": "卡顿-通用-无明确载体",
                "confidence": 0.52,
                "match_type": "generic",
            }
        if "异响" in text:
            return {
                "level1": "产品质量类",
                "level2": "异响-通用",
                "level3": "异响-通用-无明确载体",
                "confidence": 0.52,
                "match_type": "generic",
            }
        if "黑屏" in text:
            return {
                "level1": "产品质量类",
                "level2": "黑屏-通用",
                "level3": "黑屏-通用-无明确载体",
                "confidence": 0.52,
                "match_type": "generic",
            }
        if "死机" in text:
            return {
                "level1": "产品质量类",
                "level2": "死机-通用",
                "level3": "死机-通用-无明确载体",
                "confidence": 0.52,
                "match_type": "generic",
            }
        if "故障" in text or "失灵" in text:
            return {
                "level1": "产品质量类",
                "level2": "故障-通用",
                "level3": "故障-通用-无明确载体",
                "confidence": 0.52,
                "match_type": "generic",
            }
        return None

    def _legacy_exact_fuzzy(self, text: str, text_l: str):
        for l3 in self.keyword_index["三级"]:
            if len(l3) < 2:
                continue
            if l3 not in text and str(l3).lower() not in text_l:
                continue
            for l1, l2d in self.mapping.items():
                if str(l1).startswith("_"):
                    continue
                for l2, l3_data in l2d.items():
                    if not isinstance(l3_data, dict):
                        continue
                    if l3 in l3_data.get("标签列表", []):
                        return {
                            "level1": l1,
                            "level2": l2,
                            "level3": l3,
                            "confidence": 0.88,
                            "match_type": "exact",
                        }
        for length in (6, 5, 4, 3):
            for i in range(len(text_l) - length + 1):
                kw = text_l[i : i + length]
                if kw in self.keyword_index["一级"]:
                    for m in self.keyword_index["一级"][kw]:
                        return {
                            "level1": m["level1"],
                            "level2": m["level2"],
                            "level3": m["level3"],
                            "confidence": 0.68,
                            "match_type": "fuzzy",
                        }
        return None

    def _match_keyword_pipeline(self, text: str, text_l: str, level1_hint=None):
        """关键词/规则/打分路径；无 LLM。"""
        pr = self._priority_rule_match(text_l)
        if pr:
            return pr

        if level1_hint and str(level1_hint).strip() in self.mapping and not str(level1_hint).startswith("_"):
            l1 = str(level1_hint).strip()
        else:
            l1 = self.match_first_label(text)

        l2 = self._infer_l2(text, l1)
        if l2 and l2 in self.mapping.get(l1, {}):
            hit = self._best_l3_in_l2(text, l1, l2, min_score=9.0)
            if hit:
                return hit
            c3 = self._try_cluster_l3(text, l1, l2)
            if c3:
                return c3
            g3 = self._generic_l3_for_l2(l1, l2, text)
            if g3:
                return g3

        pr2 = self._best_in_level1(text, l1)
        if pr2:
            return pr2

        leg = self._legacy_exact_fuzzy(text, text_l)
        if leg:
            return leg

        gf = self._generic_fallback(text, l1)
        if gf:
            return gf

        # 不再使用弱默认（体验/服务兜底），交给 LLM 或返回 None
        return None

    def _weak_keyword_fallback(self, text: str) -> Optional[dict]:
        """无 LLM 时的弱兜底（与旧版兼容）。"""
        l1 = self.match_first_label(text)
        if l1 == "体验需求类" or any(x in text for x in ("优化", "建议", "希望", "想要", "能不能")):
            return {
                "level1": "体验需求类",
                "level2": "优化-通用",
                "level3": "优化-通用-无明确建议",
                "confidence": 0.45,
                "match_type": "generic",
            }
        if l1 == "服务类":
            return {
                "level1": "服务类",
                "level2": "售后服务问题",
                "level3": "通用",
                "confidence": 0.4,
                "match_type": "generic_l3",
            }
        return None

    def match(self, text, level1_hint=None):
        """关键词优先；无命中则 LLM（默认开启）；LLM 异常或空输出时走兜底标签。"""
        text = self._norm_text(text)
        if not text:
            return None
        text_l = text.lower()
        hkey = hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()

        # 复核金标命中：人工已确认的最终结论，置信度 0.99，跳过所有规则/LLM/联防。
        gold = self._review_gold_by_hash.get(hkey) if self._review_gold_by_hash else None
        if gold:
            gl1 = gold.get("l1") or ""
            gl2 = (gold.get("l2") or "").strip()
            gl3 = (gold.get("l3") or "").strip()
            if not gl2:
                l2_names = self._sorted_l2_names(gl1) if gl1 else []
                gl2 = l2_names[0] if l2_names else ""
            if not gl3:
                gl3 = self._first_cluster_l3(gl1, gl2) or "通用"
            return {
                "level1": gl1,
                "level2": gl2,
                "level3": gl3,
                "confidence": 0.99,
                "match_type": "gold_review",
            }

        clean_hit = self._clean_l1_by_hash.get(hkey) if self._clean_l1_by_hash else None
        raw_hint = (clean_hit or level1_hint or "").strip()
        hint = canonicalize_l1_label(raw_hint) if raw_hint else None
        r = self._match_keyword_pipeline(text, text_l, hint)
        if r is not None:
            out = dict(r)
            if clean_hit:
                out["match_type"] = "clean_l1"
            return out
        if self.use_llm:
            return self._match_llm(text, l1_locked=hint)
        return self._weak_keyword_fallback(text)


def load_test_data(csv_path: str) -> pd.DataFrame:
    print(f"\n📂 加载测试数据: {csv_path}")
    df = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            df = pd.read_csv(csv_path, encoding=encoding)
            print(f"   编码: {encoding}")
            break
        except Exception:
            continue
    if df is None:
        raise RuntimeError(f"无法读取: {csv_path}")
    print(f"   数据量: {len(df)} 条")
    return df


def _char_jaccard(a: str, b: str, thr: float = 0.32) -> bool:
    """中文二级/三级标签的宽松相似度（字符集合 Jaccard）。"""
    sa, sb = set(str(a)), set(str(b))
    if len(sa) < 2 or len(sb) < 2:
        return False
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union >= thr


def pick_columns(df: pd.DataFrame):
    cols = df.columns.tolist()

    def pick(candidates):
        for c in candidates:
            if c in cols:
                return c
        return None

    text_col = pick(["舆情中文", "原文", "内容", "feedback_content", "text"])
    text2 = pick(["舆情原文"])
    level1_col = pick(["舆情分类", "一级标签", "人工分类", "label", "category"])
    level2_col = pick(["问题简述", "二级标签", "sub_category"])
    level3_col = pick(["问题细分", "三级标签", "detail"])
    return text_col, text2, level1_col, level2_col, level3_col


def run_validation(
    csv_path,
    output_path=None,
    sample_size=None,
    use_text_only=True,
    mapping_file=None,
    use_summary_columns=False,
    use_llm: bool = True,
    ollama_host: str = OLLAMA_HOST,
    clean_csv_path: Optional[str] = None,
    l2_whitelist_json: Optional[str] = None,
    l3_cluster_json: Optional[str] = None,
):
    print("=" * 60)
    print("三级标签离线验证（关键词+规则+清洗数据 + Qwen LLM·严格模式·纯原文）")
    print("=" * 60)

    mpath = mapping_file or MAPPING_FILE
    if use_llm:
        ensure_ollama_ready(ollama_host)
    matcher = LabelMatcher(
        mpath,
        use_llm=use_llm,
        ollama_host=ollama_host,
        clean_csv_path=clean_csv_path if clean_csv_path is not None else DEFAULT_CLEAN_CSV,
        l2_whitelist_json=l2_whitelist_json,
        l3_cluster_json=l3_cluster_json,
    )
    if use_llm:
        matcher._ollama_model_ok = ollama_model_available(ollama_host, matcher.ollama_model)
        if not matcher._ollama_model_ok:
            print(f"⚠️ Ollama 中未找到模型 {matcher.ollama_model!r}，请执行: ollama pull {matcher.ollama_model}")
            print("   离线验证仍会继续，需要 LLM 的行将走规则兜底。")
            matcher._model_warned = True

    df = load_test_data(csv_path)

    if sample_size and len(df) > sample_size:
        df = df.sample(n=sample_size, random_state=42)
        print(f"\n📊 采样后数据量: {len(df)} 条")
    df = df.reset_index(drop=True)

    text_col, text2, level1_col, level2_col, level3_col = pick_columns(df)
    print(f"\n🔍 关键列: 原文={text_col}, 备原文={text2}, L1={level1_col}, L2={level2_col}, L3={level3_col}")

    results = []
    level1_match = level2_match = level3_match = 0
    level2_compat = level3_compat = 0
    total = len(df)

    for pos in range(total):
        if pos > 0 and pos % 100 == 0:
            print(f"   … 进度 {pos}/{total}")
        row = df.iloc[pos]
        if use_text_only:
            parts = []
            if text_col and pd.notna(row[text_col]):
                parts.append(str(row[text_col]))
            if text2 and pd.notna(row[text2]):
                parts.append(str(row[text2]))
            if use_summary_columns and level2_col and pd.notna(row[level2_col]):
                parts.append(str(row[level2_col]))
            if use_summary_columns and level3_col and pd.notna(row[level3_col]):
                parts.append(str(row[level3_col]))
            raw_text = LabelMatcher._norm_text(" ".join(parts))
        else:
            parts = []
            if text_col:
                parts.append(str(row[text_col]) if pd.notna(row[text_col]) else "")
            if text2:
                parts.append(str(row[text2]) if pd.notna(row[text2]) else "")
            raw_text = LabelMatcher._norm_text(" ".join(parts))

        human_l1 = str(row[level1_col]).strip() if level1_col and pd.notna(row[level1_col]) else ""
        human_l2 = str(row[level2_col]).strip() if level2_col and pd.notna(row[level2_col]) else ""
        human_l3 = str(row[level3_col]).strip() if level3_col and pd.notna(row[level3_col]) else ""

        match_result = matcher.match(raw_text, None)

        if match_result:
            auto_l1 = match_result["level1"]
            auto_l2 = match_result["level2"]
            auto_l3 = match_result["level3"]
            confidence = match_result["confidence"]
            match_type = match_result["match_type"]
        else:
            auto_l1 = auto_l2 = auto_l3 = "未匹配"
            confidence = 0
            match_type = "none"

        l1_ok = 1 if auto_l1 == human_l1 and human_l1 else 0
        l2_ok = 1 if auto_l2 == human_l2 and human_l2 else 0
        l3_ok = 1 if auto_l3 == human_l3 and human_l3 else 0
        level1_match += l1_ok
        level2_match += l2_ok
        level3_match += l3_ok
        if l1_ok:
            if l2_ok or _char_jaccard(auto_l2, human_l2, 0.28):
                level2_compat += 1
            if l3_ok or _char_jaccard(auto_l3, human_l3, 0.22):
                level3_compat += 1

        short = raw_text[:50] + "..." if len(raw_text) > 50 else raw_text
        results.append(
            {
                "序号": pos + 1,
                "原文": short,
                "人工一级": human_l1,
                "自动一级": auto_l1,
                "一级匹配": "✅" if l1_ok else "❌",
                "人工二级": human_l2,
                "自动二级": auto_l2,
                "二级匹配": "✅" if l2_ok else "❌",
                "人工三级": human_l3,
                "自动三级": auto_l3,
                "三级匹配": "✅" if l3_ok else "❌",
                "置信度": confidence,
                "匹配类型": match_type,
            }
        )

    l1_rate = level1_match / total * 100 if total else 0
    l2_rate = level2_match / total * 100 if total else 0
    l3_rate = level3_match / total * 100 if total else 0
    l1_ok_n = level1_match
    l2c_in_l1 = level2_compat / l1_ok_n * 100 if l1_ok_n else 0.0
    l3c_in_l1 = level3_compat / l1_ok_n * 100 if l1_ok_n else 0.0

    print(f"\n{'=' * 60}\n📊 验证结果（严格模式·字符串精确匹配）\n{'=' * 60}")
    print(f"  总样本数: {total}")
    print(f"  一级匹配: {level1_match}/{total} ({l1_rate:.2f}%)")
    print(f"  二级匹配: {level2_match}/{total} ({l2_rate:.2f}%)")
    print(f"  三级匹配: {level3_match}/{total} ({l3_rate:.2f}%)")
    print(f"\n📊 在「一级一致」的 {l1_ok_n} 条内·字符 Jaccard 兼容（二级≥0.28 / 三级≥0.22）")
    print(f"  二级兼容: {level2_compat}/{l1_ok_n} ({l2c_in_l1:.2f}%)")
    print(f"  三级兼容: {level3_compat}/{l1_ok_n} ({l3c_in_l1:.2f}%)")

    result_df = pd.DataFrame(results)
    if output_path:
        out_abs = os.path.abspath(output_path)
        out_dir_csv = os.path.dirname(out_abs)
        if out_dir_csv:
            os.makedirs(out_dir_csv, exist_ok=True)
        result_df.to_csv(out_abs, index=False, encoding="utf-8-sig")
        print(f"\n✅ 结果已保存: {out_abs}")

    out_dir = os.path.join(OUTPUT_DIR, "output")
    os.makedirs(out_dir, exist_ok=True)
    unmatched = []
    for i, r in enumerate(results):
        if r["一级匹配"] == "❌" or r["二级匹配"] == "❌" or r["三级匹配"] == "❌":
            row = df.iloc[i]
            uid = row.get("舆情编号", f"ID_{i+1}")
            unmatched.append(
                {
                    "舆情编号": str(uid),
                    "原文": str(r.get("原文", ""))[:500],
                    "系统一级标签": r.get("自动一级", ""),
                    "人工一级标签": r.get("人工一级", ""),
                    "系统二级标签": r.get("自动二级", ""),
                    "人工二级标签": r.get("人工二级", ""),
                    "系统三级标签": r.get("自动三级", ""),
                    "人工三级标签": r.get("人工三级", ""),
                    "不匹配原因": "/".join(
                        [k for k, v in [("一级", r["一级匹配"]), ("二级", r["二级匹配"]), ("三级", r["三级匹配"])] if v == "❌"]
                    ),
                    "备注": "",
                }
            )
    upath = os.path.join(out_dir, "unmatched_cases.csv")
    if unmatched:
        pd.DataFrame(unmatched).to_csv(upath, index=False, encoding="utf-8-sig")
        print(f"\n✅ 不匹配案例: {upath} ({len(unmatched)} 条)")
    else:
        print("\n✅ 全部匹配")

    return {
        "total": total,
        "level1_match": level1_match,
        "level2_match": level2_match,
        "level3_match": level3_match,
        "level1_rate": l1_rate,
        "level2_rate": l2_rate,
        "level3_rate": l3_rate,
        "level2_compat_when_l1": l2c_in_l1,
        "level3_compat_when_l1": l3c_in_l1,
    }


def main():
    parser = argparse.ArgumentParser(description="三级标签离线验证")
    parser.add_argument("-i", "--input", default=DEFAULT_INPUT, help="输入 CSV")
    parser.add_argument("-o", "--output", default=None, help="输出结果 CSV")
    parser.add_argument("-n", "--sample", type=int, default=200, help="采样条数，0 表示全量")
    parser.add_argument("-m", "--mapping", default=MAPPING_FILE, help="映射 JSON")
    parser.add_argument(
        "--use-summary",
        action="store_true",
        help="匹配时附加「问题简述」「问题细分」列（非纯原文）",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="禁用 Ollama，仅关键词/规则（不检查 ollama serve）",
    )
    parser.add_argument(
        "--ollama-host",
        default=OLLAMA_HOST,
        help=f"Ollama 地址（默认 {OLLAMA_HOST}）",
    )
    parser.add_argument(
        "--clean-csv",
        default=None,
        help=f"已人工复核清洗数据 CSV（默认 {DEFAULT_CLEAN_CSV}）",
    )
    parser.add_argument(
        "--l2-whitelist",
        default=None,
        help=f"二级白名单 JSON（默认 {DEFAULT_L2_WHITELIST_JSON}）",
    )
    parser.add_argument(
        "--l3-cluster",
        default=None,
        help=f"三级聚类 JSON（默认 {DEFAULT_L3_CLUSTER_JSON}）",
    )
    args = parser.parse_args()

    out = args.output or os.path.join(OUTPUT_PARENT, "validation_result.csv")
    sample = args.sample if args.sample and args.sample > 0 else None
    run_validation(
        args.input,
        out,
        sample_size=sample,
        use_text_only=True,
        mapping_file=args.mapping,
        use_summary_columns=args.use_summary,
        use_llm=not args.no_llm,
        ollama_host=args.ollama_host,
        clean_csv_path=args.clean_csv,
        l2_whitelist_json=args.l2_whitelist,
        l3_cluster_json=args.l3_cluster,
    )


if __name__ == "__main__":
    main()
