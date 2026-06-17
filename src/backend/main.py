"""
舆情复核系统后端 - V1.2优化版
"""

import sqlite3
import pandas as pd
import os
import sys
import csv
import json
import re
import hashlib
import time
import threading
import asyncio
import uuid
from datetime import datetime
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from functools import wraps
from fastapi import FastAPI, File, UploadFile, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
import uvicorn
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("voc")

# ===================== 全局配置 =====================
DB_FILE = "opinion_review.db"
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(BACKEND_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config.paths import (  # noqa: E402
    ANNUAL_DIR,
    LABEL_PROJECT_DIR as _LABEL_PROJECT_PATH,
    PROJECT_ROOT,
    TEMP_DATA_DIR,
    ensure_runtime_dirs,
)

ensure_runtime_dirs()
_LABEL_PROJECT_STR = str(_LABEL_PROJECT_PATH)
if _LABEL_PROJECT_STR not in sys.path:
    sys.path.insert(0, _LABEL_PROJECT_STR)
from taxonomy_normalize import (  # noqa: E402
    CANONICAL_L1_LABELS,
    L2_EVAL_L1,
    NON_ISSUE_L1,
    canonicalize_l1_label,
    l1_values_for_filter,
    strip_non_issue_l2,
)
from report_aggregator import (  # noqa: E402
    get_monthly_overview_data,
    get_monthly_subtag_trend_data,
    get_single_issue_trend_data,
    get_top_subtag_monthly_data,
)

ROOT_DIR = PROJECT_ROOT  # 兼容旧称：项目根目录
# 自动化测试可通过 VOC_DB_PATH / VOC_KEYWORD_FILE 指向独立文件，避免污染开发数据
DB_PATH = os.path.abspath(os.environ.get("VOC_DB_PATH", os.path.join(BACKEND_DIR, DB_FILE)))
KEYWORD_FILE = os.path.abspath(
    os.environ.get("VOC_KEYWORD_FILE", os.path.join(BACKEND_DIR, "keywords_v1.2.json"))
)
CLASSIFICATION_LOG_FILE = os.path.join(BACKEND_DIR, "classification_accuracy.log")
REFLOW_FAILURES_PATH = Path(BACKEND_DIR) / "reflow_failures.jsonl"
PENDING_REFLOW_PATH = Path(BACKEND_DIR) / "pending_reflow.jsonl"
PENDING_REFLOW_INTERVAL = int(os.environ.get("VOC_PENDING_REFLOW_INTERVAL", "60"))
_pending_reflow_lock = threading.Lock()
_pending_reflow_daemon_started = False
_pending_reflow_daemon_lock = threading.Lock()
RATE_LIMIT_CONFIG = {
    "upload_csv": {"rate": 40, "interval": 60},
    "save_review": {"rate": 100, "interval": 60},
    "get_visual_data": {"rate": 200, "interval": 60},
    "get_review_list": {"rate": 120, "interval": 60},
    "dashboard_stats": {"rate": 45, "interval": 60},
    "taxonomy": {"rate": 150, "interval": 60},
}

# 大批量时回流/分类走后台线程，避免 HTTP 长时间阻塞导致前端超时
REFLOW_ASYNC_THRESHOLD = int(os.environ.get("VOC_REFLOW_ASYNC_THRESHOLD", "25"))
CLASSIFY_ASYNC_MIN_ROWS = int(os.environ.get("VOC_CLASSIFY_ASYNC_MIN_ROWS", "10"))
classify_jobs: Dict[str, dict] = {}
classify_jobs_lock = threading.Lock()
_CLASSIFY_JOBS_MAX = 200
# 串行化年度 CSV 全量重写，避免多次确认并发写同一文件互相覆盖。
_YEARLY_CSV_LOCK = threading.Lock()
REVIEW_LIST_MAX_PAGE = int(os.environ.get("VOC_REVIEW_LIST_MAX_PAGE", "20"))
REVIEW_LIST_DEFAULT_PAGE = int(os.environ.get("VOC_REVIEW_LIST_DEFAULT_PAGE", "20"))
REVIEW_LIST_QUERY_TIMEOUT = float(os.environ.get("VOC_REVIEW_LIST_QUERY_TIMEOUT", "45"))
DASHBOARD_CACHE_TTL = float(os.environ.get("VOC_DASHBOARD_CACHE_TTL", "90"))
DASHBOARD_COMPUTE_TIMEOUT = float(os.environ.get("VOC_DASHBOARD_COMPUTE_TIMEOUT", "35"))
SQLITE_CONN_TIMEOUT = float(os.environ.get("VOC_SQLITE_CONN_TIMEOUT", "20"))
REVIEW_LIST_TEXT_PREVIEW = int(os.environ.get("VOC_REVIEW_LIST_TEXT_PREVIEW", "420"))
REVIEW_LIST_SELECT_BODY = """
    id, opinion_id, source, create_time, upload_batch, country,
    review_status, review_l1, review_l2, review_l3, review_note,
    reviewer, reviewed_at, reflow_synced,
    model_class, model_keyword, match_score, extracted_keywords,
    v3_label_meta, v3_l1, v3_l2, v3_l3, v3_confidence, v3_match_type,
    SUBSTR(COALESCE(original_text, ''), 1, ?) AS original_text,
    CASE WHEN LENGTH(COALESCE(original_text, '')) > ? THEN 1 ELSE 0 END AS original_text_truncated
""".strip()

FEEDBACK_TYPES = ["质量问题", "营销服务", "体验需求", "咨询", "非问题"]


def _map_v3_l1_to_legacy_feedback(l1: str) -> Optional[str]:
    """V3 一级与旧版 review_note 分类域的弱映射，用于同步强化旧关键词桶。"""
    if not l1:
        return None
    m = {
        "产品质量类": "质量问题",
        "服务类": "营销服务",
        "体验需求类": "体验需求",
        "非问题": "非问题",
    }
    if l1 in m:
        return m[l1]
    for t in FEEDBACK_TYPES:
        if t in str(l1) or str(l1) in t:
            return t
    return None


def _stats_day_key(*vals: Any) -> str:
    """将创建/复核时间规范为 YYYY-MM-DD，兼容 CSV 常见格式（如 2025/3/17），供趋势与近7日统计。"""
    for v in vals:
        s = str(v or "").strip()
        if not s:
            continue
        s = s.replace("/", "-").replace(".", "-")
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s[:19])
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y}-{mo:02d}-{d:02d}"
        digits = re.sub(r"\D", "", s)[:8]
        if len(digits) == 8:
            y, mo, d = int(digits[0:4]), int(digits[4:6]), int(digits[6:8])
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y}-{mo:02d}-{d:02d}"
    return ""


# 初始化FastAPI应用
app = FastAPI(
    title="舆情复核系统 V1.2",
    description="优化版舆情复核系统",
    version="1.2.0"
)

# GZip 先注册；CORS 最后注册（最外层），保证预检与业务响应均带 CORS 头
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\]|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length", "Content-Type"],
    max_age=86400,
)

# ===================== 限流机制 =====================
class RateLimiter:
    def __init__(self):
        self.limits: Dict[str, List[float]] = {}
        self.lock = threading.Lock()
    
    def is_allowed(self, key: str, rate: int, interval: int) -> Tuple[bool, int]:
        now = time.time()
        window_start = now - interval
        with self.lock:
            if key not in self.limits:
                self.limits[key] = []
            self.limits[key] = [t for t in self.limits[key] if t > window_start]
            if len(self.limits[key]) >= rate:
                return False, 0
            self.limits[key].append(now)
            return True, rate - len(self.limits[key])

rate_limiter = RateLimiter()

def rate_limit(key: str, rate: int, interval: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if os.environ.get("VOC_DISABLE_RATE_LIMIT") == "1":
                return await func(*args, **kwargs)
            allowed, remaining = rate_limiter.is_allowed(key, rate, interval)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"code": 429, "msg": "请求过于频繁，请稍后再试"},
                    media_type="application/json",
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# ===================== 关键词提取算法 =====================
STOP_WORDS = set(["的", "了", "是", "我", "你", "他", "在", "和", "有", "就", "不", "这", "那", "及", "与", "等", "呢", "吗", "吧"])
GENERAL_WORDS = set(["汽车", "新能源", "车企", "车辆", "车子", "车", "品牌", "公司", "厂家", "用户", "客户", "车主"])

_DEFAULT_CONSULT_BLACKLIST = (
    "不满", "吐槽", "抱怨", "投诉", "故障", "异常", "失灵", "失效", "坏了", "无法",
    "黑屏", "死机", "卡顿", "异响", "抖动", "漏", "断连", "报警", "告警", "报错", "bug",
    "诉求", "维权", "索赔", "退款", "退费", "退订", "退换",
    "推诿", "敷衍", "忽悠", "欺骗", "拖延", "不理", "没人理", "没处理", "未解决", "未处理",
    "建议", "希望", "期望", "增加", "优化", "改进", "完善",
    # 售后边界
    "售后", "维修", "保养", "质保", "备件", "代步", "进店检修", "服务门店", "回访", "理赔",
    # 销售边界
    "销售", "销售顾问", "顾问", "门店", "客服", "接待", "试驾", "意向金",
    "购车", "下定", "订单", "展厅", "价格",
    # 交付边界
    "交付", "提车", "订金", "定金", "合同", "发票",
    # 产品质量类边界
    "车机", "中控屏", "座舱", "hud", "屏幕", "仪表",
    "lfc", "闪充", "超充", "地锁", "公共充电", "充电桩", "充电枪", "家充",
    "智驾", "领航", "noa", "aeb", "泊车", "ad4", "辅助驾驶",
    "钥匙", "解锁", "nfc", "uwb",
    "ota", "升级失败",
    "乱收费", "加价", "不退", "不修", "不换",
)

# 正向捕获参考词（仅作为持久化展示，运行期以 qwen_ollama / offline_validate 内规则为准）
_DEFAULT_POSITIVE_PRAISE_TOKENS = (
    "表扬", "点赞", "不错", "给力", "感谢", "谢谢", "多谢", "好评", "认可", "夸赞", "赞一个",
    "满意", "挺好", "很好", "很棒", "超棒", "靠谱", "专业", "贴心", "高效", "及时", "推荐", "五星", "完美",
)
_DEFAULT_NEUTRAL_QUERY_TOKENS = (
    "咨询", "询问", "请教", "想了解", "想问一下", "想问", "请问", "问一下", "问问",
    "了解一下", "了解下", "了解", "打听",
)
_DEFAULT_SHORT_BENIGN_TOKENS = (
    "很好", "好", "挺好", "很棒", "超棒", "不错", "可以", "还行", "还可以", "没问题",
    "没事", "赞", "满意", "完美",
)


