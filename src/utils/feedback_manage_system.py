import streamlit as st

# 新增：Streamlit 版本兼容 - 适配 rerun/experimental_rerun（仅保留1处，放在最顶部）
if not hasattr(st, 'rerun'):
    st.rerun = st.experimental_rerun

import os
import time
import json
import re
import warnings
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import jieba
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ===================== 全局基础配置（仅执行一次） =====================
# 忽略无关警告，保证界面整洁
warnings.filterwarnings('ignore')
# 中文显示适配（Windows/macOS/Linux 通用，解决图表中文乱码）
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'PingFang SC', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.family'] = 'sans-serif'

# 结巴全局初始化
jieba.initialize()
# 统一 Excel 引擎
pd.set_option("io.excel.xlsx.writer", "openpyxl")

# ===================== 全局核心配置（无需修改，适配业务）=====================
# 数据存储根目录（自动创建在用户主目录）
ROOT_DIR = os.path.join(os.path.expanduser("~"), "新能源车企舆情数据")
UPLOAD_DIR = os.path.join(ROOT_DIR, "原始上传文件")
ARCHIVE_DIR = os.path.join(ROOT_DIR, "年度归档数据")
LOG_DIR = os.path.join(ROOT_DIR, "系统操作日志")
KEYWORD_DIR = os.path.join(ROOT_DIR, "关键词词库")
KEYWORD_FILE = os.path.join(KEYWORD_DIR, "舆情分类关键词.json")
TAG_MAPPING_FILE = os.path.join(KEYWORD_DIR, "tag_mapping.json")

# ===================== 智能自动化配置 =====================
ACCURACY_THRESHOLD = 98.0  # 自动化阈值
ACCURACY_HISTORY_FILE = os.path.join(LOG_DIR, "accuracy_history.json")
AUDIT_LOG_FILE = os.path.join(LOG_DIR, "audit_records.json")
AUDIT_SAMPLE_SIZE = 50  # 每次抽检数量
AUDIT_FREQUENCY = "weekly"  # 抽检频率：weekly 或 monthly

# 本地模型配置（可根据自己的 ollama 模型修改，无模型不影响关键词分类）
MODEL_NAME = "deepseek-r1"
MODEL_VERSION = "Deepseek-R1-Ollama-v1.0"

# CSV 上传必填列（舆情系统导出的列名，不可修改）
REQUIRED_COLS = ["舆情时间", "舆情中文"]
# 列名映射（转为系统内部统一列名）
FIELD_MAP = {
    "舆情时间": "反馈时间",
    "舆情中文": "反馈内容"
}

# 5 类舆情分类（全流程同步，含体验需求）
FEEDBACK_TYPES = ["产品质量问题", "销售服务问题", "产品咨询", "体验需求", "非问题"]

# 系统日志配置
LOG_FILE = os.path.join(LOG_DIR, "操作日志.csv")
LOG_COLS = ["操作时间", "操作类型", "操作内容", "操作人"]

# 关键词提取 6 重过滤配置（彻底解决无效词问题）
STOP_WORDS = ["的", "了", "是", "我", "你", "他", "在", "和", "有", "就", "不", "这", "那", "及", "与", "等", "呢", "吗", "吧"]
GENERAL_WORDS = ["汽车", "新能源", "车企", "车辆", "车子", "车", "品牌", "公司", "厂家", "用户", "客户", "车主"]
KEYWORD_TOP_N = 20  # 人工修正后提取 TOP20 关键词
MIN_WORD_LEN = 2    # 过滤单字关键词
MIN_WORD_FREQ = 2   # 过滤词频 < 2 的偶然词汇

# 正则过滤规则
PATTERN_NUM = re.compile(r'^[0-9a-zA-Z]+$')  # 纯数字 / 字母 / 数字字母组合
PATTERN_SYMBOL = re.compile(r'[^\u4e00-\u9fa5a-zA-Z0-9]')  # 非中文字母数字的特殊符号

# ===================== 系统初始化（自动创建文件夹 / 日志 / 词库 / 映射表）=====================
def init_folders():
    """初始化系统所需文件夹，不存在则自动创建"""
    folders = [ROOT_DIR, UPLOAD_DIR, ARCHIVE_DIR, LOG_DIR, KEYWORD_DIR]
    for folder in folders:
        if not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)
    
    # 初始化操作日志文件
    if not os.path.exists(LOG_FILE):
        pd.DataFrame(columns=LOG_COLS).to_csv(LOG_FILE, index=False, encoding="utf-8-sig")
    else:
        try:
            temp_df = pd.read_csv(LOG_FILE, encoding="utf-8-sig", nrows=1)
            if not all(col in temp_df.columns for col in LOG_COLS):
                pd.DataFrame(columns=LOG_COLS).to_csv(LOG_FILE, index=False, encoding="utf-8-sig")
        except:
            pd.DataFrame(columns=LOG_COLS).to_csv(LOG_FILE, index=False, encoding="utf-8-sig")
    
    # 初始化 5 类关键词词库（含预设关键词，首次使用自动创建）
    if not os.path.exists(KEYWORD_FILE):
        init_keywords = {
            "产品质量问题": ["电池", "续航", "掉电", "虚电", "充电", "故障", "异响", "抖动", "失灵", "卡顿",
                            "黑屏", "死机", "漏风", "漏水", "跑偏", "刹车", "轮胎", "爆胎", "开裂", "生锈"],
            "销售服务问题": ["服务", "销售", "售后", "门店", "顾问", "态度", "效率", "响应", "跟进", "处理",
                            "投诉", "反馈", "等待", "排队", "预约", "安装", "维修", "保养", "交付", "延期"],
            "产品咨询": ["咨询", "疑问", "请问", "如何", "怎么", "能否", "是否", "费用", "价格", "政策",
                        "活动", "权益", "保修", "配置", "参数", "提车", "续航", "充电", "功能", "使用方法"],
            "体验需求": ["体验", "优化", "建议", "希望", "期望", "改进", "提升", "增加", "添加", "完善",
                        "简化", "便捷", "人性化", "交互", "界面", "操作", "体验感", "舒适度", "便捷性", "易用性"],
            "非问题": ["感谢", "表扬", "满意", "好评", "不错", "很好", "优秀", "专业", "贴心", "高效",
                      "到位", "及时", "靠谱", "推荐", "支持", "认可", "喜欢", "方便", "实用", "点赞"]
        }
        with open(KEYWORD_FILE, "w", encoding="utf-8") as f:
            json.dump(init_keywords, f, ensure_ascii=False, indent=4)
    
    # 初始化标签映射表
    init_tag_mapping()

