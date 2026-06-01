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
    r"被坑|被骗|被忽悠|被推诿|售后糟|品控差|工艺差|做工差)",
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

# 硬负面：故障/投诉/维权等，命中即禁止正向捕获（不含单独「建议/优化/期望」等软词）
_KZ_HARD_NEGATIVE = re.compile(
    r"(故障|异常|失灵|失效|坏了|无法|不能用|不能充|充不进|充不了|黑屏|死机|卡顿|异响|抖动|漏液|漏水|漏油|断连|烧|烫|"
    r"投诉|不满意|抱怨|吐槽|失望|糟糕|太差|很差|服务差|态度差|售后差|售后慢|售后不靠谱|"
    r"难用|诉求|维权|索赔|退款|退费|退订|退换|赔偿|"
    r"推诿|敷衍|忽悠|欺骗|被坑|被骗|被忽悠|被推诿|"
    r"拖延|拖了|不理|没人理|没处理|未处理|未解决|没解决|没回复|无回复|无人处理|"
    r"问题反馈|反馈问题|反映问题|希望解决|希望处理|希望尽快|希望改善|要求处理|"
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
    r"满意|挺好|很好|不赖|很棒|超棒|靠谱|专业|贴心|高效|及时|推荐|五星|完美|nice|good|"
    r"热情|耐心|细致|周到|用心|敬业|负责|尽职|细心|温馨|友好|和善|亲切|"
    r"透彻|到位|清晰|讲解清楚|讲得好|讲明白|"
    r"惊喜|超预期|超出预期|超值|划算|放心|安心|"
    r"棒极了|棒棒的|赞|很赞|大赞|顶|过硬|出色|"
    r"良好体验|体验很好|体验不错|体验棒|服务很好|服务不错)",
    re.I,
)
# 中性问询/咨询/购车意向话术：含咨询/询问 + 购车/试驾意向
_KZ_NEUTRAL_QUERY = re.compile(
    r"(咨询|询问|请教|请问|问一下|问问|想问一下|想问|"
    r"想了解|了解一下|了解下|了解|打听|看看|了解情况|咨询一下|"
    r"想试驾|想试|预约试驾|预约|"
    r"想要一辆|想买|想入|"
    r"何时上市|什么时候上市|何时发布|什么时候发布|何时交付|什么时候交付|"
    r"价格表|配置表|参数表|有没有|是否有|是否能|"
    r"报装|电表|勘测|进度|怎么查|如何查询|查询|核实|确认一下|问一下情况|"
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

# v9.3：购车/试驾意向（无投诉语义）
_KZ_INTEREST_INTENT = re.compile(
    r"(有意向|想购买|欲购买|考虑购买|打算买|准备买|有购买计划|"
    r"想下订|想下定|想要订购|在关注|准备下单|计划购买)",
    re.I,
)

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
    r"超时占位|占位费不认可|扣费|多扣|烧|烫)",
    re.I,
)

# LFC/充电域：纯咨询/了解类 → 允许正向捕获（v9.2+）
_LFC_CHARGING_CONSULT = re.compile(
    r"(咨询|询问|请问|问一下|想了解|了解一下|了解|打听|"
    r"什么时候|何时|多久|到货|安装|价格|费用|有没有|是否有|能否|是否可以|"
    r"怎么|如何|想知道|问问|是否支持|能不能|"
    r"报装|电表|勘测|安装进度|开通|激活|绑定|申请|使用说明|操作流程|权益|免费)",
    re.I,
)

# 服务/试驾负面信号（无强烈投诉词但语义为差评/体验差）
_SRV_QUALITY_ISSUE = re.compile(
    r"(讲解少|体验不良|试驾体验不良|回访.*少|不良|不满意|不认可|"
    r"还没.*了解的多|知悉无异议|多次催促|再次联系)",
    re.I,
)

# 「不/没/未」+ 正向词 → 否定语义，禁止正向捕获
_NEGATION_BEFORE_POSITIVE = re.compile(
    r"(不|没|未|无|别|并非|不是很|不太|不够|缺少)(?:太|很|太)?(?:好|棒|满意|喜欢|开心|愉快|舒服|专业|耐心|认可|赞)",
    re.I,
)


