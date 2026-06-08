# -*- coding: utf-8 -*-
"""本地 Qwen2.5 14B/Ollama 结构化推理工具。

只封装本地 HTTP 调用、提示词、候选标签约束与只读历史样本查询；不直接修改业务数据库。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BACKEND_DIR = Path(__file__).resolve().parent
SRC_DIR = BACKEND_DIR.parent
PROJECT_ROOT = SRC_DIR.parent
LABEL_PROJECT_DIR = PROJECT_ROOT / "label_project"

import sys

if str(LABEL_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(LABEL_PROJECT_DIR))

from taxonomy_normalize import (  # noqa: E402
    CANONICAL_L1_LABELS,
    NON_ISSUE_L1,
    canonicalize_l1_label,
    is_non_issue_l1,
    strip_non_issue_l2,
    try_canonicalize_l1,
)

QWEN_MODEL = os.environ.get("VOC_QWEN_MODEL", "qwen2.5:14b-instruct-q4_K_M")
QWEN_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
QWEN_TEMPERATURE = float(os.environ.get("VOC_QWEN_TEMPERATURE", "0.1"))
QWEN_TOP_P = float(os.environ.get("VOC_QWEN_TOP_P", "0.3"))
QWEN_NUM_CTX = int(os.environ.get("VOC_QWEN_NUM_CTX", "131072"))
QWEN_NUM_PREDICT = int(os.environ.get("VOC_QWEN_NUM_PREDICT", "768"))
# 分类专用：更短输出 + 适中上下文，明显加快 14B 单次推理
QWEN_CLASSIFY_NUM_PREDICT = int(os.environ.get("VOC_QWEN_CLASSIFY_NUM_PREDICT", "340"))
QWEN_CLASSIFY_NUM_CTX = int(os.environ.get("VOC_QWEN_CLASSIFY_NUM_CTX", "12288"))
# 分类 JSON：解析或字段缺失时最多请求 2 次（首次 + 重试 1 次），与 QWEN_CLASSIFY_RETRIES 解耦
_CLASSIFY_JSON_ATTEMPTS = 2
# 批量 14B 分类调用固定采样参数（不受环境变量覆盖，保证结果稳定）
CLASSIFY_FIXED_TEMPERATURE = 0.1
CLASSIFY_FIXED_TOP_P = 0.3
QWEN_TIMEOUT = int(os.environ.get("VOC_QWEN_TIMEOUT", "300"))
QWEN_CLASSIFY_TIMEOUT = int(os.environ.get("VOC_QWEN_CLASSIFY_TIMEOUT", "60"))
QWEN_CONCURRENCY = max(1, int(os.environ.get("VOC_QWEN_CONCURRENCY", "1")))
QWEN_CACHE_PATH = Path(os.environ.get("VOC_QWEN_CACHE", str(BACKEND_DIR / "qwen_inference_cache.json")))

_SEM = threading.BoundedSemaphore(QWEN_CONCURRENCY)
_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOADED = False
_CACHE_WRITES_SINCE_FLUSH = 0
_CACHE_FLUSH_EVERY = max(1, int(os.environ.get("VOC_QWEN_CACHE_FLUSH_EVERY", "8")))
_L2_WHITELIST_CACHE: Optional[Dict[str, List[str]]] = None
_L2_WHITELIST_MTIME: float = -1.0
_HIST_EXAMPLES_CACHE: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
_HIST_EXAMPLES_TTL = float(os.environ.get("VOC_QWEN_HIST_TTL", "90"))


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def load_l2_whitelist() -> Dict[str, List[str]]:
    global _L2_WHITELIST_CACHE, _L2_WHITELIST_MTIME
    wl_path = LABEL_PROJECT_DIR / "gold_l2_whitelist_v3.json"
    hi_path = LABEL_PROJECT_DIR / "label_hierarchy_final.json"
    mt = max(
        wl_path.stat().st_mtime if wl_path.is_file() else 0.0,
        hi_path.stat().st_mtime if hi_path.is_file() else 0.0,
    )
    if _L2_WHITELIST_CACHE is not None and mt == _L2_WHITELIST_MTIME:
        return dict(_L2_WHITELIST_CACHE)
    wl = _load_json(wl_path, {})
    if not isinstance(wl, dict):
        wl = {}
    hierarchy = _load_json(hi_path, {})
    out: Dict[str, List[str]] = {k: [] for k in CANONICAL_L1_LABELS}
    for l1 in CANONICAL_L1_LABELS:
        seen = set()
        for x in list(wl.get(l1) or []):
            s = str(x or "").strip()
            if s and s not in seen:
                seen.add(s)
                out[l1].append(s)
        block = hierarchy.get(l1) if isinstance(hierarchy, dict) else {}
        if isinstance(block, dict):
            for x in block.keys():
                s = str(x or "").strip()
                if s and not s.startswith("_") and s not in seen:
                    seen.add(s)
                    out[l1].append(s)
    _L2_WHITELIST_CACHE = out
    _L2_WHITELIST_MTIME = mt
    return dict(out)


def invalidate_l2_whitelist_cache() -> None:
    """金标/体系 JSON 更新后调用，使 load_l2_whitelist 下次读盘。"""
    global _L2_WHITELIST_CACHE, _L2_WHITELIST_MTIME
    _L2_WHITELIST_CACHE = None
    _L2_WHITELIST_MTIME = -1.0


def _read_cache() -> None:
    global _CACHE_LOADED, _CACHE
    if _CACHE_LOADED:
        return
    with _CACHE_LOCK:
        if _CACHE_LOADED:
            return
        try:
            if QWEN_CACHE_PATH.is_file():
                raw = json.loads(QWEN_CACHE_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    _CACHE = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            _CACHE = {}
        _CACHE_LOADED = True


def _cache_key(kind: str, text: str, extra: str = "") -> str:
    h = hashlib.sha256()
    h.update(QWEN_MODEL.encode("utf-8"))
    h.update(b"\0")
    h.update(kind.encode("utf-8"))
    h.update(b"\0")
    h.update(extra.encode("utf-8"))
    h.update(b"\0")
    h.update(text.encode("utf-8", errors="ignore"))
    return h.hexdigest()


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    _read_cache()
    with _CACHE_LOCK:
        v = _CACHE.get(key)
        return dict(v) if isinstance(v, dict) else None


def _flush_cache_to_disk() -> None:
    global _CACHE_WRITES_SINCE_FLUSH
    with _CACHE_LOCK:
        QWEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        QWEN_CACHE_PATH.write_text(json.dumps(_CACHE, ensure_ascii=False), encoding="utf-8")
        _CACHE_WRITES_SINCE_FLUSH = 0


def _cache_set(key: str, value: Dict[str, Any]) -> None:
    global _CACHE_WRITES_SINCE_FLUSH
    _read_cache()
    with _CACHE_LOCK:
        _CACHE[key] = value
        if len(_CACHE) > 5000:
            keep = list(_CACHE.items())[-3500:]
            _CACHE.clear()
            _CACHE.update(keep)
        _CACHE_WRITES_SINCE_FLUSH += 1
        if _CACHE_WRITES_SINCE_FLUSH >= _CACHE_FLUSH_EVERY:
            QWEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            QWEN_CACHE_PATH.write_text(json.dumps(_CACHE, ensure_ascii=False), encoding="utf-8")
            _CACHE_WRITES_SINCE_FLUSH = 0


def ollama_generate(
    prompt: str,
    *,
    model: str = QWEN_MODEL,
    host: str = QWEN_HOST,
    num_predict: int = QWEN_NUM_PREDICT,
    num_ctx: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    timeout_sec: Optional[int] = None,
) -> str:
    ctx = int(num_ctx) if num_ctx is not None else QWEN_NUM_CTX
    temp = QWEN_TEMPERATURE if temperature is None else float(temperature)
    tp = QWEN_TOP_P if top_p is None else float(top_p)
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temp,
                "top_p": tp,
                "num_ctx": ctx,
                "num_predict": num_predict,
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    to = int(timeout_sec) if timeout_sec is not None else int(QWEN_TIMEOUT)
    with _SEM:
        with urllib.request.urlopen(req, timeout=to) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    return str(body.get("response") or "")


def extract_json_object(text: str) -> Dict[str, Any]:
    s = text or ""
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"模型未返回 JSON：{s[:300]}")
    return json.loads(s[start : end + 1])


def _classify_json_has_nonempty_l12(obj: Dict[str, Any]) -> bool:
    l1 = str(obj.get("l1") or obj.get("一级标签") or "").strip()
    l2 = str(obj.get("l2") or obj.get("二级标签") or "").strip()
    l1c = try_canonicalize_l1(l1)
    if not l1c:
        return False
    if l1c == NON_ISSUE_L1:
        return True
    return bool(l2)


def _strict_whitelist_l1_l2(
    raw_l1: str, raw_l2: str, l2_map: Dict[str, List[str]]
) -> Tuple[Optional[str], Optional[str]]:
    """一级须可被规范为四类之一；二级须为该一级在白名单字典中的原文字符串（非问题除外）。"""
    l1c = try_canonicalize_l1(raw_l1)
    if not l1c or l1c not in CANONICAL_L1_LABELS:
        return None, None
    if l1c == NON_ISSUE_L1:
        return l1c, ""
    l2s = (raw_l2 or "").strip()
    allowed = l2_map.get(l1c) or []
    if l2s not in allowed:
        return None, None
    return l1c, l2s


def historical_examples(db_path: str, text: str, limit: int = 4) -> List[Dict[str, str]]:
    """只读抽取历史人工确认样例，按关键词粗相似 + 最近优先（带短 TTL 内存缓存）。"""
    p = Path(db_path)
    if not p.is_file():
        return []
    cache_key = hashlib.sha256((text or "")[:240].encode("utf-8", errors="ignore")).hexdigest()
    now = time.time()
    cached = _HIST_EXAMPLES_CACHE.get(cache_key)
    if cached and now - cached[0] < _HIST_EXAMPLES_TTL:
        return list(cached[1][:limit])

    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}", (text or "")[:240])
    tokens = list(dict.fromkeys(tokens))[:6]
    rows: List[sqlite3.Row] = []
    conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        if tokens:
            cond = " OR ".join(["original_text LIKE ?"] * len(tokens))
            params = [f"%{t}%" for t in tokens]
            rows = conn.execute(
                f"""SELECT original_text, review_l1, review_l2, reviewed_at, create_time
                    FROM opinion
                    WHERE review_status = 1
                    AND TRIM(IFNULL(review_l1,'')) != ''
                    AND TRIM(IFNULL(review_l2,'')) != ''
                    AND ({cond})
                    ORDER BY COALESCE(reviewed_at, create_time, '') DESC
                    LIMIT 24""",
                params,
            ).fetchall()
        if not rows:
            rows = conn.execute(
                """SELECT original_text, review_l1, review_l2, reviewed_at, create_time
                   FROM opinion
                   WHERE review_status = 1
                   AND TRIM(IFNULL(review_l1,'')) != ''
                   AND TRIM(IFNULL(review_l2,'')) != ''
                   ORDER BY COALESCE(reviewed_at, create_time, '') DESC
                   LIMIT 20"""
            ).fetchall()
    finally:
        conn.close()
    qset = set((text or "")[:200])
    scored = []
    for r in rows:
        sample = str(r["original_text"] or "")
        inter = len(qset & set(sample[:200]))
        scored.append((inter, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for _, r in scored[:limit]:
        out.append(
            {
                "text": str(r["original_text"] or "")[:180],
                "l1": canonicalize_l1_label(r["review_l1"]),
                "l2": str(r["review_l2"] or "").strip(),
            }
        )
    _HIST_EXAMPLES_CACHE[cache_key] = (now, out)
    if len(_HIST_EXAMPLES_CACHE) > 400:
        _HIST_EXAMPLES_CACHE.clear()
    return out


# 命中下列任一，则【禁止】一级为「非问题」；同时也用于二级「咨询与表扬」的兜底驳回。
# 关键词覆盖：故障/异常/投诉/抱怨/服务差/体验不好/诉求/退款/维权/吐槽/告警 等。
_FORBID_NON_ISSUE = re.compile(
    r"(车辆故障|功能异常|充电异常|座舱故障|硬件问题|使用报错|品质瑕疵|功能失灵|用车困扰|投诉不满|诉求反馈|"
    r"故障|异常|无法|不能用|不能充|充不进|充不了|失灵|失效|坏了|黑屏|死机|卡顿|异响|"
    r"投诉|不满意|失望|糟糕|太差|体验差|难用|诉求|维权|索赔|bug|报错|报警|告警|亏电|漏电|"
    r"漏液|抖动|断连|希望官方|请尽快|尽快处理|质量问题|售后不理|"
    r"建议|强烈要求|要求退换|退费|退款|"
    # ---- 二级标签错分专项黑名单（基于 1076 条复核错误分布）----
    r"吐槽|抱怨|不满|很差|服务差|态度差|售后差|售后慢|售后不靠谱|跟进慢|响应慢|"
    r"推诿|敷衍|忽悠|欺骗|没人理|无人处理|拖延|拖了|多次反映|多次投诉|多次沟通|"
    r"问题反馈|反馈问题|反映问题|希望解决|希望处理|希望尽快|希望改善|要求处理|"
    r"必须解决|尽快解决|不给解决|没解决|没处理|未处理|未解决|没回复|无回复|"
    r"难受|郁闷|气愤|愤怒|无语|崩溃|心累|失望透顶|无奈|"
    r"扣分|扣钱|乱收费|多收费|加价|不退|不换|不修|"
    r"被坑|被骗|被忽悠|被推诿|售后糟|品控差|工艺差|做工差|"
    r"单方事故|协调拖车|有划痕|需要退|需要换|未按阶梯奖励|"
    r"又被下线|空调不制冷|门打不开|不能以质保|自费处理)",
    re.I,
)

# ===== 词典拆分：真负面词 vs 业务边界词（修复"销售/售后好评被误判为投诉"）=====
#
# 设计原则：
#   1) 真负面词 _KZ_TRUE_NEGATIVE：含明确负面情绪/故障/诉求，命中即"不可能是咨询/表扬"
#   2) 业务边界词 _KZ_BIZ_BOUNDARY：仅表明业务领域（销售/售后/试驾/保养/价格/交付等），
#      命中本身**不能**判断正负面；需要结合 _KZ_PRAISE / _KZ_NEUTRAL_QUERY 联合判断。
#
# 之前 _KZ_FORBID 把两者合并为"一票否决"，导致诸如「销售马女士热情有耐心」被强制
# 归为「销售服务问题」。现在拆开后：
#   - 正向捕获只检查 _KZ_HARD_NEGATIVE（硬负面命中才阻止捕获）
#   - 业务边界 + 正向词 → 允许捕获为「非问题/咨询与表扬」
#
# v9.2：硬/软负面拆分；LFC 域咨询 vs 投诉细分；弱正向词表
# v9.4：烫(?!金) 误拦修复；希望尽快收窄；故障叙述+好评豁免；咨询/家充词表扩展
# v9.5：别/特别误匹配修复；烫印/滚烫；软建议/损伤询价；srv_quality 收窄

# 硬负面：故障/投诉/维权等，命中即禁止正向捕获（不含单独「建议/优化/期望」等软词）
_KZ_HARD_NEGATIVE = re.compile(
    r"(故障|异常|失灵|失效|坏了|无法|不能用|不能充|充不进|充不了|黑屏|死机|卡顿|异响|抖动|漏液|漏水|漏油|断连|烧|(?<![滚])烫(?!金|印)|"
    r"投诉|不满意|抱怨|吐槽|失望|糟糕|太差|很差|服务差|态度差|售后差|售后慢|售后不靠谱|"
    r"难用|诉求|维权|索赔|退款|退费|退订|退换|赔偿|"
    r"推诿|敷衍|忽悠|欺骗|被坑|被骗|被忽悠|被推诿|"
    r"拖延|拖了|不理|没人理|没处理|未处理|未解决|没解决|没回复|无回复|无人处理|"
    r"问题反馈|反馈问题|反映问题|希望解决|希望处理|希望改善|要求处理|"
    r"希望尽快解决|希望尽快处理|希望尽快回复|希望尽快修好|希望尽快弄好|"
    r"必须解决|尽快解决|不给解决|多次反映|多次投诉|"
    r"难受|郁闷|气愤|愤怒|无语|崩溃|心累|失望透顶|无奈|"
    r"扣分|扣钱|乱收费|多收费|加价|不退|不换|不修|"
    r"报警|告警|报错|bug|警告|提示错误|"
    r"售后糟|品控差|工艺差|做工差|品质瑕疵|"
    r"希望官方|请尽快|强烈要求|要求退换)",
    re.I,
)

# 向后兼容：consultation_praise_misclass 等仍引用此名
_KZ_TRUE_NEGATIVE = _KZ_HARD_NEGATIVE

# 业务边界词：仅指业务领域（销售/售后/试驾/保养/价格/交付等），不带正负面判断
# 命中本身不能决定是不是「咨询与表扬」，需要结合其他正负面词判断
_KZ_BIZ_BOUNDARY = re.compile(
    r"(售后|维修|保养|质保|备件|代步|进店检修|服务门店|回访|理赔|"
    r"销售|销售顾问|顾问|门店|客服|接待|试驾|意向金|"
    r"购车|下定|订单|展厅|价格|提车|交付|定金|订金|合同|发票)",
    re.I,
)

# 向后兼容：_KZ_FORBID 仍提供（其他地方可能引用），合并真负面 + 业务边界 + 部分老词
# 但**新逻辑应使用 _KZ_TRUE_NEGATIVE 做"是否可归咨询表扬"的最终判定**
_KZ_FORBID = re.compile(
    r"(故障|异常|失灵|失效|坏了|无法|不能|黑屏|死机|卡顿|异响|抖动|漏|断|烧|烫|"
    r"投诉|不满|抱怨|吐槽|不满意|失望|糟糕|太差|差|难用|"
    r"诉求|维权|索赔|退款|退费|退订|退换|赔偿|"
    r"推诿|敷衍|忽悠|欺骗|拖延|不理|没人理|没处理|未解决|"
    r"建议|希望|期望|期待|增加|优化|改进|完善|"
    r"售后|维修|保养|质保|备件|代步|进店检修|服务门店|回访|理赔|"
    r"销售|销售顾问|顾问|门店|客服|接待|试驾|意向金|"
    r"购车|下定|订单|展厅|价格|提车|交付|定金|订金|合同|发票|"
    r"报警|告警|报错|bug|警告|提示错误|"
    r"乱收费|加价)",
    re.I,
)

# ===== 正向捕获相关词典（与负面词集合互补，不重叠）=====
# 正向赞美/感谢话术：含正面情感/品质/服务评价词；扩展覆盖客户常见正向表达
_KZ_PRAISE = re.compile(
    r"(表扬|点赞|不错|给力|感谢|谢谢|多谢|好评|认可|夸赞|赞一个|"
    r"诚意满满|精致|太好看了|太爱了|礼盒|越来越好|很高兴认识你|提车大吉|"
    r"满意|挺好|很好|不赖|很棒|超棒|靠谱|专业|贴心|高效|及时|推荐|五星|完美|nice|good|"
    r"热情|耐心|细致|周到|用心|敬业|负责|尽职|细心|温馨|友好|和善|亲切|"
    r"透彻|到位|清晰|讲解清楚|讲得好|讲明白|"
    r"惊喜|超预期|超出预期|超值|划算|放心|安心|"
    r"棒极了|棒棒的|赞|很赞|大赞|顶|过硬|出色|"
    r"体验真的满分|真心推荐|省时省力|让人很放心|"
    r"良好体验|体验很好|体验不错|体验棒|服务很好|服务不错)",
    re.I,
)
# 中性问询/咨询/购车意向话术：含咨询/询问 + 购车/试驾意向
_KZ_NEUTRAL_QUERY = re.compile(
    r"(咨询|询问|请教|请问|问一下|问问|想问一下|想问|"
    r"想了解|了解一下|了解下|了解|打听|看看|了解情况|咨询一下|"
    r"保养|取送|上门保养|需要保养|解押|什么时候挪|超时占位费|"
    r"想试驾|想试|预约试驾|预约|"
    r"想要一辆|想买|想入|"
    r"何时上市|什么时候上市|何时发布|什么时候发布|何时交付|什么时候交付|"
    r"价格表|配置表|参数表|有没有|是否有|是否能|"
    r"报装|电表|勘测|进度|怎么查|如何查询|查询|核实|确认一下|问一下情况|"
    r"怎么开启|怎么解决|大家都怎么|再次来电|取消预约|"
    r"需不需要|是不是|会打开吗|"
    r"能不能|可不可以|咋办|怎么办)",
    re.I,
)
# 品牌动态/活动陈述类（如"FOR ME 正式发布"、"预售价格公布"）
# 仅收录"强动态信号词"——避免与负面投诉重叠（如"权益缩水"、"优惠骗局"）
_KZ_ANNOUNCEMENT = re.compile(
    r"(正式发布|新品发布|发布会|开启预售|开启预订|开启交付|权益公布|价格公布|官宣|官方公布|"
    r"上市发布|首发|车展亮相|发布价格|发布预售|新车上市)",
    re.I,
)
# 超短无意义评价句（去空白后整段就是一/几个好评词）
_KZ_SHORT_BENIGN = re.compile(
    r"^[\s\u3000]*((很好|好|挺好|很棒|超棒|不错|可以|还行|还可以|没问题|没事|赞|满意|完美|"
    r"nice|good|ok)[\s\u3000。！!\.\?\?,，~～]*)+$",
    re.I,
)
# 销售/试驾场景好评（业务边界 + 正向语义）
_KZ_SALES_TESTDRIVE_PRAISE = re.compile(
    r"(销售|试驾|顾问|接待|门店|客服).{0,32}(热情|耐心|专业|满意|好评|不错|很好|到位|细致|"
    r"讲解|态度好|服务很好|服务不错|透彻|贴心|负责|尽职)|"
    r"(热情|耐心|专业|满意|很好|不错|到位|透彻|贴心).{0,32}(销售|试驾|顾问|接待|门店)",
    re.I,
)
# 售后/保养好评（无负面词时归非问题）
_KZ_AFTERSALE_PRAISE = re.compile(
    r"(售后|保养|维修|进店).{0,28}(很好|不错|满意|专业|耐心|热情|到位|及时|靠谱|高效)|"
    r"(很好|不错|满意|专业|耐心).{0,28}(售后|保养|维修)",
    re.I,
)
# 触发"超短评价"的最长字符数（去空白后）
_KZ_SHORT_MAX_CHARS = 15

# v9.2：弱正向/知晓类（须文本较短且无硬负面）
_KZ_WEAK_BENIGN = re.compile(
    r"(已知晓|知悉|了解了|知道了|知晓了|已知悉|收到|无异议|无意见|无诉求|"
    r"已回复|已沟通|已联系|已告知|已解释|已说明|"
    r"好的收到|好的，收到|可以收到|嗯好的|嗯，好的|正常|"
    r"暂无疑问|无进一步|配合|同意|确认|知道了谢谢)",
    re.I,
)
_KZ_WEAK_BENIGN_MAX_CHARS = 35

# v9.3：中性陈述/活动通知/意向表达（须较短且无硬负面）
_KZ_NEUTRAL_STATEMENT = re.compile(
    r"(通知|告知|提醒|推送|活动|参与|报名|邀请|"
    r"有意向|感兴趣|考虑|关注|想关注|"
    r"客户表示|用户表示|用户称|用户说|客户称|客户反馈|"
    r"已读|已收到信息|获悉|得知|"
    r"转发|分享|配合|支持|"
    r"官方发布|公众号|推文|直播|资讯|消息)",
    re.I,
)
_KZ_NEUTRAL_STMT_MAX_CHARS = 55
# v9.4：服务诉求类陈述可较长（仍须无硬负面）
_KZ_FEEDBACK_SERVICE_NEED = re.compile(
    r"(用户反馈需要|反馈需要|需要取送|上门保养|需要保养|"
    r"没有收到交付礼|需要售后报价|尽快.{0,10}联系)",
    re.I,
)
_KZ_FEEDBACK_SERVICE_MAX_CHARS = 120

# v9.5：损伤/报价/联系售后类问询（无强烈投诉词）
_KZ_DAMAGE_INQUIRY = re.compile(
    r"(需要售后报价|需要报价|想要联系.{0,16}售后|"
    r"需要换一套|磨得差不多|需要换胎|"
    r"被剐蹭.{0,24}报价|后视镜.{0,8}擦碰|下护板刮伤|"
    r"需要做车主认证|没有做车主认证|想要进店检查|需要进店检查|"
    r"需不需要去补胎|是不是坏的|没人联系.{0,20}安装|再次来电催促)",
    re.I,
)

# v9.5：软建议/体验分享（非投诉）
_KZ_SOFT_SUGGESTION = re.compile(
    r"(客户建议|建议商城|加长一下|希望有更多的|"
    r"怎么开启|大家都怎么解决|速度不够快啊|还可以再升级)",
    re.I,
)

# v9.7b：no_capture 扩展 — OTA 梗、愿望反馈、价询、协助协调、软体验分享
_KZ_OTA_HYPE_PRAISE = re.compile(
    r"(太好了\s*是?\s*ota|我们有救了)",
    re.I,
)
_KZ_WISH_FEEDBACK = re.compile(
    r"(来源[:：]?\s*意见反馈|意见反馈[，,].+|多点.{0,20}(配件|forme|FORME))",
    re.I,
)
_KZ_PRICE_INQUIRY = re.compile(
    r"(费用多少|多少钱|价格多少|怎么收费|收费标准|更换.{0,16}费用)",
    re.I,
)
_KZ_ASSIST_COORDINATION = re.compile(
    r"(扎钉.{0,32}协助|需要协助处理|协助处理|"
    r"(发生|发生了)碰撞.{0,36}需要协助|碰撞.{0,24}协助)",
    re.I,
)
_KZ_SOFT_EXPERIENCE_SHARE = re.compile(
    r"(驾驶乐趣有|.{0,48}还欠缺|.{0,48}还(?:不够|不足))",
    re.I,
)

# v9.8：no_capture 第二轮 — 账号变更、协调/价询、轻度零件、交付时间
_KZ_ACCOUNT_ADMIN = re.compile(
    r"(变更手机号|更换手机号|修改手机号|协助.*修改手机号|致电协助用户操作|"
    r"保留车辆权益|变更车主信息|车主信息变更)",
    re.I,
)
_KZ_WANT_ARRANGE = re.compile(
    r"(想要配.{0,16}(钥匙|卡片)|想要门店上门|想要.{0,12}上门)",
    re.I,
)
_KZ_WORKSLOT_INQUIRY = re.compile(
    r"(有空闲工位|空闲工位|有没有工位|工位吗)",
    re.I,
)
_KZ_FOLLOWUP_CONTACT = re.compile(
    r"(再次进线|联系不上|电话没人接|之前.{0,20}对接过)",
    re.I,
)
_KZ_DELIVERY_SCHEDULE = re.compile(
    r"(下定后交付时间|交付时间|什么时候交付|何时交付)",
    re.I,
)
_KZ_MILD_PART_NOTE = re.compile(
    r"(掉出来.{0,16}螺母|包裹里没有|没有背带|缺.{0,10}绳子|自带塑料的垫片)",
    re.I,
)
_KZ_ADVICE_ASK = re.compile(
    r"(想知道.{0,64}建议|更换轮胎.{0,32}建议)",
    re.I,
)
_KZ_MINOR_HOWTO = re.compile(
    r"((没有完全|无法).{0,24}如何解决|如何解决.{0,48}(提车的时候|就是这样子|一直这样))",
    re.I,
)
_KZ_COLLISION_ASSIST = re.compile(
    r"((发生|发生了)碰撞.{0,36}(需要协助|协助)|碰撞.{0,24}协助)",
    re.I,
)

_V98_CAPTURE_BLOCK = re.compile(
    r"(退货|退款|退订|沮丧|非常差|投诉|维权|索赔|支付保费|召回了一个多月|"
    r"持续存在.{0,16}问题|单方事故)",
    re.I,
)

# v10 M2：主靶 no_capture 第三轮
_KZ_REMOTE_SERVICE = re.compile(
    r"(远程为其|远程.{0,12}关|关下后尾门|关一下后尾门)",
    re.I,
)
_KZ_ACCIDENT_COORD = re.compile(
    r"(无人员受伤|报了保险和交警|对接售后协助|协助您车辆定损|"
    r"车辆事故.{0,80}协助)",
    re.I,
)
_KZ_SOFT_PRODUCT_FEEDBACK = re.compile(
    r"(向你们信息技术部|向.{0,16}(信息技术部|领导)反馈|请你向.*反馈一下)",
    re.I,
)
_KZ_LIFESTYLE_POSITIVE = re.compile(
    r"(乐趣无穷|格外美丽|速度飞起|雾气朦胧|千匹野兽)",
    re.I,
)
_KZ_FREE_SERVICE_ASK = re.compile(
    r"(可以免费|免费除|能否免费)",
    re.I,
)
_KZ_VALET_MOVE = re.compile(
    r"(代驾到另一个地方|车辆无售后无质量问题|想将车辆从.*代驾)",
    re.I,
)
_KZ_TOW_FLAT = re.compile(
    r"(扎钉漏气|轮胎扎钉漏气|需要拖车)",
    re.I,
)
_KZ_RESOLVED_SELF_SERVICE = re.compile(
    r"(解锁不了.*已成功解锁|随后表示已成功解锁|已成功解锁车辆)",
    re.I,
)
_KZ_MISSING_PARTS = re.compile(
    r"(少了.{0,12}配件|缺少.{0,12}配件|缺少背带|接缝处少)",
    re.I,
)

_V10_CAPTURE_BLOCK = re.compile(
    r"(定金.{0,24}还没有退|车模也没有|感到沮丧|已经在店里召回|"
    r"想要退货|支付保费|手机支架.*退货)",
    re.I,
)

# v10 r2：订单联动、英文 relay、亏电协助、诗意驾评分享
_KZ_ORDER_ACCOUNT_COORD = re.compile(
    r"(小定.*大定|大定.*小定|没有联动|下了小定|两个账号|"
    r"又.{0,12}手机.{0,24}注册|准备提车.*没有联动|发了.{0,12}邮件.*没有结果)",
    re.I | re.S,
)
_KZ_ENGLISH_SERVICE_RELAY = re.compile(
    r"(vehicle\s+towed|towed\s+to|has\s+not\s+been\s+able\s+to\s+reach|"
    r"repair\s+is\s+delayed|need\s+cc\s+if\s+repair)",
    re.I,
)
_KZ_DEAD_BATTERY_RELAY = re.compile(
    r"(目前没有电|没有电了.*打不开车门|车辆目前.*没有电|亏电.*打不开)",
    re.I | re.S,
)
_KZ_POETIC_DRIVE_SHARE = re.compile(
    r"(路特斯第二天|真的太对味|深踩电门|推背感.*笑起来|英国小女孩|"
    r"开一整天都|彪悍到不留情面|悬挂软得像|在云上飘|"
    r"补能焦虑.*可以扔了|这钱花得值|#LotusForMe|#电动车真香)",
    re.I | re.S,
)

# v10 r3：轻微事故协调、销售体系软反馈、维修计划问询、隔夜亏气
_KZ_MINOR_COLLISION_COORD = re.compile(
    r"(发生了摩擦|前保险杠受损|已报交警定责|"
    r"需要进店维修.*能正常行驶|摩擦.*保险杠.*交警)",
    re.I | re.S,
)
_KZ_SOFT_SALES_SYSTEM_FEEDBACK = re.compile(
    r"(产品非常棒.*购车体验|购车体验有待加强|不是针对我的销售|"
    r"整个.*销售体系|不应该输在这种地方)",
    re.I | re.S,
)
_KZ_REPAIR_PLAN_INQUIRY = re.compile(
    r"(有维修计划吗|上面的车有维修|repair\s+plan|"
    r"上面的车.*维修计划|此邮件来自组织外部.*维修计划)",
    re.I | re.S,
)
_KZ_TIRE_FLAT_OVERNIGHT = re.compile(
    r"(完全没气|一晚上.*没气|左前轮.*没气|一晚上下来完全没气)",
    re.I | re.S,
)

_V10_LONG_SHARE_MAX_CHARS = 520
_V10_RELAY_EMAIL_MAX_CHARS = 420
_V10_DEFAULT_MAX_CHARS = 320

# v10 r4：邮件 relay 加长、缺工具补发、品牌设计意见、外部垃圾邮件
_KZ_MISSING_TOOL_RELAY = re.compile(
    r"(防盗螺栓.*工具不在|拆卸工具不在|工具不在车上|"
    r"没有相关工具.*补发|需要补发.*工具|防盗螺栓的拆卸工具)",
    re.I | re.S,
)
_KZ_BRAND_DESIGN_OPINION = re.compile(
    r"(高度抄袭.*外观设计|照搬.*原创设计|"
    r"全方位照搬|品牌格调与独一无二|廉价化，套壳化|"
    r"游心.*抄袭.*EMEYA)",
    re.I | re.S,
)
_KZ_EXTERNAL_SPAM = re.compile(
    r"(SEO优化|Google可见性|网站流量|网站审计|"
    r"谷歌排名改进|当地SEO|你要我送过去吗|"
    r"注意到几个错误|详细报告以及建议|向您发送详细报告)",
    re.I | re.S,
)

# v10 r5：维修 ETA relay、加急联系、商城上架、异常协助、回电告知、事故后拖车、锁门求助、扎钉 relay、用车故事
_KZ_REPAIR_ETA_RELAY = re.compile(
    r"(检查维修.*更新CC|更新CC团队|维修ETA|ETA\d+月)",
    re.I | re.S,
)
_KZ_URGENT_CONTACT_RELAY = re.compile(
    r"(加急联系|还没有售后人员主动联系|快到店了.*需要售后|"
    r"需要售后人员加急|需要售后主动联系)",
    re.I | re.S,
)
_KZ_MALL_LAUNCH_ASK = re.compile(
    r"(怎么还没上|上旬上架吗|中旬上架吗|不是回复的.*上架吗)",
    re.I | re.S,
)
_KZ_UNUSUAL_ASSIST_RELAY = re.compile(
    r"(钻进去一只猫|拆底盘检查|大概.*分钟到店|"
    r"车辆有一只|高速出口.*(猫|动物)|收费站.*(猫|动物))",
    re.I | re.S,
)
_KZ_APP_SQUARE_CONSULT = re.compile(
    r"(来源：APP广场|APP广场|没法买车损险|只能买交强险)",
    re.I | re.S,
)
_KZ_STAFF_CALLBACK_RELAY = re.compile(
    r"(致电用户告知|用户知悉无异议|技术部门答复建议|并告知路径)",
    re.I | re.S,
)
_KZ_POST_ACCIDENT_TOW = re.compile(
    r"(事故.*已处理完毕|出了事故.*已处理|想要拖车进店)",
    re.I | re.S,
)
_KZ_LOCKOUT_ASSIST = re.compile(
    r"(没带手机.*被锁|没带家钥匙|没有车控功能.*需要售后|家门被锁上了)",
    re.I | re.S,
)
_KZ_NAIL_TOW_RELAY = re.compile(
    r"(扎钉子了|右后轮扎钉|电话中断.*拒接|回拨.*拒接)",
    re.I | re.S,
)
_KZ_PRODUCT_OPTION_FEEDBACK = re.compile(
    r"(没有FORME的选项|限定拉花没有|缺少帽绳)",
    re.I | re.S,
)
_KZ_OWNERSHIP_OTA_SHARE = re.compile(
    r"(也是老司机了|重新唤醒了内心驾驶|底盘能通过OTA|"
    r"更适配国内路况|汽车带给我的快乐|刚上市的For Me|"
    r"Lotus的审美和底盘调教)",
    re.I | re.S,
)


def _v10_no_capture_max_len(text: str) -> int:
    if _KZ_POETIC_DRIVE_SHARE.search(text) or _KZ_OWNERSHIP_OTA_SHARE.search(text):
        return _V10_LONG_SHARE_MAX_CHARS
    if (
        _KZ_REPAIR_PLAN_INQUIRY.search(text)
        or _KZ_EXTERNAL_SPAM.search(text)
        or _KZ_BRAND_DESIGN_OPINION.search(text)
    ):
        return _V10_RELAY_EMAIL_MAX_CHARS
    return _V10_DEFAULT_MAX_CHARS


# v11 M3：business/symptom guard 误拦豁免 + 主靶第三轮捕获
_KZ_V11_INCIDENT_PRAISE = re.compile(
    r"(轮胎boom|爆胎|压到大石头).{0,900}(点赞|强推|深表感谢|为.{0,24}门店售后点赞)",
    re.I | re.S,
)
_KZ_V11_MAINTENANCE_PRAISE = re.compile(
    r"(第三次保养|保养送去.{0,24}店里).{0,650}(有温度|复购|莲花跑车可以做到|"
    r"希望.{0,16}保持|老车主)",
    re.I | re.S,
)
_KZ_V11_REWARD_POLICY_RELAY = re.compile(
    r"(告知客户.*阶梯奖励|积分阶梯如何计算|按实际交付台数享受阶梯奖励)",
    re.I | re.S,
)
_KZ_V11_ACCIDENT_RELAY = re.compile(
    r"((发生|发生事故)交通事故|发生事故剐蹭|单方事故).{0,140}"
    r"(协调拖车|已报警|需要协助|拖车下高速|交警需要)",
    re.I | re.S,
)
_KZ_V11_FEATURE_HOWTO = re.compile(
    r"(找不到任何地方的选项|怎样附上图片|不能提交评价|"
    r"连接是否正常|连接到我的.*应用程序|应用程序连接|"
    r"车内网络不工作.*询问)",
    re.I | re.S,
)
_KZ_V11_FIRE_EXT_ASK = re.compile(
    r"(灭火器.{0,24}需不需要更换|消防灭火器)",
    re.I | re.S,
)
_KZ_V11_POINTS_ASK = re.compile(
    r"(买车有多少积分|客户在线买车有多少积分)",
    re.I,
)
_KZ_V11_TIRE_COORD = re.compile(
    r"(和.{0,24}售后门店对接.*更换.{0,12}轮胎|需要更换车辆轮胎)",
    re.I | re.S,
)
_KZ_V11_SPOILER_RELAY = re.compile(
    r"后扰流板损坏，需要更换",
    re.I,
)

_V11_LONG_SHARE_MAX_CHARS = 650


def _v11_business_guard_exempt(text: str) -> bool:
    """v11：协调/咨询/好评叙述不应被 Phase2 business/symptom guard 误拦。"""
    t = (text or "").strip()
    if not t:
        return False
    if _KZ_V11_REWARD_POLICY_RELAY.search(t) and not re.search(
        r"未按阶梯奖励", t, re.I
    ):
        return True
    if (
        _KZ_V11_INCIDENT_PRAISE.search(t)
        or _KZ_V11_MAINTENANCE_PRAISE.search(t)
        or _KZ_V11_ACCIDENT_RELAY.search(t)
        or _KZ_V11_FEATURE_HOWTO.search(t)
        or _KZ_V11_FIRE_EXT_ASK.search(t)
        or _KZ_V11_POINTS_ASK.search(t)
        or _KZ_V11_TIRE_COORD.search(t)
        or _KZ_V11_SPOILER_RELAY.search(t)
    ):
        return True
    return False


def _v11_m3_max_len(text: str) -> int:
    if _KZ_V11_INCIDENT_PRAISE.search(text) or _KZ_V11_MAINTENANCE_PRAISE.search(text):
        return _V11_LONG_SHARE_MAX_CHARS
    return _v10_no_capture_max_len(text)


def _v11_m3_eligible(text: str) -> bool:
    """v11 M3：business_guard / no_capture 池扩展（重分后 71 主靶）。"""
    t = (text or "").strip()
    if not t or _V10_CAPTURE_BLOCK.search(t):
        return False
    if _V98_CAPTURE_BLOCK.search(t) and not _KZ_V11_ACCIDENT_RELAY.search(t):
        return False
    if _business_issue_blocks_capture(t):
        return False
    if _hard_negative_blocks(t):
        return False
    if (
        _KZ_V11_INCIDENT_PRAISE.search(t)
        or _KZ_V11_MAINTENANCE_PRAISE.search(t)
        or _KZ_V11_REWARD_POLICY_RELAY.search(t)
        or _KZ_V11_ACCIDENT_RELAY.search(t)
        or _KZ_V11_FEATURE_HOWTO.search(t)
        or _KZ_V11_FIRE_EXT_ASK.search(t)
        or _KZ_V11_POINTS_ASK.search(t)
        or _KZ_V11_TIRE_COORD.search(t)
        or _KZ_V11_SPOILER_RELAY.search(t)
    ):
        norm = re.sub(r"\s+", "", t)
        return bool(norm and len(norm) <= _v11_m3_max_len(t))
    return False

# v9.5：试驾回访/星级评价记录
_KZ_TESTDRIVE_FOLLOWUP = re.compile(
    r"(试驾回访|客户评价[三四五]星|评价[三四五]星|评级星级)",
    re.I,
)

# v9.5：预约变更（身体原因取消等）
_KZ_APPOINTMENT_CHANGE = re.compile(
    r"(取消预约|改约|身体不舒服.{0,20}取消)",
    re.I,
)

# v9.5：充电域正向分享/软意见（非投诉）
_KZ_SOFT_CHARGING_SHARE = re.compile(
    r"(闪充机器人|接受了一波.{0,24}洗礼|"
    r"顺路体验了一下|场地卫生|有蟑螂)",
    re.I,
)

# v9.3：购车/试驾意向（无投诉语义）
_KZ_INTEREST_INTENT = re.compile(
    r"(有意向|想购买|欲购买|考虑购买|打算买|准备买|有购买计划|"
    r"想下订|想下定|想要订购|在关注|准备下单|计划购买|"
    r"需要购买|购买家充|家充桩套保|迁移家充|期待已久|攒了很久的期待|"
    r"有兴趣购买|期待着您的联系|期待您的联系)",
    re.I,
)

# v9.4：超短意向/期待句（去空白后整段匹配）
_KZ_SHORT_INTENT = re.compile(
    r"^[\s\u3000]*(期待|期待已久)[\s\u3000。！!~～]*$",
    re.I,
)

# v9.4：故障/异常叙述后紧跟好评收尾 → 不视为硬负面拦截
_KZ_RESOLVED_ISSUE_PRAISE = re.compile(
    r"(?:故障|报警|告警|胎压报警|坏了|爆胎|损坏|蹭了|糟心|异响|扎钉|刮蹭|小问题|问题).{0,280}"
    r"(?:五星|好评|点赞|感谢|专业|靠谱|值得信赖|必须给|强推|也太香了|满分|谁懂啊|"
    r"极致专业|服务态度也超贴心|深表感谢|为.*点赞|满意|处理及时|服务很好|服务不错)",
    re.I | re.S,
)

# v9.4：夸张式好评「不要太X」≠ 否定语义
_KZ_EXAGGERATED_PRAISE = re.compile(
    r"不要太(好|棒|好看|爱了|惊艳|香了)",
    re.I,
)

# v9.5：口语化好评中的「崩溃/滚烫/卡顿」等不应一票否决
_KZ_HARD_NEGATIVE_PRAISE_OVERRIDE = re.compile(
    r"(因你们而滚烫|"
    r"原地崩溃.{0,100}(也太香了|谁懂啊|太贴心了|移动上门|补胎)|"
    r"(评价五星|客户评价五星|客户评价[三四五]星).{0,120}(还是很好|整体还是很好|满意|不错)|"
    r"体验真的满分|产品好感度.[45]星|人员服务意识.[45]星|"
    r"不要太(好|棒|好看).{0,60}(谁懂啊|香了|爱了))",
    re.I | re.S,
)

# v9.5：场地/卫生等服务协调（非产品质量投诉）
_KZ_FACILITY_SERVICE_REQUEST = re.compile(
    r"(场地卫生|有蟑螂|尽快处理下场地|反馈.{0,24}闪充站有)",
    re.I,
)

# v9.5：进店检查/补胎问询（含异响等描述时不视为硬投诉）
_KZ_SERVICE_INSPECTION_REQUEST = re.compile(
    r"(需要进店检查|想要进店检查|需不需要去补胎|是不是坏的|"
    r"需要做车主认证|没有做车主认证|没人联系.{0,20}安装|再次来电催促)",
    re.I,
)

# v9.5：试驾回访高星 + 细节软吐槽
_KZ_TESTDRIVE_HIGH_RATING = re.compile(
    r"(试驾回访|评级星级).{0,240}(产品好感度.[45]星|人员服务意识.[45]星)",
    re.I | re.S,
)

# v9.5：总体满意 + 小遗憾（非投诉）
_KZ_MIXED_SATISFACTION = re.compile(
    r"非常满意.{0,120}(无法体会|不过有个情况|整体还是|但总体)|"
    r"(整体|总体).{0,40}(满意|不错|很好|认可)|"
    r"满意.{0,60}(小问题|小遗憾|略微)",
    re.I | re.S,
)

# v9.7b Phase B：高星回访/评价记录（硬负面词在细节里，整体非投诉）
_KZ_SURVEY_HIGH_RATING = re.compile(
    r"((试驾回访|销售回访|客户评价|评价星级|评级星级).{0,320}"
    r"(产品好感度|人员服务|服务意识|整体).{0,12}[45]星|"
    r"[四五]星.{0,160}(满意|很好|不错|认可|体验好|整体还是很好))",
    re.I | re.S,
)

# v9.7b Phase B：软建议式「希望/建议」（非诉求/维权）
_KZ_SOFT_HOPE_SUGGEST = re.compile(
    r"(希望.{0,24}(优化|改进|完善|增加|升级|开放|上架|调整)|"
    r"建议.{0,24}(商城|官方|增加|优化|延长|多点))",
    re.I,
)

# v9.7b Phase B：门店 relay / 客户表示类转述（协调记录）
_KZ_RELAY_BENIGN = re.compile(
    r"((客户表示|用户表示|客户称|用户称).{0,120}"
    r"(满意|认可|同意|配合|无意见|没问题|知晓|收到)|"
    r"(门店|中心|建议).{0,100}(客户要求|延长.{0,12}租车|继续维修)|"
    r"RSA.{0,120}(请提供反馈|维修何时完成|是否需要延期))",
    re.I | re.S,
)

# v9.6 Phase 2：业务诉求/纠纷/故障 — 禁止正向捕获，并配合 non_issue_guardrail 拉回
_KZ_BUSINESS_ISSUE_GUARD = re.compile(
    r"(积分.{0,48}(只|少|未|不对|一个渠道|核实|未收到|没到账)|"
    r"未按阶梯奖励|阶梯奖励|权益膨胀积分|活动积分还未收到|"
    r"催促.{0,12}礼盒|交付礼包.{0,24}没有|车模也没有下文|"
    r"有划痕.{0,16}需要处理|需要退.{0,8}需要换|不支持换货|"
    r"单方事故|协调拖车|拖至门店维修|"
    r"服务态度.{0,8}(差|非常差)|被挂断.{0,24}(生气|很生气)|"
    r"损坏.{0,36}(报价|等待.{0,10}小时|主动询问)|"
    r"又没.{0,8}到账|没有收到.{0,16}(尾款|定金|支付|45w)|"
    r"又被下线|搜索不到了|需要重新上线|处理时效太长|"
    r"不能以质保|自费处理后才能|有更换的痕迹|"
    r"空调不制冷|门打不开|里面也打不开|橡胶条掉渣|"
    r"快撞上|极速转向|突然.{0,8}急刹|"
    r"洗车服务需要加强|水渍都不擦|太难吃了|"
    r"权益.*什么时候发放|询问先启积分催促|"
    r"报价.{0,20}差异|对齐工作.{0,20}差异|"
    r"定金.{0,24}未退回|工作日还未到|"
    r"延迟的原因|等所需的零件|已经在.{0,16}两个月)",
    re.I | re.S,
)

# v9.7 Phase 2：症状/功能异常咨询、交付投诉 — 不应正向捕获，且需 non_issue_guard 拉回
_KZ_SYMPTOM_INQUIRY = re.compile(
    r"(这正常吗|是否正常|正常吗|"
    r"不准确|显示不对|"
    r"找不到.{0,12}功能|没有找到对应|无法使用|不能使用|发不出|"
    r"怎么发送图片|怎么附上图片|附上图片|"
    r"坑坑洼洼|什么原因造成|想了解.{0,12}原因|"
    r"可以进店检查|想了解是什么原因|"
    r"智驾.{0,32}(退出|撞|急刹)|"
    r"闷响|"
    r"自动泊车.{0,20}(不了|不能用)|"
    r"行程显示.*不准确|"
    r"只有一个人服务|接待只有一个人|"
    r"车辆在.{0,24}(有一段时间|两个月)|"
    r"还在等待所需的零件|"
    r"咨询自己车辆的OTA进度|"
    r"后扰流板损坏|扰流板损坏.{0,16}需要更换|"
    r"连接.{0,8}应用程序|找不到任何地方的选项)",
    re.I | re.S,
)

_KZ_DELIVERY_COMPLAINT = re.compile(
    r"((销售|售后)回访|客户打的是五星|评价五星).{0,160}"
    r"(流程太长|等待时间久|等待久|还未交付|交付可以快|没有提交按钮|"
    r"意见建议|从下定到交车|太寒酸|寒酸|拍不出|拍不出来|太简陋)|"
    r"((试驾|交付)回访).{0,200}(太寒酸|寒酸|拍不出|拍不出来|太简陋|"
    r"无人联系|没有联系)|"
    r"想给5星好评获取积分.{0,32}没有提交按钮|"
    r"5星好评获取积分.{0,24}没有提交按钮|"
    r"希望交付可以快一点",
    re.I | re.S,
)

# v9.7：社区闲聊/晒车 — 人工虽标业务，但不宜从非问题强行拉回
_KZ_COMMUNITY_CHATTER = re.compile(
    r"(不懂就问！|顺便看看各位|斯皮尔伯格入镜|申请加精|"
    r"纯个人喜好|forme黑武士提车|这个绿牌|"
    r"太好了\s*是?\s*ota|我们有救了)",
    re.I,
)


# v12 M4-B：回归池（非问题→业务）guard 扩展 — 误捕获 relay/反馈 拉回
_KZ_V12_SERVICE_PULLBACK = re.compile(
    r"((用户反馈|客户反馈|用户表示|客户表示).{0,160}"
    r"(核实|催促|催办|什么时候|进度|对接|扣除|不一致|纠纷|未收到|没收到|"
    r"还未|怎么办|什么情况|怎么回事|前后说法|投诉|不满|态度|等待|延迟|"
    r"维修|保养|交付|售后|门店|订金|定金|尾款|积分|联系不上|无人接听))",
    re.I | re.S,
)
_KZ_V12_PRODUCT_PULLBACK = re.compile(
    r"((用户反馈|客户反馈|用户表示).{0,120}(异响|黑屏|死机|失灵|无法|不能|"
    r"故障|报警|告警|坏了|漏|卡顿|抖动|损坏|不制冷|打不开|断连|"
    r"充不进|充不了|跳枪|提示错误|解错|扫码提示|信号差|迟钝|"
    r"ota|升级后))",
    re.I | re.S,
)
_KZ_V12_EXPERIENCE_PULLBACK = re.compile(
    r"((用户反馈|反馈|希望|建议|意见反馈).{0,80}(功能|软件|车机|智驾|自动泊车|"
    r"语音|导航|辅助驾驶|后备箱|尾门|歌词|网易云).{0,80}(不能|无法|异常|失灵|改进|优化|增加|"
    r"不好用|难用|升级|关闭))",
    re.I | re.S,
)
# v12 r2：206 回归未拉回 Top 诊断 — 回访/ praise / 咨询 误捕获拉回
_KZ_V12_FOLLOWUP_NEGATIVE = re.compile(
    r"((试驾|交付)回访|客户评价[三四五]星|评价[三四五]星|评级星级).{0,220}"
    r"(太|很|不|差|寒酸|简陋|慢|久|不满意|问题|投诉|拍不出|无建议|"
    r"无人联系|没有联系|没有回复|拍不出来|等待|还未|寒|太简陋)",
    re.I | re.S,
)
_KZ_V12_MIXED_PRAISE_ISSUE = re.compile(
    r"((感谢|谢谢|满意|好评|点赞|五星|四星|三星).{0,120}"
    r"(但是|不过|然而|却|问题|不满|差|慢|还未|没有|希望|要求|投诉|寒|"
    r"太|拍不出|无建议|寒酸|简陋))",
    re.I | re.S,
)
_KZ_V12_ISSUE_WITH_PRAISE = re.compile(
    r"((问题|不满|差|慢|投诉|还未|没有|寒酸|拍不出|无人联系|太).{0,120}"
    r"(感谢|谢谢|满意|好评|点赞|五星|四星|三星))",
    re.I | re.S,
)
_KZ_V12_QUERY_AS_ISSUE = re.compile(
    r"((咨询|询问|请问|想了解).{0,100}(故障|坏了|无法|不能|异响|"
    r"核实|扣除|纠纷|维修|保养|交付|进度|怎么回事|"
    r"需不需要|是不是坏|卡住|跳闸|解错|进店检|制动|改到|改约|到店|"
    r"没有人联系|无人联系)|"
    r"(用户反馈|客户反馈).{0,80}(故障|核实|扣除|纠纷|维修|保养|交付|"
    r"改到|改约|到店|没有人联系|无人联系|制动))",
    re.I | re.S,
)
_KZ_V12_STATEMENT_ISSUE = re.compile(
    r"((来电手机号|下定|用户表示|客户反馈|尾款|顾问咨询).{0,120}"
    r"(不能|无法|未接|POS|POS机|POSS机|没有人联系|"
    r"一直没有人|指标问题|改到|改约|太寒酸))",
    re.I | re.S,
)
_KZ_V12_NEUTRAL_QUERY_PULLBACK = re.compile(
    r"((咨询|询问|请问|想了解).{0,120}"
    r"(什么时候交付|交付时间|交付进度|如何处理|投诉|不满|怎么回事|"
    r"为什么没有|为何没有|无人联系|没有人联系|什么情况|前后说法不一致))"
    r"|((用户|客户).{0,40}(反馈|表示|告知).{0,120}"
    r"(咨询|询问).{0,120}(积分|订金|尾款|权益|交付).{0,80}"
    r"(扣除|不一致|不满|投诉|核实|怎么回事|为什么|无人联系))",
    re.I | re.S,
)
_L1_FAULT_HINT = re.compile(
    r"(故障|坏了|无法|异常|黑屏|死机|异响|失灵|不能|充不进|跳枪|"
    r"报警|告警|损坏|卡顿|抖动|提示错误|漏液|断连)",
    re.I,
)


def _v12_benign_neutral_query_only(text: str) -> bool:
    """纯咨询（无诉求词）不应走 v12 false_capture → guard 拉回。"""
    t = (text or "").strip()
    if not t or not _KZ_NEUTRAL_QUERY.search(t):
        return False
    if re.search(
        r"不满|投诉|怎么回事|为什么|无人联系|没有人联系|扣除|不一致|核实|"
        r"用户反馈|客户反馈|故障|坏了|无法|不能|异响|纠纷|太寒酸|拍不出|"
        r"前后说法|什么情况|要求尽快",
        t,
        re.I,
    ):
        return False
    return True


def _v12_false_capture_pullback_needed(text: str) -> bool:
    """v12 r2：回访差评/混合 praise/咨询诉求等误捕获路径 → guard 拉回。"""
    t = (text or "").strip()
    if not t:
        return False
    if (
        _KZ_V12_FOLLOWUP_NEGATIVE.search(t)
        or _KZ_V12_MIXED_PRAISE_ISSUE.search(t)
        or _KZ_V12_ISSUE_WITH_PRAISE.search(t)
        or _KZ_V12_STATEMENT_ISSUE.search(t)
        or _KZ_V12_NEUTRAL_QUERY_PULLBACK.search(t)
    ):
        return True
    if _KZ_V12_QUERY_AS_ISSUE.search(t) and not _v12_benign_neutral_query_only(t):
        return True
    return False


def _v12_regression_pullback_exempt(text: str) -> bool:
    """v12 拉回豁免：明确好评/协调 relay/M3 捕获模式（不调用 _v11_m3_eligible 避免递归）。"""
    t = (text or "").strip()
    if not t:
        return True
    if (
        _KZ_V11_INCIDENT_PRAISE.search(t)
        or _KZ_V11_MAINTENANCE_PRAISE.search(t)
        or _KZ_V11_ACCIDENT_RELAY.search(t)
        or _KZ_V11_FEATURE_HOWTO.search(t)
        or _KZ_V11_FIRE_EXT_ASK.search(t)
        or _KZ_V11_POINTS_ASK.search(t)
        or _KZ_V11_TIRE_COORD.search(t)
        or _KZ_V11_SPOILER_RELAY.search(t)
    ):
        return True
    if _KZ_V11_REWARD_POLICY_RELAY.search(t) and not re.search(
        r"未按阶梯奖励", t, re.I
    ):
        return True
    if _v12_false_capture_pullback_needed(t):
        return False
    if _KZ_PRAISE.search(t) or _KZ_RESOLVED_ISSUE_PRAISE.search(t):
        return True
    if _KZ_TESTDRIVE_HIGH_RATING.search(t) or _KZ_SURVEY_HIGH_RATING.search(t):
        return True
    if _KZ_RELAY_BENIGN.search(t):
        return True
    return False


def _v12_regression_pullback_needed(text: str) -> bool:
    """v12 M4-B：217 回归池 — 扩大 non_issue_guard 拉回（服务/产品/体验诉求）。"""
    t = (text or "").strip()
    if not t or _v12_regression_pullback_exempt(t):
        return False
    if _v12_false_capture_pullback_needed(t):
        return True
    return bool(
        _KZ_V12_SERVICE_PULLBACK.search(t)
        or _KZ_V12_PRODUCT_PULLBACK.search(t)
        or _KZ_V12_EXPERIENCE_PULLBACK.search(t)
    )


def _phase2_pullback_needed(text: str) -> bool:
    """是否应将「非问题」拉回业务类（Phase 2 v9.7）。"""
    t = (text or "").strip()
    if not t or _KZ_COMMUNITY_CHATTER.search(t):
        return False
    if _KZ_DELIVERY_COMPLAINT.search(t):
        return True
    if _KZ_SYMPTOM_INQUIRY.search(t):
        return True
    if _v12_regression_pullback_needed(t):
        return True
    return False

# LFC/充电域：疑问语气（无投诉时放行捕获，v9.3）
_LFC_QUESTION_TONE = re.compile(r"[?？]|吗|呢")

# LFC/充电/家充桩业务域（守卫与域检测共用）
_LFC_CHARGING_DOMAIN = re.compile(
    r"(lfc|极充|超充|闪充|家充|充电站|充站|充电桩|充电枪|占位费|超时占位|"
    r"充电功率|功率只有|跳枪|地锁|异位充电|公共充电|自动闪充|"
    r"家充桩|充桩|电缆米数|充完电|驶离|闪充站|超充站|极充站)",
    re.I,
)

# LFC/充电域：有问题/投诉信号 → 禁止正向捕获
_LFC_CHARGING_COMPLAINT = re.compile(
    r"(不认可|投诉|跳枪|充不进|充不了|无法充|不能充|充不上|充不进|"
    r"功率只有|功率低|功率不足|功率异常|充电慢|充得慢|太慢|"
    r"地锁无|无下一步|无反应|故障|坏了|异常|"
    r"占位费.{0,8}(贵|高|不合理|不该|投诉|多收)|"
    r"超时占位|占位费不认可|扣费|多扣|烧|(?<![滚])烫(?!金|印))",
    re.I,
)

# LFC/充电域：纯咨询/了解类 → 允许正向捕获（v9.2+）
_LFC_CHARGING_CONSULT = re.compile(
    r"(咨询|询问|请问|问一下|想了解|了解一下|了解|打听|"
    r"什么时候|何时|多久|到货|安装|价格|费用|有没有|是否有|能否|是否可以|"
    r"怎么|如何|想知道|问问|是否支持|能不能|"
    r"购买|需要购买|想买|迁移家充|家充桩套保|自费购买|"
    r"报装|电表|勘测|安装进度|开通|激活|绑定|申请|使用说明|操作流程|权益|免费)",
    re.I,
)

# 服务/试驾负面信号（无强烈投诉词但语义为差评/体验差）
_SRV_QUALITY_ISSUE = re.compile(
    r"(讲解少|体验不良|试驾体验不良|回访.*少|不良|不满意|不认可|"
    r"还没.*了解的多|多次催促|再次催促)",
    re.I,
)

# 「不/没/未」+ 正向词 → 否定语义，禁止正向捕获（v9.5：排除「特别」中的「别」）
_NEGATION_BEFORE_POSITIVE = re.compile(
    r"(不|没|未|无|(?<![特])别(?![人特])|并非|不是很|不太|不够|缺少)(?:太|很|太)?"
    r"(?:好|棒|满意|喜欢|开心|愉快|舒服|专业|耐心|认可|赞)",
    re.I,
)


def _business_issue_blocks_capture(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _KZ_COMMUNITY_CHATTER.search(t):
        return False
    if _v11_business_guard_exempt(t):
        return False
    if _KZ_BUSINESS_ISSUE_GUARD.search(t):
        return True
    if _KZ_DELIVERY_COMPLAINT.search(t):
        return True
    if _KZ_SYMPTOM_INQUIRY.search(t):
        return True
    return False


def _relay_benign_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_RELAY_BENIGN.search(t):
        return False
    if re.search(r"投诉|不满|非常差|退[款订换]|维权", t, re.I):
        return False
    return not _hard_negative_blocks(t)


def _soft_hope_suggest_eligible(text: str) -> bool:
    return _soft_hope_suggest_exempt(text or "")


def _soft_hope_suggest_exempt(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_SOFT_HOPE_SUGGEST.search(t):
        return False
    if re.search(
        r"希望(尽快|官方尽快)|必须解决|投诉|不满|退[款订换]|维权|索赔|非常差",
        t,
        re.I,
    ):
        return False
    return True


def _hard_negative_blocks(text: str) -> bool:
    """硬负面是否阻止正向捕获（v9.4+：好评收尾/场地服务/口语好评豁免）。"""
    t = (text or "").strip()
    if not t or not _KZ_HARD_NEGATIVE.search(t):
        return False
    if _KZ_RESOLVED_ISSUE_PRAISE.search(t):
        return False
    if _KZ_HARD_NEGATIVE_PRAISE_OVERRIDE.search(t):
        return False
    if _KZ_FACILITY_SERVICE_REQUEST.search(t):
        return False
    if _KZ_SERVICE_INSPECTION_REQUEST.search(t) and not re.search(
        r"非常沮丧|持续存在.{0,16}问题|召回了一个多月", t, re.I
    ):
        return False
    if _KZ_MIXED_SATISFACTION.search(t):
        return False
    if _KZ_TESTDRIVE_HIGH_RATING.search(t):
        return False
    if _KZ_SURVEY_HIGH_RATING.search(t):
        return False
    if _soft_hope_suggest_exempt(t):
        return False
    if _KZ_RELAY_BENIGN.search(t):
        return False
    if _KZ_V11_ACCIDENT_RELAY.search(t):
        return False
    return True


def _damage_inquiry_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_DAMAGE_INQUIRY.search(t):
        return False
    if _KZ_SERVICE_INSPECTION_REQUEST.search(t):
        norm = re.sub(r"\s+", "", t)
        return bool(norm and len(norm) <= 200)
    if _KZ_HARD_NEGATIVE.search(t) and not _KZ_FACILITY_SERVICE_REQUEST.search(t):
        if _hard_negative_blocks(t):
            return False
    norm = re.sub(r"\s+", "", t)
    return bool(norm and len(norm) <= _KZ_FEEDBACK_SERVICE_MAX_CHARS)


def _soft_suggestion_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_SOFT_SUGGESTION.search(t):
        return False
    return not _hard_negative_blocks(t)


def _ota_hype_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_OTA_HYPE_PRAISE.search(t):
        return False
    return not _hard_negative_blocks(t)


def _wish_feedback_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_WISH_FEEDBACK.search(t):
        return False
    if re.search(r"退货|退款|投诉|不满意|太差", t, re.I):
        return False
    return not _hard_negative_blocks(t)


def _price_inquiry_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_PRICE_INQUIRY.search(t):
        return False
    if re.search(r"乱收费|多收|投诉|不满|退订", t, re.I):
        return False
    return not _hard_negative_blocks(t)


def _assist_coordination_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_ASSIST_COORDINATION.search(t):
        return False
    if re.search(r"投诉|不满|非常差|退|换货", t, re.I):
        return False
    norm = re.sub(r"\s+", "", t)
    return bool(norm and len(norm) <= _KZ_FEEDBACK_SERVICE_MAX_CHARS)


def _soft_experience_share_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_SOFT_EXPERIENCE_SHARE.search(t):
        return False
    if _KZ_HARD_NEGATIVE.search(t) and _hard_negative_blocks(t):
        return False
    norm = re.sub(r"\s+", "", t)
    return bool(norm and len(norm) <= 90)


def _v98_no_capture_eligible(text: str) -> bool:
    """v9.8：账号/工位/协调/轻度零件/建议咨询类 no_capture 扩展。"""
    t = (text or "").strip()
    if not t or _V98_CAPTURE_BLOCK.search(t):
        return False
    if _business_issue_blocks_capture(t):
        return False
    if _hard_negative_blocks(t):
        return False
    if (
        _KZ_ACCOUNT_ADMIN.search(t)
        or _KZ_WANT_ARRANGE.search(t)
        or _KZ_WORKSLOT_INQUIRY.search(t)
        or _KZ_FOLLOWUP_CONTACT.search(t)
        or _KZ_DELIVERY_SCHEDULE.search(t)
        or _KZ_MILD_PART_NOTE.search(t)
        or _KZ_ADVICE_ASK.search(t)
        or _KZ_MINOR_HOWTO.search(t)
        or _KZ_COLLISION_ASSIST.search(t)
    ):
        norm = re.sub(r"\s+", "", t)
        return bool(norm and len(norm) <= 220)
    return False


def _v10_no_capture_eligible(text: str) -> bool:
    """v10 M2：远程协助、事故对接、生活方式分享、代驾/拖车等 no_capture 扩展。"""
    t = (text or "").strip()
    if not t or _V98_CAPTURE_BLOCK.search(t) or _V10_CAPTURE_BLOCK.search(t):
        return False
    if _business_issue_blocks_capture(t):
        return False
    if _hard_negative_blocks(t):
        return False
    if (
        _KZ_REMOTE_SERVICE.search(t)
        or _KZ_ACCIDENT_COORD.search(t)
        or _KZ_SOFT_PRODUCT_FEEDBACK.search(t)
        or _KZ_LIFESTYLE_POSITIVE.search(t)
        or _KZ_FREE_SERVICE_ASK.search(t)
        or _KZ_VALET_MOVE.search(t)
        or _KZ_TOW_FLAT.search(t)
        or _KZ_RESOLVED_SELF_SERVICE.search(t)
        or _KZ_MISSING_PARTS.search(t)
        or _KZ_ORDER_ACCOUNT_COORD.search(t)
        or _KZ_ENGLISH_SERVICE_RELAY.search(t)
        or _KZ_DEAD_BATTERY_RELAY.search(t)
        or _KZ_POETIC_DRIVE_SHARE.search(t)
        or _KZ_MINOR_COLLISION_COORD.search(t)
        or _KZ_SOFT_SALES_SYSTEM_FEEDBACK.search(t)
        or _KZ_REPAIR_PLAN_INQUIRY.search(t)
        or _KZ_TIRE_FLAT_OVERNIGHT.search(t)
        or _KZ_MISSING_TOOL_RELAY.search(t)
        or _KZ_BRAND_DESIGN_OPINION.search(t)
        or _KZ_EXTERNAL_SPAM.search(t)
        or _KZ_REPAIR_ETA_RELAY.search(t)
        or _KZ_URGENT_CONTACT_RELAY.search(t)
        or _KZ_MALL_LAUNCH_ASK.search(t)
        or _KZ_UNUSUAL_ASSIST_RELAY.search(t)
        or _KZ_APP_SQUARE_CONSULT.search(t)
        or _KZ_STAFF_CALLBACK_RELAY.search(t)
        or _KZ_POST_ACCIDENT_TOW.search(t)
        or _KZ_LOCKOUT_ASSIST.search(t)
        or _KZ_NAIL_TOW_RELAY.search(t)
        or _KZ_PRODUCT_OPTION_FEEDBACK.search(t)
        or _KZ_OWNERSHIP_OTA_SHARE.search(t)
    ):
        norm = re.sub(r"\s+", "", t)
        return bool(norm and len(norm) <= _v10_no_capture_max_len(t))
    return False


def _soft_charging_share_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_SOFT_CHARGING_SHARE.search(t):
        return False
    if _LFC_CHARGING_COMPLAINT.search(t):
        return False
    return not _hard_negative_blocks(t)


def _feedback_service_need_eligible(text: str) -> bool:
    t = (text or "").strip()
    if not t or not _KZ_FEEDBACK_SERVICE_NEED.search(t):
        return False
    norm = re.sub(r"\s+", "", t)
    return bool(norm and len(norm) <= _KZ_FEEDBACK_SERVICE_MAX_CHARS)


def _negation_blocks(text: str) -> bool:
    """否定语义是否阻止正向捕获（v9.4+：不要太X / 身体不舒服取消预约 除外）。"""
    t = (text or "").strip()
    if not t or not _NEGATION_BEFORE_POSITIVE.search(t):
        return False
    if _KZ_EXAGGERATED_PRAISE.search(t):
        return False
    if re.search(r"身体不舒服", t, re.I) and _KZ_APPOINTMENT_CHANGE.search(t):
        return False
    if _KZ_TESTDRIVE_HIGH_RATING.search(t):
        return False
    return True


def _lfc_blocks_positive_capture(text: str) -> bool:
    """LFC/充电域是否应阻止正向捕获（v9.2：咨询类放行，投诉类拦截）。"""
    t = (text or "").strip()
    if not t or not _LFC_CHARGING_DOMAIN.search(t):
        return False
    if _LFC_CHARGING_COMPLAINT.search(t):
        return True
    if _soft_charging_share_eligible(t):
        return False
    if _KZ_SOFT_SUGGESTION.search(t) and not _hard_negative_blocks(t):
        return False
    if _LFC_CHARGING_CONSULT.search(t) or _KZ_NEUTRAL_QUERY.search(t):
        return False
    if _LFC_QUESTION_TONE.search(t):
        return False
    return True


def _pick_lfc_l2(text: str, l2_map: Dict[str, List[str]]) -> str:
    """LFC/充电域二级优先 LFC问题，其次车端充电问题。"""
    opts = l2_map.get("产品质量类") or []
    t = text or ""
    if re.search(r"极充|超充|lfc|闪充|占位|充站|家充|充桩|地锁|功率", t, re.I):
        for k in ("LFC问题", "车端充电问题"):
            if k in opts:
                return k
    return _pick_l2_after_remap("产品质量类", t, l2_map)


def apply_charging_domain_guard(
    text: str, l1: str, l2: str, l2_map: Dict[str, List[str]]
) -> Tuple[str, str, bool]:
    """LFC/充电/家充桩域：业务咨询/反馈/投诉不得归「非问题/咨询与表扬」。

    诊断显示 cap 列全为 '-'，213 条 L2 反向误判均为 14B 直接输出；
    典型误例：家充桩到货咨询、占位费不认可、充电功率询问、地锁无下一步按钮。
    """
    t = (text or "").strip()
    if not t or not _LFC_CHARGING_DOMAIN.search(t):
        return l1, l2, False
    l1s = (l1 or "").strip()
    l2s = (l2 or "").strip()
    if not is_non_issue_l1(l1s):
        return l1, l2, False
    nl1 = "产品质量类"
    nl2 = _pick_lfc_l2(t, l2_map)
    return nl1, nl2, True


def apply_service_quality_guard(
    text: str, l1: str, l2: str, l2_map: Dict[str, List[str]]
) -> Tuple[str, str, bool]:
    """试驾/销售服务体验差评（讲解少、体验不良等）不得归「非问题/咨询与表扬」。"""
    t = (text or "").strip()
    if not t or not _SRV_QUALITY_ISSUE.search(t):
        return l1, l2, False
    l1s = (l1 or "").strip()
    l2s = (l2 or "").strip()
    if not is_non_issue_l1(l1s):
        return l1, l2, False
    nl1 = "服务类"
    nl2 = _pick_l2_after_remap(nl1, t, l2_map)
    opts = l2_map.get(nl1) or []
    if _KZ_FOLLOWUP_AFTERSALE.search(t) and "售后服务问题" in opts:
        nl2 = "售后服务问题"
    elif _KZ_FOLLOWUP_DELIVERY.search(t) and "交付问题" in opts:
        nl2 = "交付问题"
    elif _KZ_FOLLOWUP_SALES.search(t) and "销售服务问题" in opts:
        nl2 = "销售服务问题"
    elif re.search(r"试驾|讲解|回访", t) and "销售服务问题" in opts:
        nl2 = "销售服务问题"
    elif "试驾体验不良" in (t or "") and "试驾体验不良" in opts:
        nl2 = "试驾体验不良"
    return nl1, nl2, True

_PROD_TILT = re.compile(
    r"(车机|中控屏|座舱|充电|[闪超]充|充电桩|充电枪|地锁|电池|电机|异响|黑屏|死机|"
    r"故障灯|雷达|激光雷达|钥匙|软件|ota|底盘|车门|轮胎|气囊|空调|尾门|漏水|漏油|"
    r"辅助驾驶|智驾|自动驾驶|流媒体|信号|唤醒|小蓝灯|音乐|网易云)",
    re.I,
)

_SRV_TILT = re.compile(
    r"(售后|门店|交付|销售|顾问|客服|接待|4s|提车|订金|定金|合同|积分|保养|维修工单|服务态度|推诿|"
    r"回访|服务站|交车|生产太慢|权益不相同)",
    re.I,
)

_EXP_TILT = re.compile(
    r"(希望|建议|能否|最好|意见).{0,48}(便宜|六座|复杂|路况|浮动歌词|歌词|"
    r"手动关闭|升级改进|出.{0,8}车|商城|配件|陈列|"
    r"OTA|功能|智驾|自动|语音|导航|后备箱|尾门)",
    re.I,
)

_KZ_FOLLOWUP_SALES = re.compile(r"(销售回访|试驾回访)", re.I)
_KZ_FOLLOWUP_DELIVERY = re.compile(r"交付回访", re.I)
_KZ_FOLLOWUP_AFTERSALE = re.compile(r"售后回访", re.I)


def _pick_service_l2_from_text(text: str, l2_map: Dict[str, List[str]]) -> str:
    """服务类二级：回访类型优先于默认顺序（修复 售后服务→销售服务 误映射）。"""
    opts = l2_map.get("服务类") or []
    if not opts:
        return ""
    t = text or ""
    if _KZ_FOLLOWUP_SALES.search(t) and "销售服务问题" in opts:
        return "销售服务问题"
    if _KZ_FOLLOWUP_DELIVERY.search(t) and "交付问题" in opts:
        return "交付问题"
    if _KZ_FOLLOWUP_AFTERSALE.search(t) and "售后服务问题" in opts:
        return "售后服务问题"
    if re.search(r"试驾|销售", t, re.I) and "销售服务问题" in opts:
        if not re.search(r"售后回访|售后服务|售后,", t, re.I):
            return "销售服务问题"
    if re.search(r"交付", t, re.I) and "交付问题" in opts:
        return "交付问题"
    for k in ("售后服务问题", "销售服务问题", "交付问题", "流程慢-通用"):
        if k in opts:
            return k
    return opts[0]


def _pick_non_issue_guard_l1(text: str) -> str:
    """non_issue_guard 拉回时的一级分类（r3-A：v12 误捕获池 L1 tilt 修正）。"""
    t = text or ""
    fault = bool(
        re.search(
            r"(故障|坏了|无法|异常|黑屏|死机|异响|撞|碰撞|失灵|"
            r"充不进|跳枪|没.{0,6}音乐|信号差|迟钝|下载安装)",
            t,
            re.I,
        )
    )
    if _KZ_V12_PRODUCT_PULLBACK.search(t) or (
        bool(_PROD_TILT.search(t)) and fault
    ):
        return "产品质量类"
    if _KZ_V12_EXPERIENCE_PULLBACK.search(t) or (
        bool(_EXP_TILT.search(t)) and not fault and not _KZ_V12_SERVICE_PULLBACK.search(t)
    ):
        return "体验需求类"
    srv = bool(_SRV_TILT.search(t))
    prod = bool(_PROD_TILT.search(t))
    if srv and not prod:
        return "服务类"
    if prod:
        return "产品质量类"
    if re.search(r"(建议|希望|能否|最好|增加|优化|多出|加上)", t, re.I) and not fault:
        return "体验需求类"
    return "产品质量类"


def apply_l1_category_rebalance(
    text: str, l1: str, l2: str, l2_map: Dict[str, List[str]]
) -> Tuple[str, str, bool]:
    """M4-B2：业务三类之间 L1 tilt 纠偏（跨类 92 主战场）。"""
    l1c = canonicalize_l1_label(l1)
    if l1c not in ("服务类", "产品质量类", "体验需求类"):
        return l1, l2, False
    t = text or ""
    srv = bool(_SRV_TILT.search(t))
    prod = bool(_PROD_TILT.search(t))
    exp = bool(_EXP_TILT.search(t))
    fault = bool(_L1_FAULT_HINT.search(t))
    nl1 = l1c
    if l1c == "服务类" and prod and fault and not srv:
        nl1 = "产品质量类"
    elif l1c == "产品质量类" and srv and not prod:
        nl1 = "服务类"
    elif l1c == "产品质量类" and srv and prod and not fault:
        if re.search(r"(交付|销售|回访|下定|提车|积分|门店|顾问)", t, re.I):
            nl1 = "服务类"
    elif l1c == "体验需求类" and prod and fault:
        nl1 = "产品质量类"
    elif l1c == "体验需求类" and srv and not prod and not fault and not exp:
        nl1 = "服务类"
    elif l1c == "产品质量类" and exp and not fault and not srv:
        nl1 = "体验需求类"
    if nl1 == l1c:
        return l1, l2, False
    nl2 = _pick_l2_after_remap(nl1, t, l2_map)
    return nl1, nl2, True


def _pick_l2_after_remap(l1: str, text: str, l2_map: Dict[str, List[str]]) -> str:
    opts = l2_map.get(l1) or []
    if not opts:
        return ""
    t = text or ""
    if l1 == "产品质量类":
        if re.search(r"充电|[闪超]充|家充|桩|地锁|跳枪", t):
            for k in ("车端充电问题", "LFC问题"):
                if k in opts:
                    return k
        if re.search(r"车机|座舱|屏幕|hud|抬头|中控屏", t, re.I):
            for k in ("座舱问题", "电子电器问题"):
                if k in opts:
                    return k
        if "异响" in t:
            for k in ("异响问题",):
                if k in opts:
                    return k
        for k in ("故障-通用", "卡顿-通用"):
            if k in opts:
                return k
        return opts[0]
    if l1 == "服务类":
        return _pick_service_l2_from_text(t, l2_map)
    if l1 == "体验需求类":
        for k in ("OTA建议", "充电建议", "车机智能化", "智驾建议"):
            if k in opts:
                return k
        return opts[0]
    for k in ("咨询与表扬", "其他非问题"):
        if k in opts:
            return k
    return opts[0]


def apply_non_issue_guardrail(
    text: str, l1: str, l2: str, l2_map: Dict[str, List[str]]
) -> Tuple[str, str, bool]:
    """
    若模型将带诉求/异常的内容判为「非问题」，强制改判；并保证二级落在对应一级的候选字典内。
    返回 (l1, l2, 是否修正)。
    """
    l1c = canonicalize_l1_label(l1)
    opts = l2_map.get(l1c) or []
    l2s = (l2 or "").strip()
    if opts and l2s not in opts:
        l2s = _nearest(l2s, opts)

    if l1c != "非问题":
        return l1c, l2s, False

    t = text or ""
    if not _FORBID_NON_ISSUE.search(t) and not _KZ_BUSINESS_ISSUE_GUARD.search(t) and not _phase2_pullback_needed(t):
        return l1c, "", False

    nl1 = _pick_non_issue_guard_l1(t)
    nl2 = _pick_l2_after_remap(nl1, t, l2_map)
    return nl1, nl2, True


def apply_positive_consult_capture(
    text: str, l1: str, l2: str, l2_map: Dict[str, List[str]]
) -> Tuple[str, str, bool]:
    """正向捕获：销售/试驾/售后好评、纯咨询、品牌动态 → 「非问题」（l2 留空）。

    v9.1：扩展销售/试驾/售后好评模式；业务边界词不再单独阻止捕获。
    v9.2：LFC 咨询放行、硬负面拆分、弱正向词表。
    v9.3：LFC 咨询捕获 bugfix、中性陈述/意向词表、硬负面再收窄。
    v9.4：烫金误拦、咨询词表、家充购买、故障+好评豁免。
    v9.5：别/特别误匹配、损伤询价、软建议、srv_quality 收窄。
    v9.6：Phase 2 业务诉求守卫，收紧误捕获。
    v9.7：症状/功能咨询、交付投诉拉回；社区闲聊豁免。
    v9.7b：no_capture 扩展（OTA 梗、愿望反馈、价询、协助协调、软体验分享）；
            Phase B 硬负面豁免（高星回访、故障+好评、relay、软希望建议）；
            v9.8 no_capture 第二轮（账号/工位/协调/轻度零件/建议咨询）。
            v10 M2 主靶 no_capture 第三轮（远程/事故协调/生活方式/代驾拖车等）；
            v10 r2（订单联动/英文 relay/亏电协助/诗意驾评）；
            v10 r3（长文驾评 520 字/轻微事故/销售软反馈/维修问询/隔夜亏气）；
            v10 r4（邮件 relay 420 字/缺工具补发/品牌设计意见/外部垃圾邮件）；
            v10 r5（维修 ETA/加急联系/回电告知/事故拖车/锁门求助/用车故事等）。
            v11 M3（guard 误拦豁免 + 好评/事故 relay/功能咨询/积分问询）。
            v12 M4-B（回归池 guard 扩展：服务/产品/体验诉求误捕获拉回）。
            v12 r2（回访差评/混合 praise/咨询诉求误捕获 guard 扩展）。
            v12 r3（guard L1 tilt 修正 + 回访 L2 映射）。
            v12 r4（neutral_query 拉回 + 跨类 L1 tilt 纠偏）。
    """
    del l2_map  # 非问题不写二级
    t = (text or "").strip()
    if not t or not _eligible_for_positive_capture(t):
        return l1, l2, False
    l1s = (l1 or "").strip()
    if is_non_issue_l1(l1s) and not (l2 or "").strip():
        return l1s, "", False
    return "非问题", "", True


def _eligible_for_positive_capture(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _business_issue_blocks_capture(t):
        return False
    if _v12_regression_pullback_needed(t):
        return False
    if _hard_negative_blocks(t):
        return False
    if _negation_blocks(t):
        return False
    if _lfc_blocks_positive_capture(t):
        return False
    if _SRV_QUALITY_ISSUE.search(t):
        return False
    norm = re.sub(r"\s+", "", t)
    if norm and len(norm) <= _KZ_SHORT_MAX_CHARS and _KZ_SHORT_INTENT.match(norm):
        return True
    if norm and len(norm) <= _KZ_SHORT_MAX_CHARS and _KZ_SHORT_BENIGN.match(norm):
        return True
    if _KZ_WEAK_BENIGN.search(t) and norm and len(norm) <= _KZ_WEAK_BENIGN_MAX_CHARS:
        return True
    if _KZ_INTEREST_INTENT.search(t):
        return True
    if _feedback_service_need_eligible(t):
        return True
    if _damage_inquiry_eligible(t):
        return True
    if _soft_suggestion_eligible(t):
        return True
    if _ota_hype_eligible(t):
        return True
    if _wish_feedback_eligible(t):
        return True
    if _price_inquiry_eligible(t):
        return True
    if _assist_coordination_eligible(t):
        return True
    if _soft_experience_share_eligible(t):
        return True
    if _relay_benign_eligible(t):
        return True
    if _soft_hope_suggest_eligible(t):
        return True
    if _v98_no_capture_eligible(t):
        return True
    if _v10_no_capture_eligible(t):
        return True
    if _v11_m3_eligible(t):
        return True
    if _KZ_TESTDRIVE_FOLLOWUP.search(t) and not _hard_negative_blocks(t):
        if not _KZ_V12_FOLLOWUP_NEGATIVE.search(t):
            return True
    if _KZ_APPOINTMENT_CHANGE.search(t) and not _hard_negative_blocks(t):
        return True
    if _soft_charging_share_eligible(t):
        return True
    if _KZ_NEUTRAL_STATEMENT.search(t) and norm and len(norm) <= _KZ_NEUTRAL_STMT_MAX_CHARS:
        return True
    if _KZ_PRAISE.search(t):
        return True
    if _KZ_NEUTRAL_QUERY.search(t):
        return True
    if _KZ_ANNOUNCEMENT.search(t):
        return True
    if _KZ_SALES_TESTDRIVE_PRAISE.search(t):
        return True
    if _KZ_AFTERSALE_PRAISE.search(t):
        return True
    if _KZ_BIZ_BOUNDARY.search(t) and _KZ_PRAISE.search(t):
        return True
    if _is_lfc_consult_text(t):
        return True
    return False


def explain_positive_capture_block(text: str) -> str:
    """诊断正向捕获 eligibility；未命中任何捕获模式时返回 no_capture_pattern。"""
    t = (text or "").strip()
    if not t:
        return "empty_text"
    if _business_issue_blocks_capture(t):
        return "blocked:business_issue_guard"
    if _v12_regression_pullback_needed(t):
        return "blocked:v12_regression_pullback"
    if _hard_negative_blocks(t):
        return "blocked:hard_negative"
    if _negation_blocks(t):
        return "blocked:negation"
    if _lfc_blocks_positive_capture(t):
        if _LFC_CHARGING_COMPLAINT.search(t):
            return "blocked:lfc_charging_complaint"
        return "blocked:lfc_charging_domain"
    if _SRV_QUALITY_ISSUE.search(t):
        return "blocked:srv_quality_issue"
    norm = re.sub(r"\s+", "", t)
    if norm and len(norm) <= _KZ_SHORT_MAX_CHARS and _KZ_SHORT_INTENT.match(norm):
        return "eligible:short_intent"
    if norm and len(norm) <= _KZ_SHORT_MAX_CHARS and _KZ_SHORT_BENIGN.match(norm):
        return "eligible:short_benign"
    if _KZ_WEAK_BENIGN.search(t) and norm and len(norm) <= _KZ_WEAK_BENIGN_MAX_CHARS:
        return "eligible:weak_benign"
    if _KZ_INTEREST_INTENT.search(t):
        return "eligible:interest_intent"
    if _feedback_service_need_eligible(t):
        return "eligible:feedback_service_need"
    if _damage_inquiry_eligible(t):
        return "eligible:damage_inquiry"
    if _soft_suggestion_eligible(t):
        return "eligible:soft_suggestion"
    if _ota_hype_eligible(t):
        return "eligible:ota_hype"
    if _wish_feedback_eligible(t):
        return "eligible:wish_feedback"
    if _price_inquiry_eligible(t):
        return "eligible:price_inquiry"
    if _assist_coordination_eligible(t):
        return "eligible:assist_coordination"
    if _soft_experience_share_eligible(t):
        return "eligible:soft_experience_share"
    if _relay_benign_eligible(t):
        return "eligible:relay_benign"
    if _soft_hope_suggest_eligible(t):
        return "eligible:soft_hope_suggest"
    if _v98_no_capture_eligible(t):
        return "eligible:v98_no_capture"
    if _v10_no_capture_eligible(t):
        return "eligible:v10_no_capture"
    if _v11_m3_eligible(t):
        return "eligible:v11_m3"
    if _KZ_TESTDRIVE_FOLLOWUP.search(t) and not _hard_negative_blocks(t):
        return "eligible:testdrive_followup"
    if _KZ_APPOINTMENT_CHANGE.search(t) and not _hard_negative_blocks(t):
        return "eligible:appointment_change"
    if _soft_charging_share_eligible(t):
        return "eligible:soft_charging_share"
    if _KZ_NEUTRAL_STATEMENT.search(t) and norm and len(norm) <= _KZ_NEUTRAL_STMT_MAX_CHARS:
        return "eligible:neutral_statement"
    if _KZ_PRAISE.search(t):
        return "eligible:praise"
    if _KZ_NEUTRAL_QUERY.search(t):
        return "eligible:neutral_query"
    if _KZ_ANNOUNCEMENT.search(t):
        return "eligible:announcement"
    if _KZ_SALES_TESTDRIVE_PRAISE.search(t):
        return "eligible:sales_testdrive_praise"
    if _KZ_AFTERSALE_PRAISE.search(t):
        return "eligible:aftersale_praise"
    if _KZ_BIZ_BOUNDARY.search(t) and _KZ_PRAISE.search(t):
        return "eligible:biz_boundary_praise"
    if _is_lfc_consult_text(t):
        return "eligible:lfc_consult"
    return "no_capture_pattern"


def consultation_praise_misclass(text: str, l2: str) -> bool:
    """
    L2「咨询与表扬」专项兜底：**仅当**原文含真负面词（故障/不满/吐槽/诉求/退款/告警等）时，
    判定为误分类。

    【核心修复（2026-05）】之前使用 _KZ_FORBID（含业务边界词），导致诸如
    「销售服务很好」「试驾体验棒」也被驳回。现改用 _KZ_TRUE_NEGATIVE，
    业务边界词单独命中不再触发误分类。

    返回 True 时上层会触发 needs_review=True，强制人工复核而非强行归类。
    """
    if (l2 or "").strip() != "咨询与表扬":
        return False
    t = text or ""
    if not t:
        return False
    return bool(_KZ_TRUE_NEGATIVE.search(t))


def consultation_l2_relocate(
    text: str, l2_map: Dict[str, List[str]]
) -> Tuple[str, str]:
    """
    将被误判为「咨询与表扬」的内容按规则改判到合适的产品/服务二级，便于复核员定向修正。
    返回 (新一级, 新二级)；无法精准重定位则回退到产品质量类的「故障-通用」。
    """
    t = text or ""
    tl = t.lower()
    # ---- 服务类边界（按需求三类：售后/销售/交付）----
    if re.search(
        r"售后|维修|保养|质保|备件|代步|道路救援|售后慢|售后差|进店检修|服务门店|回访|理赔",
        t,
    ):
        return "服务类", "售后服务问题"
    if re.search(
        r"销售|销售顾问|顾问|门店|客服|接待|试驾|意向金|购车|下定|订单|展厅|价格",
        t,
    ):
        return "服务类", "销售服务问题"
    if re.search(r"交付|提车|订金|定金|合同|发票|延期交付", t):
        return "服务类", "交付问题"
    # ---- 产品质量边界（车机/座舱/LFC/钥匙/充电/AD4 等）----
    if re.search(r"车机|中控屏|座舱|hud|抬头|屏幕|仪表", t, re.I):
        return "产品质量类", "座舱问题"
    if re.search(r"lfc|闪充站|超充|地锁|公共充电", t, re.I):
        return "产品质量类", "LFC问题"
    if re.search(r"充电|快充|慢充|家充|充电桩|充电枪", t, re.I):
        return "产品质量类", "车端充电问题"
    if re.search(r"钥匙|解锁|nfc|uwb", t, re.I):
        return "产品质量类", "钥匙问题"
    if re.search(r"智驾|领航|noa|aeb|泊车|ad4|辅助驾驶", t, re.I):
        return "产品质量类", "AD4问题"
    if "异响" in t:
        return "产品质量类", "异响问题"
    # ---- 体验需求边界 ----
    if re.search(r"建议|希望|期望|增加|优化|改进|完善", t):
        return "体验需求类", "OTA建议"
    # ---- 最终兜底 ----
    return "产品质量类", "故障-通用"


def _nearest(candidate: str, options: List[str]) -> str:
    c = (candidate or "").strip()
    if not options:
        return c
    if c in options:
        return c
    cset = set(c)
    best = options[0]
    best_score = -1
    for opt in options:
        os_ = set(opt)
        score = len(cset & os_) / max(1, len(cset | os_))
        if score > best_score:
            best = opt
            best_score = score
    return best


def _is_lfc_consult_text(text: str) -> bool:
    """LFC/充电域内的咨询/了解类表述（v9.2 正向捕获候选）。"""
    t = (text or "").strip()
    if not _LFC_CHARGING_DOMAIN.search(t):
        return False
    return bool(_LFC_CHARGING_CONSULT.search(t) or _KZ_NEUTRAL_QUERY.search(t))


def apply_classification_post_rules(
    text: str, l1: str, l2: str, l2_map: Dict[str, List[str]]
) -> Tuple[str, str, Dict[str, bool]]:
    """14B 白名单输出之后的规则链（可离线重放，用于子集评估 / 不写库）。

    输入 l1/l2 通常为库内 v3 标签（对 praise 误判样本，≈ 14B 原始输出）。
    返回 (l1, l2, flags)。
    """
    flags: Dict[str, bool] = {}
    l1, l2, g = apply_non_issue_guardrail(text, l1, l2, l2_map)
    if g:
        flags["non_issue_guard"] = True

    if consultation_praise_misclass(text, l2):
        flags["consult_misclass"] = True
        nl1, nl2 = consultation_l2_relocate(text, l2_map)
        allowed = l2_map.get(nl1) or []
        if nl2 not in allowed and allowed:
            nl2 = allowed[0]
        l1, l2 = nl1, nl2

    nl1g, nl2g, cg = apply_charging_domain_guard(text, l1, l2, l2_map)
    if cg:
        flags["charging_guard"] = True
        l1, l2 = nl1g, nl2g
        flags.pop("consult_misclass", None)

    nl1sq, nl2sq, sg = apply_service_quality_guard(text, l1, l2, l2_map)
    if sg:
        flags["srv_quality_guard"] = True
        l1, l2 = nl1sq, nl2sq
        flags.pop("consult_misclass", None)

    nl1rb, nl2rb, rb = apply_l1_category_rebalance(text, l1, l2, l2_map)
    if rb:
        flags["l1_category_rebalance"] = True
        l1, l2 = nl1rb, nl2rb
        flags.pop("consult_misclass", None)

    charging_guard_applied = bool(flags.get("charging_guard"))
    nl1c, nl2c, captured = apply_positive_consult_capture(text, l1, l2, l2_map)
    if captured and charging_guard_applied and _is_lfc_consult_text(text):
        # 回归保护：非问题→charging_guard→产品质量 后，不再 LFC 咨询捕获回非问题
        captured = False
    if captured:
        flags["pos_captured"] = True
        l1, l2 = nl1c, nl2c
        flags.pop("consult_misclass", None)
        flags.pop("charging_guard", None)
        flags.pop("srv_quality_guard", None)

    l1, l2 = strip_non_issue_l2(l1, l2)
    return l1, l2, flags


def classify_text(text: str, *, db_path: str, country: str = "", model: str = QWEN_MODEL, host: str = QWEN_HOST) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"l1": "", "l2": "", "l3": "", "keywords": [], "risk_level": "低", "confidence": 0.0, "match_type": "qwen_empty"}
    l2_map = load_l2_whitelist()
    examples = historical_examples(db_path, text)
    extra = json.dumps({"l2": l2_map, "examples": examples, "country": country}, ensure_ascii=False)
    key = _cache_key("classify_v12_m4b_r4", text, extra)
    cached = _cache_get(key)
    if cached:
        cached["cache_hit"] = True
        return cached
    prompt = build_classify_prompt(text, l2_map, examples, country=country)
    obj: Optional[Dict[str, Any]] = None
    last_err = ""
    for attempt in range(_CLASSIFY_JSON_ATTEMPTS):
        try:
            raw = ollama_generate(
                prompt,
                model=model,
                host=host,
                num_predict=QWEN_CLASSIFY_NUM_PREDICT,
                num_ctx=QWEN_CLASSIFY_NUM_CTX,
                temperature=CLASSIFY_FIXED_TEMPERATURE,
                top_p=CLASSIFY_FIXED_TOP_P,
                timeout_sec=QWEN_CLASSIFY_TIMEOUT,
            )
            cand = extract_json_object(raw)
            if _classify_json_has_nonempty_l12(cand):
                obj = cand
                break
            last_err = "json_fields_incomplete"
        except Exception as e:
            last_err = str(e)
            obj = None
        if attempt + 1 < _CLASSIFY_JSON_ATTEMPTS:
            time.sleep(0.28 * (attempt + 1))

    if obj is None:
        return {
            "l1": "",
            "l2": "",
            "l3": "",
            "keywords": [],
            "risk_level": "低",
            "confidence": 0.0,
            "match_type": "qwen_parse_failed",
            "model": model,
            "cache_hit": False,
            "needs_review": True,
            "error": (last_err or "qwen_parse_failed")[:300],
        }

    raw_l1 = str(obj.get("l1") or obj.get("一级标签") or "").strip()
    raw_l2 = str(obj.get("l2") or obj.get("二级标签") or "").strip()
    wl1, wl2 = _strict_whitelist_l1_l2(raw_l1, raw_l2, l2_map)
    if wl1 is None or wl2 is None:
        return {
            "l1": "",
            "l2": "",
            "l3": "",
            "keywords": [],
            "risk_level": "低",
            "confidence": 0.0,
            "match_type": "qwen_whitelist_reject",
            "model": model,
            "cache_hit": False,
            "needs_review": True,
            "raw_model_l1": raw_l1[:120],
            "raw_model_l2": raw_l2[:120],
        }

    keywords = obj.get("问题关键词") or obj.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [x.strip() for x in keywords.split(",") if x.strip()]
    if not isinstance(keywords, list):
        keywords = []
    risk = str(obj.get("风险等级") or obj.get("risk_level") or "低").strip()
    if risk not in ("高", "中", "低"):
        risk = "低"
    conf = float(obj.get("confidence") or 0.75)

    l1, l2, rule_flags = apply_classification_post_rules(text, wl1, wl2, l2_map)
    guard = bool(rule_flags.get("non_issue_guard"))
    if guard:
        conf = max(conf, 0.82)
    if rule_flags.get("consult_misclass"):
        conf = min(conf, 0.45)
    if rule_flags.get("charging_guard"):
        conf = max(conf, 0.84)
    if rule_flags.get("srv_quality_guard"):
        conf = max(conf, 0.83)
    if rule_flags.get("pos_captured"):
        conf = max(conf, 0.86)

    consult_misclass = bool(rule_flags.get("consult_misclass"))
    pos_captured = bool(rule_flags.get("pos_captured"))
    charging_guard = bool(rule_flags.get("charging_guard"))
    srv_quality_guard = bool(rule_flags.get("srv_quality_guard"))

    out = {
        "l1": l1,
        "l2": l2,
        "l3": "",
        "keywords": [str(x).strip() for x in keywords if str(x).strip()][:12],
        "risk_level": risk,
        "confidence": conf,
        "match_type": "qwen14b_structured",
        "model": model,
        "cache_hit": False,
        "needs_review": consult_misclass,
    }
    if guard:
        out["non_issue_guard"] = True
    if consult_misclass:
        out["l2_consult_guard"] = True
        out["raw_model_l2"] = wl2[:120]
    if pos_captured:
        out["positive_capture"] = True
        out["raw_model_l1"] = wl1[:120]
        out["raw_model_l2"] = wl2[:120]
    if charging_guard:
        out["charging_domain_guard"] = True
        out["raw_model_l1"] = wl1[:120]
        out["raw_model_l2"] = wl2[:120]
    if srv_quality_guard:
        out["srv_quality_guard"] = True
        out["raw_model_l1"] = wl1[:120]
        out["raw_model_l2"] = wl2[:120]
    l1, l2 = strip_non_issue_l2(l1, l2)
    out["l1"] = l1
    out["l2"] = l2
    _cache_set(key, out)
    return out


def build_classify_prompt(text: str, l2_map: Dict[str, List[str]], examples: List[Dict[str, str]], *, country: str = "") -> str:
    return (
        "你是专业车载 VOC 舆情标签分类专家，严格按照给定标签体系做一级、二级标签分类，"
        "严禁自创标签、严禁随意归类。\n\n"
        "【一级标签仅四类（必须从中四选一）】\n"
        "产品质量类、服务类、体验需求类、非问题\n\n"
        "【核心判定逻辑：先判领域，再判语义】\n"
        "判定顺序（必须按此顺序思考）：\n"
        "  第0步（最高优先）：是否涉及【LFC/充电/家充桩/占位费/功率/地锁/超充/极充】？\n"
        "    · 若是（含咨询/反馈/询问/不认可/到货/安装/功率低/无下一步按钮等）→ "
        "禁止归「非问题/咨询与表扬」，应归「产品质量类」+「LFC问题」或「车端充电问题」。\n"
        "  第1步：是否含【真负面】——故障/异常/投诉/不满/不认可/讲解少/体验不良/退款/告警 等？\n"
        "    · 若有 → 按领域归 产品质量类 / 服务类 / 体验需求类\n"
        "    · 若无 → 进入第2步\n"
        "  第2步：是否是【销售/试驾好评 / 非充电业务咨询 / 品牌动态 / 购车意向】？\n"
        "    · 是 → 「非问题」（二级留空 \"\"，勿填咨询与表扬/其他非问题）\n"
        "    · 否 → 按业务领域归类\n\n"
        "【一级分类详解】\n"
        "1. 产品质量类：硬件/LFC/充电/座舱/车机故障；以及 LFC/家充桩/占位费/充电功率/地锁 业务咨询与反馈。\n"
        "2. 服务类：销售/售后/交付负面（态度差、讲解少、体验不良、推诿、拖延）。\n"
        "   ★ 销售/售后好评 → 「非问题」；讲解少/体验不良 → 「服务类」。\n"
        "3. 体验需求类：功能改进建议（希望/建议/增加/优化），且无故障。\n"
        "4. 非问题：纯表扬/销售试驾好评/品牌动态/购车意向（不含 LFC/充电/家充桩业务）。**仅输出 l1，l2 必须为 \"\"**。\n\n"
        "【二级标签强约束】\n"
        "A. 一级为「非问题」时：l2 固定输出空字符串 \"\"，禁止输出咨询与表扬/其他非问题。\n"
        "B. LFC/充电业务（优先于非问题）：极充/超充/家充桩/占位费/充电功率/地锁 → 「产品质量类」+「LFC问题」或「车端充电问题」。\n"
        "   即使只有「咨询/询问/反馈」无负面词，也按此规则。\n"
        "C. 服务负面：讲解少/体验不良/态度差 → 「服务类」对应二级。\n"
        "D. 二级互斥：一条舆情只输出 1 个二级标签（非问题除外）。\n"
        "E. 二级须从字典原文字符串择一（非问题除外）。\n"
        "F. 有 LFC/充电域 → 禁止归非问题；无 LFC 域的销售好评 → 「非问题」且 l2=\"\"。\n\n"
        "【关键示例（必须严格遵循同模式判定）】\n"
        "示例1：\n"
        '  原文：「销售马女士热情有耐心,讲解很透彻到位」\n'
        '  判定：销售好评，无 LFC 域 → {"l1":"非问题","l2":""}\n'
        "示例2：\n"
        '  原文：「用户咨询是否有价格表」\n'
        '  判定：非充电业务咨询 → {"l1":"非问题","l2":""}\n'
        "示例3：\n"
        '  原文：「试驾人员费经理很专业,服务很好」\n'
        '  判定：试驾好评 → {"l1":"非问题","l2":""}\n'
        "示例3b：\n"
        '  原文：「销售王顾问热情有耐心,讲解透彻到位」\n'
        '  判定：销售好评 → {"l1":"非问题","l2":""}\n'
        "示例3c：\n"
        '  原文：「保养体验很好,售后很专业」\n'
        '  判定：售后好评 → {"l1":"非问题","l2":""}\n'
        "示例4（LFC·必判产品质量）：\n"
        '  原文：「用户咨询家充桩什么时候到货」\n'
        '  判定：家充桩业务 → {"l1":"产品质量类","l2":"LFC问题"}\n'
        "示例5（LFC·必判产品质量）：\n"
        '  原文：「用户表示不认可超时占位费」\n'
        '  判定：占位费+LFC域 → {"l1":"产品质量类","l2":"LFC问题"}\n'
        "示例6（LFC·必判产品质量）：\n"
        '  原文：「充电功率只有38kw,询问是什么原因」\n'
        '  判定：充电功率咨询 → {"l1":"产品质量类","l2":"LFC问题"}\n'
        "示例7（服务差评）：\n"
        '  原文：「试驾回访,讲解少,还没客户自己了解的多」\n'
        '  判定：讲解少 → {"l1":"服务类","l2":"销售服务问题"}\n'
        "示例8（真负面）：\n"
        '  原文：「售后多次反映没解决，态度差，要求退款」\n'
        '  判定：{"l1":"服务类","l2":"售后服务问题"}\n'
        "示例9（座舱）：\n"
        '  原文：「车机黑屏卡顿，多次重启无效」\n'
        '  判定：{"l1":"产品质量类","l2":"座舱问题"}\n\n'
        f"【二级候选字典(JSON)】：{json.dumps(l2_map, ensure_ascii=False)}\n\n"
        f"【历史人工复核样例（仅参考，以原文语义为准）】：{json.dumps(examples, ensure_ascii=False)}\n\n"
        f"【区域/国家】：{country or '未知'}\n"
        f"【待分类原文】：\n{text[:12000]}\n\n"
        "【输出要求】严格 JSON，无解释、无 markdown：\n"
        '{"l1":"一级标签","l2":"二级标签"}\n'
        "约束：l1 必须是「产品质量类/服务类/体验需求类/非问题」四者之一；"
        "l1 为「非问题」时 l2 必须为 \"\"；"
        "其余 l1 的 l2 必须是字典中该 l1 下的某一子项原文字符串；不得有其他键。"
    )


def summarize_opinions(rows: List[Dict[str, Any]], *, period: str, region: str, model: str = QWEN_MODEL, host: str = QWEN_HOST) -> Dict[str, Any]:
    rows = rows[:300]
    compact = []
    l2_counter: Counter = Counter()
    for r in rows:
        l1 = str(r.get("review_l1") or r.get("model_class") or "").strip()
        l2 = str(r.get("review_l2") or r.get("model_keyword") or "").split(",", 1)[0].strip()
        if l2:
            l2_counter[l2] += 1
        compact.append(
            {
                "time": r.get("create_time") or "",
                "country": r.get("country") or "",
                "l1": l1,
                "l2": l2,
                "text": str(r.get("original_text") or "")[:240],
            }
        )
    key = _cache_key("summary", json.dumps(compact, ensure_ascii=False), f"{period}|{region}")
    cached = _cache_get(key)
    if cached:
        cached["cache_hit"] = True
        return cached
    prompt = (
        "你是 VOC 舆情周报/月报分析专家。基于下列已脱敏舆情摘要，输出严格 JSON，供前端直接展示。\n"
        "要求：中文输出；聚焦高频问题、风险、归因、行动建议；不要编造数据；只输出 JSON。\n"
        f"统计周期：{period}\n区域：{region}\n"
        f"Top二级计数：{json.dumps(l2_counter.most_common(12), ensure_ascii=False)}\n"
        f"样本(JSON)：{json.dumps(compact, ensure_ascii=False)}\n"
        "JSON Schema：{\"summary\":\"总体解读\", \"top_issues\":[{\"label\":\"二级标签\",\"count\":1,\"analysis\":\"原因\"}],"
        "\"risks\":[\"风险1\"],\"actions\":[\"建议1\"],\"ppt_text\":\"可直接放入PPT的一段话\"}"
    )
    try:
        obj = extract_json_object(ollama_generate(prompt, model=model, host=host, num_predict=1200))
    except Exception:
        top = [{"label": k, "count": v, "analysis": "高频问题，建议结合原文复核原因。"} for k, v in l2_counter.most_common(8)]
        obj = {
            "summary": f"{period} {region} 共纳入 {len(rows)} 条舆情，高频问题集中在 " + "、".join([x["label"] for x in top[:5]]),
            "top_issues": top,
            "risks": ["模型摘要不可用，已回退为规则统计摘要。"],
            "actions": ["优先复核 Top 二级问题并跟进服务/质量责任归因。"],
            "ppt_text": f"{period} {region} VOC 舆情共 {len(rows)} 条，Top 问题为 " + "、".join([x["label"] for x in top[:5]]) + "。",
        }
    obj["total"] = len(rows)
    obj["period"] = period
    obj["region"] = region
    obj["cache_hit"] = False
    _cache_set(key, obj)
    return obj