class KeywordExtractor:
    def __init__(self):
        self.keyword_dict: Dict[str, List[str]] = {}
        self.v3_l1_keywords: Dict[str, List[str]] = {}
        self.l1_keyword_weights: Dict[str, float] = {}
        self.consult_blacklist: List[str] = []
        self.load_keywords()

    def is_consultation_blocked(self, text: str) -> bool:
        """是否命中咨询与表扬黑名单（任一命中即视为不可归类为咨询/表扬）。"""
        if not text:
            return False
        bl = self.consult_blacklist or list(_DEFAULT_CONSULT_BLACKLIST)
        tl = str(text).lower()
        for kw in bl:
            k = (kw or "").strip().lower()
            if k and k in tl:
                return True
        return False

    def should_force_consult(self, text: str) -> bool:
        """正向捕获判定：纯咨询/纯表扬/超短善意 + 无禁止词 → 应强制归「非问题/咨询与表扬」。

        与 is_consultation_blocked 双向互补：禁止词命中 → False；否则命中任一
        正向词集 → True；其它情况 → False。供统计、复核辅助提示等使用，分类
        落库以 qwen_ollama.apply_positive_consult_capture / 规则层为准。
        """
        if not text:
            return False
        if self.is_consultation_blocked(text):
            return False
        t = str(text).strip()
        tl = t.lower()
        # A) 超短善意短句
        norm = re.sub(r"\s+", "", t)
        if 0 < len(norm) <= 15:
            for token in _DEFAULT_SHORT_BENIGN_TOKENS:
                if token in norm:
                    return True
        # B) 正向赞美/感谢
        for token in _DEFAULT_POSITIVE_PRAISE_TOKENS:
            if token.lower() in tl:
                return True
        # C) 中性问询
        for token in _DEFAULT_NEUTRAL_QUERY_TOKENS:
            if token.lower() in tl:
                return True
        return False
    
    def _tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        text = str(text).strip().lower()
        text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', ' ', text)
        words = []
        current_word = ""
        for char in text:
            if char.isalnum():
                current_word += char
            else:
                if len(current_word) >= 2:
                    words.append(current_word)
                current_word = ""
        if len(current_word) >= 2:
            words.append(current_word)
        return words
    
    def _filter_words(self, words: List[str]) -> List[str]:
        return [w for w in words if w not in STOP_WORDS and w not in GENERAL_WORDS and len(w) >= 2 and not w.isdigit()]
    
    def extract_tfidf(self, texts: List[str], top_n: int = 20) -> Dict[str, float]:
        if not texts:
            return {}
        all_text = " ".join(texts)
        words = self._tokenize(all_text)
        words = self._filter_words(words)
        if not words:
            return {}
        word_counts = Counter(words)
        total_words = len(words)
        tf_scores = {word: count / total_words for word, count in word_counts.items()}
        idf_scores = {word: 1.0 for word in set(words)}
        tfidf_scores = {word: tf_scores.get(word, 0) * idf_scores.get(word, 0) for word in set(words)}
        return dict(sorted(tfidf_scores.items(), key=lambda x: x[1], reverse=True)[:top_n])
    
    def extract_textrank(self, texts: List[str], top_n: int = 20) -> Dict[str, float]:
        if not texts:
            return {}
        all_text = " ".join(texts)
        words = list(set(self._filter_words(self._tokenize(all_text))))
        if len(words) < 2:
            return {}
        window_size = 3
        word_graph = {word: set() for word in words}
        for i, word in enumerate(words):
            for j in range(max(0, i - window_size), min(len(words), i + window_size + 1)):
                if i != j:
                    word_graph[word].add(words[j])
        scores = {word: 1.0 for word in words}
        damping = 0.85
        for _ in range(100):
            new_scores = {}
            for word in word_graph:
                rank_sum = sum(scores.get(neighbor, 0) for neighbor in word_graph[word])
                new_scores[word] = (1 - damping) + damping * rank_sum / len(word_graph[word]) if word_graph[word] else (1 - damping)
            scores = new_scores
        return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n])
    
    def extract_keywords(self, text: str, method: str = "tfidf") -> List[str]:
        if not text:
            return []
        if method == "tfidf":
            scores = self.extract_tfidf([text])
        else:
            scores = self.extract_textrank([text])
        return list(scores.keys())
    
    def update_keywords_from_correction(self, original_text: str, correct_class: str):
        if not original_text or correct_class not in FEEDBACK_TYPES:
            return
        keywords = self.extract_keywords(original_text)
        if correct_class not in self.keyword_dict:
            self.keyword_dict[correct_class] = []
        self.keyword_dict[correct_class].extend(keywords)
        self.keyword_dict[correct_class] = list(set(self.keyword_dict[correct_class]))
        self.save_keywords()

    def apply_review_reflow_boost(self, human_l1: str, kws: List[str]) -> None:
        """复核回流：按一级标签强化关键词权重（不微调模型）。"""
        if not human_l1 or not kws:
            return
        if human_l1 not in self.v3_l1_keywords:
            self.v3_l1_keywords[human_l1] = []
        for kw in kws:
            kw_l = str(kw).strip().lower()
            if len(kw_l) < 2:
                continue
            if kw_l not in self.v3_l1_keywords[human_l1]:
                self.v3_l1_keywords[human_l1].append(kw_l)
            wkey = f"{human_l1}|{kw_l}"
            self.l1_keyword_weights[wkey] = round(
                min(6.0, float(self.l1_keyword_weights.get(wkey, 1.0)) * 1.08), 4
            )
        self.v3_l1_keywords[human_l1] = self.v3_l1_keywords[human_l1][-280:]
        legacy = _map_v3_l1_to_legacy_feedback(human_l1)
        if legacy and legacy in FEEDBACK_TYPES:
            if legacy not in self.keyword_dict:
                self.keyword_dict[legacy] = []
            for kw in kws[:15]:
                kl = str(kw).strip().lower()
                if len(kl) >= 2 and kl not in self.keyword_dict[legacy]:
                    self.keyword_dict[legacy].append(kl)
            self.keyword_dict[legacy] = list(dict.fromkeys(self.keyword_dict[legacy]))[-200:]
        self.save_keywords()
    
    def save_keywords(self):
        try:
            clean_dict = {}
            for class_name, keywords in self.keyword_dict.items():
                clean_dict[class_name] = list(set([k.strip().lower() for k in keywords if k.strip()]))
            with open(KEYWORD_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "keywords": clean_dict,
                        "v3_l1_keywords": self.v3_l1_keywords,
                        "l1_keyword_weights": self.l1_keyword_weights,
                        "_consult_blacklist": self.consult_blacklist
                        or list(_DEFAULT_CONSULT_BLACKLIST),
                        "updated_at": datetime.now().isoformat(),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as e:
            print(f"关键词保存失败: {e}")
    
    def load_keywords(self):
        try:
            if os.path.exists(KEYWORD_FILE):
                with open(KEYWORD_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.keyword_dict = data.get("keywords", {})
                    self.v3_l1_keywords = data.get("v3_l1_keywords") or {}
                    self.l1_keyword_weights = {str(k): float(v) for k, v in (data.get("l1_keyword_weights") or {}).items()}
                    self.consult_blacklist = list(
                        data.get("_consult_blacklist") or list(_DEFAULT_CONSULT_BLACKLIST)
                    )
                    print(
                        f"关键词词库加载成功，共 {sum(len(v) for v in self.keyword_dict.values())} 个关键词，"
                        f"咨询黑名单 {len(self.consult_blacklist)} 个"
                    )
            else:
                self._init_default_keywords()
        except Exception as e:
            print(f"关键词词库加载失败: {e}")
            self._init_default_keywords()
    
    def _init_default_keywords(self):
        self.v3_l1_keywords = {}
        self.l1_keyword_weights = {}
        self.consult_blacklist = list(_DEFAULT_CONSULT_BLACKLIST)
        self.keyword_dict = {
            "质量问题": ["电池", "续航", "掉电", "虚电", "充电", "故障", "异响", "抖动", "失灵", "卡顿", "黑屏", "死机", "漏风", "漏水"],
            "营销服务": ["服务", "销售", "售后", "门店", "顾问", "态度", "效率", "响应", "跟进", "处理", "等待", "排队", "预约", "维修", "保养"],
            "体验需求": ["体验", "优化", "建议", "希望", "期望", "改进", "提升", "增加", "添加", "完善", "简化", "便捷", "人性化", "交互"],
            "咨询": ["咨询", "疑问", "如何", "怎么", "能否", "是否", "费用", "价格", "政策", "活动", "权益", "保修", "配置", "参数"],
            "非问题": ["感谢", "表扬", "满意", "好评", "不错", "很好", "优秀", "专业", "贴心", "高效", "到位", "及时", "靠谱", "推荐"]
        }
        self.save_keywords()

keyword_extractor = KeywordExtractor()

# ===================== 数据仪表盘缓存（避免每次全表拉取 original_text）=====================
_dashboard_cache_lock = threading.Lock()
_dashboard_cache_payload: Optional[Dict[str, Any]] = None
_dashboard_cache_ts: float = 0.0

# ===================== 数据库操作 =====================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_CONN_TIMEOUT)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS opinion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opinion_id TEXT,
            source TEXT,
            original_text TEXT,
            create_time TEXT,
            model_class TEXT,
            model_keyword TEXT,
            match_score REAL,
            upload_batch TEXT,
            review_status INTEGER DEFAULT 0,
            review_result TEXT,
            review_note TEXT,
            country TEXT,
            phone TEXT,
            vin TEXT,
            car_model TEXT,
            extracted_keywords TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    ''')
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_opinion_id ON opinion(opinion_id)",
        "CREATE INDEX IF NOT EXISTS idx_create_time ON opinion(create_time)",
        "CREATE INDEX IF NOT EXISTS idx_review_status ON opinion(review_status)",
        "CREATE INDEX IF NOT EXISTS idx_upload_batch ON opinion(upload_batch)",
        "CREATE INDEX IF NOT EXISTS idx_model_class ON opinion(model_class)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_batch_ctime ON opinion(upload_batch, create_time DESC)",
    ]
    for idx_sql in indexes:
        try:
            c.execute(idx_sql)
        except Exception:
            pass
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-64000")
        c.execute("PRAGMA temp_store=MEMORY")
    except Exception:
        pass
    conn.commit()
    conn.close()
    print("数据库初始化完成，索引已创建")


def _ensure_db_wal_mode():
    """启动时设置 WAL 模式，提升并发读写性能。"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        conn.execute("PRAGMA busy_timeout=60000")  # 60s busy timeout
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
        conn.close()
        logger.info("SQLite WAL 模式已启用 (db=%s)", DB_PATH)
    except Exception as e:
        logger.warning("启用 SQLite WAL 模式失败 (非阻塞): %s", e)


def query_db(sql, params=[], fetch_all=True):
    """查询数据库。注意：SQLite 对 SELECT 的 rowcount 为 -1，fetch_all=False 时必须用 fetchone()。"""
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_CONN_TIMEOUT)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute(sql, params)
        if fetch_all:
            result = [dict(row) for row in c.fetchall()]
        else:
            row = c.fetchone()
            result = dict(row) if row else None
    finally:
        conn.close()
    return result


def _voc_sql_json_extract(meta: Any, path_arg: Any) -> Optional[str]:
    """注册到 SQLite：用 Python json 容错解析 v3，避免原生 json_extract 遇到残缺 JSON 时整句查询报 malformed JSON。"""

    def _out(v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            return v
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)

    if meta is None:
        return None
    if isinstance(meta, (bytes, bytearray)):
        try:
            meta = meta.decode("utf-8", errors="replace")
        except Exception:
            return None
    raw = str(meta).strip()
    if not raw:
        return None
    p = str(path_arg or "").strip()
    if not p.startswith("$."):
        return None
    key = p[2:].strip()
    if not key or "." in key:
        return None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    return _out(obj.get(key))


def query_db_with_voc_json(sql: str, params: Optional[List[Any]] = None, fetch_all: bool = True):
    """与 query_db 相同，但注册 voc_json_extract；供复核列表等含 v3 JSON 条件的查询使用。"""
    if params is None:
        params = []
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_CONN_TIMEOUT)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    conn.create_function("voc_json_extract", 2, _voc_sql_json_extract)
    c = conn.cursor()
    try:
        c.execute(sql, params)
        if fetch_all:
            result = [dict(row) for row in c.fetchall()]
        else:
            row = c.fetchone()
            result = dict(row) if row else None
    finally:
        conn.close()
    return result


def execute_db(sql, params=[], return_last_id=False):
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_CONN_TIMEOUT)
    conn.execute("PRAGMA busy_timeout=60000")
    c = conn.cursor()
    if isinstance(params, list) and len(params) > 0 and isinstance(params[0], list):
        c.executemany(sql, params)
    else:
        c.execute(sql, params)
    conn.commit()
    last_id = c.lastrowid if return_last_id else None
    conn.close()
    return last_id


def execute_db_many(sql: str, rows: List[List[Any]]) -> int:
    """单连接批量写入，避免循环 execute_db 反复 connect/commit。"""
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_CONN_TIMEOUT)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        conn.executemany(sql, rows)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def _normalize_review_l1(raw: Any) -> Any:
    if raw is None or str(raw).strip() == "":
        return raw
    return canonicalize_l1_label(raw)


def _normalize_review_labels(raw_l1: Any, raw_l2: Any) -> Tuple[str, str]:
    return strip_non_issue_l2(raw_l1, raw_l2)


_REVIEW_BATCH_UPDATE_SQL = (
    "UPDATE opinion SET review_status = ?, review_result = ?, review_note = ?, "
    "review_l1 = ?, review_l2 = ?, review_l3 = ?, reviewer = ?, reviewed_at = ? "
    "WHERE opinion_id = ?"
)


def ensure_performance_indexes():
    """迁移后补全批量筛选常用组合索引（IF NOT EXISTS）。"""
    for s in (
        "CREATE INDEX IF NOT EXISTS idx_opinion_review_l1 ON opinion(review_l1)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_review_l2 ON opinion(review_l2)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_batch_rs ON opinion(upload_batch, review_status)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_rs_ctime ON opinion(review_status, create_time)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_create_time ON opinion(create_time)",
        # 报表/时间轴：首列时间归一后范围扫描（表达式索引，SQLite 3.31+）
        "CREATE INDEX IF NOT EXISTS idx_opinion_evt_day ON opinion(substr(replace(replace(trim(coalesce(nullif(create_time,''), nullif(reviewed_at,''), nullif(created_at,''))),'/','-'),'.','-'),1,10))",
        "CREATE INDEX IF NOT EXISTS idx_opinion_v3_l1 ON opinion(v3_l1)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_v3_l2 ON opinion(v3_l2)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_v3_match_type ON opinion(v3_match_type)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_batch_v3l1 ON opinion(upload_batch, v3_l1)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_batch_rs_v3conf ON opinion(upload_batch, review_status, v3_confidence)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_rs_v3conf ON opinion(review_status, v3_confidence)",
    ):
        try:
            execute_db(s)
        except sqlite3.OperationalError:
            pass


def _chunk_select_exists(column: str, values: List[str]) -> set:
    """按列分批 IN 查询，避免单次 SQL 过长；适合海量库表下去重校验。"""
    out: set = set()
    if not values:
        return out
    uniq = list(dict.fromkeys(str(v).strip() for v in values if v and str(v).strip()))
    chunk_sz = 450
    for i in range(0, len(uniq), chunk_sz):
        chunk = uniq[i : i + chunk_sz]
        ph = ",".join(["?"] * len(chunk))
        rows = query_db(f"SELECT {column} AS v FROM opinion WHERE {column} IN ({ph})", chunk)
        for r in rows:
            out.add(str(r["v"]).strip())
    return out


def _next_daily_upload_batch_seq(day_yyyymmdd: str) -> int:
    """生成当日批次序号：匹配 upload_batch 形如 YYYYMMDD_seq_N 的记录，取 seq 最大值 +1。"""
    prefix = f"{day_yyyymmdd}_"
    rows = query_db(
        "SELECT DISTINCT upload_batch AS u FROM opinion WHERE upload_batch LIKE ?",
        [f"{prefix}%"],
    )
    m = 0
    for r in rows or []:
        u = str(r.get("u") or "").strip()
        if not u.startswith(prefix):
            continue
        rest = u[len(prefix) :]
        seg = rest.split("_", 1)[0]
        try:
            m = max(m, int(seg))
        except ValueError:
            continue
    return m + 1


def _count_classify_targets(upload_batch: Optional[str], opinion_ids: Optional[List[str]]) -> int:
    if opinion_ids is not None:
        if len(opinion_ids) == 0:
            return 0
        ph = ",".join(["?"] * len(opinion_ids))
        r = query_db(
            f"SELECT COUNT(*) AS c FROM opinion WHERE opinion_id IN ({ph})",
            list(opinion_ids),
            fetch_all=False,
        )
        return int(r["c"]) if r and r.get("c") is not None else 0
    if upload_batch:
        r = query_db(
            "SELECT COUNT(*) AS c FROM opinion WHERE upload_batch = ?",
            [upload_batch],
            fetch_all=False,
        )
        return int(r["c"]) if r and r.get("c") is not None else 0
    return 0


def _append_reflow_failure_jsonl(opinion_id: str, reason: str) -> None:
    entry = {
        "opinion_id": opinion_id,
        "reason": reason,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    REFLOW_FAILURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REFLOW_FAILURES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _mark_reflow_row_failed(opinion_id: str, reason: str) -> None:
    """回流失败：写 jsonl 审计，并将 opinion.reflow_synced 标为 -1。"""
    clipped = (reason or "unknown")[:500]
    _append_reflow_failure_jsonl(opinion_id, clipped)
    try:
        execute_db(
            "UPDATE opinion SET reflow_synced = -1, reflow_sync_reason = ? WHERE opinion_id = ?",
            [clipped, opinion_id],
        )
    except Exception:
        logger.exception("mark reflow failed db update oid=%s", opinion_id)


def _read_reflow_failures(limit: int = 50) -> List[dict]:
    if not REFLOW_FAILURES_PATH.is_file():
        return []
    lines: List[str] = []
    try:
        with open(REFLOW_FAILURES_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        logger.exception("read reflow_failures.jsonl failed")
        return []
    out: List[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


def _refresh_label_matcher_gold_cache() -> None:
    """复核确认后异步刷新规则路径的金标缓存，不阻塞主线程；失败仅记日志。"""
    def runner():
        try:
            from voc_classifier_service import refresh_matcher_gold_cache

            n = refresh_matcher_gold_cache(DB_PATH)
            logger.info("LabelMatcher 金标缓存已刷新：%d 条", n)
        except Exception:
            logger.exception("refresh_matcher_gold_cache failed")

    threading.Thread(target=runner, daemon=True).start()


def _reflow_rows_background(
    rows: List[dict],
    reviewer: str,
    trigger: str,
    also_yearly: bool = False,
    *,
    write_csv: bool = False,
    upload_batch: Optional[str] = None,
) -> None:
    """后台回流：
    - rows 非空时回流清洗库并将这些行 reflow_synced 标 1；
    - write_csv=True 时（确认复核路径）总是按"全部已复核行"重写年度 CSV，
      确保部分复核的数据也即时落盘存档（不仅是整批完成时）；
    - also_yearly 且该导入批次已全部复核时，升级为 reflow_synced=2 并刷新金标。
    """
    def runner():
        try:
            if rows:
                from reflow_service import reflow_batch_rows

                reflow_batch_rows(rows, keyword_extractor, reviewer=reviewer, trigger=trigger)
                for row in rows:
                    execute_db(
                        "UPDATE opinion SET reflow_synced = 1 WHERE opinion_id = ?",
                        [row["opinion_id"]],
                    )
                _refresh_label_matcher_gold_cache()

            if write_csv:
                try:
                    with _YEARLY_CSV_LOCK:
                        _write_yearly_csv_to_disk()
                except Exception:
                    logger.exception("background annual CSV write failed trigger=%s", trigger)

            if also_yearly:
                batches = list({str(r.get("upload_batch") or "") for r in rows if r.get("upload_batch")})
                batches = [b for b in batches if b]
                ub = upload_batch or (batches[0] if len(batches) == 1 else "")
                if ub:
                    pend = query_db(
                        "SELECT COUNT(*) as cnt FROM opinion WHERE upload_batch = ? AND review_status != 1",
                        [ub],
                        fetch_all=False,
                    )
                    pc = int(pend["cnt"] or 0) if pend else 0
                    if pc == 0:
                        try:
                            with _YEARLY_CSV_LOCK:
                                _apply_yearly_archive_for_batch(ub, reviewer)
                        except Exception:
                            logger.exception("background yearly archive failed batch=%s", ub)
        except Exception as e:
            reason = str(e)[:500] or type(e).__name__
            logger.exception("background reflow failed trigger=%s rows=%s", trigger, len(rows))
            for row in rows:
                oid = (row.get("opinion_id") or "").strip()
                if oid:
                    _mark_reflow_row_failed(oid, reason)

    threading.Thread(target=runner, daemon=True).start()


def _read_pending_reflow_entries() -> List[dict]:
    if not PENDING_REFLOW_PATH.is_file():
        return []
    try:
        with open(PENDING_REFLOW_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        logger.exception("read pending_reflow.jsonl failed")
        return []
    out: List[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_pending_reflow_entries(entries: List[dict]) -> None:
    PENDING_REFLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_REFLOW_PATH.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    tmp.replace(PENDING_REFLOW_PATH)


def _enqueue_pending_reflow(opinion_id: str, upload_batch: str, source: str) -> None:
    oid = (opinion_id or "").strip()
    if not oid:
        return
    entry = {
        "opinion_id": oid,
        "upload_batch": (upload_batch or "").strip(),
        "source": source,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    PENDING_REFLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _pending_reflow_lock:
        with open(PENDING_REFLOW_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info("pending reflow queued oid=%s batch=%s source=%s", oid, upload_batch, source)


def _maybe_enqueue_pending_reflow(
    opinion_id: str,
    review_l1: Any,
    source: str = "save_review",
) -> bool:
    """已复核且尚未回流时写入延迟回流队列（不阻塞请求）。"""
    row = query_db(
        "SELECT review_status, reflow_synced, review_l1, upload_batch FROM opinion WHERE opinion_id = ?",
        [opinion_id],
        fetch_all=False,
    )
    if not row or int(row.get("review_status") or 0) != 1:
        return False
    if int(row.get("reflow_synced") or 0) >= 1:
        return False
    l1 = (str(review_l1).strip() if review_l1 is not None else "") or str(row.get("review_l1") or "").strip()
    if not l1:
        return False
    _enqueue_pending_reflow(opinion_id, str(row.get("upload_batch") or ""), source)
    return True


def _flush_pending_reflow_once() -> None:
    with _pending_reflow_lock:
        entries = _read_pending_reflow_entries()
    if not entries:
        return

    by_oid: Dict[str, dict] = {}
    for entry in entries:
        oid = str(entry.get("opinion_id") or "").strip()
        if oid:
            by_oid[oid] = entry

    processed_oids: set = set()
    by_batch: Dict[str, List[str]] = defaultdict(list)
    no_batch: List[str] = []
    for oid, entry in by_oid.items():
        ub = str(entry.get("upload_batch") or "").strip()
        if ub:
            by_batch[ub].append(oid)
        else:
            no_batch.append(oid)

    def _try_reflow_oids(oids: List[str]) -> None:
        if not oids:
            return
        need: List[str] = []
        for oid in oids:
            row = query_db(
                "SELECT review_status, reflow_synced, review_l1 FROM opinion WHERE opinion_id = ?",
                [oid],
                fetch_all=False,
            )
            if not row:
                processed_oids.add(oid)
                continue
            if int(row.get("review_status") or 0) != 1:
                continue
            if int(row.get("reflow_synced") or 0) >= 1:
                processed_oids.add(oid)
                continue
            if not str(row.get("review_l1") or "").strip():
                continue
            need.append(oid)
        if not need:
            return
        ph = ",".join(["?"] * len(need))
        rows = query_db(
            f"SELECT * FROM opinion WHERE opinion_id IN ({ph}) AND review_status = 1 "
            f"AND IFNULL(reflow_synced, 0) < 1 AND TRIM(IFNULL(review_l1, '')) != ''",
            need,
        )
        if not rows:
            return
        try:
            from reflow_service import reflow_batch_rows

            reflow_batch_rows(rows, keyword_extractor, reviewer="", trigger="pending_reflow")
            for row in rows:
                oid = str(row.get("opinion_id") or "").strip()
                if oid:
                    execute_db(
                        "UPDATE opinion SET reflow_synced = 1 WHERE opinion_id = ?",
                        [oid],
                    )
                    processed_oids.add(oid)
            _refresh_label_matcher_gold_cache()
            logger.info("pending reflow flushed count=%s", len(rows))
        except Exception as e:
            reason = str(e)[:500] or type(e).__name__
            logger.exception("pending reflow flush failed count=%s", len(rows))
            for row in rows:
                oid = str(row.get("opinion_id") or "").strip()
                if oid:
                    _mark_reflow_row_failed(oid, reason)

    _try_reflow_oids(no_batch)
    for upload_batch, oids in by_batch.items():
        pend = query_db(
            "SELECT COUNT(*) as cnt FROM opinion WHERE upload_batch = ? AND review_status != 1",
            [upload_batch],
            fetch_all=False,
        )
        if pend and int(pend.get("cnt") or 0) > 0:
            continue
        _try_reflow_oids(oids)

    if not processed_oids:
        return
    with _pending_reflow_lock:
        current = _read_pending_reflow_entries()
        remaining = [
            e for e in current if str(e.get("opinion_id") or "").strip() not in processed_oids
        ]
        _write_pending_reflow_entries(remaining)


def _flush_pending_reflow_loop() -> None:
    while True:
        try:
            _flush_pending_reflow_once()
        except Exception:
            logger.exception("pending reflow loop error")
        time.sleep(max(5, PENDING_REFLOW_INTERVAL))


def _start_pending_reflow_daemon() -> None:
    global _pending_reflow_daemon_started
    with _pending_reflow_daemon_lock:
        if _pending_reflow_daemon_started:
            return
        _pending_reflow_daemon_started = True
    threading.Thread(
        target=_flush_pending_reflow_loop,
        daemon=True,
        name="pending-reflow-flush",
    ).start()
    logger.info("pending reflow daemon started interval=%ss", PENDING_REFLOW_INTERVAL)


def _start_classify_job(
    job_id: str,
    upload_batch: Optional[str],
    opinion_ids: Optional[List[str]],
    use_llm: bool,
    host: str,
) -> None:
    def runner():
        try:
            from voc_classifier_service import run_batch_classify

            def progress(done: int, total: int, msg: str) -> None:
                with classify_jobs_lock:
                    cur = classify_jobs.get(job_id, {})
                    cur.update(
                        {
                            "status": "running",
                            "processed": int(done),
                            "total": int(total),
                            "msg": msg,
                            "updated_at": time.time(),
                        }
                    )
                    classify_jobs[job_id] = cur

            with classify_jobs_lock:
                total_planned = int((classify_jobs.get(job_id) or {}).get("total") or 0)
            result = run_batch_classify(
                DB_PATH,
                upload_batch=upload_batch,
                opinion_ids=opinion_ids,
                use_llm=use_llm,
                ollama_host=host,
                progress_cb=progress,
            )
            proc = int(result.get("updated") or 0)
            with classify_jobs_lock:
                classify_jobs[job_id] = {
                    "status": "done",
                    "result": result,
                    "processed": proc,
                    "total": total_planned or proc,
                    "msg": result.get("msg", "分类完成"),
                    "updated_at": time.time(),
                }
        except Exception as e:
            logger.exception("classify job %s", job_id)
            with classify_jobs_lock:
                classify_jobs[job_id] = {
                    "status": "error",
                    "msg": str(e),
                    "updated_at": time.time(),
                }

    threading.Thread(target=runner, daemon=True).start()


def _backfill_keywords_for_opinion_ids(opinion_ids: List[str]) -> None:
    """导入后补全 extracted_keywords，避免上传请求内同步跑数千次提取。"""
    if not opinion_ids:
        return
    updates: List[List[Any]] = []
    chunk_sz = 450
    for i in range(0, len(opinion_ids), chunk_sz):
        chunk = [x for x in opinion_ids[i : i + chunk_sz] if x]
        if not chunk:
            continue
        ph = ",".join(["?"] * len(chunk))
        try:
            rows = query_db(
                f"SELECT opinion_id, original_text FROM opinion WHERE opinion_id IN ({ph})",
                chunk,
            )
        except Exception:
            continue
        for row in rows:
            text = (row or {}).get("original_text") or ""
            if not text:
                continue
            try:
                kws = keyword_extractor.extract_keywords(text)[:35]
                updates.append([",".join(kws), row["opinion_id"]])
            except Exception:
                pass
    if updates:
        execute_db_many(
            "UPDATE opinion SET extracted_keywords = ? WHERE opinion_id = ?",
            updates,
        )


def _backfill_v3_materialized_for_ids(opinion_ids: List[str]) -> None:
    """导入后同步 v3 物化列，使列表筛选可走索引。"""
    if not opinion_ids:
        return
    try:
        from v3_materialized import backfill_v3_materialized

        n = backfill_v3_materialized(DB_PATH, opinion_ids=list(opinion_ids))
        logger.info("v3 物化列回填 opinion_ids=%d updated=%d", len(opinion_ids), n)
    except Exception:
        logger.exception("backfill v3 materialized failed")


def _original_text_md5_hash(text: str) -> str:
    """与离线校验一致：去空白后 MD5，用于原文去重。"""
    norm = re.sub(r"\s+", "", str(text or "").strip())
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


def _norm_email_subject_sender(s: str) -> str:
    t = str(s or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"^(re|fw|fwd|wg|aw|回复|转发)\s*[:,：\[\s-]+", "", t, flags=re.I)
    return t[:800]


def _parse_subject_sender_from_body(text: str) -> Tuple[str, str]:
    """从正文头部解析「主题」「发件人」（用于无外列邮件）。仅在片段内扫描提升性能。"""
    head = (text or "")[:4200]
    subj = ""
    sender = ""
    for line in head.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(主题|标题)\s*[：:]\s*(.+)$", line, re.I)
        if m:
            subj = m.group(2).strip()
        m = re.match(r"^发件人\s*[：:]\s*(.+)$", line, re.I)
        if m:
            sender = m.group(1).strip()
        m = re.match(r"^from\s*[：:]\s*(.+)$", line, re.I)
        if m and not sender:
            sender = m.group(1).strip()
    return _norm_email_subject_sender(subj), _norm_email_subject_sender(sender)


def _csv_subject_sender(row: pd.Series) -> Tuple[str, str]:
    subj_cols = ("邮件主题", "主题", "email_subject", "EmailSubject", "mail_subject")
    send_cols = ("发件人", "sender", "Sender", "邮件发件人", "发件")
    subj = ""
    snd = ""
    for c in subj_cols:
        if c in row.index:
            v = str(row.get(c, "") or "").strip()
            if v:
                subj = v
                break
    for c in send_cols:
        if c in row.index:
            v = str(row.get(c, "") or "").strip()
            if v:
                snd = v
                break
    return _norm_email_subject_sender(subj), _norm_email_subject_sender(snd)


def _is_email_like_row(row: pd.Series, original_text: str, source: str) -> bool:
    sj, sd = _csv_subject_sender(row)
    if sj and sd:
        return True
    hints = [
        x.strip()
        for x in os.environ.get(
            "VOC_EMAIL_SOURCE_HINTS",
            "邮件,email,e-mail,outlook,Outlook,Exchange,SMTP,邮箱",
        ).split(",")
        if x.strip()
    ]
    src = str(source or "")
    low = src.lower()
    for h in hints:
        if h.lower() in low:
            return True
    ps, pe = _parse_subject_sender_from_body(original_text)
    if ps and pe:
        return True
    snippet = (original_text or "")[:2800].lower()
    if ("此电子邮件" in snippet or "this email" in snippet) and (
        re.search(r"主题\s*[：:]", original_text[:3000], re.I)
        or re.search(r"发件人\s*[：:]", original_text[:3000], re.I)
    ):
        return True
    return False


def migrate_v3_columns():
    """V3：扩展 v3_label_meta（JSON）；列已存在则跳过，不删表。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("PRAGMA table_info(opinion)")
        cols = [r[1] for r in c.fetchall()]
        if not cols:
            conn.close()
            return
        if "v3_label_meta" not in cols:
            c.execute("ALTER TABLE opinion ADD COLUMN v3_label_meta TEXT")
            conn.commit()
            print("已迁移：新增列 v3_label_meta")
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"迁移 v3_label_meta 跳过: {e}")


def migrate_review_human_columns():
    """人工复核 V3：一二级/审计/回流标记；已存在则跳过。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("PRAGMA table_info(opinion)")
        cols = [r[1] for r in c.fetchall()]
        if not cols:
            conn.close()
            return
        for col in ("review_l2", "review_l3", "review_l1", "reviewer", "reviewed_at", "reflow_synced"):
            if col not in cols:
                typ = "INTEGER" if col == "reflow_synced" else "TEXT"
                c.execute(f"ALTER TABLE opinion ADD COLUMN {col} {typ}")
                print(f"已迁移：新增列 {col}")
        if "reflow_sync_reason" not in cols:
            c.execute("ALTER TABLE opinion ADD COLUMN reflow_sync_reason TEXT DEFAULT ''")
            print("已迁移：新增列 reflow_sync_reason")
        conn.commit()
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"迁移人工复核列跳过: {e}")


def migrate_original_text_md5_column():
    """原文 MD5 去重列 + 索引（启动时仅 DDL，回填见后台任务）。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("PRAGMA table_info(opinion)")
        cols = [r[1] for r in c.fetchall()]
        if not cols:
            conn.close()
            return
        if "original_text_md5" not in cols:
            c.execute("ALTER TABLE opinion ADD COLUMN original_text_md5 TEXT")
            conn.commit()
            print("已迁移：新增列 original_text_md5")
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_opinion_text_md5 ON opinion(original_text_md5)")
            conn.commit()
        except Exception:
            pass
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"迁移 original_text_md5 结构跳过: {e}")
        return


def _backfill_original_text_md5_batch(batch_size: int = 500) -> int:
    """批量回填 original_text_md5，供后台任务调用。"""
    updated = 0
    while True:
        pending = query_db(
            """SELECT opinion_id, original_text FROM opinion
               WHERE original_text_md5 IS NULL OR original_text_md5 = ''
               LIMIT ?""",
            [batch_size],
        )
        if not pending:
            break
        params: List[List[Any]] = []
        for row in pending:
            oid = row.get("opinion_id")
            if not oid:
                continue
            params.append([_original_text_md5_hash(row.get("original_text") or ""), oid])
        if not params:
            break
        try:
            execute_db_many(
                "UPDATE opinion SET original_text_md5 = ? WHERE opinion_id = ?",
                params,
            )
            updated += len(params)
        except Exception as ex:
            logger.warning("回填 original_text_md5 批次跳过: %s", ex)
            break
        if len(pending) < batch_size:
            break
    return updated


def migrate_v3_materialized_columns():
    """P3：v3 物化列结构 + 索引（启动时仅 DDL，回填见后台任务）。"""
    try:
        from v3_materialized import ensure_v3_materialized_schema

        conn = sqlite3.connect(DB_PATH, timeout=SQLITE_CONN_TIMEOUT)
        ensure_v3_materialized_schema(conn)
        conn.close()
        print("v3 物化列结构已就绪")
    except Exception as e:
        print(f"迁移 v3 物化列警告: {e}")


def _run_deferred_db_backfills():
    """后台回填历史数据，避免阻塞 uvicorn 绑端口。"""
    try:
        from v3_materialized import backfill_v3_materialized

        n = backfill_v3_materialized(DB_PATH)
        if n:
            logger.info("v3 物化列后台回填完成: %d 条", n)
    except Exception:
        logger.exception("v3 物化列后台回填失败")
    try:
        n_md5 = _backfill_original_text_md5_batch()
        if n_md5:
            logger.info("original_text_md5 后台回填完成: %d 条", n_md5)
    except Exception:
        logger.exception("original_text_md5 后台回填失败")


def _verify_and_migrate_db() -> None:
    """模块加载时：迁移 schema、启用 WAL、启动延迟回流 daemon。"""
    init_db()
    migrate_v3_columns()
    migrate_review_human_columns()
    migrate_original_text_md5_column()
    migrate_v3_materialized_columns()
    ensure_performance_indexes()
    _ensure_db_wal_mode()
    _start_pending_reflow_daemon()


_verify_and_migrate_db()


@app.on_event("startup")
async def _on_startup_deferred_backfills():
    _ensure_db_wal_mode()
    threading.Thread(
        target=_run_deferred_db_backfills,
        daemon=True,
        name="db-deferred-backfill",
    ).start()
    _start_pending_reflow_daemon()

# ===================== 分类准确率日志 =====================
def log_classification_result(opinion_id: str, original_class: str, corrected_class: str, is_corrected: bool):
    try:
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "opinion_id": opinion_id,
            "original_class": original_class,
            "corrected_class": corrected_class,
            "is_corrected": is_corrected
        }
        logs = []
        if os.path.exists(CLASSIFICATION_LOG_FILE):
            with open(CLASSIFICATION_LOG_FILE, "r", encoding="utf-8") as f:
                try:
                    logs = json.load(f)
                except:
                    pass
        logs.append(log_entry)
        if len(logs) > 10000:
            logs = logs[-10000:]
        with open(CLASSIFICATION_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
        if is_corrected and corrected_class:
            text = query_db("SELECT original_text FROM opinion WHERE opinion_id = ?", [opinion_id])
            if text and text[0]:
                keyword_extractor.update_keywords_from_correction(text[0]["original_text"], corrected_class)
    except Exception as e:
        print(f"分类日志记录失败: {e}")

def get_classification_accuracy() -> Dict:
    try:
        rows = query_db(
            """SELECT review_l1, review_l2, model_class, model_keyword, v3_l1, v3_l2, v3_label_meta
               FROM opinion
               WHERE review_status = 1
               AND TRIM(IFNULL(review_l1,'')) != ''""",
            [],
        )
        l1_total = l1_ok = l2_total = l2_ok = 0
        for r in rows:
            ml1 = str(r.get("v3_l1") or "").strip()
            ml2 = str(r.get("v3_l2") or "").strip()
            if not ml1 or not ml2:
                meta = r.get("v3_label_meta") or ""
                if meta:
                    try:
                        j = json.loads(meta) if isinstance(meta, str) else meta
                        if isinstance(j, dict):
                            if not ml1:
                                ml1 = str(j.get("l1") or "").strip()
                            if not ml2:
                                ml2 = str(j.get("l2") or "").strip()
                    except Exception:
                        pass
            if not ml1:
                ml1 = str(r.get("model_class") or "").strip()
            if not ml2:
                mk = str(r.get("model_keyword") or "").strip()
                ml2 = mk.split(",", 1)[0].strip() if mk else ""
            ml1c = canonicalize_l1_label(ml1) if ml1 else ""
            _, ml2 = strip_non_issue_l2(ml1c, ml2)
            h1 = canonicalize_l1_label(r.get("review_l1") or "")
            h2 = str(r.get("review_l2") or "").strip()
            _, h2 = strip_non_issue_l2(h1, h2)
            if ml1c:
                l1_total += 1
                if ml1c == h1:
                    l1_ok += 1
            if h1 in L2_EVAL_L1 and ml1c == h1 and h2 and ml2:
                l2_total += 1
                if h2 == ml2:
                    l2_ok += 1
        l1_acc = round(l1_ok / l1_total * 100, 2) if l1_total else None
        l2_acc = round(l2_ok / l2_total * 100, 2) if l2_total else None
        return {
            "total": l1_total,
            "correct": l1_ok,
            "accuracy": l1_acc,
            "l1_accuracy": l1_acc,
            "l2_accuracy": l2_acc,
            "l1_total": l1_total,
            "l2_total": l2_total,
        }
    except Exception as e:
        return {"total": 0, "correct": 0, "accuracy": None, "l1_accuracy": None, "l2_accuracy": None}

# ===================== API接口 =====================

@app.get("/")
async def root():
    return {"code": 200, "msg": "舆情复核系统 V1.2 已启动", "data": {"version": "1.2.0"}}

@app.get("/api/get_review_list")
@app.get("/get_review_list")
@rate_limit(
    "get_review_list",
    RATE_LIMIT_CONFIG["get_review_list"]["rate"],
    RATE_LIMIT_CONFIG["get_review_list"]["interval"],
)
async def get_review_list_api(
    page: int = 1,
    size: int = REVIEW_LIST_DEFAULT_PAGE,
    searchKey: str = "",
    reviewStatus: Optional[int] = None,
    uploadBatch: Optional[str] = None,
    filterL1: str = "",
    filterL2: str = "",
    filterL3: str = "",
    confidenceMin: Optional[float] = None,
    confidenceMax: Optional[float] = None,
    matchFamily: str = "",
    year: str = "",
    dateFrom: str = "",
    dateTo: str = "",
    pendingOnly: bool = False,
    filterSource: str = "",
    mismatchFilter: str = "",
    opinionIds: str = "",
):
    try:
        page = max(1, int(page or 1))
        size = min(REVIEW_LIST_MAX_PAGE, max(1, int(size or REVIEW_LIST_DEFAULT_PAGE)))
    except (TypeError, ValueError):
        page, size = 1, REVIEW_LIST_DEFAULT_PAGE
    where_conditions = []
    params = []
    if searchKey:
        where_conditions.append("(original_text LIKE ? OR source LIKE ? OR model_keyword LIKE ? OR opinion_id LIKE ?)")
        params.extend([f"%{searchKey}%", f"%{searchKey}%", f"%{searchKey}%", f"%{searchKey}%"])
    if reviewStatus is not None:
        where_conditions.append("review_status = ?")
        params.append(reviewStatus)
    if uploadBatch:
        where_conditions.append("upload_batch = ?")
        params.append(uploadBatch)
    if opinionIds:
        id_list = [x.strip() for x in str(opinionIds).split(",") if x.strip()][:500]
        if id_list:
            ph = ",".join(["?"] * len(id_list))
            where_conditions.append(f"opinion_id IN ({ph})")
            params.extend(id_list)
    if filterSource:
        where_conditions.append("source LIKE ?")
        params.append(f"%{filterSource}%")
    if year and len(year) == 4:
        where_conditions.append("SUBSTR(create_time, 1, 4) = ?")
        params.append(year)
    if dateFrom:
        where_conditions.append("create_time >= ?")
        params.append(dateFrom)
    if dateTo:
        where_conditions.append("create_time <= ?")
        params.append(dateTo + " 23:59:59" if len(dateTo) <= 10 else dateTo)
    if filterL1:
        fl1 = (filterL1 or "").strip()
        if fl1:
            variants = l1_values_for_filter(fl1)
            ph = ",".join(["?"] * len(variants))
            where_conditions.append(
                f"(IFNULL(review_l1, '') IN ({ph}) OR IFNULL(v3_l1, '') IN ({ph}))"
            )
            params.extend(variants)
            params.extend(variants)
    if filterL2:
        fl2 = (filterL2 or "").strip()
        if fl2:
            where_conditions.append("(IFNULL(review_l2, '') = ? OR IFNULL(v3_l2, '') = ?)")
            params.extend([fl2, fl2])
    if filterL3:
        where_conditions.append("IFNULL(v3_l3, '') LIKE ?")
        params.append(f"%{filterL3}%")
    conf_sql = "COALESCE(v3_confidence, match_score, 0)"
    if confidenceMin is not None:
        where_conditions.append(f"({conf_sql} >= ?)")
        params.append(confidenceMin)
    if confidenceMax is not None:
        where_conditions.append(f"({conf_sql} <= ?)")
        params.append(confidenceMax)
    if matchFamily == "clean":
        where_conditions.append("v3_match_type = 'clean_l1'")
    elif matchFamily == "llm":
        from v3_materialized import V3_MATCH_LLM_WHERE

        where_conditions.append(V3_MATCH_LLM_WHERE)
    elif matchFamily == "rule":
        from v3_materialized import V3_MATCH_RULE_WHERE

        where_conditions.append(V3_MATCH_RULE_WHERE)
    if pendingOnly:
        where_conditions.append(
            f"(review_status = 2 OR (review_status != 1 AND {conf_sql} < 0.52))"
        )
    if mismatchFilter == "l1_disagree":
        where_conditions.append(
            "(review_status IN (0, 2) AND IFNULL(model_class, '') != '' AND "
            "IFNULL(v3_l1, '') != '' AND v3_l1 != model_class)"
        )
    elif mismatchFilter == "l2_disagree":
        where_conditions.append(
            "(review_status IN (0, 2) AND IFNULL(v3_l1, '') != '' AND "
            "IFNULL(model_keyword, '') != '' AND "
            "v3_l2 != "
            "CASE WHEN INSTR(model_keyword, ',') > 0 "
            "THEN TRIM(SUBSTR(model_keyword, 1, INSTR(model_keyword, ',') - 1)) "
            "ELSE TRIM(model_keyword) END)"
        )
    elif mismatchFilter == "human_fixed_l1":
        where_conditions.append(
            "(review_status = 1 AND IFNULL(review_l1, '') != '' AND "
            "IFNULL(v3_l1, '') != '' AND v3_l1 != review_l1)"
        )
    elif mismatchFilter == "human_fixed_l2":
        where_conditions.append(
            "(review_status = 1 AND IFNULL(review_l2, '') != '' AND "
            "IFNULL(v3_l2, '') != '' AND v3_l2 != review_l2)"
        )
    where_sql = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
    count_sql = f"SELECT COUNT(*) as cnt FROM opinion {where_sql}"

    def _fetch_review_list_page() -> Dict[str, Any]:
        try:
            count_result = query_db(count_sql, params, fetch_all=False)
            total = int(count_result["cnt"]) if count_result and count_result.get("cnt") is not None else 0
            offset = (page - 1) * size
            preview_n = REVIEW_LIST_TEXT_PREVIEW
            data_sql = f"""SELECT {REVIEW_LIST_SELECT_BODY} FROM opinion {where_sql} ORDER BY create_time DESC LIMIT ? OFFSET ?"""
            list_params = [preview_n, preview_n, *params, size, offset]
            data = query_db(data_sql, list_params)
            for row in data:
                r1 = (row.get("review_l1") or "").strip()
                v3l1 = (row.get("v3_l1") or "").strip()
                if not v3l1:
                    try:
                        vm = row.get("v3_label_meta")
                        if vm:
                            v3l1 = str(json.loads(vm).get("l1") or "").strip()
                    except Exception:
                        pass
                mc = (row.get("model_class") or "").strip()
                row["canonical_l1"] = canonicalize_l1_label(r1 or v3l1 or mc)
            return {"code": 200, "msg": "获取数据成功", "total": total, "data": data}
        except Exception as e:
            logger.exception("get_review_list 失败: %s", e)
            return {
                "code": 500,
                "msg": f"查询失败：{str(e)}",
                "total": 0,
                "data": [],
            }

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_review_list_page),
            timeout=REVIEW_LIST_QUERY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "code": 503,
            "msg": "列表查询超时，请缩小筛选条件、指定批次或稍后重试",
            "total": 0,
            "data": [],
            "timeout": True,
        }
    except Exception as e:
        logger.exception("get_review_list 线程调度失败: %s", e)
        return {"code": 500, "msg": str(e), "total": 0, "data": []}


@app.get("/api/opinion_detail")
@app.get("/opinion_detail")
async def get_opinion_detail_api(opinion_id: str = ""):
    """复核抽屉按需拉取原文全文（列表接口仅返回 preview）。"""
    oid = (opinion_id or "").strip()
    if not oid:
        return {"code": 400, "msg": "需要 opinion_id"}
    try:
        row = query_db(
            """SELECT opinion_id, original_text, extracted_keywords, v3_label_meta,
               review_l1, review_l2, review_note, review_status
               FROM opinion WHERE opinion_id = ? LIMIT 1""",
            [oid],
            fetch_all=False,
        )
        if not row:
            return {"code": 404, "msg": "未找到该舆情"}
        return {"code": 200, "msg": "ok", "data": dict(row)}
    except Exception as e:
        logger.exception("get_opinion_detail failed opinion_id=%s", oid)
        return {"code": 500, "msg": str(e)}


# 2. 保存复核结果（优化版 - 记录分类日志）
@app.post("/api/save_review")
@app.post("/save_review")
async def save_review_api(request: Request):
    data = await request.json()
    if "opinion_id" not in data or "review_status" not in data:
        return {"code": 400, "msg": "舆情ID和复核状态不能为空"}
    original = query_db("SELECT model_class, review_note FROM opinion WHERE opinion_id = ?", [data["opinion_id"]])
    _rl1 = data.get("review_l1")
    _rl2 = data.get("review_l2")
    if _rl1 is not None and str(_rl1).strip() != "":
        _l1_out, _l2_out = _normalize_review_labels(_rl1, _rl2)
    else:
        _l1_out, _l2_out = _rl1, _rl2
    has_reviewed_at = "reviewed_at" in data
    if has_reviewed_at:
        execute_db(
            "UPDATE opinion SET review_status = ?, review_result = ?, review_note = ?, review_l1 = ?, review_l2 = ?, review_l3 = ?, reviewer = ?, reviewed_at = ? WHERE opinion_id = ?",
            [
                data["review_status"],
                data.get("review_result"),
                data.get("review_note"),
                _l1_out,
                _l2_out,
                data.get("review_l3"),
                data.get("reviewer"),
                data.get("reviewed_at"),
                data["opinion_id"],
            ],
        )
    else:
        execute_db(
            "UPDATE opinion SET review_status = ?, review_result = ?, review_note = ?, review_l1 = ?, review_l2 = ?, review_l3 = ?, reviewer = ? WHERE opinion_id = ?",
            [
                data["review_status"],
                data.get("review_result"),
                data.get("review_note"),
                _l1_out,
                _l2_out,
                data.get("review_l3"),
                data.get("reviewer"),
                data["opinion_id"],
            ],
        )
    if original and original[0]:
        original_class = original[0]["model_class"] or ""
        corrected_class = data.get("review_note") or ""
        is_corrected = original_class != corrected_class and data["review_status"] == 1
        log_classification_result(data["opinion_id"], original_class, corrected_class, is_corrected)
    queued_reflow = False
    if int(data.get("review_status") or 0) == 1:
        queued_reflow = _maybe_enqueue_pending_reflow(data["opinion_id"], _l1_out)
    return {"code": 200, "msg": "保存成功", "queued_reflow": queued_reflow}

# 3. 批量保存复核结果（新增）
@app.post("/api/batch_save_review")
@app.post("/batch_save_review")
async def batch_save_review_api(request: Request):
    data = await request.json()
    if "reviews" not in data or not isinstance(data["reviews"], list):
        return {"code": 400, "msg": "缺少reviews参数"}
    try:
        success_count = 0
        batch_rows: List[List[Any]] = []
        for review in data["reviews"]:
            if "opinion_id" not in review or "review_status" not in review:
                continue
            rl1 = review.get("review_l1")
            rl2 = review.get("review_l2")
            if rl1 is not None and str(rl1).strip() != "":
                l1n, l2n = _normalize_review_labels(rl1, rl2)
            else:
                l1n, l2n = rl1, rl2
            batch_rows.append(
                [
                    review["review_status"],
                    review.get("review_result"),
                    review.get("review_note"),
                    l1n,
                    l2n,
                    review.get("review_l3"),
                    review.get("reviewer"),
                    review.get("reviewed_at"),
                    review["opinion_id"],
                ]
            )
        success_count = execute_db_many(_REVIEW_BATCH_UPDATE_SQL, batch_rows)
        queued_reflow = 0
        confirmed_oids = [
            str(review["opinion_id"]).strip()
            for review in data["reviews"]
            if review.get("opinion_id") and int(review.get("review_status") or 0) == 1
        ]
        for oid in confirmed_oids:
            review_l1 = next(
                (r.get("review_l1") for r in data["reviews"] if str(r.get("opinion_id") or "").strip() == oid),
                None,
            )
            if _maybe_enqueue_pending_reflow(oid, review_l1, source="batch_save_review"):
                queued_reflow += 1
        return {
            "code": 200,
            "msg": f"批量保存成功，共处理{success_count}条",
            "queued_reflow": queued_reflow,
        }
    except Exception as e:
        return {"code": 500, "msg": f"批量保存失败: {str(e)}"}


@app.post("/api/draft_save_reviews")
@app.post("/draft_save_reviews")
async def draft_save_reviews_api(request: Request):
    """暂存并定稿本批：已填一级的条目写入库、标记已复核、关键词提取与回流（与确认复核一致，支持跨页/跨天继续处理未填项）。"""
    try:
        data = await request.json()
        reviews = data.get("reviews")
        if not reviews or not isinstance(reviews, list):
            return {"code": 400, "msg": "需要 reviews 数组"}
        reviewer = (data.get("reviewer") or "").strip()
        with_reflow = data.get("with_reflow", True)
        now = datetime.now().isoformat(timespec="seconds")
        rows_for_reflow: List[dict] = []
        n = 0
        oids = [str(item.get("opinion_id") or "").strip() for item in reviews]
        oids = [x for x in oids if x]
        base_map: Dict[str, dict] = {}
        chunk_sz = 450
        for i in range(0, len(oids), chunk_sz):
            chunk = oids[i : i + chunk_sz]
            ph = ",".join(["?"] * len(chunk))
            fetched = query_db(
                f"""SELECT opinion_id, review_status, review_l1, review_l2, review_note,
                    original_text, v3_l1, v3_l2, v3_l3, v3_label_meta, upload_batch, country,
                    create_time, model_class, model_keyword, extracted_keywords, reflow_synced
                    FROM opinion WHERE opinion_id IN ({ph})""",
                chunk,
            )
            for row in fetched:
                base_map[str(row.get("opinion_id") or "")] = dict(row)

        draft_updates: List[List[Any]] = []
        for item in reviews:
            oid = str(item.get("opinion_id") or "").strip()
            if not oid:
                continue
            base = base_map.get(oid)
            if not base:
                continue
            if int(base.get("review_status") or 0) == 1:
                continue
            auto_l1 = str(base.get("v3_l1") or "").strip()
            auto_l2 = str(base.get("v3_l2") or "").strip()
            if not auto_l1 or not auto_l2:
                v3_raw = base.get("v3_label_meta") or ""
                try:
                    if v3_raw:
                        mj = json.loads(v3_raw) if isinstance(v3_raw, str) else {}
                        if not auto_l1:
                            auto_l1 = str(mj.get("l1") or "")
                        if not auto_l2:
                            auto_l2 = str(mj.get("l2") or "")
                except Exception:
                    pass
            raw_l1 = (item.get("review_l1") or base.get("review_l1") or auto_l1 or "").strip()
            if not raw_l1:
                continue
            l1, l2 = _normalize_review_labels(
                raw_l1,
                item.get("review_l2") or base.get("review_l2") or auto_l2 or "",
            )
            note = item.get("review_note")
            if note is None:
                note = base.get("review_note")
            text = base.get("original_text") or ""
            kws = keyword_extractor.extract_keywords(text)[:35]
            extracted = ",".join(kws)
            existing_l1 = (base.get("review_l1") or "").strip()
            existing_l2 = (base.get("review_l2") or "").strip()
            existing_reflow = int(base.get("reflow_synced") or 0)
            labels_changed = (l1 != existing_l1) or (l2 != existing_l2)
            reflow_synced_val = 0 if labels_changed else existing_reflow
            draft_updates.append(
                [l1, l2, note, extracted, reviewer or None, now, reflow_synced_val, oid]
            )
            n += 1
            if with_reflow and labels_changed:
                rows_for_reflow.append(
                    {
                        **base,
                        "review_l1": l1,
                        "review_l2": l2,
                        "review_note": note,
                        "extracted_keywords": extracted,
                        "reviewer": reviewer,
                        "reviewed_at": now,
                    }
                )
        if draft_updates:
            execute_db_many(
                """UPDATE opinion SET review_status=1, review_l1=?, review_l2=?, review_note=?,
                extracted_keywords=?, reviewer=?, reviewed_at=?, review_l3='', reflow_synced=?
                WHERE opinion_id=?""",
                draft_updates,
            )
        reflowed = new_c = over_c = 0
        reflow_async = False
        if with_reflow and rows_for_reflow:
            if len(rows_for_reflow) > REFLOW_ASYNC_THRESHOLD:
                reflow_async = True
                _reflow_rows_background(rows_for_reflow, reviewer, "draft")
                msg = (
                    f"已暂存并标记已复核 {n} 条；清洗库与金标回流已在后台执行（{len(rows_for_reflow)} 条），"
                    "完成后将自动标记 reflow_synced。未填一级的行未改动。"
                )
            else:
                from reflow_service import reflow_batch_rows

                r = reflow_batch_rows(
                    rows_for_reflow,
                    keyword_extractor,
                    reviewer=reviewer,
                    trigger="draft",
                )
                reflowed = int(r.get("reflowed") or 0)
                new_c = int(r.get("new_in_clear") or 0)
                over_c = int(r.get("overwritten_in_clear") or 0)
                execute_db_many(
                    "UPDATE opinion SET reflow_synced = 1 WHERE opinion_id = ?",
                    [[row["opinion_id"]] for row in rows_for_reflow],
                )
                _refresh_label_matcher_gold_cache()
                msg = (
                    f"已暂存并标记已复核 {n} 条；本次回流 {reflowed} 条，覆盖历史 {over_c} 条"
                    f"（清洗库新建 {new_c} 条）。未填一级的行未改动。"
                )
        elif n:
            msg = f"已暂存并标记已复核 {n} 条（未触发回流）。未填一级的行未改动。"
        else:
            msg = "没有可保存条目：请为需暂存的行填写「人工一级」后再试。"
        logger.info("draft_save_reviews: confirmed=%s reflowed=%s async=%s", n, reflowed, reflow_async)
        return {
            "code": 200,
            "msg": msg,
            "saved": n,
            "reflowed": reflowed,
            "reflow_async": reflow_async,
            "new_in_clear": new_c,
            "overwritten_in_clear": over_c,
        }
    except Exception as e:
        logger.exception("draft_save_reviews: %s", e)
        return {"code": 500, "msg": str(e), "saved": 0}


# 4. CSV上传接口（优化版 - 限流）
@app.post("/api/upload_csv")
@app.post("/upload_csv")
@rate_limit("upload_csv", RATE_LIMIT_CONFIG["upload_csv"]["rate"], RATE_LIMIT_CONFIG["upload_csv"]["interval"])
async def upload_csv_api(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".csv"):
        return JSONResponse(
            status_code=200,
            content={"code": 400, "msg": "仅支持CSV格式文件！"},
            media_type="application/json",
        )
    safe_name = re.sub(r"[^\w.\-]", "_", os.path.basename(file.filename or "upload.csv"))[:180]
    os.makedirs(TEMP_DATA_DIR, exist_ok=True)
    temp_path = os.path.join(str(TEMP_DATA_DIR), f"temp_{int(time.time() * 1000)}_{safe_name}")
    try:
        content = await file.read()
        if len(content) > 50 * 1024 * 1024:
            return JSONResponse(
                status_code=200,
                content={"code": 400, "msg": "文件大小超过50MB限制"},
                media_type="application/json",
            )
        with open(temp_path, "wb") as f:
            f.write(content)
        try:
            df = pd.read_csv(temp_path, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(temp_path, encoding="utf-8-sig")
        required_cols = ["舆情编号"]
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return JSONResponse(
                status_code=200,
                content={"code": 400, "msg": f"缺少列：{','.join(missing_cols)}"},
                media_type="application/json",
            )
        df = df.fillna("")
        total_rows = int(len(df))
        skipped_invalid_id = 0
        pending_map: Dict[str, dict] = {}
        for _, row in df.iterrows():
            opinion_id = str(row["舆情编号"]).strip()
            if not opinion_id:
                skipped_invalid_id += 1
                continue
            original_text = str(row.get("舆情中文", "")).strip()
            source = str(row.get("渠道", "")).strip()
            text_md5 = _original_text_md5_hash(original_text)
            email_like = _is_email_like_row(row, original_text, source)
            pending_map[opinion_id] = {
                "opinion_id": opinion_id,
                "text_md5": text_md5,
                "row": row,
                "email_like": email_like,
            }
        pending = list(pending_map.values())
        n_import = len(pending)
        day_key = datetime.now().strftime("%Y%m%d")
        batch_id = ""
        if n_import:
            seq = _next_daily_upload_batch_seq(day_key)
            batch_id = f"{day_key}_{seq}_{n_import}"

        insert_data: List[list] = []
        update_data: List[list] = []
        imported_opinion_ids: List[str] = []
        existing_oids: set = set()
        if n_import:
            oids_keys = [p["opinion_id"] for p in pending]
            existing_oids = _chunk_select_exists("opinion_id", oids_keys)

        for p in pending:
            opinion_id = p["opinion_id"]
            text_md5 = p["text_md5"]
            row = p["row"]
            original_text = str(row.get("舆情中文", "")).strip()
            source = str(row.get("渠道", "")).strip()
            create_time = str(row.get("舆情时间", "")).strip()
            model_class = str(row.get("一级标签", "")).strip()
            model_keyword = ",".join([str(row.get("二级标签", "")), str(row.get("三级标签", ""))]).strip(",")
            country = str(row.get("国家", "")).strip()
            phone = str(row.get("手机号", "")).strip()
            vin = str(row.get("VIN", "")).strip()
            car_model = str(row.get("车型", "")).strip()
            v3_meta = ""
            if "v3_label_meta" in df.columns:
                v3_meta = str(row.get("v3_label_meta", "")).strip()
            elif "标签体系JSON" in df.columns:
                v3_meta = str(row.get("标签体系JSON", "")).strip()

            insert_row = [
                opinion_id,
                source,
                original_text,
                create_time,
                model_class,
                model_keyword,
                0.0,
                batch_id,
                0,
                "",
                "",
                country,
                phone,
                vin,
                car_model,
                "",
                v3_meta,
                text_md5,
            ]
            if opinion_id in existing_oids:
                update_data.append(
                    [
                        source,
                        original_text,
                        create_time,
                        model_class,
                        model_keyword,
                        0.0,
                        batch_id,
                        country,
                        phone,
                        vin,
                        car_model,
                        "",
                        v3_meta,
                        text_md5,
                        opinion_id,
                    ]
                )
            else:
                insert_data.append(insert_row)
            imported_opinion_ids.append(opinion_id)

        batch_size = 500
        if insert_data:
            for i in range(0, len(insert_data), batch_size):
                batch = insert_data[i : i + batch_size]
                execute_db(
                    """INSERT INTO opinion (opinion_id, source, original_text, create_time, model_class, model_keyword, match_score, upload_batch,
                review_status, review_result, review_note, country, phone, vin, car_model, extracted_keywords, v3_label_meta, original_text_md5) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    batch,
                )
        upd_sql = """UPDATE opinion SET source=?, original_text=?, create_time=?, model_class=?, model_keyword=?, match_score=?, upload_batch=?,
                country=?, phone=?, vin=?, car_model=?, extracted_keywords=?, v3_label_meta=?, original_text_md5=? WHERE opinion_id=?"""
        if update_data:
            for i in range(0, len(update_data), batch_size):
                execute_db(upd_sql, update_data[i : i + batch_size])

        inserted_count = len(insert_data)
        updated_count = len(update_data)
        imported_count = inserted_count + updated_count
        if imported_opinion_ids:
            background_tasks.add_task(_backfill_keywords_for_opinion_ids, imported_opinion_ids)
            background_tasks.add_task(_backfill_v3_materialized_for_ids, imported_opinion_ids)
        msg = (
            f"本次上传共{total_rows}条：有效舆情编号 {n_import} 条，批次 {batch_id or '—'}。"
            f"新建 {inserted_count} 条，同编号更新 {updated_count} 条（仅按舆情ID去重，不再按原文MD5拦截）。"
        )
        if skipped_invalid_id:
            msg += f" {skipped_invalid_id}条缺少有效舆情编号未导入。"
        payload = {
            "code": 200,
            "msg": msg,
            "batch_id": batch_id if imported_count > 0 else "",
            "total_rows": total_rows,
            "imported_count": imported_count,
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "skipped_duplicates": 0,
            "skipped_id_exists": 0,
            "skipped_text_md5": 0,
            "skipped_invalid_id": skipped_invalid_id,
            "skipped_email_near_duplicate": 0,
            "email_new_body_imports": updated_count,
        }
        return JSONResponse(status_code=200, content=payload, media_type="application/json")
    except Exception as e:
        logger.exception("upload_csv 失败: %s", e)
        return JSONResponse(
            status_code=200,
            content={"code": 500, "msg": f"解析失败：{str(e)}", "total_rows": 0, "imported_count": 0, "skipped_duplicates": 0},
            media_type="application/json",
        )
    finally:
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except OSError:
            pass

# 5. 获取可视化数据（优化版）
@app.get("/api/get_visual_data")
@app.get("/get_visual_data")
@rate_limit("get_visual_data", RATE_LIMIT_CONFIG["get_visual_data"]["rate"], RATE_LIMIT_CONFIG["get_visual_data"]["interval"])
async def get_visual_data_api():
    try:
        # 统计总数（所有状态）
        total_sql = "SELECT COUNT(*) as total FROM opinion"
        total_result = query_db(total_sql, fetch_all=False)
        total_count = total_result["total"] if total_result else 0

        # 统计已复核数量（review_status = 1）
        reviewed_sql = "SELECT COUNT(*) as reviewed FROM opinion WHERE review_status = 1"
        reviewed_result = query_db(reviewed_sql, fetch_all=False)
        reviewed_count = reviewed_result["reviewed"] if reviewed_result else 0

        # 统计各分类（只统计已复核的数据）
        class_sql = '''SELECT review_note as class_name, COUNT(*) as count,
            ROUND(COUNT(*) * 100.0 / NULLIF((SELECT COUNT(*) FROM opinion WHERE review_status = 1), 0), 2) as percentage
            FROM opinion WHERE review_status = 1 AND review_note IN ('质量问题', '营销服务', '体验需求', '咨询', '非问题') GROUP BY review_note'''
        class_data = query_db(class_sql)
        return {"code": 200, "data": {"total_count": total_count, "reviewed_count": reviewed_count, "class_data": class_data}}
    except Exception as e:
        print(f"统计失败：{str(e)}")
        return {"code": 500, "data": {"total_count": 0, "reviewed_count": 0, "class_data": []}}

# 6. 获取年度汇总
@app.get("/api/get_yearly_summary")
@app.get("/get_yearly_summary")
async def get_yearly_summary_api(filterYear: str = "", filterMonth: str = ""):
    try:
        wc = "WHERE review_status = 1"
        pr: List = []
        if filterYear and len(filterYear) == 4:
            wc += " AND SUBSTR(create_time, 1, 4) = ?"
            pr.append(filterYear)
        if filterMonth and len(filterMonth) == 7:
            wc += " AND SUBSTR(create_time, 1, 7) = ?"
            pr.append(filterMonth)
        summary_sql = f'''SELECT SUBSTR(create_time, 1, 4) as year, SUBSTR(create_time, 1, 7) as year_month,
            COALESCE(review_status, 0) as review_status,
            COALESCE(review_note, '未填写') as review_note, COUNT(*) as count, MAX(create_time) as latest_time
            FROM opinion {wc} GROUP BY year, year_month, review_status, review_note ORDER BY year DESC, year_month DESC, latest_time DESC'''
        data = query_db(summary_sql, pr)
        return {"code": 200, "msg": "获取汇总成功", "data": data}
    except Exception as e:
        print(f"年度汇总失败：{str(e)}")
        return {"code": 500, "msg": f"汇总失败：{str(e)}"}

# 7. 获取上传批次
@app.get("/api/get_upload_batches")
@app.get("/get_upload_batches")
async def get_upload_batches_api():
    try:
        sql = """SELECT upload_batch, COUNT(*) as count, MAX(id) as sort_max_id
            FROM opinion WHERE upload_batch IS NOT NULL AND TRIM(upload_batch) != ''
            GROUP BY upload_batch ORDER BY sort_max_id DESC"""
        data = query_db(sql)
        return {"code": 200, "msg": "获取批次成功", "data": data}
    except Exception as e:
        return {"code": 500, "msg": f"获取批次失败：{str(e)}"}

# 8. 批量删除重复数据（新增）
@app.post("/api/batch_clear_duplicates")
@app.post("/batch_clear_duplicates")
async def batch_clear_duplicates_api(request: Request):
    data = await request.json()
    upload_batch = data.get("upload_batch")
    if not upload_batch:
        return {"code": 400, "msg": "请指定要清理的批次"}
    try:
        check_sql = "SELECT COUNT(*) as cnt FROM opinion WHERE upload_batch = ? AND review_status != 1"
        result = query_db(check_sql, [upload_batch], fetch_all=False)
        if result and result["cnt"] > 0:
            return {"code": 400, "msg": f"该批次还有{result['cnt']}条未复核数据，请先复核后再清理"}
        delete_sql = '''DELETE FROM opinion WHERE upload_batch = ? AND id NOT IN (SELECT MIN(id) FROM opinion WHERE upload_batch = ? GROUP BY opinion_id)'''
        execute_db(delete_sql, [upload_batch, upload_batch])
        return {"code": 200, "msg": "重复数据清理完成"}
    except Exception as e:
        return {"code": 500, "msg": f"清理失败：{str(e)}"}

# 9. 关键词词库管理接口（新增）
@app.get("/api/keywords")
async def get_keywords_api():
    return {"code": 200, "data": keyword_extractor.keyword_dict}

@app.post("/api/keywords/update")
async def update_keywords_api(request: Request):
    data = await request.json()
    keyword_extractor.keyword_dict.update(data.get("keywords", {}))
    keyword_extractor.save_keywords()
    return {"code": 200, "msg": "词库更新成功"}

@app.get("/api/keywords/statistics")
async def get_keywords_statistics_api():
    stats = {"total_keywords": sum(len(v) for v in keyword_extractor.keyword_dict.values()), "by_class": {k: len(v) for k, v in keyword_extractor.keyword_dict.items()}}
    return {"code": 200, "data": stats}


# 9b. 规则冲突词库（方案 D：外置 JSON + 热加载）
@app.get("/api/rule_conflict_keywords")
async def get_rule_conflict_keywords_api():
    try:
        from rule_conflict_detector import get_rule_conflict_config_public

        return {"code": 200, "data": get_rule_conflict_config_public()}
    except Exception as e:
        logger.exception("get_rule_conflict_keywords failed")
        return {"code": 500, "msg": str(e)}


@app.post("/api/rule_conflict_keywords/reload")
async def reload_rule_conflict_keywords_api():
    try:
        from rule_conflict_detector import refresh_rule_conflict_keywords

        meta = refresh_rule_conflict_keywords()
        return {"code": 200, "msg": "冲突词库已热加载", "data": meta}
    except Exception as e:
        logger.exception("reload_rule_conflict_keywords failed")
        return {"code": 500, "msg": str(e)}


@app.post("/api/rule_conflict_keywords/update")
async def update_rule_conflict_keywords_api(request: Request):
    try:
        from rule_conflict_detector import save_rule_conflict_config

        data = await request.json()
        payload = data.get("config") if isinstance(data.get("config"), dict) else data
        if not isinstance(payload, dict):
            return {"code": 400, "msg": "需要 JSON 对象（conflicts + keyword_groups）"}
        meta = save_rule_conflict_config(payload)
        return {"code": 200, "msg": "冲突词库已保存并热加载", "data": meta}
    except ValueError as e:
        return {"code": 400, "msg": str(e)}
    except Exception as e:
        logger.exception("update_rule_conflict_keywords failed")
        return {"code": 500, "msg": str(e)}


# 10. 分类准确率统计接口（新增）
@app.get("/api/classification/accuracy")
async def get_classification_accuracy_api():
    accuracy = get_classification_accuracy()
    return {"code": 200, "data": accuracy}

@app.get("/api/classification/route_metrics")
async def get_classification_route_metrics_api(limit: int = 50):
    """最近 N 次批量分类的路由指标（gold_hit / conflict / llm_arbitrated 等）。"""
    try:
        from classify_metrics_logger import read_classify_route_metrics

        data = read_classify_route_metrics(limit=limit)
        return {"code": 200, "data": data, "total": len(data)}
    except Exception as e:
        logger.exception("get_classification_route_metrics failed")
        return {"code": 500, "msg": str(e)}


@app.get("/api/classification/logs")
async def get_classification_logs_api(limit: int = 100):
    try:
        if not os.path.exists(CLASSIFICATION_LOG_FILE):
            return {"code": 200, "data": []}
        with open(CLASSIFICATION_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
        return {"code": 200, "data": logs[-limit:]}
    except Exception as e:
        return {"code": 500, "msg": f"获取日志失败：{str(e)}"}

# 11. 健康检查（新增）
@app.get("/api/health")
async def health_check():
    try:
        query_db("SELECT 1")
        return {"code": 200, "data": {"status": "healthy", "database": "connected", "keywords_loaded": sum(len(v) for v in keyword_extractor.keyword_dict.values()), "timestamp": datetime.now().isoformat()}}
    except Exception as e:
        return {"code": 500, "data": {"status": "unhealthy", "error": str(e)}}


@app.get("/health")
async def health_check_alias():
    """兼容自动化与探活：/health 与 /api/health 等价。"""
    return await health_check()


@app.get("/api/health/detail")
async def health_detail_api():
    """详细的系统健康检查（数据库、回流积压、清洗库、失败审计、年度 CSV）。"""
    checks: Dict[str, Any] = {}

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute("PRAGMA integrity_check")
        db_integrity = c.fetchone()[0]
        c.execute("PRAGMA journal_mode")
        journal_mode = c.fetchone()[0]
        conn.close()
        checks["db"] = {
            "integrity_check": db_integrity,
            "journal_mode": journal_mode,
            "path": DB_PATH,
            "size_bytes": os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
        }
    except Exception as e:
        checks["db"] = {"error": str(e)}

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute(
            """
            SELECT COUNT(*) as cnt FROM opinion
            WHERE reflow_synced = 0 AND review_status = 1
            AND reviewed_at < datetime('now', '-1 day')
            """
        )
        stale_reflow = c.fetchone()[0]
        conn.close()
        checks["stale_reflow"] = stale_reflow
    except Exception as e:
        checks["stale_reflow"] = {"error": str(e)}

    from reflow_service import DATA_CLEAR_PATH

    try:
        n_clear = sum(1 for _ in open(DATA_CLEAR_PATH)) - 1 if DATA_CLEAR_PATH.exists() else 0
        checks["data_clear_rows"] = max(0, n_clear)
    except Exception:
        checks["data_clear_rows"] = -1

    try:
        n_fail = sum(1 for _ in open(REFLOW_FAILURES_PATH)) if REFLOW_FAILURES_PATH.exists() else 0
        checks["reflow_failures_count"] = n_fail
    except Exception:
        checks["reflow_failures_count"] = -1

    try:
        annual_files = list(Path(ANNUAL_DIR).glob("*.csv")) if Path(ANNUAL_DIR).exists() else []
        checks["annual_csv_files"] = len(annual_files)
        checks["annual_csv_paths"] = [str(f) for f in annual_files]
    except Exception as e:
        checks["annual_csv"] = {"error": str(e)}

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM opinion")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM opinion WHERE review_status = 1")
        reviewed = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM opinion WHERE reflow_synced = 0 AND review_status = 1")
        not_reflowed = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM opinion WHERE reflow_synced = 1")
        reflowed = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM opinion WHERE reflow_synced = 2")
        archived = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM opinion WHERE reflow_synced = -1")
        reflow_failed = c.fetchone()[0]
        conn.close()
        checks["stats"] = {
            "total_rows": total,
            "reviewed": reviewed,
            "not_reflowed_yet": not_reflowed,
            "reflow_synced_1": reflowed,
            "reflow_synced_2_archived": archived,
            "reflow_synced_minus1_failed": reflow_failed,
        }
    except Exception as e:
        checks["stats"] = {"error": str(e)}

    stale_val = checks.get("stale_reflow", 0)
    stale_ok = isinstance(stale_val, int) and stale_val == 0
    db_ok = "error" not in str(checks.get("db", {}))
    checks["status"] = "healthy" if stale_ok and db_ok else "degraded"

    return {"code": 200, "data": checks}


def _apply_yearly_archive_for_batch(upload_batch: str, reviewer: str) -> Dict[str, Any]:
    """批次内全部已复核时：对未归档行执行回流、写入年度 CSV、reflow_synced=2（2=已年度归档，防重复加权/重复操作计数）。"""
    check_sql = "SELECT COUNT(*) as cnt FROM opinion WHERE upload_batch = ? AND review_status != 1"
    check_result = query_db(check_sql, [upload_batch], fetch_all=False)
    if check_result and int(check_result.get("cnt") or 0) > 0:
        raise ValueError(f"该批次还有{int(check_result['cnt'])}条数据未复核，无法保存")
    rows_all = query_db(
        "SELECT * FROM opinion WHERE upload_batch = ? AND review_status = 1",
        [upload_batch],
    )
    if not rows_all:
        return {
            "reflowed": 0,
            "new_in_clear": 0,
            "overwritten_in_clear": 0,
            "synced_new": 0,
            "skipped_yearly": 0,
            "msg": "批次内无已复核数据，跳过归档。",
        }
    before = {str(r.get("opinion_id") or ""): int(r.get("reflow_synced") or 0) for r in rows_all}
    before.pop("", None)
    skipped_yearly = sum(1 for v in before.values() if v == 2)
    synced_new = len(before) - skipped_yearly
    reflow_n = new_c = over_c = 0
    from reflow_service import reflow_batch_rows

    already_kw = {oid for oid, v in before.items() if v >= 1}
    r = reflow_batch_rows(
        rows_all,
        keyword_extractor,
        reviewer=reviewer,
        trigger="yearly",
        skip_keyword_gold_for_ids=already_kw,
    )
    reflow_n = int(r.get("reflowed") or 0)
    new_c = int(r.get("new_in_clear") or 0)
    over_c = int(r.get("overwritten_in_clear") or 0)
    for row in rows_all:
        execute_db(
            "UPDATE opinion SET reflow_synced = 2 WHERE opinion_id = ?",
            [row["opinion_id"]],
        )
    _refresh_label_matcher_gold_cache()
    _write_yearly_csv_to_disk(upload_batch=upload_batch)
    msg = (
        f"已归档至年度数据表。本次新归档 {synced_new} 条，跳过此前已归档 {skipped_yearly} 条（已刷新年度 CSV）。"
        f" 本次回流 {reflow_n} 条，覆盖历史 {over_c} 条（清洗库新建 {new_c} 条）。"
    )
    return {
        "reflowed": reflow_n,
        "new_in_clear": new_c,
        "overwritten_in_clear": over_c,
        "synced_new": synced_new,
        "skipped_yearly": skipped_yearly,
        "msg": msg,
    }


def _write_yearly_csv_to_disk(upload_batch: Optional[str] = None) -> str:
    """写入年度汇总 CSV（优先使用人工复核后的一二级 + 系统提取关键词作三级）。"""
    wc = "WHERE review_status = 1"
    pr: List = []
    if upload_batch:
        wc += " AND upload_batch = ?"
        pr.append(upload_batch)
    summary_sql = f'''SELECT opinion_id as 舆情编号, original_text as 舆情中文, source as 渠道, create_time as 舆情时间,
        COALESCE(NULLIF(review_l1, ''), model_class) as 一级标签,
        COALESCE(NULLIF(review_l2, ''),
            CASE WHEN INSTR(model_keyword, ',') > 0 THEN TRIM(SUBSTR(model_keyword, 1, INSTR(model_keyword, ',') - 1))
            ELSE TRIM(model_keyword) END) as 二级标签,
        COALESCE(NULLIF(extracted_keywords, ''),
            CASE WHEN INSTR(model_keyword, ',') > 0 THEN TRIM(SUBSTR(model_keyword, INSTR(model_keyword, ',') + 1)) ELSE '' END) as 三级标签,
        COALESCE(country, '') as 国家, COALESCE(phone, '') as 手机号, COALESCE(vin, '') as VIN, COALESCE(car_model, '') as 车型,
        '已复核' as 复核状态, COALESCE(review_note, '未填写') as 复核备注,
        SUBSTR(create_time, 1, 4) as 年度, SUBSTR(create_time, 1, 7) as 年月,
        COALESCE(reviewer, '') as 复核人, COALESCE(reviewed_at, '') as 复核时间
        FROM opinion {wc} ORDER BY create_time DESC, opinion_id ASC'''
    data = query_db(summary_sql, pr)
    fieldnames = [
        "舆情编号", "舆情中文", "渠道", "舆情时间", "一级标签", "二级标签", "三级标签",
        "国家", "手机号", "VIN", "车型", "复核状态", "复核备注", "年度", "年月", "复核人", "复核时间",
    ]
    annual_dir = os.path.join(str(ANNUAL_DIR))
    os.makedirs(annual_dir, exist_ok=True)
    by_year: Dict[str, List] = defaultdict(list)
    for row in data:
        ys = str((row.get("年度") or ""))[:4]
        if len(ys) == 4 and ys.isdigit():
            by_year[ys].append(row)
    for year, rows in sorted(by_year.items(), key=lambda kv: kv[0]):
        with open(
            os.path.join(annual_dir, f"{year}.csv"), "w", newline="", encoding="utf-8-sig"
        ) as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    csv_path = os.path.join(annual_dir, "年度数据总和.CSV")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    return csv_path


# 12. 保存至年度数据表（归档 + 未回流条目的自动回流）
@app.post("/api/save_to_yearly_table")
@app.post("/save_to_yearly_table")
async def save_to_yearly_table_api(request: Request):
    data = await request.json()
    upload_batch = data.get("upload_batch")
    reviewer = (data.get("reviewer") or "").strip()
    if not upload_batch:
        return {"code": 400, "msg": "请指定要保存的批次！"}
    try:
        out = _apply_yearly_archive_for_batch(str(upload_batch), reviewer)
        return {
            "code": 200,
            "msg": out["msg"],
            "reflowed": out["reflowed"],
            "new_in_clear": out["new_in_clear"],
            "overwritten_in_clear": out["overwritten_in_clear"],
            "synced_new": out.get("synced_new", 0),
            "skipped_yearly": out.get("skipped_yearly", 0),
        }
    except ValueError as e:
        return {"code": 400, "msg": str(e)}
    except Exception as e:
        print(f"保存至年度数据表失败：{str(e)}")
        return {"code": 500, "msg": f"保存失败：{str(e)}"}


# 13. 导出年度数据CSV
@app.get("/api/export_yearly_csv")
@app.get("/export_yearly_csv")
async def export_yearly_csv_api(uploadBatch: Optional[str] = None):
    try:
        path = _write_yearly_csv_to_disk(upload_batch=uploadBatch)
        return FileResponse(path, filename="年度数据总和.CSV", media_type="text/csv")
    except Exception as e:
        print(f"导出CSV失败：{str(e)}")
        return {"code": 500, "msg": f"导出CSV失败：{str(e)}"}


@app.get("/api/list_annual_csv")
@app.get("/list_annual_csv")
async def list_annual_csv_api():
    """列出 data/annual 下按年归档的 CSV，供前端年度筛选等读取。"""
    try:
        ensure_runtime_dirs()
        years: List[str] = []
        files: List[Dict[str, str]] = []
        ad = Path(str(ANNUAL_DIR))
        if ad.is_dir():
            for p in sorted(ad.glob("[0-9][0-9][0-9][0-9].csv")):
                stem = p.stem
                if len(stem) == 4 and stem.isdigit():
                    years.append(stem)
                    files.append({"year": stem, "name": p.name})
        return {"code": 200, "msg": "ok", "data": {"years": sorted(set(years)), "files": files}}
    except Exception as e:
        logger.exception("list_annual_csv: %s", e)
        return {"code": 500, "msg": str(e), "data": {"years": [], "files": []}}


@app.get("/api/reflow_failures")
async def get_reflow_failures_api(limit: int = 50):
    """返回 reflow_failures.jsonl 中最近 N 条回流失败记录（默认 50）。"""
    cap = max(1, min(int(limit or 50), 200))
    return {"code": 200, "msg": "ok", "data": _read_reflow_failures(cap)}


def _write_confirm_reviews(
    reviews: List[dict],
    reviewer: str,
    with_reflow: bool,
    *,
    chunk: int = 100,
) -> Tuple[List[dict], int]:
    """确认复核核心：分块短事务写库，返回 (需回流的行, 实际确认条数)。

    分块提交（每 chunk 条一次事务）避免一次性大事务长时间持有写锁，
    使整批（200-300 条）确认能与 14B 分类等其他写操作并存。
    """
    now = datetime.now().isoformat(timespec="seconds")
    rows_for_reflow: List[dict] = []
    confirmed = 0
    items = [it for it in reviews if it and it.get("opinion_id")]
    step = max(1, int(chunk))
    for start in range(0, len(items), step):
        part = items[start : start + step]
        conn = sqlite3.connect(DB_PATH, timeout=60)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA busy_timeout=60000")
            cur.execute("BEGIN IMMEDIATE")
            for item in part:
                oid = item.get("opinion_id")
                if not oid:
                    continue
                cur.execute("SELECT * FROM opinion WHERE opinion_id = ?", [oid])
                row = cur.fetchone()
                if not row:
                    continue
                base = dict(row)
                v3_raw = base.get("v3_label_meta") or ""
                auto_l1, auto_l2 = "", ""
                try:
                    if v3_raw:
                        mj = json.loads(v3_raw) if isinstance(v3_raw, str) else {}
                        auto_l1 = str(mj.get("l1") or "")
                        auto_l2 = str(mj.get("l2") or "")
                except Exception:
                    pass
                raw_l1 = (item.get("review_l1") or base.get("review_l1") or auto_l1 or "").strip()
                if not raw_l1:
                    continue
                l1, l2 = _normalize_review_labels(
                    raw_l1,
                    item.get("review_l2") or base.get("review_l2") or auto_l2 or "",
                )
                note = item.get("review_note")
                if note is None:
                    note = base.get("review_note")
                text = base.get("original_text") or ""
                kws = keyword_extractor.extract_keywords(text)[:35]
                extracted = ",".join(kws)
                existing_l1 = (base.get("review_l1") or "").strip()
                existing_l2 = (base.get("review_l2") or "").strip()
                existing_reflow = int(base.get("reflow_synced") or 0)
                labels_changed = (l1 != existing_l1) or (l2 != existing_l2)
                reflow_synced_val = 0 if labels_changed else existing_reflow
                cur.execute(
                    """UPDATE opinion SET review_status=1, review_l1=?, review_l2=?, review_note=?,
                    extracted_keywords=?, reviewer=?, reviewed_at=?, review_l3='', reflow_synced=?
                    WHERE opinion_id=?""",
                    [l1, l2, note, extracted, reviewer or None, now, reflow_synced_val, oid],
                )
                confirmed += 1
                if with_reflow and labels_changed:
                    rows_for_reflow.append(
                        {
                            **base,
                            "review_l1": l1,
                            "review_l2": l2,
                            "review_note": note,
                            "extracted_keywords": extracted,
                            "reviewer": reviewer,
                            "reviewed_at": now,
                        }
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            raise
        conn.close()
    return rows_for_reflow, confirmed


@app.post("/api/confirm_review")
@app.post("/confirm_review")
async def confirm_review_api(request: Request):
    """确认复核：事务写入库表；可选立即回流；可选在本批次已全部已复核时自动年度归档（reflow_synced=2）。"""
    data = await request.json()
    reviews = data.get("reviews")
    if not reviews or not isinstance(reviews, list):
        return {"code": 400, "msg": "需要 reviews 数组"}
    reviewer = (data.get("reviewer") or "").strip()
    with_reflow = data.get("with_reflow", True)
    also_yearly = bool(data.get("also_yearly", False))
    try:
        rows_for_reflow, _confirmed = _write_confirm_reviews(reviews, reviewer, with_reflow)
    except Exception as e:
        logger.exception("confirm_review 事务失败: %s", e)
        return {"code": 500, "msg": f"复核保存失败：{e}"}

    reflowed = new_c = over_c = 0
    reflow_async = False
    yearly_extra = ""
    yearly_data: Dict[str, Any] = {}
    if with_reflow and rows_for_reflow:
        reflow_async = True
        _reflow_rows_background(
            rows_for_reflow, reviewer, "confirm", also_yearly=also_yearly, write_csv=True
        )
        msg = (
            f"复核成功，共 {len(rows_for_reflow)} 条；清洗库与金标回流已在后台执行，"
            "完成后将更新 reflow_synced；关键词已写入舆情库。"
        )
        if also_yearly:
            batches = list({str(r.get("upload_batch") or "") for r in rows_for_reflow if r.get("upload_batch")})
            batches = [b for b in batches if b]
            if len(batches) != 1:
                yearly_extra = (
                    f" 已跳过年度自动归档（本次勾选涉及 {len(batches)} 个导入批次，仅支持单批次一键归档）。"
                )
            else:
                ub = batches[0]
                pend = query_db(
                    "SELECT COUNT(*) as cnt FROM opinion WHERE upload_batch = ? AND review_status != 1",
                    [ub],
                    fetch_all=False,
                )
                pc = int(pend["cnt"] or 0) if pend else 0
                if pc > 0:
                    yearly_extra = (
                        f" 本批次尚有 {pc} 条未复核，年度归档将在后台跳过；"
                        "待批次全部复核后可再次「批量确认」或手动「保存至年度数据表」。"
                    )
                else:
                    yearly_extra = (
                        " 年度归档将在后台回流完成后自动尝试；请稍后刷新列表查看 reflow_synced 状态。"
                    )
    elif with_reflow:
        msg = "复核成功。未产生有效回流条目（请检查是否已选择一级标签）。"
    else:
        msg = "复核成功。已跳过即时回流；归档至年度表时将统一写入清洗库与统计。"

    out: Dict[str, Any] = {
        "code": 200,
        "msg": msg + yearly_extra,
        "reflowed": reflowed,
        "reflow_async": reflow_async,
        "new_in_clear": new_c,
        "overwritten_in_clear": over_c,
    }
    out.update(yearly_data)
    return out


def _effective_review_labels_from_row(row: dict) -> Tuple[str, str]:
    """从人工复核字段或 v3_label_meta 解析有效一二级（用于整批确认就绪判定）。"""
    rl1 = str(row.get("review_l1") or "").strip()
    rl2 = str(row.get("review_l2") or "").strip()
    if not rl1:
        v3_raw = row.get("v3_label_meta") or ""
        try:
            if v3_raw:
                mj = json.loads(v3_raw) if isinstance(v3_raw, str) else {}
                rl1 = str(mj.get("l1") or "").strip()
                if not rl2:
                    rl2 = str(mj.get("l2") or "").strip()
        except Exception:
            pass
    return rl1, rl2


def _batch_confirm_block_reason(l1: str, l2: str) -> Optional[str]:
    """未就绪原因：缺一级 / 业务类缺二级；None 表示可确认。"""
    if not l1:
        return "no_l1"
    l1c = canonicalize_l1_label(l1)
    if l1c and l1c != NON_ISSUE_L1 and not (l2 or "").strip():
        return "missing_l2"
    return None


_BATCH_CONFIRM_REASON_TEXT = {
    "no_l1": "缺一级标签（需先 14B 分类或人工填写）",
    "missing_l2": "缺二级标签（业务类须填写人工二级）",
}


def _batch_confirm_readiness(upload_batch: str) -> Dict[str, Any]:
    """分析导入批次整批确认就绪情况。"""
    rows = query_db(
        """SELECT opinion_id, original_text, review_l1, review_l2, review_note,
           v3_label_meta, review_status FROM opinion WHERE upload_batch = ?""",
        [upload_batch],
    )
    confirmable: List[dict] = []
    unreviewed: List[dict] = []
    already_confirmed = 0
    for r in rows:
        oid = str(r.get("opinion_id") or "").strip()
        if not oid:
            continue
        rs = int(r.get("review_status") or 0)
        if rs == 1:
            already_confirmed += 1
            continue
        l1, l2 = _effective_review_labels_from_row(r)
        reason = _batch_confirm_block_reason(l1, l2)
        if reason:
            unreviewed.append(
                {
                    "opinion_id": oid,
                    "review_status": rs,
                    "reason": reason,
                    "reason_text": _BATCH_CONFIRM_REASON_TEXT.get(reason, reason),
                    "original_text_preview": str(r.get("original_text") or "")[:120],
                }
            )
            continue
        l1n, l2n = _normalize_review_labels(l1, l2)
        confirmable.append(
            {
                "opinion_id": oid,
                "review_l1": l1n,
                "review_l2": l2n,
                "review_note": r.get("review_note"),
            }
        )
    return {
        "upload_batch": upload_batch,
        "total": len(rows),
        "confirmable_count": len(confirmable),
        "unreviewed_count": len(unreviewed),
        "already_confirmed_count": already_confirmed,
        "confirmable": confirmable,
        "unreviewed": unreviewed,
    }


def _collect_batch_reviews(upload_batch: str) -> Tuple[List[dict], int, int]:
    """收集某导入批次内所有"已可确认"的行。返回 (reviews, 可确认条数, 未就绪条数)。"""
    info = _batch_confirm_readiness(upload_batch)
    return info["confirmable"], info["confirmable_count"], info["unreviewed_count"]


@app.get("/api/confirm_review_batch/preview")
@app.get("/confirm_review_batch/preview")
async def confirm_review_batch_preview_api(upload_batch: str = ""):
    """整批确认前预览：返回未就绪（不可确认）条目，供前端提示并筛选列表。"""
    ub = str(upload_batch or "").strip()
    if not ub:
        return {"code": 400, "msg": "需要 upload_batch"}
    info = _batch_confirm_readiness(ub)
    ready = info["unreviewed_count"] == 0 and info["confirmable_count"] > 0
    all_done = info["unreviewed_count"] == 0 and info["confirmable_count"] == 0
    msg = "可以整批确认"
    if info["unreviewed_count"] > 0:
        msg = f"尚有 {info['unreviewed_count']} 条未就绪，请先完成分类或填写标签"
    elif all_done:
        msg = "该批次已全部复核完成"
    return {
        "code": 200,
        "msg": msg,
        "ready": ready,
        "all_done": all_done,
        **{k: info[k] for k in (
            "upload_batch", "total", "confirmable_count", "unreviewed_count",
            "already_confirmed_count", "unreviewed",
        )},
    }


@app.post("/api/confirm_review_batch")
@app.post("/confirm_review_batch")
async def confirm_review_batch_api(request: Request):
    """整批确认：对某导入批次内所有已可确认（已填或可回退到 v3 自动标签）的行一次性确认，
    不受复核表分页（≤20 条）限制；确认后回流并即时写入年度 CSV（部分复核也存档）。"""
    data = await request.json()
    upload_batch = str(data.get("upload_batch") or "").strip()
    reviewer = (data.get("reviewer") or "").strip()
    with_reflow = bool(data.get("with_reflow", True))
    also_yearly = bool(data.get("also_yearly", True))
    if not upload_batch:
        return {"code": 400, "msg": "需要 upload_batch"}

    readiness = _batch_confirm_readiness(upload_batch)
    reviews = readiness["confirmable"]
    unreviewed_count = readiness["unreviewed_count"]
    if unreviewed_count > 0:
        return {
            "code": 409,
            "msg": (
                f"该批次尚有 {unreviewed_count} 条未就绪（缺标签或未填二级），"
                "请先完成分类/人工填写后再整批确认。"
            ),
            "confirmed": 0,
            "unreviewed_count": unreviewed_count,
            "unreviewed": readiness["unreviewed"],
            "confirmable_count": readiness["confirmable_count"],
            "already_confirmed_count": readiness["already_confirmed_count"],
        }
    if not reviews:
        return {
            "code": 200,
            "msg": "该批次已全部复核完成，无需重复确认。",
            "confirmed": 0,
            "skipped_no_label": 0,
            "already_confirmed_count": readiness["already_confirmed_count"],
        }

    try:
        rows_for_reflow, confirmed = await asyncio.to_thread(
            _write_confirm_reviews, reviews, reviewer, with_reflow
        )
    except Exception as e:
        logger.exception("confirm_review_batch 事务失败: %s", e)
        return {"code": 500, "msg": f"整批确认保存失败：{e}"}

    reflow_async = False
    if with_reflow:
        # 即使本次没有"标签变更"的回流行（全部此前已回流），也要触发后台写年度 CSV，
        # 并在整批复核完成时升级 reflow_synced=2，确保数据完整存档。
        reflow_async = True
        _reflow_rows_background(
            rows_for_reflow,
            reviewer,
            "confirm_batch",
            also_yearly=also_yearly,
            write_csv=True,
            upload_batch=upload_batch,
        )
        msg = f"已整批确认 {confirmed} 条；清洗库回流与年度 CSV 写入已在后台执行，请稍后刷新查看 reflow_synced 状态。"
    else:
        msg = f"已整批确认 {confirmed} 条（未回流）。"

    return {
        "code": 200,
        "msg": msg,
        "confirmed": confirmed,
        "skipped_no_label": 0,
        "unreviewed_count": 0,
        "reflow_pending": len(rows_for_reflow),
        "reflow_async": reflow_async,
        "already_confirmed_count": readiness["already_confirmed_count"],
    }


@app.post("/api/preview_keywords")
async def preview_keywords_api(request: Request):
    data = await request.json()
    text = data.get("text") or ""
    kws = keyword_extractor.extract_keywords(str(text))[:40]
    return {"code": 200, "data": {"keywords": kws, "joined": ",".join(kws)}}

# 14. 压测报告生成（新增）
@app.get("/api/stress_test/report")
async def get_stress_test_report():
    try:
        health = await health_check()
        db_stats = query_db("SELECT COUNT(*) as total_records, SUM(CASE WHEN review_status = 1 THEN 1 ELSE 0 END) as reviewed_count, COUNT(DISTINCT upload_batch) as batch_count FROM opinion", fetch_all=False)
        accuracy = get_classification_accuracy()
        return {"code": 200, "data": {"timestamp": datetime.now().isoformat(), "version": "1.2.0", "system_health": health["data"], "database_stats": db_stats, "classification_accuracy": accuracy, "rate_limit_config": RATE_LIMIT_CONFIG}}
    except Exception as e:
        return {"code": 500, "msg": f"生成报告失败：{str(e)}"}

# ---------- V1.5 扩展：仪表盘、批量分类、金标回流、导出 ----------

def _dashboard_empty_data() -> Dict[str, Any]:
    return {
        "total": 0,
        "reviewed_count": 0,
        "archived_count": 0,
        "new_7d": 0,
        "l1_accuracy": None,
        "l2_accuracy": None,
        "l1_eval_count": 0,
        "l2_eval_count": 0,
        "l1_pie": [],
        "l2_top5": [],
        "l2_bar": [],
        "trend_7d": [],
        "trend_30d": [],
        "pending_review": 0,
    }


def _dashboard_stats_compute() -> Dict[str, Any]:
    """全局概览聚合：仅窄列拉取（不拉 original_text）；v3_label_meta 在 Python 侧容错解析。

    存量数据中 v3_label_meta 可能残缺/非标准 JSON；不在 SQL 层使用 json_extract/json_valid，
    避免驱动/版本差异导致整查询失败或前端 JSON 解析异常。单条解析失败时忽略该条 V3 字段，
    仍计入总量与日期趋势，模型的一级二级回退到 model_class/model_keyword（与优化前一致）。
    """
    from datetime import datetime, timedelta
    import math

    dash_sql = """SELECT create_time, reviewed_at, created_at, review_status,
        review_l1, review_l2, model_class, model_keyword, reflow_synced,
        v3_l1, v3_l2, v3_confidence, match_score, v3_label_meta
        FROM opinion
        WHERE create_time >= date('now', '-2 year')"""

    def _parse_v3_meta_fields(meta_raw: Any) -> Tuple[str, str, Optional[float]]:
        """返回 (j_l1, j_l2, confidence)；与旧版一致：仅当 JSON 成功解析为 dict 时 conf 有值（缺省 0）。"""
        if meta_raw is None:
            return "", "", None
        if isinstance(meta_raw, (bytes, bytearray)):
            try:
                meta_raw = meta_raw.decode("utf-8", errors="replace")
            except Exception:
                return "", "", None
        s = str(meta_raw).strip()
        if not s:
            return "", "", None
        try:
            j = json.loads(s)
        except (TypeError, ValueError, json.JSONDecodeError):
            return "", "", None
        if not isinstance(j, dict):
            return "", "", None
        try:
            l1 = str(j.get("l1") or "").strip()
            l2 = str(j.get("l2") or "").strip()
            conf = float(j.get("confidence") or 0)
            return l1, l2, conf
        except (TypeError, ValueError):
            try:
                l1 = str(j.get("l1") or "").strip()
                l2 = str(j.get("l2") or "").strip()
            except Exception:
                l1, l2 = "", ""
            return l1, l2, None

    def _dash_model_l1_l2(j_l1: str, j_l2: str, r: Dict[str, Any]) -> Tuple[str, str]:
        l1 = (j_l1 or "").strip()
        l2 = (j_l2 or "").strip()
        if not l1:
            l1 = str(r.get("model_class") or "").strip()
        if not l2:
            mk = str(r.get("model_keyword") or "").strip()
            l2 = mk.split(",", 1)[0].strip() if mk else ""
        return l1, l2

    try:
        rows = query_db(dash_sql, [])
    except Exception as e:
        logger.exception("dashboard_stats 查询失败: %s", e)
        return {
            "code": 500,
            "msg": str(e),
            "data": _dashboard_empty_data(),
        }

    l1_cnt: Counter = Counter()
    l2_cnt: Counter = Counter()
    trend_30_counter: Counter = Counter()
    pending = reviewed = archived = new_7d = 0
    l1_eval = l1_ok = 0
    l2_eval = l2_ok = 0
    total = len(rows)
    today = datetime.now().date()
    keys_7 = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    keys_30 = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    key30_set = set(keys_30)
    key7_set = set(keys_7)

    try:
        for r in rows:
            try:
                st = int(r.get("review_status") or 0)
            except (TypeError, ValueError):
                st = 0
            try:
                if int(r.get("reflow_synced") or 0) >= 2:
                    archived += 1
            except (TypeError, ValueError):
                pass

            try:
                vj1 = str(r.get("v3_l1") or "").strip()
                vj2 = str(r.get("v3_l2") or "").strip()
                conf: Optional[float] = None
                try:
                    if r.get("v3_confidence") is not None and str(r.get("v3_confidence")).strip() != "":
                        conf = float(r.get("v3_confidence"))
                except (TypeError, ValueError):
                    conf = None
                if conf is None:
                    try:
                        if r.get("match_score") is not None:
                            conf = float(r.get("match_score"))
                    except (TypeError, ValueError):
                        conf = None
                if not vj1 or not vj2 or conf is None:
                    pj1, pj2, pconf = _parse_v3_meta_fields(r.get("v3_label_meta"))
                    if not vj1:
                        vj1 = pj1
                    if not vj2:
                        vj2 = pj2
                    if conf is None:
                        conf = pconf
                ml1_raw, ml2 = _dash_model_l1_l2(vj1, vj2, r)
            except Exception:
                logger.debug("dashboard_stats 单条 v3 解析跳过 opinion 行", exc_info=True)
                conf, ml1_raw, ml2 = None, str(r.get("model_class") or "").strip(), ""
                mk = str(r.get("model_keyword") or "").strip()
                if ml2 == "" and mk:
                    ml2 = mk.split(",", 1)[0].strip()

            human_l1_raw = str(r.get("review_l1") or "").strip()
            human_l2 = str(r.get("review_l2") or "").strip()
            human_l1 = canonicalize_l1_label(human_l1_raw) if human_l1_raw else ""
            _, human_l2 = strip_non_issue_l2(human_l1, human_l2)
            ml1c = canonicalize_l1_label(ml1_raw) if ml1_raw else ""
            _, ml2 = strip_non_issue_l2(ml1c, ml2)

            display_l1_raw = human_l1_raw or ml1_raw
            display_l2 = human_l2 or ml2
            if display_l1_raw:
                l1 = canonicalize_l1_label(display_l1_raw)
                name = "销售服务类" if l1 == "服务类" else l1
                l1_cnt[name] += 1
            if display_l2:
                l2_cnt[display_l2] += 1

            if st == 1:
                reviewed += 1
                if human_l1_raw and ml1_raw:
                    l1_eval += 1
                    if human_l1 == ml1c:
                        l1_ok += 1
                if human_l1 in L2_EVAL_L1 and ml1c == human_l1 and human_l2 and ml2:
                    l2_eval += 1
                    if human_l2 == ml2:
                        l2_ok += 1

            if st == 2 or (st != 1 and conf is not None and conf < 0.52):
                pending += 1

            dkey = _stats_day_key(r.get("create_time"), r.get("reviewed_at"), r.get("created_at"))
            if dkey in key30_set:
                trend_30_counter[dkey] += 1
            if dkey in key7_set:
                new_7d += 1

        l1_pie = [{"name": k, "value": v} for k, v in l1_cnt.most_common()]
        l2_top5 = [{"name": k, "value": v} for k, v in l2_cnt.most_common(5)]
        trend_30 = [{"date": k, "count": trend_30_counter.get(k, 0)} for k in keys_30]
        trend_7 = [{"date": k, "count": trend_30_counter.get(k, 0)} for k in keys_7]
        l1_acc = round(l1_ok * 100.0 / l1_eval, 2) if l1_eval else None
        l2_acc = round(l2_ok * 100.0 / l2_eval, 2) if l2_eval else None
        if l1_acc is not None and (math.isnan(l1_acc) or math.isinf(l1_acc)):
            l1_acc = None
        if l2_acc is not None and (math.isnan(l2_acc) or math.isinf(l2_acc)):
            l2_acc = None

        return {
            "code": 200,
            "msg": "ok",
            "data": {
                "total": total,
                "reviewed_count": reviewed,
                "archived_count": archived,
                "new_7d": new_7d,
                "l1_accuracy": l1_acc,
                "l2_accuracy": l2_acc,
                "l1_eval_count": l1_eval,
                "l2_eval_count": l2_eval,
                "l1_pie": l1_pie,
                "l2_bar": l2_top5,
                "l2_top5": l2_top5,
                "trend_7d": trend_7,
                "trend_30d": trend_30,
                "pending_review": pending,
            },
        }
    except Exception as e:
        logger.exception("dashboard_stats 聚合失败: %s", e)
        return {
            "code": 500,
            "msg": str(e),
            "data": {
                "total": total,
                "reviewed_count": reviewed,
                "archived_count": archived,
                "new_7d": new_7d,
                "l1_accuracy": None,
                "l2_accuracy": None,
                "l1_eval_count": l1_eval,
                "l2_eval_count": l2_eval,
                "l1_pie": [],
                "l2_bar": [],
                "l2_top5": [],
                "trend_7d": [],
                "trend_30d": [],
                "pending_review": 0,
            },
        }


def _dashboard_cache_read() -> Optional[Dict[str, Any]]:
    """内存缓存命中则秒回；适度允许过期缓存避免突刺全量计算。"""
    with _dashboard_cache_lock:
        if _dashboard_cache_payload is None:
            return None
        age = time.time() - _dashboard_cache_ts
        if age > DASHBOARD_CACHE_TTL * 5:
            return None
        base = _dashboard_cache_payload
        if base.get("code") != 200 or not isinstance(base.get("data"), dict):
            return dict(base)
        data = dict(base["data"])
        data["from_cache"] = True
        data["cache_age_sec"] = round(age, 1)
        return {"code": 200, "msg": base.get("msg", "ok"), "data": data}


def _dashboard_stale_any() -> Optional[Dict[str, Any]]:
    """超时时仍可返回任意年龄的 200 缓存，避免概览全空白。"""
    with _dashboard_cache_lock:
        base = _dashboard_cache_payload
        if not base or base.get("code") != 200 or not isinstance(base.get("data"), dict):
            return None
        age = time.time() - _dashboard_cache_ts
        data = dict(base["data"])
        data["from_cache"] = True
        data["cache_age_sec"] = round(age, 1)
        data["stale_fallback"] = True
        return {"code": 200, "msg": "ok（缓存）", "data": data}


def _voc_dashboard_background_refresh_loop():
    time.sleep(2.0)
    global _dashboard_cache_payload, _dashboard_cache_ts
    while True:
        try:
            out = _dashboard_stats_compute()
            if out.get("code") == 200:
                with _dashboard_cache_lock:
                    _dashboard_cache_payload = out
                    _dashboard_cache_ts = time.time()
        except Exception:
            logger.exception("仪表盘后台刷新失败")
        time.sleep(max(35.0, DASHBOARD_CACHE_TTL))


threading.Thread(target=_voc_dashboard_background_refresh_loop, daemon=True, name="voc-dashboard-cache").start()


@app.get("/api/dashboard_stats")
@app.get("/dashboard_stats")
@rate_limit(
    "dashboard_stats",
    RATE_LIMIT_CONFIG["dashboard_stats"]["rate"],
    RATE_LIMIT_CONFIG["dashboard_stats"]["interval"],
)
async def dashboard_stats_api():
    global _dashboard_cache_payload, _dashboard_cache_ts
    hit = _dashboard_cache_read()
    if hit is not None:
        return hit
    try:
        out = await asyncio.wait_for(
            asyncio.to_thread(_dashboard_stats_compute),
            timeout=DASHBOARD_COMPUTE_TIMEOUT,
        )
        if out.get("code") == 200 and isinstance(out.get("data"), dict):
            with _dashboard_cache_lock:
                _dashboard_cache_payload = out
                _dashboard_cache_ts = time.time()
            data = dict(out["data"])
            data["from_cache"] = False
            data["cache_age_sec"] = 0.0
            return {"code": 200, "msg": out.get("msg", "ok"), "data": data}
        return out
    except asyncio.TimeoutError:
        logger.warning(
            "dashboard_stats 实时计算超时（>%ss），尝试返回缓存或占位",
            DASHBOARD_COMPUTE_TIMEOUT,
        )
        stale = _dashboard_stale_any()
        if stale is not None:
            stale["msg"] = "统计数据计算超时，暂显示缓存结果，后台仍在刷新"
            return stale
        return {
            "code": 503,
            "msg": "统计数据计算超时，请稍后刷新页面",
            "data": {**_dashboard_empty_data(), "timeout": True},
        }
    except Exception as e:
        logger.exception("dashboard_stats 线程调度失败: %s", e)
        stale = _dashboard_stale_any()
        if stale is not None:
            return stale
        return {"code": 500, "msg": str(e), "data": _dashboard_empty_data()}


@app.get("/api/get_monthly_overview")
async def api_get_monthly_overview(
    date_from: str = "",
    date_to: str = "",
    region: str = "all",
    nocache: Optional[int] = None,
):
    """按月聚合：产品质量类 vs 销售服务类（数据口径=服务类）。region: all | cn | rest"""
    try:
        df, dt = (date_from or "")[:10], (date_to or "")[:10]
        if len(df) < 10 or len(dt) < 10:
            return {"code": 400, "msg": "需要有效的 date_from、date_to（YYYY-MM-DD）", "data": {}}
        skip = nocache is not None
        data = await asyncio.to_thread(
            get_monthly_overview_data, query_db, date_from, date_to, region, skip
        )
        return {"code": 200, "msg": "ok", "data": data}
    except Exception as e:
        logger.exception("get_monthly_overview: %s", e)
        return {"code": 500, "msg": str(e), "data": {}}


@app.get("/api/get_monthly_subtag_trend")
async def api_get_monthly_subtag_trend(
    date_from: str = "",
    date_to: str = "",
    region: str = "all",
    l1: str = "",
    top_secondary: int = 1,
    sub_tag_rank: Optional[int] = None,
    nocache: Optional[int] = None,
):
    """双折线：两类核心二级按月趋势。l1 预留；sub_tag_rank / top_secondary=第 N 高频二级。"""
    try:
        df, dt = (date_from or "")[:10], (date_to or "")[:10]
        if len(df) < 10 or len(dt) < 10:
            return {"code": 400, "msg": "需要有效的 date_from、date_to（YYYY-MM-DD）", "data": {}}
        rank = int(sub_tag_rank if sub_tag_rank is not None else (top_secondary or 1))
        skip = nocache is not None
        data = await asyncio.to_thread(
            get_monthly_subtag_trend_data,
            query_db,
            date_from,
            date_to,
            region,
            l1,
            rank,
            skip,
        )
        return {"code": 200, "msg": "ok", "data": data}
    except Exception as e:
        logger.exception("get_monthly_subtag_trend: %s", e)
        return {"code": 500, "msg": str(e), "data": {}}


@app.get("/api/get_top_subtag_monthly")
async def api_get_top_subtag_monthly(
    date_from: str = "",
    date_to: str = "",
    region: str = "all",
    top_n: int = 8,
    nocache: Optional[int] = None,
):
    """Top N 二级标签按月分布。"""
    try:
        df, dt = (date_from or "")[:10], (date_to or "")[:10]
        if len(df) < 10 or len(dt) < 10:
            return {"code": 400, "msg": "需要有效的 date_from、date_to（YYYY-MM-DD）", "data": {}}
        skip = nocache is not None
        data = await asyncio.to_thread(
            get_top_subtag_monthly_data,
            query_db,
            date_from,
            date_to,
            region,
            int(top_n or 8),
            skip,
        )
        return {"code": 200, "msg": "ok", "data": data}
    except Exception as e:
        logger.exception("get_top_subtag_monthly: %s", e)
        return {"code": 500, "msg": str(e), "data": {}}


@app.get("/api/get_single_issue_trend")
async def api_get_single_issue_trend(
    date_from: str = "",
    date_to: str = "",
    region: str = "all",
    l1: str = "",
    l2: str = "",
    keyword: str = "",
):
    """精准问题月度趋势：只读聚合，不修改任何业务数据。"""
    try:
        df, dt = (date_from or "")[:10], (date_to or "")[:10]
        if len(df) < 10 or len(dt) < 10:
            return {"code": 400, "msg": "需要有效的 date_from、date_to（YYYY-MM-DD）", "data": {}}
        data = await asyncio.to_thread(
            get_single_issue_trend_data,
            query_db,
            date_from,
            date_to,
            region,
            l1,
            l2,
            keyword,
        )
        return {"code": 200, "msg": "ok", "data": data}
    except Exception as e:
        logger.exception("get_single_issue_trend: %s", e)
        return {"code": 500, "msg": str(e), "data": {}}


@app.get("/api/get_opinion_summary")
async def api_get_opinion_summary(
    date_from: str = "",
    date_to: str = "",
    region: str = "all",
    period: str = "month",
    limit: int = 220,
):
    """只读：用本地 Qwen2.5 14B 生成图表解读/周报月报文案，不修改任何业务数据。"""
    try:
        df, dt = (date_from or "")[:10], (date_to or "")[:10]
        if len(df) < 10 or len(dt) < 10:
            return {"code": 400, "msg": "需要有效的 date_from、date_to（YYYY-MM-DD）", "data": {}}
        where = [
            "review_status = 1",
            "substr(REPLACE(REPLACE(TRIM(COALESCE(NULLIF(create_time,''), NULLIF(reviewed_at,''), NULLIF(created_at,''))), '/', '-'), '.', '-'), 1, 10) >= ?",
            "substr(REPLACE(REPLACE(TRIM(COALESCE(NULLIF(create_time,''), NULLIF(reviewed_at,''), NULLIF(created_at,''))), '/', '-'), '.', '-'), 1, 10) <= ?",
        ]
        params: List[Any] = [df, dt]
        r = (region or "all").strip().lower()
        cn_cond = (
            "TRIM(IFNULL(country,'')) = '' OR IFNULL(country,'') LIKE '%中国%' OR "
            "UPPER(TRIM(IFNULL(country,''))) IN ('CN','CHINA')"
        )
        if r in ("cn", "china", "中国"):
            where.append(f"({cn_cond})")
        elif r in ("rest", "abroad", "海外", "非中国"):
            where.append(f"(TRIM(IFNULL(country,'')) != '' AND NOT ({cn_cond}))")
        lim = max(20, min(int(limit or 220), 500))
        sql = f"""SELECT opinion_id, original_text, create_time, reviewed_at, country,
            review_l1, review_l2, model_class, model_keyword, v3_label_meta
            FROM opinion
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(reviewed_at, create_time, created_at, '') DESC
            LIMIT ?"""
        params.append(lim)
        rows = query_db(sql, params)
        if not isinstance(rows, list):
            rows = []
        try:
            from qwen_ollama import summarize_opinions

            summary = await asyncio.to_thread(
                summarize_opinions,
                rows,
                period=f"{period}:{df}~{dt}",
                region=region,
            )
        except Exception as e:
            logger.exception("get_opinion_summary qwen failed: %s", e)
            l2_counter: Counter = Counter()
            for row in rows:
                l2 = (row.get("review_l2") or row.get("model_keyword") or "").split(",", 1)[0].strip()
                if l2:
                    l2_counter[l2] += 1
            top = [{"label": k, "count": v, "analysis": "高频问题，建议结合原文复核原因。"} for k, v in l2_counter.most_common(8)]
            summary = {
                "summary": f"{df} 至 {dt} 区域 {region} 共纳入 {len(rows)} 条已复核舆情。",
                "top_issues": top,
                "risks": ["本地模型摘要暂不可用，已返回规则统计摘要。"],
                "actions": ["优先跟进 Top 二级问题并复核责任归因。"],
                "ppt_text": f"{df} 至 {dt} VOC 舆情共 {len(rows)} 条，Top 问题为 " + "、".join([x["label"] for x in top[:5]]) + "。",
                "total": len(rows),
                "period": f"{period}:{df}~{dt}",
                "region": region,
                "cache_hit": False,
            }
        return {"code": 200, "msg": "ok", "data": summary}
    except Exception as e:
        logger.exception("get_opinion_summary: %s", e)
        return {"code": 500, "msg": str(e), "data": {}}


@app.post("/api/batch_classify")
@app.post("/batch_classify")
async def batch_classify_api(request: Request):
    """对批次或 opinion_id 列表分类；大批量默认异步任务，避免 HTTP 阻塞超时。"""
    try:
        from voc_classifier_service import run_batch_classify
    except ImportError as e:
        return {"code": 500, "msg": f"分类模块不可用: {e}"}
    data = await request.json()
    batch = data.get("upload_batch")
    ids = data.get("opinion_ids")
    use_llm = bool(data.get("use_llm", os.environ.get("VOC_CLASSIFY_USE_QWEN", "1") == "1"))
    host = str(data.get("ollama_host") or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
    async_flag = data.get("async")
    n = _count_classify_targets(batch, ids)
    if n <= 0:
        return {"code": 400, "msg": "需要有效的 upload_batch 或 opinion_ids（库中无匹配行）", "updated": 0}
    # 14B 智能分类一律走后台线程，HTTP 立即返回 202，避免阻塞 Worker/前端长连接
    if use_llm:
        use_async = True
    elif async_flag is None:
        use_async = n >= CLASSIFY_ASYNC_MIN_ROWS
    else:
        use_async = bool(async_flag)
    if use_async:
        with classify_jobs_lock:
            if len(classify_jobs) > _CLASSIFY_JOBS_MAX:
                for k in sorted(classify_jobs, key=lambda x: classify_jobs[x].get("updated_at", 0))[:80]:
                    classify_jobs.pop(k, None)
            job_id = str(uuid.uuid4())
            classify_jobs[job_id] = {
                "status": "running",
                "total": n,
                "processed": 0,
                "msg": "排队中…",
                "updated_at": time.time(),
            }
        _start_classify_job(job_id, batch, ids, use_llm, host)
        return {
            "code": 202,
            "job_id": job_id,
            "msg": f"已提交异步分类任务（共 {n} 条），请轮询任务状态",
            "total": n,
        }
    return await asyncio.to_thread(
        run_batch_classify,
        DB_PATH,
        batch,
        ids,
        use_llm,
        host,
    )


@app.get("/api/batch_classify/status/{job_id}")
@app.get("/batch_classify/status/{job_id}")
async def batch_classify_status_api(job_id: str):
    with classify_jobs_lock:
        job = classify_jobs.get(job_id)
    if not job:
        return {"code": 404, "status": "unknown", "msg": "任务不存在或已过期"}
    st = job.get("status")
    if st == "done":
        return {"code": 200, "status": "done", "result": job.get("result")}
    if st == "error":
        return {"code": 200, "status": "error", "msg": job.get("msg", "分类失败")}
    return {
        "code": 200,
        "status": "running",
        "msg": job.get("msg", "处理中"),
        "total": job.get("total", 0),
        "processed": job.get("processed", 0),
    }


@app.post("/api/commit_gold_feedback")
@app.post("/commit_gold_feedback")
async def commit_gold_feedback_api(request: Request):
    """将已复核条目的 L2/L3 合并入金标 JSON。"""
    try:
        from voc_classifier_service import merge_gold_feedback
    except ImportError as e:
        return {"code": 500, "msg": str(e)}
    data = await request.json()
    items = data.get("items")
    if not items or not isinstance(items, list):
        return {"code": 400, "msg": "需要 items 数组"}
    return merge_gold_feedback(items)


def _taxonomy_options_sync(l1: str = "", l2: str = "") -> Dict[str, Any]:
    """线程内执行：标签字典读取 + 二级合并。"""
    try:
        from voc_classifier_service import (
            flatten_all_hierarchy_l2,
            load_taxonomy_json,
            merged_l2_whitelist_for_l1,
        )

        wl, by_l1_l2 = load_taxonomy_json()
        l1_list = list(CANONICAL_L1_LABELS)
        l1c = canonicalize_l1_label(l1) if l1 else ""
        l2_list = merged_l2_whitelist_for_l1(wl, l1c, db_path=DB_PATH) if l1c else []
        if l1c and not l2_list:
            l2_list = flatten_all_hierarchy_l2()
        l3_list = []
        if l1c and l2 and l1c in by_l1_l2 and l2 in by_l1_l2[l1c]:
            for block in by_l1_l2[l1c][l2]:
                if isinstance(block, dict) and block.get("canonical"):
                    l3_list.append(block["canonical"])
        return {
            "code": 200,
            "data": {"l1_list": l1_list, "l2_whitelist": l2_list, "l3_canonicals": l3_list},
        }
    except ImportError as e:
        logger.warning("taxonomy_options ImportError: %s", e)
        return {"code": 500, "msg": str(e), "data": {}}
    except Exception as e:
        logger.exception("taxonomy_options 失败: %s", e)
        try:
            from voc_classifier_service import flatten_all_hierarchy_l2

            fb = flatten_all_hierarchy_l2()
        except Exception:
            fb = []
        return {
            "code": 200,
            "msg": "已降级为全量体系二级（详情见服务端日志）",
            "data": {
                "l1_list": list(CANONICAL_L1_LABELS),
                "l2_whitelist": fb,
                "l3_canonicals": [],
            },
        }


def _get_l2_by_l1_sync(l1: str = "") -> Dict[str, Any]:
    try:
        from voc_classifier_service import (
            flatten_all_hierarchy_l2,
            load_taxonomy_json,
            merged_l2_whitelist_for_l1,
        )

        wl, _ = load_taxonomy_json()
        l1c = canonicalize_l1_label(l1) if (l1 or "").strip() else ""
        if not l1c:
            return {"code": 200, "msg": "ok", "data": {"l2_whitelist": [], "l1_canonical": ""}}
        l2_list = merged_l2_whitelist_for_l1(wl, l1c, db_path=DB_PATH)
        if not l2_list:
            l2_list = flatten_all_hierarchy_l2()
        return {"code": 200, "msg": "ok", "data": {"l2_whitelist": l2_list, "l1_canonical": l1c}}
    except ImportError as e:
        logger.warning("get_l2_by_l1 ImportError: %s", e)
        return {"code": 500, "msg": str(e), "data": {"l2_whitelist": [], "l1_canonical": ""}}
    except Exception as e:
        logger.exception("get_l2_by_l1 失败: %s", e)
        try:
            from voc_classifier_service import flatten_all_hierarchy_l2

            fb = flatten_all_hierarchy_l2()
        except Exception:
            fb = []
        l1c = canonicalize_l1_label(l1) if (l1 or "").strip() else ""
        return {
            "code": 200,
            "msg": str(e),
            "data": {"l2_whitelist": fb, "l1_canonical": l1c},
        }


def _taxonomy_options_batch_sync(cleaned: List[str]) -> Dict[str, Any]:
    try:
        from voc_classifier_service import flatten_all_hierarchy_l2, l2_whitelist_map_for_l1_list

        by_l1 = l2_whitelist_map_for_l1_list(cleaned, db_path=DB_PATH)
        fb = flatten_all_hierarchy_l2()
        for k in list(by_l1.keys()):
            if not by_l1.get(k) and fb:
                by_l1[k] = list(fb)
        return {"code": 200, "data": {"by_l1": by_l1, "l1_list": list(CANONICAL_L1_LABELS)}}
    except Exception as e:
        logger.exception("taxonomy_options_batch 失败: %s", e)
        try:
            from voc_classifier_service import flatten_all_hierarchy_l2

            fb = flatten_all_hierarchy_l2()
        except Exception:
            fb = []
        return {
            "code": 200,
            "msg": str(e),
            "data": {"by_l1": {k: list(fb) for k in cleaned}, "l1_list": list(CANONICAL_L1_LABELS)},
        }


@app.get("/api/v3/taxonomy_options")
@rate_limit("taxonomy", RATE_LIMIT_CONFIG["taxonomy"]["rate"], RATE_LIMIT_CONFIG["taxonomy"]["interval"])
async def taxonomy_options_api(l1: str = "", l2: str = ""):
    """返回金标二级白名单与指定 L1+L2 下的三级聚类 canonical 列表。"""
    return await asyncio.to_thread(_taxonomy_options_sync, l1, l2)


@app.get("/get_l2_by_l1")
@app.get("/api/get_l2_by_l1")
@rate_limit("taxonomy", RATE_LIMIT_CONFIG["taxonomy"]["rate"], RATE_LIMIT_CONFIG["taxonomy"]["interval"])
async def get_l2_by_l1_api(l1: str = ""):
    """按一级返回二级白名单（金标 ∪ 体系 ∪ 历史），与 taxonomy_options 数据源一致。"""
    return await asyncio.to_thread(_get_l2_by_l1_sync, l1)


@app.post("/api/v3/taxonomy_options_batch")
@rate_limit("taxonomy", RATE_LIMIT_CONFIG["taxonomy"]["rate"], RATE_LIMIT_CONFIG["taxonomy"]["interval"])
async def taxonomy_options_batch_api(request: Request):
    """一次请求返回多个人工一级下的二级白名单，减少复核列表页 N+1 接口调用。"""
    try:
        data = await request.json()
        l1_list = data.get("l1_list") or []
        if not isinstance(l1_list, list):
            return {"code": 400, "msg": "l1_list 须为数组", "data": {}}
        seen: set = set()
        cleaned: List[str] = []
        for x in l1_list[:120]:
            s = (str(x) if x is not None else "").strip()
            if s and s not in seen:
                seen.add(s)
                cleaned.append(s)
        return await asyncio.to_thread(_taxonomy_options_batch_sync, cleaned)
    except Exception as e:
        logger.exception("taxonomy_options_batch 请求解析失败: %s", e)
        return {"code": 500, "msg": str(e), "data": {}}


@app.get("/api/export_reviews_csv")
async def export_reviews_csv_api(
    uploadBatch: Optional[str] = None,
    reviewStatus: Optional[int] = None,
):
    """按条件导出当前复核库 CSV。"""
    wc = []
    pr = []
    if uploadBatch:
        wc.append("upload_batch = ?")
        pr.append(uploadBatch)
    if reviewStatus is not None:
        wc.append("review_status = ?")
        pr.append(reviewStatus)
    where_sql = "WHERE " + " AND ".join(wc) if wc else ""
    sql = f"""SELECT opinion_id, source, original_text, create_time, model_class, model_keyword,
        match_score, upload_batch, review_status, review_note, v3_label_meta, review_l1, review_l2, review_l3,
        extracted_keywords, reviewer, reviewed_at, reflow_synced
        FROM opinion {where_sql} ORDER BY create_time DESC"""
    data = query_db(sql, pr)
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=".csv", text=True)
    os.close(fd)
    if not data:
        fieldnames = ["opinion_id", "source", "original_text", "create_time", "model_class", "model_keyword",
                      "match_score", "upload_batch", "review_status", "review_note", "v3_label_meta",
                      "review_l1", "review_l2", "review_l3", "extracted_keywords", "reviewer", "reviewed_at", "reflow_synced"]
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
    else:
        fieldnames = list(data[0].keys())
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in data:
                w.writerow(row)
    return FileResponse(tmp, filename="voc_reviews_export.csv", media_type="text/csv")


# 启动服务
if __name__ == "__main__":
    print("=" * 50)
    print("舆情复核系统 V1.2 启动中...")
    print(f"数据库: {os.path.basename(DB_PATH)}")
    print(f"关键词词库: {KEYWORD_FILE}")
    print("=" * 50)
    _ensure_db_wal_mode()
    _start_pending_reflow_daemon()
    _reload = os.environ.get("VOC_UVICORN_RELOAD", "").strip().lower() in ("1", "true", "yes")
    _workers = max(1, int(os.environ.get("VOC_UVICORN_WORKERS", "1") or "1"))
    if _reload:
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
    else:
        uvicorn.run("main:app", host="0.0.0.0", port=8000, workers=_workers)