def init_tag_mapping():
    """初始化标签映射表，如果不存在则创建"""
    if not os.path.exists(TAG_MAPPING_FILE):
        # 预设一些常见的映射关系
        default_mapping = {
            # 一级标签映射
            "产品质量": "产品质量问题",
            "质量": "产品质量问题",
            "销售服务": "销售服务问题",
            "售后服务": "销售服务问题",
            "售前服务": "销售服务问题",
            "咨询": "产品咨询",
            "体验需求": "体验需求",
            "非问题": "非问题",
            
            # 二级标签映射
            "电池问题": "产品质量问题",
            "续航问题": "产品质量问题",
            "充电问题": "产品质量问题",
            "故障问题": "产品质量问题",
            "销售态度": "销售服务问题",
            "售后态度": "销售服务问题",
            "交付延期": "销售服务问题",
            "价格咨询": "产品咨询",
            "配置咨询": "产品咨询",
            "功能咨询": "产品咨询",
            "体验优化": "体验需求",
            "功能建议": "体验需求",
            "表扬": "非问题",
            "感谢": "非问题"
        }
        with open(TAG_MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(default_mapping, f, ensure_ascii=False, indent=4)

def write_log(oper_type, oper_content, oper_person="产品总监"):
    """写入系统操作日志，记录所有关键操作"""
    try:
        if not os.path.exists(LOG_FILE):
            pd.DataFrame(columns=LOG_COLS).to_csv(LOG_FILE, index=False, encoding="utf-8-sig")
        new_log = pd.DataFrame([{
            "操作时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "操作类型": oper_type,
            "操作内容": oper_content,
            "操作人": oper_person
        }])
        new_log.to_csv(LOG_FILE, mode="a", header=False, index=False, encoding="utf-8-sig")
    except Exception as e:
        st.error(f"❌ 日志写入失败：{str(e)}")

# ===================== 标签映射器 =====================
class TagMapper:
    """客服标签到标准分类的映射器"""
    
    def __init__(self):
        self.mapping = {}
        self.load_mapping()
    
    def load_mapping(self):
        """加载映射表"""
        try:
            if os.path.exists(TAG_MAPPING_FILE):
                with open(TAG_MAPPING_FILE, "r", encoding="utf-8") as f:
                    self.mapping = json.load(f)
            else:
                self.mapping = {}
        except Exception as e:
            st.sidebar.warning(f"⚠️ 映射表加载失败：{str(e)}")
            self.mapping = {}
    
    def save_mapping(self):
        """保存映射表"""
        try:
            with open(TAG_MAPPING_FILE, "w", encoding="utf-8") as f:
                json.dump(self.mapping, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            st.error(f"❌ 映射表保存失败：{str(e)}")
            return False
    
    def map_to_standard(self, first_level_tag="", second_level_tag="", third_level_tag=""):
        """
        将客服的一、二、三级标签映射到标准一级分类
        优先级：三级 > 二级 > 一级，越具体越优先匹配
        """
        # 清理标签
        first = str(first_level_tag).strip() if first_level_tag else ""
        second = str(second_level_tag).strip() if second_level_tag else ""
        third = str(third_level_tag).strip() if third_level_tag else ""
        
        # 尝试精确匹配三级标签
        if third and third in self.mapping:
            return self.mapping[third]
        
        # 尝试匹配二级标签
        if second and second in self.mapping:
            return self.mapping[second]
        
        # 尝试匹配一级标签
        if first and first in self.mapping:
            return self.mapping[first]
        
        # 如果没有映射，返回 None（由后续关键词/模型处理）
        return None
    
    def learn_from_correction(self, original_tags, corrected_class):
        """从人工修正中学习映射关系"""
        if not original_tags or not corrected_class or corrected_class not in FEEDBACK_TYPES:
            return
        
        # 解析原始标签
        try:
            tags = json.loads(original_tags) if isinstance(original_tags, str) else original_tags
            first = tags.get("first", "")
            second = tags.get("second", "")
            third = tags.get("third", "")
            
            # 尝试学习三级标签
            if third and third not in self.mapping:
                self.mapping[third] = corrected_class
                st.toast(f"📚 学习新映射：{third} → {corrected_class}")
            
            # 如果三级没有，尝试二级
            elif second and second not in self.mapping and not third:
                self.mapping[second] = corrected_class
                st.toast(f"📚 学习新映射：{second} → {corrected_class}")
            
            # 最后尝试一级
            elif first and first not in self.mapping and not second and not third:
                self.mapping[first] = corrected_class
                st.toast(f"📚 学习新映射：{first} → {corrected_class}")
            
            # 保存更新
            self.save_mapping()
        except Exception as e:
            st.warning(f"⚠️ 映射学习失败：{str(e)}")

# 创建全局映射器实例
tag_mapper = TagMapper()

# ===================== 文本清洗核心函数 =====================
def clean_text(series_data):
    """通用单列文本清洗：填充空值→转字符串→去首尾空格"""
    return series_data.fillna('').astype(str).str.strip()

def clean_dataframe(df):
    """通用整表文本清洗：遍历所有列执行单列清洗"""
    df_clean = df.copy(deep=True)
    for col in df_clean.columns:
        df_clean[col] = clean_text(df_clean[col])
    return df_clean

# ===================== 关键词词库核心功能 =====================
def load_keywords():
    """加载本地关键词词库，自动清洗去重"""
    try:
        with open(KEYWORD_FILE, "r", encoding="utf-8") as f:
            keywords = json.load(f)
        # 兜底：确保词库包含所有 5 类分类
        for type_name in FEEDBACK_TYPES:
            if type_name not in keywords or not isinstance(keywords[type_name], list):
                keywords[type_name] = []
        # 词库清洗：去空格 + 转小写 + 去重
        for type_name in keywords:
            clean_words = [word.strip().lower() for word in keywords[type_name] if word.strip()]
            keywords[type_name] = list(set(clean_words))
        return keywords
    except Exception as e:
        st.warning(f"⚠️ 词库加载失败，使用空词库：{str(e)}")
        return {type_name: [] for type_name in FEEDBACK_TYPES}

def save_keywords(keywords):
    """保存关键词词库到本地，自动清洗去重"""
    try:
        for type_name in keywords:
            clean_words = [word.strip().lower() for word in keywords[type_name] if word.strip()]
            keywords[type_name] = list(set(clean_words))
        with open(KEYWORD_FILE, "w", encoding="utf-8") as f:
            json.dump(keywords, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        st.error(f"❌ 词库保存失败：{str(e)}")
        return False

def cut_text(text):
    """结巴分词 + 6 重过滤，仅保留有效业务词汇"""
    if not text or text == '':
        return []
    text = str(text).strip().lower()
    text = PATTERN_SYMBOL.sub('', text)
    words = jieba.lcut(text)
    # 6 重过滤
    words = [word for word in words if
             word not in STOP_WORDS and
             word not in GENERAL_WORDS and
             len(word) >= MIN_WORD_LEN and
             word.strip() != '' and
             not PATTERN_NUM.match(word) and
             not PATTERN_SYMBOL.search(word)]
    return words

def keyword_match_classify(text, keywords):
    """关键词优先匹配分类"""
    if not text or text == '' or pd.isna(text):
        return None
    text_words = cut_text(text)
    if not text_words:
        return None
    # 计算各分类关键词匹配数
    match_score = {type_name: 0 for type_name in FEEDBACK_TYPES}
    for type_name, word_list in keywords.items():
        if not word_list:
            continue
        match_count = len([word for word in text_words if word in word_list])
        match_score[type_name] = match_count
    # 取匹配数最高的分类
    max_score = max(match_score.values())
    if max_score == 0:
        return None
    match_type = [k for k, v in match_score.items() if v == max_score][0]
    return match_type

def extract_and_update_keywords(df_corrected):
    """从人工修正数据中提取关键词，自动更新词库"""
    st.info(f"🔍 正在提取关键词（TOP {KEYWORD_TOP_N}，6 重过滤，词频≥{MIN_WORD_FREQ}）")
    keywords = load_keywords()
    df_corrected = clean_dataframe(df_corrected)
    
    # 按分类提取关键词
    for type_name in FEEDBACK_TYPES:
        type_df = df_corrected[df_corrected["最终分类结果"] == type_name]
        if type_df.empty:
            continue
        all_text = "".join(type_df[type_df["反馈内容"]!=""]["反馈内容"].tolist())
        if all_text == "":
            continue
        words = cut_text(all_text)
        if not words:
            continue
        # 词频统计 + 过滤低频次
        word_count = Counter(words)
        word_count = {word: count for word, count in word_count.items() if count >= MIN_WORD_FREQ}
        if not word_count:
            continue
        # 提取 TOP N 并追加到词库
        top_words = [word for word, count in word_count.most_common(KEYWORD_TOP_N)]
        keywords[type_name].extend(top_words)
    
    # 保存词库
    if save_keywords(keywords):
        total_key = sum([len(v) for v in keywords.values()])
        st.success(f"✅ 词库更新完成！总关键词 {total_key} 个")
        write_log("词库更新", f"提取有效关键词，总关键词数 {total_key} 个")
    else:
        st.error("❌ 词库更新失败")
    return keywords

# ===================== 本地大模型调用 =====================
def call_local_llm(feedback_content):
    """调用本地 ollama 模型分类"""
    if not feedback_content or pd.isna(feedback_content):
        return None
    # 严格分类 Prompt
    prompt = f"""
你是新能源车企舆情分类专家，严格按照【{FEEDBACK_TYPES[0]}、{FEEDBACK_TYPES[1]}、{FEEDBACK_TYPES[2]}、{FEEDBACK_TYPES[3]}、{FEEDBACK_TYPES[4]}】5 类分类。
要求：1. 仅输出分类结果文字；2. 必须选指定 5 类之一；3. 结合汽车行业特性。
舆情内容：{feedback_content}
分类结果：
"""
    try:
        result = subprocess.run(
            ["ollama", "run", MODEL_NAME, prompt],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="ignore"
        )
        if result.returncode == 0:
            res = result.stdout.strip()
            for _type in FEEDBACK_TYPES:
                if _type in res:
                    return _type
        return None
    except Exception as e:
        return None

# ===================== 并行分类处理（大幅提升效率）=====================
thread_local = threading.local()

def get_thread_local_model():
    """获取线程本地的ollama客户端"""
    if not hasattr(thread_local, "model_ready"):
        thread_local.model_ready = True
    return True

def parallel_classify_single(args):
    """单条分类的包装函数（用于并行处理）"""
    idx, content, has_keywords, keywords, use_mapping, original_tags = args
    
    classify_res = None
    classify_way = "未分类"
    
    # 1. 标签映射（最快）
    if use_mapping and original_tags:
        try:
            tags = json.loads(original_tags) if isinstance(original_tags, str) else original_tags
            mapped_class = tag_mapper.map_to_standard(
                tags.get("first", ""),
                tags.get("second", ""),
                tags.get("third", "")
            )
            if mapped_class and mapped_class in FEEDBACK_TYPES:
                return idx, mapped_class, "标签映射"
        except:
            pass
    
    # 2. 关键词匹配（次快）
    if not classify_res and has_keywords:
        kw_res = keyword_match_classify(content, keywords)
        if kw_res:
            return idx, kw_res, "关键词匹配"
    
    # 3. 模型兜底（最慢）
    model_res = call_local_llm(content)
    if model_res:
        return idx, model_res, "本地模型"
    else:
        return idx, "", "分类失败"

def parallel_classify(df, keywords, use_mapping=True, max_workers=10):
    """并行分类处理"""
    start_time = time.time()
    
    # 准备参数列表
    args_list = []
    for idx in df.index:
        content = df.loc[idx, "反馈内容"]
        original_tags = df.loc[idx, "原始标签"] if "原始标签" in df.columns else None
        args_list.append((idx, content, keywords is not None, keywords, use_mapping, original_tags))
    
    # 使用线程池并行处理
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_idx = {executor.submit(parallel_classify_single, args): args[0] 
                        for args in args_list}
        
        # 创建进度条
        progress_bar = st.progress(0, text="并行分类中...")
        completed = 0
        
        # 收集结果
        for future in as_completed(future_to_idx):
            idx, res, way = future.result()
            results[idx] = (res, way)
            
            completed += 1
            progress_bar.progress(completed / len(args_list), 
                                 text=f"处理进度：{completed}/{len(args_list)}")
    
    progress_bar.empty()
    elapsed = time.time() - start_time
    
    return results, elapsed

# ===================== CSV 文件校验 + 列名映射 =====================
def check_csv_format(df):
    """校验 CSV 格式，自动映射列名并清洗"""
    # 清洗列名
    df.columns = clean_text(pd.Series(df.columns))
    # 检查必填列
    missing_cols = [col for col in REQUIRED_COLS if col not in df.columns]
    if missing_cols:
        st.error(f"❌ CSV 校验失败：缺失必填列 → {','.join(missing_cols)}")
        st.info(f"✅ 要求必填列：{','.join(REQUIRED_COLS)}")
        write_log("数据上传", f"校验失败，缺失列：{','.join(missing_cols)}")
        return False
    # 清洗必填列
    for col in REQUIRED_COLS:
        df[col] = clean_text(df[col])
    # 列名映射
    df.rename(columns=FIELD_MAP, inplace=True)
    write_log("数据上传", "CSV 格式校验通过，已映射列名")
    return True

# ===================== 舆情数据归档 =====================
def archive_feedback_data(df_classified):
    """将最终分类数据归档，按年度生成 Excel"""
    try:
        # 创建副本避免修改原始数据
        df = df_classified.copy(deep=True)
        
        # 处理时间列
        df["反馈时间"] = pd.to_datetime(df["反馈时间"], errors="coerce")
        df = df.dropna(subset=["反馈时间"])
        if df.empty:
            st.warning("⚠️ 无有效舆情时间数据，归档失败！")
            return False
        
        # 提取年份和月份
        df.loc[:, "年"] = df["反馈时间"].dt.year
        df.loc[:, "月"] = df["反馈时间"].dt.month
        
        # 整表清洗
        df = clean_dataframe(df)
        
        # 确保所有复核相关列都有值
        for col in ["最终分类结果", "复核状态", "分类方式", "人工修正结果", "模型分类结果"]:
            if col in df.columns:
                df.loc[:, col] = df[col].astype(str).str.strip()
            else:
                df.loc[:, col] = ""
        
        # 按年份分组处理
        for year in df["年"].unique():
            year_df = df[df["年"] == year].copy()
            archive_file = os.path.join(ARCHIVE_DIR, f"{year} 年度舆情分类总表.xlsx")
            file_exists = os.path.exists(archive_file)
            
            # 按月份处理
            sheet_data = {}
            for month in year_df["月"].unique():
                month_df = year_df[year_df["月"] == month].copy()
                sheet_name = f"{year} 年 {month} 月"
                
                # 去重
                month_df_new = month_df.drop_duplicates(subset=["反馈时间", "反馈内容"], keep="last")
                month_df_new = month_df_new.drop(columns=["年", "月"], errors="ignore")
                
                # 合并历史数据
                if file_exists:
                    try:
                        month_df_old = pd.read_excel(archive_file, sheet_name=sheet_name, engine="openpyxl")
                        month_df_old = clean_dataframe(month_df_old)
                        month_df_old = month_df_old.drop_duplicates(subset=["反馈时间", "反馈内容"], keep="last")
                        
                        month_df_combined = pd.concat([month_df_old, month_df_new], ignore_index=True, join="outer")
                        month_df_combined = month_df_combined.drop_duplicates(subset=["反馈时间", "反馈内容"], keep="last")
                        
                        for col in ["最终分类结果", "复核状态", "人工修正结果"]:
                            if col in month_df_combined.columns:
                                month_df_combined.loc[:, col] = month_df_combined[col].fillna("")
                        
                        st.toast(f"📊 【{sheet_name}】已合并历史数据", icon="📈")
                    except:
                        month_df_combined = month_df_new
                else:
                    month_df_combined = month_df_new
                
                sheet_data[sheet_name] = month_df_combined
            
            # 写入 Excel
            writer_kwargs = {"engine": "openpyxl", "mode": "a" if file_exists else "w"}
            if file_exists:
                writer_kwargs["if_sheet_exists"] = "replace"
            
            with pd.ExcelWriter(archive_file, **writer_kwargs) as writer:
                for sheet_name, sheet_df in sheet_data.items():
                    sheet_df = clean_dataframe(sheet_df)
                    sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        st.success(f"✅ 数据归档完成！共 {len(df)} 条")
        write_log("数据归档", f"归档成功，数据量 {len(df)} 条")
        return True
    except Exception as e:
        st.error(f"❌ 数据归档失败：{str(e)}")
        write_log("数据归档", f"归档失败：{str(e)[:100]}")
        return False

# ===================== 数据可视化 =====================
def convert_time_dimension(df, dimension="日"):
    """按日/周/月聚合数据"""
    df_copy = df.copy(deep=True)
    df_copy["反馈时间"] = pd.to_datetime(df_copy["反馈时间"], errors="coerce")
    df_copy = df_copy.dropna(subset=["反馈时间", "最终分类结果"])
    
    if df_copy.empty:
        return pd.DataFrame(), "时间维度"
    
    if dimension == "日":
        df_copy.loc[:, "时间维度"] = df_copy["反馈时间"].dt.strftime("%Y-%m-%d")
    elif dimension == "周":
        def get_week_str(date):
            year = date.year
            week = date.isocalendar()[1]
            return f"{year} 年第 {week:02d} 周"
        df_copy.loc[:, "时间维度"] = df_copy["反馈时间"].apply(get_week_str)
    elif dimension == "月":
        df_copy.loc[:, "时间维度"] = df_copy["反馈时间"].dt.strftime("%Y-%m")
    
    agg_df = df_copy.groupby(["时间维度", "最终分类结果"])["反馈内容"].count().reset_index()
    agg_df.rename(columns={"反馈内容": "舆情数量"}, inplace=True)
    
    if not agg_df.empty:
        agg_df = agg_df.sort_values("时间维度").reset_index(drop=True)
    
    return agg_df, "时间维度"

def generate_feedback_chart(df, dimension="日", select_type="全部", time_start=None, time_end=None):
    """生成舆情分类趋势图"""
    try:
        df_copy = df.copy(deep=True)
        df_copy = clean_dataframe(df_copy)
        df_copy["反馈时间"] = pd.to_datetime(df_copy["反馈时间"], errors="coerce")
        
        if time_start is not None and time_end is not None:
            time_start = pd.to_datetime(time_start)
            time_end = pd.to_datetime(time_end) + timedelta(days=1)
            df_copy = df_copy[(df_copy["反馈时间"] >= time_start) & (df_copy["反馈时间"] <= time_end)]
        
        df_copy = df_copy.dropna(subset=["反馈时间", "最终分类结果"])
        
        if df_copy.empty:
            return None
        
        if select_type != "全部" and select_type in FEEDBACK_TYPES:
            df_copy = df_copy[df_copy["最终分类结果"] == select_type]
        
        if df_copy.empty:
            return None
        
        agg_df, time_col = convert_time_dimension(df_copy, dimension)
        
        if agg_df.empty:
            return None
        
        fig, ax = plt.subplots(figsize=(14, 7), dpi=100)
        categories = agg_df["最终分类结果"].unique()
        
        colors = plt.cm.Set3(np.linspace(0, 1, len(categories)))
        
        for i, cat in enumerate(categories):
            cat_df = agg_df[agg_df["最终分类结果"] == cat]
            ax.bar(cat_df[time_col], cat_df["舆情数量"], label=cat, 
                  alpha=0.8, color=colors[i], edgecolor="black", linewidth=0.5)
        
        ax.set_xlabel(f"{dimension} 维度", fontsize=12)
        ax.set_ylabel("舆情数量", fontsize=12)
        ax.set_title(f"舆情分类趋势图", fontsize=14, fontweight="bold")
        ax.legend(loc="upper right", fontsize=10)
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        plt.close(fig)
        
        return fig
    except Exception as e:
        st.error(f"❌ 趋势图生成失败：{str(e)}")
        return None

# ===================== 会话状态管理 =====================
def sync_business_data():
    """同步业务数据"""
    if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
        business_data = st.session_state.uploaded_data.copy(deep=True)
        business_data = clean_dataframe(business_data)
        st.session_state.uploaded_data = business_data
        return True
    return False

def on_editor_change_callback():
    """数据编辑器变更回调，自动更新复核状态"""
    try:
        # 获取编辑器当前数据
        if "_data_editor_review_simple" not in st.session_state:
            return
            
        editor_raw_data = st.session_state._data_editor_review_simple
        
        # 转换为DataFrame
        if isinstance(editor_raw_data, dict):
            edited_df = pd.DataFrame(editor_raw_data)
        elif isinstance(editor_raw_data, pd.DataFrame):
            edited_df = editor_raw_data
        else:
            return
        
        if edited_df.empty or "uploaded_data" not in st.session_state:
            return
            
        original_df = st.session_state.uploaded_data.copy(deep=True)
        modified = False
        
        # 遍历每一行
        for idx in edited_df.index:
            if idx >= len(original_df):
                continue
            
            # 获取当前行的人工修正结果（从编辑器中）
            current_correct = None
            if "人工修正结果" in edited_df.columns:
                current_correct = edited_df.loc[idx, "人工修正结果"]
            
            # 获取原始的人工修正结果
            original_correct = original_df.loc[idx, "人工修正结果"] if "人工修正结果" in original_df.columns else None
            
            # 调试信息
            print(f"行 {idx}: 当前={current_correct}, 原始={original_correct}")
            
            # 如果人工修正结果有变化（且不是空值）
            if current_correct != original_correct and pd.notna(current_correct) and str(current_correct).strip() != "":
                # 更新原始数据
                original_df.loc[idx, "人工修正结果"] = current_correct
                
                # 如果选择了有效分类，自动更新复核状态
                if current_correct in FEEDBACK_TYPES:
                    original_df.loc[idx, "复核状态"] = "已复核"
                    original_df.loc[idx, "最终分类结果"] = current_correct
                    
                    # 学习映射关系
                    if "原始标签" in original_df.columns:
                        original_tags = original_df.loc[idx, "原始标签"]
                        if original_tags and pd.notna(original_tags) and str(original_tags).strip() != "":
                            tag_mapper.learn_from_correction(original_tags, current_correct)
                    
                    modified = True
                    print(f"✅ 行 {idx} 已更新为已复核：{current_correct}")
                else:
                    # 如果选择了无效值，保持未复核状态
                    original_df.loc[idx, "复核状态"] = "未复核"
                    modified = True
                    print(f"⚠️ 行 {idx} 选择无效值：{current_correct}")
        
        if modified:
            # 保存更新后的数据
            st.session_state.uploaded_data = clean_dataframe(original_df)
            st.success("✅ 复核状态已更新")
            st.rerun()
            
    except Exception as e:
        st.warning(f"⚠️ 数据同步异常：{str(e)}")

# ===================== 获取统计数据 =====================
def get_statistics_data():
    """获取最新的统计数据（同时考虑当前数据和归档数据）"""
    total_count = 0
    reviewed_count = 0
    correct_count = 0
    
    # 1. 从当前上传数据中统计
    if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
        df = st.session_state.uploaded_data
        total_count += len(df)
        reviewed_count += len(df[df["复核状态"] == "已复核"])
        
        # 计算当前数据的准确率
        for idx in df.index:
            model_res = df.loc[idx, "模型分类结果"]
            manual_res = df.loc[idx, "人工修正结果"]
            final_res = df.loc[idx, "最终分类结果"]
            
            if manual_res in FEEDBACK_TYPES:
                if model_res == manual_res:
                    correct_count += 1
            else:
                if model_res == final_res and final_res in FEEDBACK_TYPES:
                    correct_count += 1
    
    # 2. 从归档数据中统计
    archived_count = 0
    archived_reviewed = 0
    archived_correct = 0
    
    # 读取年度归档文件
    if os.path.exists(ARCHIVE_DIR):
        for file in os.listdir(ARCHIVE_DIR):
            if file.endswith(".xlsx"):
                try:
                    # 读取Excel文件的所有sheet
                    excel_file = pd.ExcelFile(os.path.join(ARCHIVE_DIR, file), engine="openpyxl")
                    for sheet_name in excel_file.sheet_names:
                        df_archived = pd.read_excel(excel_file, sheet_name=sheet_name, engine="openpyxl")
                        if not df_archived.empty:
                            archived_count += len(df_archived)
                            if "复核状态" in df_archived.columns:
                                archived_reviewed += len(df_archived[df_archived["复核状态"] == "已复核"])
                            
                            # 计算归档数据的准确率（简化计算）
                            if "模型分类结果" in df_archived.columns and "最终分类结果" in df_archived.columns:
                                for _, row in df_archived.iterrows():
                                    if row.get("最终分类结果") in FEEDBACK_TYPES:
                                        if row.get("模型分类结果") == row.get("最终分类结果"):
                                            archived_correct += 1
                except Exception as e:
                    print(f"读取归档文件 {file} 失败：{str(e)}")
    
    # 3. 合并统计
    total_all = total_count + archived_count
    reviewed_all = reviewed_count + archived_reviewed
    correct_all = correct_count + archived_correct
    
    accuracy = round(correct_all / max(total_all, 1) * 100, 2) if total_all > 0 else 0
    
    return {
        "total_count": total_all,
        "reviewed_count": reviewed_all,
        "accuracy": accuracy,
        "keyword_count": sum(len(v) for v in load_keywords().values())
    }

def record_accuracy_history():
    """记录准确率历史"""
    try:
        stats = get_statistics_data()
        current_accuracy = stats["accuracy"]
        
        history = []
        if os.path.exists(ACCURACY_HISTORY_FILE):
            with open(ACCURACY_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        
        history.append({
            "timestamp": datetime.now().isoformat(),
            "accuracy": current_accuracy,
            "total_count": stats["total_count"],
            "reviewed_count": stats["reviewed_count"]
        })
        
        if len(history) > 30:
            history = history[-30:]
        
        with open(ACCURACY_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        
        # 使用 session_state 存储自动化状态
        if "auto_mode" not in st.session_state:
            st.session_state.auto_mode = False
            
        if current_accuracy >= ACCURACY_THRESHOLD and not st.session_state.auto_mode:
            st.session_state.auto_mode = True
            st.balloons()
            st.success(f"🎉 分类准确率已达到 {current_accuracy}%，系统已自动切换为自动化模式！")
        elif current_accuracy < ACCURACY_THRESHOLD and st.session_state.auto_mode:
            st.session_state.auto_mode = False
            st.warning(f"⚠️ 分类准确率降至 {current_accuracy}%，系统已退出自动化模式")
        
    except Exception as e:
        st.error(f"❌ 记录准确率历史失败：{str(e)}")

def generate_audit_sample(period="weekly"):
    """生成抽检样本"""
    try:
        if "uploaded_data" not in st.session_state or st.session_state.uploaded_data.empty:
            return None, -1
        
        df = st.session_state.uploaded_data
        
        now = datetime.now()
        if period == "weekly":
            start_date = now - timedelta(days=7)
        else:
            start_date = now - timedelta(days=30)
        
        df["反馈时间"] = pd.to_datetime(df["反馈时间"], errors="coerce")
        recent_df = df[df["反馈时间"] >= start_date]
        
        if len(recent_df) < AUDIT_SAMPLE_SIZE:
            sample_df = recent_df
        else:
            sample_df = recent_df.sample(n=AUDIT_SAMPLE_SIZE, random_state=int(time.time()))
        
        audit_record = {
            "timestamp": now.isoformat(),
            "period": period,
            "sample_size": len(sample_df),
            "sample_data": sample_df.to_dict('records'),
            "audit_status": "pending",
            "audit_results": []
        }
        
        audits = []
        if os.path.exists(AUDIT_LOG_FILE):
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                audits = json.load(f)
        
        audits.append(audit_record)
        
        if len(audits) > 10:
            audits = audits[-10:]
        
        with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(audits, f, ensure_ascii=False, indent=2)
        
        return sample_df, len(audits) - 1
    
    except Exception as e:
        st.error(f"❌ 生成抽检样本失败：{str(e)}")
        return None, -1

# ===================== 主程序 =====================
def main():
    # 系统初始化
    init_folders()
    
    st.set_page_config(
        page_title="舆情分类管理系统",
        page_icon="🚗",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # ===================== 标题区域 =====================
    st.title("🚗 新能源车企舆情分类管理系统")
    st.caption(f"本地模型：{MODEL_NAME} | 数据存储：{ROOT_DIR}")
    
    # ===================== 实时统计卡片 =====================
    # 添加自动刷新按钮和定时器
    col_refresh, col_status = st.columns([1, 3])
    with col_refresh:
        if st.button("🔄 刷新统计", use_container_width=True):
            st.rerun()
    with col_status:
        st.caption(f"最后更新：{datetime.now().strftime('%H:%M:%S')}")
    
    # 获取最新统计数据
    stats = get_statistics_data()
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            label="📊 累计处理反馈总数",
            value=stats["total_count"],
            delta=None
        )
    with col2:
        reviewed_pct = round(stats['reviewed_count']/max(stats['total_count'],1)*100,1)
        st.metric(
            label="✅ 已复核数量",
            value=stats["reviewed_count"],
            delta=f"{reviewed_pct}%"
        )
    with col3:
        st.metric(
            label="🎯 分类准确率",
            value=f"{stats['accuracy']}%",
            delta=None
        )
    with col4:
        st.metric(
            label="📚 关键词词库",
            value=stats["keyword_count"],
            delta=None
        )
    
    # ===================== 分类占比柱状图 =====================
    if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
        df_stats = st.session_state.uploaded_data.copy()
        
        if "最终分类结果" in df_stats.columns:
            class_counts = df_stats["最终分类结果"].value_counts()
            
            fig, ax = plt.subplots(figsize=(10, 4))
            colors = plt.cm.Blues(np.linspace(0.4, 0.8, len(class_counts)))
            
            bars = ax.bar(class_counts.index, class_counts.values, 
                         color=colors, edgecolor='black', linewidth=0.5)
            
            total = class_counts.sum()
            for bar, count in zip(bars, class_counts.values):
                height = bar.get_height()
                percentage = count/total*100
                ax.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                       f'{count}\n({percentage:.1f}%)', 
                       ha='center', va='bottom', fontsize=9)
            
            ax.set_ylabel('数量', fontsize=11)
            ax.set_title('舆情分类分布', fontsize=13, fontweight='bold')
            ax.tick_params(axis='x', rotation=45)
            ax.grid(axis='y', linestyle='--', alpha=0.3)
            
            plt.tight_layout()
            st.pyplot(fig)
            plt.close(fig)
    
    st.divider()
    
    # ===================== 侧边栏 =====================
    with st.sidebar:
        st.markdown("## ⚡ 快捷操作")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📤 上传", use_container_width=True):
                st.session_state.active_tab = 0
                st.rerun()
        with col2:
            if st.button("✏️ 复核", use_container_width=True):
                st.session_state.active_tab = 1
                st.rerun()
        
        col3, col4 = st.columns(2)
        with col3:
            if st.button("📊 图表", use_container_width=True):
                st.session_state.active_tab = 2
                st.rerun()
        with col4:
            if st.button("🔍 抽检", use_container_width=True):
                st.session_state.active_tab = 3
                st.rerun()
        
        st.divider()
        
        # 自动化状态
        with st.expander("🤖 智能自动化", expanded=True):
            auto_mode = st.session_state.get("auto_mode", False)
            if auto_mode:
                st.success(f"✅ 自动化模式\n准确率：{stats['accuracy']}%")
            else:
                st.warning(f"⚠️ 人工复核模式\n准确率：{stats['accuracy']}%\n目标：{ACCURACY_THRESHOLD}%")
            
            if st.button("🔍 发起抽检", use_container_width=True):
                sample_df, audit_idx = generate_audit_sample(AUDIT_FREQUENCY)
                if sample_df is not None:
                    st.session_state.audit_sample = sample_df
                    st.session_state.audit_idx = audit_idx
                    st.success(f"✅ 已生成抽检样本，共 {len(sample_df)} 条")
                    st.rerun()
        
        # 映射表管理
        with st.expander("📌 标签映射"):
            st.caption(f"规则数：{len(tag_mapper.mapping)}")
            if st.button("重新加载", use_container_width=True):
                tag_mapper.load_mapping()
                st.success("✅ 已重新加载")
    
    # ===================== 标签页切换 =====================
    if "active_tab" not in st.session_state:
        st.session_state.active_tab = 0
    
    tabs = ["📤 上传", "✏️ 复核", "📊 可视化", "🔍 抽检"]
    selected_tab = st.radio(
        "功能模块",
        tabs,
        index=st.session_state.active_tab,
        horizontal=True,
        label_visibility="collapsed"
    )
    
    tab_map = {tabs[0]: 0, tabs[1]: 1, tabs[2]: 2, tabs[3]: 3}
    st.session_state.active_tab = tab_map[selected_tab]
    
    # ===================== 标签页 1：上传 =====================
    if st.session_state.active_tab == 0:
        st.subheader("📤 上传 CSV 文件")
        
        uploaded_file = st.file_uploader("选择文件", type=["csv"], key="csv_upload")
        if uploaded_file is not None:
            try:
                # 读取文件
                try:
                    df = pd.read_csv(uploaded_file, encoding="utf-8")
                except UnicodeDecodeError:
                    df = pd.read_csv(uploaded_file, encoding="gbk")
                except:
                    df = pd.read_csv(uploaded_file, encoding="utf-8-sig")
                
                st.dataframe(df.head(10), use_container_width=True, height=200)
                
                if check_csv_format(df):
                    df = df.reset_index(drop=True)
                    
                    # 自动去重
                    existing_ids = set()
                    if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
                        if "舆情编号" in st.session_state.uploaded_data.columns:
                            existing_ids = set(st.session_state.uploaded_data["舆情编号"].astype(str).str.strip())
                    
                    duplicate_count = 0
                    if "舆情编号" in df.columns:
                        df["舆情编号"] = clean_text(df["舆情编号"])
                        new_ids = set(df["舆情编号"].astype(str).str.strip())
                        duplicate_ids = new_ids.intersection(existing_ids)
                        duplicate_count = len(duplicate_ids)
                        
                        if duplicate_count > 0:
                            df = df[~df["舆情编号"].astype(str).str.strip().isin(duplicate_ids)]
                            st.warning(f"⚠️ 自动去重：跳过 {duplicate_count} 条")
                    
                    # 保存原始标签
                    original_tags_list = []
                    for i in range(len(df)):
                        tags = {
                            "first": str(df.loc[i, "一级标签"]).strip() if "一级标签" in df.columns else "",
                            "second": str(df.loc[i, "二级标签"]).strip() if "二级标签" in df.columns else "",
                            "third": str(df.loc[i, "三级标签"]).strip() if "三级标签" in df.columns else ""
                        }
                        original_tags_list.append(json.dumps(tags, ensure_ascii=False))
                    
                    df["原始标签"] = original_tags_list
                    
                    # 保存文件
                    file_name = f"舆情数据_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
                    file_path = os.path.join(UPLOAD_DIR, file_name)
                    df.to_csv(file_path, index=False, encoding="utf-8-sig")
                    
                    # 初始化分类列
                    init_cols = ["模型分类结果", "分类时间", "模型版本", "复核状态", 
                                "人工修正结果", "分类方式", "最终分类结果"]
                    for col in init_cols:
                        if col not in df.columns:
                            if col == "复核状态":
                                df[col] = "未复核"
                            elif col == "模型版本":
                                df[col] = MODEL_VERSION
                            elif col == "分类方式":
                                df[col] = "未分类"
                            else:
                                df[col] = ""
                    
                    df = clean_dataframe(df)
                    
                    # 合并数据
                    existing_df = None
                    if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
                        existing_df = st.session_state.uploaded_data.copy(deep=True)
                        df = pd.concat([existing_df, df], ignore_index=True)
                    
                    st.session_state.uploaded_data = df.copy(deep=True)
                    st.session_state.file_name = file_name
                    
                    st.success(f"✅ 上传成功！新增 {len(df) - (len(existing_df) if existing_df is not None else 0)} 条")
                    
                    if st.button("➡️ 前往复核", use_container_width=True):
                        st.session_state.active_tab = 1
                        st.rerun()
                        
            except Exception as e:
                st.error(f"❌ 上传失败：{str(e)}")
    
    # ===================== 标签页 2：复核 =====================
    elif st.session_state.active_tab == 1:
        st.subheader("✏️ 人工复核")
        
        if "uploaded_data" not in st.session_state or st.session_state.uploaded_data.empty:
            st.warning("⚠️ 暂无数据，请先上传")
        else:
            sync_business_data()
            df = st.session_state.uploaded_data.copy(deep=True)
            keywords = load_keywords()
            has_keywords = sum(len(v) for v in keywords.values()) > 0
            
            # 操作按钮
            col1, col2 = st.columns([1, 1])
            with col1:
                btn_label = "🚀 自动分类" if has_keywords else "📱 模型分类"
                if st.button(btn_label, use_container_width=True):
                    with st.spinner("分类中..."):
                        results, cost_time = parallel_classify(df, keywords if has_keywords else None, 
                                                              max_workers=10)
                        
                        kw_count = 0
                        model_count = 0
                        fail_count = 0
                        for idx, (res, way) in results.items():
                            df.loc[idx, "模型分类结果"] = str(res) if res else ""
                            df.loc[idx, "分类方式"] = str(way)
                            df.loc[idx, "分类时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            df.loc[idx, "最终分类结果"] = df.loc[idx, "模型分类结果"]
                            
                            if way in ["标签映射", "关键词匹配"]:
                                kw_count += 1
                            elif way == "本地模型":
                                model_count += 1
                            elif way == "分类失败":
                                fail_count += 1
                        
                        st.success(f"✅ 完成！耗时 {cost_time:.1f}秒")
                        st.session_state.uploaded_data = clean_dataframe(df)
                        st.rerun()
            
            with col2:
                if st.button("💾 归档", use_container_width=True):
                    with st.spinner("归档中..."):
                        df_final = df.copy()
                        for idx in df_final.index:
                            manual = df_final.loc[idx, "人工修正结果"]
                            model = df_final.loc[idx, "模型分类结果"]
                            if manual in FEEDBACK_TYPES:
                                df_final.loc[idx, "最终分类结果"] = manual
                            else:
                                df_final.loc[idx, "最终分类结果"] = model if model in FEEDBACK_TYPES else "分类失败"
                        
                        if archive_feedback_data(df_final):
                            df_corrected = df_final[df_final["复核状态"] == "已复核"]
                            if not df_corrected.empty:
                                extract_and_update_keywords(df_corrected)
                            
                            # 保存归档记录到 session_state
                            if "archived_batches" not in st.session_state:
                                st.session_state.archived_batches = []
                            
                            st.session_state.archived_batches.append({
                                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "count": len(df_final)
                            })
                            
                            # 清空当前编辑区域，但保留归档记录
                            st.session_state.uploaded_data = pd.DataFrame()
                            st.session_state.file_name = None
                            
                            st.success(f"✅ 归档完成！共 {len(df_final)} 条数据已保存至年度汇总")
                            st.rerun()
            
            # 数据编辑器
            st.info("📌 修改「人工修正结果」自动更新复核状态")
            
            all_cols = df.columns.tolist()
            display_cols = []
            
            for col in ["舆情编号", "反馈内容", "反馈时间", "国家", "VIN"]:
                if col in all_cols:
                    display_cols.append(col)
                elif col == "反馈内容" and "舆情中文" in all_cols:
                    display_cols.append("舆情中文")
            
            if "模型分类结果" in all_cols:
                display_cols.append("模型分类结果")
            
            for col in ["复核状态", "人工修正结果", "最终分类结果"]:
                if col in all_cols:
                    display_cols.append(col)
            
            display_cols = list(dict.fromkeys(display_cols))
            display_df = df[display_cols].copy()
            
            rename_map = {
                "反馈内容": "舆情原文", 
                "舆情中文": "舆情原文",
                "反馈时间": "舆情时间", 
                "VIN": "车辆VIN",
                "模型分类结果": "🤖 模型分类"
            }
            display_df.rename(columns=rename_map, inplace=True)
            
            # 配置列
            config = {}
            for col in display_df.columns:
                if col == "人工修正结果":
                    config[col] = st.column_config.SelectboxColumn(
                        "👤 人工修正", options=FEEDBACK_TYPES, width="medium"
                    )
                elif col in ["复核状态", "最终分类结果", "🤖 模型分类"]:
                    config[col] = st.column_config.TextColumn(col, disabled=True)
                else:
                    config[col] = st.column_config.TextColumn(col, disabled=True)
            
            edited_df = st.data_editor(
                display_df, 
                column_config=config, 
                use_container_width=True,
                hide_index=True, 
                height=500, 
                key="_data_editor_review_simple",
                on_change=on_editor_change_callback
            )
    
    # ===================== 标签页 3：可视化 =====================
    elif st.session_state.active_tab == 2:
        st.subheader("📊 可视化分析")
        
        if "uploaded_data" not in st.session_state or st.session_state.uploaded_data.empty:
            st.warning("⚠️ 暂无数据")
        else:
            df = st.session_state.uploaded_data.copy()
            df = clean_dataframe(df)
            df["反馈时间"] = pd.to_datetime(df["反馈时间"], errors="coerce")
            df = df.dropna(subset=["反馈时间", "最终分类结果"])
            
            if df.empty:
                st.warning("⚠️ 无有效数据")
            else:
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    dim = st.selectbox("维度", ["日", "周", "月"])
                with col2:
                    ftype = st.selectbox("分类", ["全部"] + FEEDBACK_TYPES)
                with col3:
                    start = st.date_input("开始", df["反馈时间"].min().date())
                with col4:
                    end = st.date_input("结束", df["反馈时间"].max().date())
                
                fig = generate_feedback_chart(df, dim, ftype, start, end)
                if fig:
                    st.pyplot(fig)
                    
                    buf = BytesIO()
                    fig.savefig(buf, format="png", dpi=300)
                    buf.seek(0)
                    st.download_button("📥 导出图表", data=buf, 
                                     file_name=f"图表_{datetime.now().strftime('%Y%m%d')}.png",
                                     mime="image/png")
    
    # ===================== 标签页 4：抽检 =====================
    elif st.session_state.active_tab == 3:
        st.subheader("🔍 人工抽检")
        
        if "audit_sample" in st.session_state and st.session_state.audit_sample is not None:
            sample_df = st.session_state.audit_sample
            
            st.info(f"📋 抽检样本：{len(sample_df)} 条")
            
            audit_display = sample_df[["舆情编号", "反馈内容", "模型分类结果", "最终分类结果"]].copy()
            audit_display.rename(columns={
                "反馈内容": "舆情原文",
                "模型分类结果": "🤖 系统分类",
                "最终分类结果": "✅ 当前结果"
            }, inplace=True)
            audit_display["👤 抽检结果"] = "待定"
            
            config = {
                "舆情编号": st.column_config.TextColumn("编号", disabled=True, width="small"),
                "舆情原文": st.column_config.TextColumn("内容", disabled=True, width="large"),
                "🤖 系统分类": st.column_config.TextColumn("系统", disabled=True, width="medium"),
                "✅ 当前结果": st.column_config.TextColumn("当前", disabled=True, width="medium"),
                "👤 抽检结果": st.column_config.SelectboxColumn(
                    "结果", options=["正确", "错误", "待定"], default="待定", width="small"
                )
            }
            
            edited = st.data_editor(audit_display, column_config=config, 
                                   use_container_width=True, hide_index=True, height=400)
            
            if st.button("✅ 提交结果", use_container_width=True):
                correct = len(edited[edited["👤 抽检结果"] == "正确"])
                accuracy = correct / len(edited) * 100 if len(edited) > 0 else 0
                
                st.success(f"✅ 抽检准确率：{accuracy:.1f}%")
                
                if accuracy < 98:
                    st.warning("⚠️ 准确率低于98%，建议优化")
                    st.session_state.auto_mode = False
                
                # 记录抽检结果
                try:
                    audits = []
                    if os.path.exists(AUDIT_LOG_FILE):
                        with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                            audits = json.load(f)
                    
                    if hasattr(st.session_state, 'audit_idx') and st.session_state.audit_idx < len(audits):
                        audits[st.session_state.audit_idx]["audit_status"] = "completed"
                        audits[st.session_state.audit_idx]["audit_results"] = edited.to_dict('records')
                        audits[st.session_state.audit_idx]["audit_accuracy"] = accuracy
                        
                        with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
                            json.dump(audits, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    st.warning(f"⚠️ 保存抽检记录失败：{str(e)}")
                
                del st.session_state.audit_sample
                st.rerun()
        else:
            st.info("请在侧边栏发起抽检")

# ===================== 程序入口 =====================
if __name__ == "__main__":
    main()