def _lfc_blocks_positive_capture(text: str) -> bool:
    """LFC/充电域是否应阻止正向捕获（v9.2：咨询类放行，投诉类拦截）。"""
    t = (text or "").strip()
    if not t or not _LFC_CHARGING_DOMAIN.search(t):
        return False
    if _LFC_CHARGING_COMPLAINT.search(t):
        return True
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
    if re.search(r"试驾|讲解|回访", t) and "销售服务问题" in opts:
        nl2 = "销售服务问题"
    elif "试驾体验不良" in (t or "") and "试驾体验不良" in opts:
        nl2 = "试驾体验不良"
    return nl1, nl2, True

_PROD_TILT = re.compile(
    r"(车机|中控屏|座舱|充电|[闪超]充|充电桩|充电枪|地锁|电池|电机|异响|黑屏|死机|"
    r"故障灯|雷达|钥匙|软件|ota|底盘|车门|轮胎|气囊|空调|尾门|漏水|漏油)",
    re.I,
)

_SRV_TILT = re.compile(
    r"(售后|门店|交付|销售|顾问|客服|接待|4s|提车|订金|定金|合同|积分|保养|维修工单|服务态度|推诿)",
    re.I,
)


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
        for k in ("售后服务问题", "销售服务问题", "交付问题", "流程慢-通用"):
            if k in opts:
                return k
        return opts[0]
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
    if not _FORBID_NON_ISSUE.search(t):
        return l1c, "", False

    srv = bool(_SRV_TILT.search(t))
    prod = bool(_PROD_TILT.search(t))
    if srv and not prod:
        nl1 = "服务类"
    elif prod:
        nl1 = "产品质量类"
    elif re.search(r"(建议|希望|能否|最好|增加|优化|多出|加上)", t) and not re.search(
        r"(故障|坏了|无法|异常|黑屏|死机|异响)", t
    ):
        nl1 = "体验需求类"
    else:
        nl1 = "产品质量类"

    nl2 = _pick_l2_after_remap(nl1, t, l2_map)
    return nl1, nl2, True


def apply_positive_consult_capture(
    text: str, l1: str, l2: str, l2_map: Dict[str, List[str]]
) -> Tuple[str, str, bool]:
    """正向捕获：销售/试驾/售后好评、纯咨询、品牌动态 → 「非问题」（l2 留空）。

    v9.1：扩展销售/试驾/售后好评模式；业务边界词不再单独阻止捕获。
    v9.2：LFC 咨询放行、硬负面拆分、弱正向词表。
    v9.3：LFC 咨询捕获 bugfix、中性陈述/意向词表、硬负面再收窄。
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
    if _KZ_HARD_NEGATIVE.search(t):
        return False
    if _NEGATION_BEFORE_POSITIVE.search(t):
        return False
    if _lfc_blocks_positive_capture(t):
        return False
    if _SRV_QUALITY_ISSUE.search(t):
        return False
    norm = re.sub(r"\s+", "", t)
    if norm and len(norm) <= _KZ_SHORT_MAX_CHARS and _KZ_SHORT_BENIGN.match(norm):
        return True
    if _KZ_WEAK_BENIGN.search(t) and norm and len(norm) <= _KZ_WEAK_BENIGN_MAX_CHARS:
        return True
    if _KZ_INTEREST_INTENT.search(t):
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
    if _KZ_HARD_NEGATIVE.search(t):
        return "blocked:hard_negative"
    if _NEGATION_BEFORE_POSITIVE.search(t):
        return "blocked:negation"
    if _lfc_blocks_positive_capture(t):
        if _LFC_CHARGING_COMPLAINT.search(t):
            return "blocked:lfc_charging_complaint"
        return "blocked:lfc_charging_domain"
    if _SRV_QUALITY_ISSUE.search(t):
        return "blocked:srv_quality_issue"
    norm = re.sub(r"\s+", "", t)
    if norm and len(norm) <= _KZ_SHORT_MAX_CHARS and _KZ_SHORT_BENIGN.match(norm):
        return "eligible:short_benign"
    if _KZ_WEAK_BENIGN.search(t) and norm and len(norm) <= _KZ_WEAK_BENIGN_MAX_CHARS:
        return "eligible:weak_benign"
    if _KZ_INTEREST_INTENT.search(t):
        return "eligible:interest_intent"
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
    key = _cache_key("classify_v9_3_neutral_stmt", text, extra)
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
