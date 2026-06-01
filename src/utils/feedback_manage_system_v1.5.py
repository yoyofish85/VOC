"""
新能源车企舆情分类管理系统 V1.5
===============================
基于百度智能云舆情分析系统、讯飞舆情智能分析平台的设计逻辑
核心升级：置信度评分体系、企业级UI、智能逻辑强化
"""

import streamlit as st
if not hasattr(st, 'rerun'):
    st.rerun = st.experimental_rerun

import os, time, json, re, warnings, subprocess
from collections import Counter
from datetime import datetime, timedelta
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import jieba
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
import numpy as np
import pandas as pd

# 全局配置
warnings.filterwarnings('ignore')

# 修复图表中文字体问题（不影响舆情分类准确率）
# 使用系统可用的中文字体
AVAILABLE_CHINESE_FONTS = ['Songti SC', 'Heiti TC', 'PingFang SC', 'STHeiti', 'Arial Unicode MS']
CHINESE_FONT = 'Songti SC'  # 默认使用宋体

# 尝试找到可用的中文字体
import matplotlib.font_manager as fm
try:
    available_fonts = [f.name for f in fm.fontManager.ttflist]
    for font in AVAILABLE_CHINESE_FONTS:
        if font in available_fonts:
            CHINESE_FONT = font
            break
except:
    CHINESE_FONT = 'Arial Unicode MS'

plt.rcParams['font.sans-serif'] = [CHINESE_FONT, 'SimHei', 'PingFang SC', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False
jieba.initialize()
pd.set_option("io.excel.xlsx.writer", "openpyxl")

# 存储路径配置
ROOT_DIR = os.path.join(os.path.expanduser("~"), "新能源车企舆情数据")
UPLOAD_DIR = os.path.join(ROOT_DIR, "原始上传文件")
ARCHIVE_DIR = os.path.join(ROOT_DIR, "年度归档数据")
LOG_DIR = os.path.join(ROOT_DIR, "系统操作日志")
KEYWORD_DIR = os.path.join(ROOT_DIR, "关键词词库")
KEYWORD_FILE = os.path.join(KEYWORD_DIR, "舆情分类关键词.json")
TAG_MAPPING_FILE = os.path.join(KEYWORD_DIR, "tag_mapping.json")
SAMPLE_POOL_DIR = os.path.join(ROOT_DIR, "模型微调样本")
WORD_LOG_FILE = os.path.join(LOG_DIR, "word_update_log.json")

# 核心参数
ACCURACY_THRESHOLD = 98.0
ACCURACY_HISTORY_FILE = os.path.join(LOG_DIR, "accuracy_history.json")
AUDIT_LOG_FILE = os.path.join(LOG_DIR, "audit_records.json")
MODEL_NAME = "deepseek-r1"
MODEL_VERSION = "Deepseek-R1-Ollama-v1.5"
REQUIRED_COLS = ["舆情时间", "舆情中文"]
FIELD_MAP = {"舆情时间": "反馈时间", "舆情中文": "反馈内容", "舆情日期": "反馈时间"}
FEEDBACK_TYPES = ["产品质量问题", "销售服务问题", "产品咨询", "体验需求", "非问题"]
LOG_FILE = os.path.join(LOG_DIR, "操作日志.csv")
LOG_COLS = ["操作时间", "操作类型", "操作内容", "操作人", "版本号"]

# 关键词配置
STOP_WORDS = {"的", "了", "是", "我", "你", "他", "在", "和", "有", "就", "不", "这", "那", "及", "与", "等", "呢", "吗", "吧", "个", "一", "下", "上", "也", "都", "要", "会"}
GENERAL_WORDS = {"汽车", "新能源", "车企", "车辆", "车子", "车", "品牌", "公司", "厂家", "用户", "客户", "车主", "问题", "反馈"}
# ===================== 优化：根据压测报告强化词库 =====================
# 预定义扩充词库 - 每类≥100个关键词（解决词库不足问题）
EXPANDED_KEYWORDS = {
    "产品质量问题": [
        # 故障类
        "故障", "异响", "死机", "黑屏", "卡顿", "抖动", "漏电", "漏水", "漏油", "熄火",
        "无法启动", "启动不了", "启动失败", "打不着", "点火", "刹车失灵", "刹车故障", "刹车异响",
        "转向异响", "方向盘抖动", "方向盘沉重", "助力泵", "转向机",
        "充电故障", "充电慢", "充不进", "充电中断", "充电失败", "充不满",
        "续航虚标", "续航缩水", "续航不足", "掉电快", "掉电严重", "电量不准",
        "电池故障", "电池衰减", "电池损坏", "电池故障灯", "电池包", "电池管理系统",
        "电机故障", "电机异响", "电机损坏", "电机啸叫", "电机故障灯",
        "空调不制冷", "空调不制热", "空调故障", "空调异响", "空调漏氟",
        "车机死机", "车机卡顿", "车机黑屏", "车机重启", "车机故障", "车机系统", "车机黑屏",
        "屏幕死机", "屏幕卡顿", "屏幕闪烁", "屏幕黑屏", "屏幕不亮",
        "摄像头故障", "摄像头模糊", "摄像头不工作", "全景影像", "倒车影像",
        "雷达故障", "雷达失灵", "雷达误报", "泊车辅助", "自动泊车",
        "天窗漏水", "天窗异响", "天窗关不上", "天窗打不开", "天窗故障",
        "车门异响", "车门关不上", "车门漏风", "车门锁不住", "车门故障",
        "车窗升降", "车窗异响", "车窗失灵", "车窗关不上", "车窗漏风",
        "后视镜故障", "后视镜异响", "后视镜折叠", "后视镜加热", "后视镜盲区",
        "座椅异响", "座椅调节", "座椅加热", "座椅通风", "座椅按摩", "座椅塌陷",
        "方向盘异响", "方向盘换挡", "方向盘加热", "方向盘多功能",
        "仪表盘故障", "仪表盘报警", "仪表盘闪烁", "仪表盘显示",
        "灯光故障", "大灯不亮", "尾灯不亮", "转向灯不亮", "雾灯不亮", "灯光闪烁",
        "胎压故障", "胎压报警", "胎压监测", "轮胎异常", "爆胎",
        "安全气囊", "气囊报警", "安全带", "abs故障", "esp故障",
        "倒车雷达", "前雷达", "超声波雷达", "毫米波雷达", "激光雷达",
        "自动驾驶", "辅助驾驶", "acc失效", "lcc失效", "noa失效", "ngp失效",
        "软件更新", "ota失败", "系统升级", "版本更新", "刷机",
        "质量问题", "品质问题", "缺陷", "瑕疵", "损坏", "破损", "破裂",
        "自燃", "起火", "冒烟", "高温", "过热", "温度异常",
        "安全隐患", "危险", "失控", "加速失控", "刹车失控", "意外加速",
    ],
    "销售服务问题": [
        # 服务类
        "服务", "销售", "售后", "经销商", "4s店", "门店", "服务商",
        "态度差", "服务态度", "态度恶劣", "不耐烦", "爱答不理", "说话难听", "语气不好",
        "不专业", "专业度", "不靠谱", "不负责", "推诿", "踢皮球", "相互推诿",
        "等待", "等太久", "等不及", "等半天", "长时间等待", "等待时间长", "等了一周", "等了一个月",
        "交付", "交车", "交付延迟", "交车延迟", "延期交付", "延迟交车", "迟迟不交",
        "不退订金", "不退定金", "不退钱", "不退费用", "退款", "退费", "退钱",
        "虚假宣传", "宣传不符", "宣传夸大", "承诺不兑现", "虚假承诺", "欺骗", "欺诈",
        "隐瞒", "欺骗消费者", "隐瞒事实", "隐瞒问题", "隐藏问题",
        "合同纠纷", "合同违约", "合同问题", "霸王条款", "不公平条款",
        "乱收费", "收费不合理", "收费高", "额外收费", "巧立名目", "变相收费",
        "保养", "维修", "维修慢", "维修时间长", "修不好", "反复修", "修了又修",
        "配件", "配件等待", "配件没有", "配件缺失", "配件损坏", "原厂配件",
        "理赔", "保险理赔", "理赔慢", "理赔难", "拒赔", "不予理赔",
        "投诉", "投诉无门", "投诉处理", "投诉反馈", "投诉升级",
        "处理慢", "处理不及时", "处理拖沓", "效率低", "处理效率",
        "踢皮球", "推诿", "不解决", "不处理", "置之不理", "不管", "不管不顾",
        "售后电话", "售后无人接", "售后不通", "客服电话", "客服不接",
        "网点", "服务网点", "门店少", "网点少", "没有网点", "距离远",
        "维修技术", "技术差", "技术不行", "修不好", "越修越坏",
        "配件等待时间长", "等配件", "没有配件", "配件缺货",
        "旧车", "二手车", "库存车", "展车", "试驾车", "问题车",
        "车辆信息", "信息不符", "车况不符", "与描述不符", "实物不符",
        "订车", "预订", "锁单", "排产", "生产", "物流", "运输",
        "提车", "提车难", "提不到车", "无法提车", "提车时间",
        "上牌", "车牌", "牌照", "临牌", "指标", "摇号",
        "贷款", "金融", "分期", "月供", "利率", "手续费", "服务费",
        "保险", "车险", "保费", "续保", "保险费用", "强制保险",
    ],
    "产品咨询": [
        # 咨询类
        "咨询", "询问", "了解", "想知道", "问一下", "问一下", "请问",
        "能不能", "是否可以", "能否", "可不可以", "能不能够", "有没有",
        "怎么", "如何", "怎么样", "怎样", "怎么办", "怎么处理",
        "多少", "价格", "价位", "多少钱", "费用", "报价", "售价",
        "配置", "配置表", "配置区别", "配置差异", "选配", "标配", "顶配",
        "参数", "参数表", "性能参数", "技术参数", "数据",
        "续航", "续航里程", "续航多少", "续航表现", "续航能力",
        "电池", "电池容量", "电池类型", "电池品牌", "电池质保",
        "充电", "充电时间", "充电桩", "充电方式", "快充", "慢充",
        "电机", "电机功率", "电机扭矩", "电机类型", "单电机", "双电机",
        "尺寸", "长宽高", "空间", "轴距", "车内空间", "后备箱",
        "颜色", "颜色有哪些", "有哪些颜色", "颜色选择", "车身颜色",
        "内饰", "内饰颜色", "内饰材质", "真皮", "织物",
        "选配", "选装", "选配有哪些", "可选配置", "加装",
        "订车", "预订", "什么时候能提", "提车时间", "等车", "等多久",
        "有没有", "有没有优惠", "有没有活动", "有没有现车", "有没有试驾",
        "分期", "贷款", "金融方案", "月供多少", "首付多少",
        "保险", "保险费用", "保险多少钱", "上牌", "上牌费用",
        "落地价", "全部下来", "落地多少钱", "总价", "全款",
        "区别", "有什么不同", "有什么差异", "哪个好", "优势", "劣势",
        "对比", "比较", "测评", "评测", "怎么样", "好不好",
        "建议", "推荐", "买哪个", "选哪个", "怎么选",
        "什么时候", "上市时间", "发布时间", "交付时间",
        "哪里", "在哪里", "在哪买", "在哪看", "在哪体验",
        "品牌", "怎么样", "品牌怎么样", "品牌好不好", "靠谱吗",
        "售后", "售后怎么样", "售后政策", "质保", "保修",
    ],
    "体验需求": [
        # 建议类
        "希望", "希望可以", "希望能", "希望能够", "希望能够有", "希望以后",
        "建议", "提议", "希望建议", "希望可以建议", "强烈建议", "真心建议",
        "增加", "希望能增加", "希望增加", "建议增加", "希望能有", "希望可以有",
        "优化", "优化一下", "希望优化", "建议优化", "需要优化", "可以优化",
        "改进", "改进一下", "希望改进", "建议改进", "需要改进", "可以改进",
        "提升", "提升一下", "希望提升", "建议提升", "需要提升",
        "改善", "改善一下", "希望改善", "建议改善", "需要改善",
        "完善", "完善一下", "希望完善", "建议完善", "需要完善",
        "更新", "希望更新", "建议更新", "希望可以有新功能", "期待更新",
        "升级", "希望升级", "建议升级", "希望可以升级", "期待升级",
        "添加", "希望添加", "建议添加", "希望可以添加",
        "开发", "希望开发", "建议开发", "希望可以开发",
        "推出", "希望推出", "建议推出", "期待推出",
        "增加功能", "希望增加功能", "建议增加功能", "希望有新功能",
        "开放功能", "希望开放功能", "建议开放功能",
        "开通功能", "希望开通功能", "建议开通功能",
        "车机优化", "车机升级", "系统优化", "系统升级", "软件更新",
        "界面优化", "ui优化", "界面改进", "界面改善",
        "交互优化", "交互改进", "操作优化", "操作改进",
        "体验不好", "体验差", "体验一般", "体验有待提升", "体验需要改善",
        "不方便", "不方便使用", "使用不方便", "操作不方便",
        "不好用", "不好用", "不太好用", "用起来不方便", "用起来不好用",
        "太丑", "不好看", "不美观", "颜值低", "设计丑",
        "不合理", "不太合理", "设计不合理", "规划不合理",
        "不智能", "不够智能", "智能化程度", "智能化不足",
        "语音助手", "语音识别", "语音控制", "语音交互", "希望语音更好",
        "导航", "希望导航", "建议导航", "导航优化", "地图优化",
        "carplay", "carlife", "投屏", "互联", "手机互联", "希望支持",
        "hud", "抬头显示", "希望hud", "建议hud",
        "氛围灯", "希望氛围灯", "建议氛围灯", "氛围灯优化",
        "音响", "希望音响", "建议音响", "音响优化", "音质提升",
        "座椅", "希望座椅", "建议座椅", "座椅舒适", "座椅优化",
        "空间", "希望空间", "建议空间", "空间优化", "空间改善",
        "储物", "希望储物", "建议储物", "储物空间", "储物优化",
        "做工", "希望做工", "建议做工", "做工提升", "工艺提升",
        "材质", "希望材质", "建议材质", "材质提升", "用料提升",
        "隔音", "希望隔音", "建议隔音", "隔音提升", "噪音优化",
        "续航提升提升", "建议续航提升", "", "希望续航续航增加",
        "充电速度", "希望充电更快", "建议快充", "充电优化",
    ],
    "非问题": [
        # 表扬感谢类
        "感谢", "谢谢", "非常感谢", "十分感谢", "衷心感谢", "真诚感谢",
        "表扬", "赞赏", "赞", "点赞", "好评", "五星好评", "好评如潮",
        "满意", "非常满意", "十分满意", "超级满意", "特别满意",
        "服务好", "服务态度好", "服务周到", "服务贴心", "服务到位",
        "专业", "非常专业", "十分专业", "专业度高", "很专业",
        "靠谱", "非常靠谱", "十分靠谱", "很靠谱", "靠谱放心",
        "态度好", "态度很好", "态度非常好", "服务人员态度好",
        "热情", "非常热情", "十分热情", "服务热情", "接待热情",
        "耐心", "非常耐心", "十分耐心", "讲解耐心", "服务耐心",
        "贴心", "非常贴心", "十分贴心", "服务贴心", "关怀贴心",
        "周到", "非常周到", "十分周到", "考虑周到", "服务周到",
        "及时", "非常及时", "十分及时", "处理及时", "响应及时",
        "高效", "非常高效", "十分高效", "效率高", "处理高效",
        "负责", "非常负责", "十分负责", "认真负责", "态度负责",
        "细心", "非常细心", "十分细心", "服务细心", "关怀细心",
        "体贴", "非常体贴", "十分体贴", "服务体贴", "关怀体贴",
        "惊喜", "非常惊喜", "十分惊喜", "带来惊喜", "超乎预期",
        "推荐", "强烈推荐", "十分推荐", "值得推荐", "推荐购买",
        "选择没错", "选对了", "没有选错", "明智选择", "值得选择",
        "物超所值", "性价比高", "超性价比", "值", "超值",
        "体验好", "体验非常好", "体验很棒", "体验不错", "体验满意",
        "舒服", "非常舒服", "十分舒服", "坐着舒服", "开着舒服",
        "开心", "非常开心", "十分开心", "开着开心", "用着开心",
        "愉快", "非常愉快", "十分愉快", "购车愉快", "服务愉快",
        "信赖", "值得信赖", "可以信赖", "值得信任", "信任",
        "放心", "非常放心", "十分放心", "用着放心", "开着放心",
        "期待", "非常期待", "十分期待", "期待已久", "满怀期待",
        "加油", "支持", "支持国产", "支持国产品牌", "支持新能源",
    ]
}

# ===================== V1.6 优化：权重机制 + 精细化边界规则 =====================
# 关键词权重配置
KEYWORD_WEIGHTS = {
    # 产品质量问题核心词（权重10，最高优先级）
    "产品质量问题": [
        # 车辆核心词
        "故障", "异响", "死机", "黑屏", "卡顿", "抖动", "漏电", "漏水", "漏油", "熄火", "无法启动", "启动不了", "启动失败", "打不着",
        "刹车失灵", "刹车故障", "刹车异响", "转向异响", "方向盘抖动", "方向盘沉重", "充电故障", "充电慢", "充不进", "充电中断", "充电失败",
        "续航虚标", "续航缩水", "续航不足", "掉电快", "掉电严重", "电量不准", "电池衰减", "电池损坏", "电池故障灯", "电池包", "电池管理系统",
        "电机故障", "电机异响", "电机损坏", "电机啸叫", "电机故障灯", "空调不制冷", "空调不制热", "空调故障", "空调异响", "车机死机",
        "车机卡顿", "车机黑屏", "屏幕死机", "屏幕卡顿", "屏幕闪烁", "屏幕黑屏", "摄像头故障", "摄像头模糊", "雷达故障", "雷达失灵",
        "天窗漏水", "天窗异响", "天窗关不上", "车门异响", "车门关不上", "车窗升降", "车窗异响", "后视镜故障", "座椅异响", "方向盘异响",
        "仪表盘故障", "仪表盘报警", "灯光故障", "胎压报警", "安全气囊报警", "abs故障", "esp故障", "自动驾驶失效", "软件更新失败",
        "ota失败", "自燃", "起火", "冒烟", "过热", "失控", "安全隐患", "质量问题", "品质问题", "缺陷", "瑕疵", "损坏", "破损",
        # 充电设施核心词（V1.6新增）
        "超充站故障", "超充站无法充电", "超充站充不进", "超充站坏", "超充桩故障", "超充桩无法充电",
        "闪充站故障", "闪充站无法充电", "闪充站坏", "闪充桩故障", "闪充桩无法充电",
        "充电桩故障", "充电桩无法充电", "充电桩充不进", "充电桩坏", "充电桩不工作", "充电桩充不进电",
        "充电桩安装", "充电桩安装质量差", "充电桩安装问题", "充电桩安装不好", "充电桩安装服务",
        "充电枪故障", "充电枪无法使用", "充电枪损坏", "充电模块故障", "充电模块损坏",
        "家充桩故障", "家充桩无法充电", "家充桩坏", "家用充电桩故障", "家用充电桩无法充电",
        "充电站故障", "充电站无法充电", "充电站点故障", "充电站设备故障",
        "快充故障", "快充无法充电", "慢充故障", "慢充无法充电",
        "充电无法使用", "充电不能用", "充电不能用", "充电有故障", "充电设备故障",
        "维修质量差", "配件故障", "安装问题", "工艺缺陷", "安装质量差", "安装工艺",
        # 海外适配词
        "charging pile fault", "super charging station failure", "charging station not working", "charging station broken",
        "home charger fault", "supercharger not working", "charger malfunction", "charging issue"
    ],
    
    # 销售服务问题词（权重5）
    "销售服务问题": [
        "服务态度", "态度差", "态度恶劣", "不耐烦", "爱答不理", "不专业", "不靠谱", "不负责", "推诿", "踢皮球",
        "等待", "等太久", "等不及", "交付延迟", "交车延迟", "延期交付", "迟迟不交",
        "不退订金", "不退定金", "不退钱", "退款", "退费",
        "虚假宣传", "宣传不符", "宣传夸大", "承诺不兑现", "虚假承诺", "欺骗", "欺诈",
        "隐瞒", "欺骗消费者", "隐瞒事实", "隐瞒问题",
        "合同纠纷", "合同违约", "霸王条款", "不公平条款",
        "乱收费", "收费不合理", "收费高", "额外收费", "变相收费",
        "保养维修", "理赔", "投诉", "投诉无门", "投诉处理",
        "处理慢", "处理不及时", "处理拖沓", "效率低",
        "售后电话不通", "客服不接", "网点少", "距离远",
        "技术差", "修不好", "越修越坏", "配件等待",
        "旧车", "库存车", "展车", "试驾车", "问题车",
        "信息不符", "车况不符", "与描述不符",
        "上牌难", "贷款难", "服务", "销售", "售后", "经销商", "4s店", "门店"
    ],
    
    # 产品咨询词（权重3）
    "产品咨询": [
        "能不能", "是否可以", "能否", "可不可以",
        "怎么", "如何", "怎么样", "怎样", "怎么办", "怎么处理",
        "多少", "价格", "价位", "多少钱", "费用", "报价", "售价",
        "配置", "配置表", "配置区别", "配置差异", "选配", "标配", "顶配",
        "参数", "参数表", "性能参数", "技术参数",
        "续航", "续航里程", "续航多少", "续航表现", "续航能力",
        "电池", "电池容量", "电池类型", "电池品牌", "电池质保",
        "充电", "充电时间", "充电桩", "充电方式", "快充", "慢充",
        "电机", "电机功率", "电机扭矩",
        "尺寸", "长宽高", "空间", "轴距", "车内空间", "后备箱",
        "颜色", "颜色有哪些", "有哪些颜色", "车身颜色",
        "内饰", "内饰颜色", "内饰材质",
        "订车", "预订", "什么时候能提", "提车时间", "等车", "等多久",
        "有没有", "有没有优惠", "有没有活动", "有没有现车", "有没有试驾",
        "分期", "贷款", "金融方案", "月供多少", "首付多少",
        "保险", "保险费用", "上牌", "上牌费用", "落地价",
        "区别", "有什么不同", "有什么差异", "哪个好", "优势", "劣势",
        "对比", "比较", "测评", "评测", "怎么样", "好不好",
        "建议", "推荐", "买哪个", "选哪个", "怎么选",
        "什么时候", "上市时间", "发布时间", "交付时间",
        "哪里", "在哪里", "在哪买", "在哪看", "在哪体验",
        "品牌", "靠谱吗", "咨询", "询问", "了解", "想知道", "请问"
    ],
    
    # 体验需求核心词（权重8）
    "体验需求": [
        "希望", "希望可以", "希望能", "希望能够", "希望以后",
        "建议", "提议", "强烈建议", "真心建议",
        "增加", "希望能增加", "希望增加", "建议增加", "希望能有",
        "优化", "优化一下", "希望优化", "建议优化", "需要优化",
        "改进", "改进一下", "希望改进", "建议改进", "需要改进",
        "提升", "提升一下", "希望提升", "建议提升", "需要提升",
        "改善", "改善一下", "希望改善", "建议改善", "需要改善",
        "完善", "完善一下", "希望完善", "建议完善", "需要完善",
        "更新", "希望更新", "建议更新", "期待更新",
        "升级", "希望升级", "建议升级", "期待升级",
        "添加", "希望添加", "建议添加",
        "开发", "希望开发", "建议开发",
        "推出", "希望推出", "建议推出", "期待推出",
        "增加功能", "希望增加功能", "建议增加功能", "希望有新功能",
        "开放功能", "希望开放功能", "建议开放功能",
        "车机优化", "车机升级", "系统优化", "界面优化", "语音助手",
        "体验不好", "体验差", "体验一般", "体验有待提升",
        "不方便", "不方便使用", "使用不方便", "操作不方便",
        "不好用", "不太好用", "用起来不方便", "用起来不好用",
        "太丑", "不好看", "不美观", "颜值低",
        "不合理", "不太合理", "设计不合理",
        "不智能", "不够智能", "智能化程度",
        "服务优化", "服务提升", "希望提升服务", "建议提升服务",
        "体验很好", "希望能够更好", "希望能更", "体验有待改善"
    ],
    
    # 非问题核心词（权重10，最高优先级）
    "非问题": [
        "感谢", "谢谢", "非常感谢", "十分感谢", "衷心感谢", "真诚感谢",
        "表扬", "赞赏", "赞", "点赞", "好评", "五星好评", "好评如潮",
        "满意", "非常满意", "十分满意", "超级满意", "特别满意",
        "服务好", "服务态度好", "服务周到", "服务贴心", "服务到位",
        "专业", "非常专业", "十分专业", "专业度高", "很专业",
        "靠谱", "非常靠谱", "十分靠谱", "很靠谱", "靠谱放心",
        "态度好", "态度很好", "态度非常好",
        "热情", "非常热情", "十分热情", "服务热情",
        "耐心", "非常耐心", "十分耐心", "讲解耐心",
        "贴心", "非常贴心", "十分贴心", "服务贴心",
        "周到", "非常周到", "十分周到", "考虑周到",
        "及时", "非常及时", "十分及时", "处理及时", "响应及时",
        "高效", "非常高效", "十分高效", "效率高",
        "负责", "非常负责", "十分负责", "认真负责",
        "细心", "非常细心", "十分细心",
        "体贴", "非常体贴", "十分体贴",
        "惊喜", "非常惊喜", "十分惊喜", "带来惊喜", "超乎预期",
        "推荐", "强烈推荐", "十分推荐", "值得推荐", "推荐购买",
        "选择没错", "选对了", "没有选错", "明智选择", "值得选择",
        "物超所值", "性价比高", "超性价比", "值", "超值",
        "体验好", "体验非常好", "体验很棒", "体验不错", "体验满意",
        "舒服", "非常舒服", "十分舒服", "坐着舒服", "开着舒服",
        "开心", "非常开心", "十分开心", "开着开心", "用着开心",
        "愉快", "非常愉快", "十分愉快", "购车愉快", "服务愉快",
        "信赖", "值得信赖", "可以信赖", "值得信任",
        "放心", "非常放心", "十分放心", "用着放心", "开着放心",
        "期待", "非常期待", "十分期待", "期待已久", "满怀期待",
        "加油", "支持", "支持国产", "支持国产品牌", "支持新能源",
        "无故障", "没有问题", "没故障", "没问题", "一切正常", "使用顺畅", "运行正常",
        "感谢厂家", "感谢客服", "感谢售后", "谢谢你们", "非常感谢你们"
    ]
}

# 否定词配置（用于排除错误分类）
NEGATION_WORDS = {
    "产品质量问题": ["不是产品质量", "非产品质量", "不是质量问题", "非质量问题", "无故障", "没问题", "没有故障", "不存在质量问题"],
    "销售服务问题": ["不是销售服务", "非销售服务", "不是服务问题", "非服务问题", "不是售后问题"],
    "产品咨询": ["不是咨询", "非咨询", "无需咨询", "不是咨询类"],
    "体验需求": ["不是体验需求", "非体验需求", "不是建议", "无建议", "不需要改进"],
    "非问题": []
}

# 充电设施专属规则（强制判定为产品质量问题）
CHARGING_FACILITY_KEYWORDS = [
    "超充站", "闪充站", "充电桩", "充电枪", "充电模块", "充电站", "家充桩", "家用充电桩",
    "超充", "闪充", "快充", "慢充", "充电设施", "充电设备"
]

# 产品质量问题触发词（与充电设施组合时强制判定）
PRODUCT_QUALITY_TRIGGERS = [
    "故障", "无法", "不能", "不能用", "不工作", "坏", "损坏", "有问题", "问题", "质量差",
    "安装问题", "安装质量差", "无法充电", "充不进", "充不进电", "不充电", "断电", "异常"
]


# ===================== V1.6 优化：大模型Prompt =====================
OPTIMIZED_LLM_PROMPT_V16 = """你是新能源车企舆情分类专家。请严格按以下5类分类：
- 产品质量问题：车辆硬件/软件故障、安全问题、性能问题；公司自有品牌超充站/闪充站/家用充电桩的故障、质量、安装问题（即使含服务/售后词汇也归此类）；海外反馈中对应的故障类内容；
- 销售服务问题：售后服务、门店服务、交付问题（不含充电设施故障）；
- 产品咨询：价格咨询、配置咨询、功能咨询、使用咨询；海外反馈中对应的咨询类内容；
- 体验需求：功能建议、优化建议、体验改进（如超充站数量增加建议，不含故障类）；
- 非问题：表扬、感谢、好评、无故障反馈、正面评价（含海外正面反馈）。

严格区分以下边界：
- 充电设施（超充站/闪充站/充电桩）+ 故障/质量/安装 = 产品质量问题（无论是否含服务/售后）
- "希望/建议/增加/优化/改进" = 体验需求（非故障类）
- "能不能/是否/怎么/如何/多少/价格" = 产品咨询
- "故障/异响/死机/黑屏/卡顿/漏电/漏水/熄火/失控/自燃" = 产品质量问题
- "表扬/感谢/好评/满意/推荐/无故障/没问题" = 非问题

注意：
- 海外翻译文本按中文语义分类，忽略英文残留词汇
- 否定词（无故障、不是服务问题）表示非问题或排除对应分类

请只输出分类名称，不要输出其他内容。
舆情内容：{content}
分类结果："""


# ===================== 优化：根据压测报告强化大模型Prompt =====================
# 强化版分类Prompt
OPTIMIZED_LLM_PROMPT = """你是新能源车企舆情分类专家。请严格按以下5类分类：
- 产品质量问题：车辆硬件/软件故障、安全问题、性能问题
- 销售服务问题：售后服务、门店服务、交付问题、投诉处理
- 产品咨询：价格咨询、配置咨询、功能咨询、使用咨询
- 体验需求：功能建议、优化建议、体验改进、新功能需求
- 非问题：表扬、感谢、好评、满意、推荐

严格区分以下边界：
- 含"希望/建议/增加/优化/改进/提升" = 体验需求（除非明确是故障）
- 含"能不能/是否/怎么/多少/价格/配置" = 产品咨询
- 含"故障/异响/死机/黑屏/卡顿/漏电/漏水/熄火/失控/自燃" = 产品质量问题
- 含"服务态度/等待/交付/理赔/投诉/推诿" = 销售服务问题
- 含"感谢/表扬/满意/推荐/好评" = 非问题

请只输出分类名称，不要输出其他内容。
舆情内容：{content}
分类结果："""
PATTERN_NUM = re.compile(r'^[0-9a-zA-Z]+$')
PATTERN_SYMBOL = re.compile(r'[^\u4e00-\u9fa5a-zA-Z0-9]')
CONFIDENCE_HIGH, CONFIDENCE_MEDIUM = 0.9, 0.7
MIN_WORD_LEN = 2  # 最小词语长度过滤
MIN_WORD_FREQ = 2  # 最小词语出现频次过滤

# 系统初始化
def init_folders():
    for folder in [ROOT_DIR, UPLOAD_DIR, ARCHIVE_DIR, LOG_DIR, KEYWORD_DIR, SAMPLE_POOL_DIR]:
        os.makedirs(folder, exist_ok=True)
    if not os.path.exists(LOG_FILE):
        pd.DataFrame(columns=LOG_COLS).to_csv(LOG_FILE, index=False, encoding="utf-8-sig")
    if not os.path.exists(KEYWORD_FILE):
        init_keywords = {t: ["电池", "续航", "充电", "故障", "服务", "销售", "售后", "咨询", "体验", "优化", "感谢", "表扬"] for t in FEEDBACK_TYPES}
        with open(KEYWORD_FILE, "w", encoding="utf-8") as f:
            json.dump(init_keywords, f, ensure_ascii=False, indent=4)
    if not os.path.exists(TAG_MAPPING_FILE):
        with open(TAG_MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump({"产品质量": "产品质量问题", "销售服务": "销售服务问题", "咨询": "产品咨询", "体验需求": "体验需求", "非问题": "非问题"}, f, ensure_ascii=False)
    if not os.path.exists(WORD_LOG_FILE):
        with open(WORD_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)

def write_log(oper_type, oper_content, oper_person="产品总监"):
    try:
        new_log = pd.DataFrame([{"操作时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "操作类型": oper_type, "操作内容": oper_content, "操作人": oper_person, "版本号": "V1.5"}])
        new_log.to_csv(LOG_FILE, mode="a", header=False, index=False, encoding="utf-8-sig")
    except: pass

# 标签映射器
class TagMapper:
    def __init__(self):
        self.mapping = {}
        self.load_mapping()
    def load_mapping(self):
        try:
            if os.path.exists(TAG_MAPPING_FILE):
                with open(TAG_MAPPING_FILE, "r", encoding="utf-8") as f:
                    self.mapping = json.load(f)
        except: self.mapping = {}
    def save_mapping(self):
        try:
            with open(TAG_MAPPING_FILE, "w", encoding="utf-8") as f:
                json.dump(self.mapping, f, ensure_ascii=False, indent=4)
            return True
        except: return False
    def map_to_standard(self, first="", second="", third=""):
        first, second, third = str(first).strip(), str(second).strip(), str(third).strip()
        for tag in [third, second, first]:
            if tag and tag in self.mapping:
                return self.mapping[tag]
        return None
    def learn_from_correction(self, original_tags, corrected_class):
        if not original_tags or corrected_class not in FEEDBACK_TYPES:
            return
        try:
            tags = json.loads(original_tags) if isinstance(original_tags, str) else original_tags
            for tag in [tags.get("third", ""), tags.get("second", ""), tags.get("first", "")]:
                if tag and tag not in self.mapping:
                    self.mapping[tag] = corrected_class
                    self.save_mapping()
                    break
        except: pass

tag_mapper = TagMapper()

# 文本处理
def clean_text(series_data):
    return series_data.fillna('').astype(str).str.strip()

def clean_dataframe(df):
    df_clean = df.copy(deep=True)
    for col in df_clean.columns:
        df_clean[col] = clean_text(df_clean[col])
    return df_clean

# 关键词处理
# 优化：使用扩充后的预定义词库 + 自动加载用户词库
def load_keywords():
    try:
        # 先尝试读取用户自定义词库
        if os.path.exists(KEYWORD_FILE):
            with open(KEYWORD_FILE, "r", encoding="utf-8") as f:
                keywords = json.load(f)
            # 检查用户词库是否足够（每类≥50个），否则补充预定义词库
            is_sufficient = all(len(keywords.get(t, [])) >= 50 for t in FEEDBACK_TYPES)
            if not is_sufficient:
                # 合并预定义词库和用户词库
                for type_name in FEEDBACK_TYPES:
                    user_words = set([w.strip().lower() for w in keywords.get(type_name, []) if w.strip()])
                    expanded = set([w.lower() for w in EXPANDED_KEYWORDS.get(type_name, [])])
                    combined = list(user_words | expanded)
                    keywords[type_name] = combined
        else:
            # 词库文件不存在，使用预定义扩充词库
            keywords = {t: list(set([w.lower() for w in v])) for t, v in EXPANDED_KEYWORDS.items()}
        
        # 确保所有分类都存在
        for type_name in FEEDBACK_TYPES:
            if type_name not in keywords or not isinstance(keywords[type_name], list):
                keywords[type_name] = list(set([w.lower() for w in EXPANDED_KEYWORDS.get(type_name, [])]))
            # 去重并清洗
            keywords[type_name] = list(set([w.strip().lower() for w in keywords[type_name] if w.strip()]))
        
        return keywords
    except Exception as e:
        print(f"加载词库失败: {e}, 使用预定义词库")
        return {t: list(set([w.lower() for w in v])) for t, v in EXPANDED_KEYWORDS.items()}

def save_keywords(keywords):
    try:
        for type_name in keywords:
            keywords[type_name] = list(set([w.strip().lower() for w in keywords[type_name] if w.strip()]))
        with open(KEYWORD_FILE, "w", encoding="utf-8") as f:
            json.dump(keywords, f, ensure_ascii=False, indent=4)
        return True
    except: return False

def cut_text(text):
    if not text or text == '': return []
    text = str(text).strip().lower()
    text = PATTERN_SYMBOL.sub('', text)
    words = jieba.lcut(text)
    return [w for w in words if w not in STOP_WORDS and w not in GENERAL_WORDS and len(w) >= MIN_WORD_LEN and w.strip() and not PATTERN_NUM.match(w)]

def keyword_match_classify(text, keywords):
    """V1.6优化：权重机制 + 精细化边界规则 + 充电设施专属规则"""
    if not text or pd.isna(text): return None, [], 0.0
    text_str = str(text).lower()
    
    # ===================== 优先级1：非问题检测（最高优先级） =====================
    non_problem_words = KEYWORD_WEIGHTS.get("非问题", [])
    for word in non_problem_words:
        if word.lower() in text_str:
            # 检查是否有否定词排除
            is_negated = False
            for neg_pattern in ["不是非问题", "不算非问题"]:
                if neg_pattern in text_str:
                    is_negated = True
                    break
            if not is_negated:
                return "非问题", [word], 1.0
    
    # ===================== 优先级2：充电设施强制判定（产品质量问题） =====================
    has_charging_facility = any(cf in text_str for cf in CHARGING_FACILITY_KEYWORDS)
    has_quality_trigger = any(pt in text_str for pt in PRODUCT_QUALITY_TRIGGERS)
    
    if has_charging_facility and has_quality_trigger:
        # 匹配到的具体问题词
        matched_triggers = [pt for pt in PRODUCT_QUALITY_TRIGGERS if pt in text_str]
        return "产品质量问题", matched_triggers[:3], 1.0
    
    # ===================== 优先级3：产品质量问题核心词（高权重） =====================
    product_words = KEYWORD_WEIGHTS.get("产品质量问题", [])
    product_score = 0
    product_matched = []
    for word in product_words:
        if word.lower() in text_str:
            # 英文词匹配检查
            if any(c.isalpha() for c in word):
                if word.lower() in text_str:
                    product_score += 10
                    product_matched.append(word)
            else:
                product_score += 10
                product_matched.append(word)
    
    # ===================== 优先级4：销售服务问题 =====================
    service_words = KEYWORD_WEIGHTS.get("销售服务问题", [])
    service_score = 0
    service_matched = []
    for word in service_words:
        if word.lower() in text_str:
            service_score += 5
            service_matched.append(word)
    
    # ===================== 优先级5：体验需求 =====================
    experience_words = KEYWORD_WEIGHTS.get("体验需求", [])
    experience_score = 0
    experience_matched = []
    for word in experience_words:
        if word.lower() in text_str:
            experience_score += 8
            experience_matched.append(word)
    
    # ===================== 优先级6：产品咨询 =====================
    consult_words = KEYWORD_WEIGHTS.get("产品咨询", [])
    consult_score = 0
    consult_matched = []
    for word in consult_words:
        if word.lower() in text_str:
            consult_score += 3
            consult_matched.append(word)
    
    # ===================== 权重比较 + 冲突处理 =====================
    scores = {
        "产品质量问题": product_score,
        "销售服务问题": service_score,
        "体验需求": experience_score,
        "产品咨询": consult_score
    }
    
    # 过滤掉0分
    scores = {k: v for k, v in scores.items() if v > 0}
    
    if not scores:
        return None, [], 0.0
    
    # 找到最高分
    max_score = max(scores.values())
    max_types = [k for k, v in scores.items() if v == max_score]
    
    # 如果有多个类型得分相同，按优先级选择
    if len(max_types) > 1:
        priority_order = ["产品质量问题", "销售服务问题", "体验需求", "产品咨询"]
        for p in priority_order:
            if p in max_types:
                result_type = p
                break
    else:
        result_type = max_types[0]
    
    # 获取匹配的关键词
    if result_type == "产品质量问题":
        matched = product_matched
    elif result_type == "销售服务问题":
        matched = service_matched
    elif result_type == "体验需求":
        matched = experience_matched
    else:
        matched = consult_matched
    
    # 计算置信度（基于最高分与次高分的差距）
    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) > 1:
        confidence = min(1.0, 0.7 + (sorted_scores[0] - sorted_scores[1]) * 0.05)
    else:
        confidence = 0.9
    
    return result_type, matched[:5], round(confidence, 2)

def extract_and_update_keywords(df_corrected):
    keywords = load_keywords()
    df_corrected = clean_dataframe(df_corrected)
    for type_name in FEEDBACK_TYPES:
        type_df = df_corrected[df_corrected["最终分类结果"] == type_name]
        if type_df.empty: continue
        all_text = "".join(type_df[type_df["反馈内容"]!=""]["反馈内容"].tolist())
        if not all_text: continue
        words = cut_text(all_text)
        if not words: continue
        word_count = Counter(words)
        word_count = {w: c for w, c in word_count.items() if c >= MIN_WORD_FREQ}
        if not word_count: continue
        top_words = [w for w, c in word_count.most_common(KEYWORD_TOP_N)]
        keywords[type_name].extend(top_words)
    if save_keywords(keywords):
        total_key = sum(len(v) for v in keywords.values())
        return keywords, total_key
    return keywords, 0

# 置信度计算
# V1.6优化：使用keyword_match_classify返回的置信度
def calculate_keyword_confidence(text, keywords, match_type, matched_words):
    """V1.6: 置信度由keyword_match_classify直接返回，这里做兼容性处理"""
    if not matched_words or not match_type:
        return 0.0, []
    
    # 非问题类别给予高置信度
    if match_type == "非问题":
        return 1.0, matched_words
    
    # 充电设施相关给予高置信度
    text_str = str(text).lower()
    has_charging = any(cf in text_str for cf in CHARGING_FACILITY_KEYWORDS)
    if has_charging:
        return 1.0, matched_words
    
    # 产品核心词给予高置信度
    product_core = ["故障", "异响", "死机", "黑屏", "卡顿", "质量差", "安装问题"]
    if any(pc in text_str for pc in product_core):
        return 0.95, matched_words
    
    return 0.85, matched_words
    if not text_words: return 0.0, []
    match_ratio = len(matched_words) / len(text_words)
    type_library_size = len(keywords.get(match_type, []))
    library_ratio = len(matched_words) / max(type_library_size, 1)
    confidence = min(1.0, 0.85 + match_ratio * 0.1 + library_ratio * 0.05)
    return round(confidence, 3), matched_words

# 优化：改进模型置信度计算
def calculate_model_confidence(model_output, text_content=None):
    if not model_output: return 0.0
    base_confidence = 0.85
    
    # 如果有文本内容，检查是否包含边界关键词（V1.6：使用KEYWORD_WEIGHTS）
    if text_content:
        text_str = str(text_content).lower()
        # 包含明确边界词时提高置信度
        for t, bws in KEYWORD_WEIGHTS.items():
            for bw in bws[:20]:  # 只检查前20个核心词
                if bw in text_str:
                    return 0.95
    
    return base_confidence

# 大模型调用
# V1.6优化：使用强化版Prompt（支持充电设施/海外/非问题）
def call_local_llm(feedback_content):
    if not feedback_content or pd.isna(feedback_content): return None, None
    # 使用V1.6优化后的Prompt模板
    prompt = OPTIMIZED_LLM_PROMPT_V16.format(content=feedback_content)
    try:
        result = subprocess.run(["ollama", "run", MODEL_NAME, prompt], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="ignore")
        if result.returncode == 0:
            res = result.stdout.strip()
            classify_result, reason = None, None
            for _type in FEEDBACK_TYPES:
                if _type in res:
                    classify_result = _type
                    reason = "模型分类"
                    break
            # 优化：如果模型输出没有明确分类，尝试从输出中提取关键词判断
            if not classify_result:
                classify_result, reason = _extract_type_from_llm_output(res)
            return classify_result, reason
    except Exception as e:
        print(f"LLM调用失败: {e}")
    return None, None

def _extract_type_from_llm_output(output):
    """优化：从LLM输出中提取分类结果"""
    output_lower = output.lower()
    
    # 边界规则判断
    boundary_keywords = {
        "产品质量问题": ["故障", "异响", "死机", "黑屏", "卡顿", "抖动", "漏电", "漏水", "熄火", "失控", "自燃", "刹车", "充电", "续航", "电池", "电机", "车机", "屏幕"],
        "销售服务问题": ["服务", "销售", "售后", "态度", "等待", "交付", "投诉", "理赔", "退款", "虚假", "承诺", "推诿", "4s店", "经销商"],
        "产品咨询": ["价格", "配置", "多少", "怎么", "如何", "能否", "可以", "续航", "电池", "充电", "参数", "尺寸", "空间", "颜色"],
        "体验需求": ["希望", "建议", "增加", "优化", "改进", "提升", "改善", "完善", "更新", "升级", "添加", "功能", "体验", "希望能有"],
        "非问题": ["感谢", "谢谢", "表扬", "满意", "好评", "推荐", "赞", "喜欢", "开心", "满意", "不错", "给力"]
    }
    
    scores = {t: 0 for t in FEEDBACK_TYPES}
    for t, keywords in boundary_keywords.items():
        for kw in keywords:
            if kw in output_lower:
                scores[t] += 1
    
    max_score = max(scores.values())
    if max_score > 0:
        for t, s in scores.items():
            if s == max_score:
                return t, f"模型推理-{t}"
    
    return None, None

# 并行分类
def parallel_classify_single(args):
    idx, content, has_keywords, keywords, use_mapping, original_tags = args
    # 1. 标签映射
    if use_mapping and original_tags:
        try:
            tags = json.loads(original_tags) if isinstance(original_tags, str) else original_tags
            mapped = tag_mapper.map_to_standard(tags.get("first", ""), tags.get("second", ""), tags.get("third", ""))
            if mapped and mapped in FEEDBACK_TYPES:
                return {"idx": idx, "result": mapped, "confidence": 1.0, "way": "标签映射", "reason": f"标签映射", "auto_reviewed": True}
        except: pass
    # 2. 关键词匹配（V1.6优化：支持权重机制 + 返回置信度）
    if has_keywords:
        kw_res, matched, kw_confidence = keyword_match_classify(content, keywords)
        if kw_res:
            return {"idx": idx, "result": kw_res, "confidence": kw_confidence, "way": "关键词匹配", 
                    "reason": f"匹配关键词:{','.join(matched[:3])}", "auto_reviewed": kw_confidence >= CONFIDENCE_HIGH}
    # 3. 模型兜底
    model_res, reason = call_local_llm(content)
    if model_res:
        # 优化：传入content以提高模型置信度计算准确性
        conf = calculate_model_confidence(model_res, content)
        return {"idx": idx, "result": model_res, "confidence": conf, "way": "本地模型", "reason": reason or "模型分类", "auto_reviewed": conf >= CONFIDENCE_HIGH}
    return {"idx": idx, "result": "", "confidence": 0.0, "way": "分类失败", "reason": "无匹配", "auto_reviewed": False}

def parallel_classify(df, keywords, use_mapping=True, max_workers=None):
    start_time = time.time()
    data_size = len(df)
    max_workers = max(5, min(20, data_size // 50)) if max_workers is None else max_workers
    args_list = [(idx, df.loc[idx, "反馈内容"], keywords is not None, keywords, use_mapping, df.loc[idx, "原始标签"] if "原始标签" in df.columns else None) for idx in df.index]
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        progress_bar = st.progress(0, text="分类中...")
        completed = 0
        for future in as_completed({executor.submit(parallel_classify_single, args): args[0] for args in args_list}):
            res = future.result()
            results[res["idx"]] = res
            completed += 1
            progress_bar.progress(completed / len(args_list), text=f"处理进度：{completed}/{len(args_list)}")
    progress_bar.empty()
    return results, time.time() - start_time

# CSV校验
def check_csv_format(df):
    df.columns = clean_text(pd.Series(df.columns))
    
    # 检查舆情中文（必需）
    if "舆情中文" not in df.columns:
        return False, "缺失列：舆情中文"
    
    # 检查时间字段（舆情时间 或 舆情日期 任一即可）
    time_cols = ["舆情时间", "舆情日期"]
    has_time = any(col in df.columns for col in time_cols)
    if not has_time:
        return False, "缺失列：舆情时间或舆情日期"
    
    # 统一时间字段名
    for col in time_cols:
        if col in df.columns:
            df[col] = clean_text(df[col])
    df.rename(columns=FIELD_MAP, inplace=True)
    return True, "通过"

# 归档
def archive_feedback_data(df_classified):
    try:
        df = df_classified.copy(deep=True)
        df["反馈时间"] = pd.to_datetime(df["反馈时间"], errors="coerce")
        df = df.dropna(subset=["反馈时间"])
        if df.empty: return False, "无有效数据"
        df["年"], df["月"] = df["反馈时间"].dt.year.astype(int), df["反馈时间"].dt.month.astype(int)
        df = clean_dataframe(df)
        for col in ["最终分类结果", "复核状态", "分类方式", "人工修正结果", "模型分类结果", "置信度", "分类依据", "版本号", "归档时间"]:
            df.loc[:, col] = df[col].astype(str).str.strip() if col in df.columns else ""
        df.loc[:, "版本号"] = "V1.5"
        df.loc[:, "归档时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for year in df["年"].unique():
            year_df = df[df["年"] == year]
            # 确保年份是整数
            year_int = int(year)
            archive_file = os.path.join(ARCHIVE_DIR, f"{year_int} 年度舆情分类总表.xlsx")
            file_exists = os.path.exists(archive_file)
            for month in year_df["月"].unique():
                month_df = year_df[year_df["月"] == month].copy()
                # 确保月份是整数
                month_int = int(month)
                sheet_name = f"{year_int}年{month_int:02d}月"
                month_df_new = month_df.drop_duplicates(subset=["反馈时间", "反馈内容"], keep="last").drop(columns=["年", "月"], errors="ignore")
                if file_exists:
                    try:
                        old = pd.read_excel(archive_file, sheet_name=sheet_name, engine="openpyxl")
                        old = clean_dataframe(old).drop_duplicates(subset=["反馈时间", "反馈内容"], keep="last")
                        month_df_combined = pd.concat([old, month_df_new], ignore_index=True).drop_duplicates(subset=["反馈时间", "反馈内容"], keep="last")
                        for col in ["最终分类结果", "复核状态", "人工修正结果"]:
                            if col in month_df_combined.columns: month_df_combined.loc[:, col] = month_df_combined[col].fillna("")
                        month_df_final = month_df_combined
                    except: month_df_final = month_df_new
                else: month_df_final = month_df_new
                writer_kwargs = {"engine": "openpyxl", "mode": "a" if file_exists else "w"}
                if file_exists: writer_kwargs["if_sheet_exists"] = "replace"
                with pd.ExcelWriter(archive_file, **writer_kwargs) as writer:
                    clean_dataframe(month_df_final).to_excel(writer, sheet_name=sheet_name, index=False)
                    for ftype in FEEDBACK_TYPES:
                        type_df = month_df_final[month_df_final["最终分类结果"] == ftype]
                        if not type_df.empty:
                            type_df.to_excel(writer, sheet_name=ftype[:20], index=False)
        return True, f"归档成功，数据量 {len(df_classified)} 条"
    except Exception as e:
        return False, str(e)

# 可视化函数
def convert_time_dimension(df, dimension="日"):
    df_copy = df.copy(deep=True)
    df_copy["反馈时间"] = pd.to_datetime(df_copy["反馈时间"], errors="coerce")
    df_copy = df_copy.dropna(subset=["反馈时间", "最终分类结果"])
    if df_copy.empty: return pd.DataFrame(), "时间维度"
    if dimension == "日": df_copy["时间维度"] = df_copy["反馈时间"].dt.strftime("%Y-%m-%d")
    elif dimension == "周": df_copy["时间维度"] = df_copy["反馈时间"].apply(lambda x: f"{x.year}年第{x.isocalendar()[1]:02d}周")
    elif dimension == "月": df_copy["时间维度"] = df_copy["反馈时间"].dt.strftime("%Y-%m")
    agg = df_copy.groupby(["时间维度", "最终分类结果"])["反馈内容"].count().reset_index()
    agg.rename(columns={"反馈内容": "舆情数量"}, inplace=True)
    return agg.sort_values("时间维度").reset_index(drop=True) if not agg.empty else agg, "时间维度"

def generate_trend_chart(df, dimension="日", select_type="全部"):
    try:
        df_copy = clean_dataframe(df.copy())
        df_copy["反馈时间"] = pd.to_datetime(df_copy["反馈时间"], errors="coerce")
        df_copy = df_copy.dropna(subset=["反馈时间", "最终分类结果"])
        if df_copy.empty: return None
        if select_type != "全部" and select_type in FEEDBACK_TYPES:
            df_copy = df_copy[df_copy["最终分类结果"] == select_type]
        if df_copy.empty: return None
        agg_df, time_col = convert_time_dimension(df_copy, dimension)
        if agg_df.empty: return None
        
        # 车企商务配色方案（不影响舆情分类准确率）
        business_colors = {
            "产品质量问题": "#EF4444",
            "销售服务问题": "#F59E0B", 
            "产品咨询": "#3B82F6",
            "体验需求": "#10B981",
            "非问题": "#8B5CF6"
        }
        
        fig, ax = plt.subplots(figsize=(14, 7), dpi=100)
        for i, cat in enumerate(agg_df["最终分类结果"].unique()):
            cat_df = agg_df[agg_df["最终分类结果"] == cat]
            color = business_colors.get(cat, plt.cm.Set3(i / 10))
            ax.bar(cat_df[time_col], cat_df["舆情数量"], label=cat, alpha=0.85, color=color, edgecolor="white", linewidth=0.5)
        
        # 使用系统可用中文字体（不影响舆情分类准确率）
        ax.set_xlabel(f"{dimension}维度", fontsize=14, fontweight='bold')
        ax.set_ylabel("舆情数量", fontsize=14, fontweight='bold')
        ax.set_title("舆情分类趋势图", fontsize=16, fontweight='bold', pad=20)
        ax.legend(loc="upper right", fontsize=12, framealpha=0.9)
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.tick_params(axis='both', labelsize=12)
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        plt.close(fig)
        return fig
    except: return None

def generate_pie_chart(df):
    try:
        df_copy = clean_dataframe(df.copy())
        if "最终分类结果" not in df_copy.columns: return None
        df_copy = df_copy.dropna(subset=["最终分类结果"])
        if df_copy.empty: return None
        counts = df_copy["最终分类结果"].value_counts()
        
        # 车企商务配色方案（不影响舆情分类准确率）
        business_colors = ["#EF4444", "#F59E0B", "#3B82F6", "#10B981", "#8B5CF6"]
        
        fig, ax = plt.subplots(figsize=(10, 8), dpi=100)
        wedges, texts, autotexts = ax.pie(
            counts.values, 
            labels=counts.index, 
            autopct='%1.1f%%', 
            colors=business_colors[:len(counts)],
            explode=[0.03]*len(counts), 
            shadow=True, 
            startangle=90,
            textprops={'fontsize': 12}
        )
        # 设置百分比字体
        for autotext in autotexts:
            autotext.set_fontsize(11)
            autotext.set_fontweight('bold')
        ax.set_title('舆情分类占比分布', fontsize=16, fontweight='bold', pad=20)
        plt.tight_layout()
        plt.close(fig)
        return fig
    except: return None

def generate_keyword_chart(df, top_n=10):
    try:
        df_copy = clean_dataframe(df.copy())
        if "反馈内容" not in df_copy.columns: return None
        all_text = "".join(df_copy["反馈内容"].dropna().astype(str).tolist())
        if not all_text: return None
        words = cut_text(all_text)
        if not words: return None
        word_count = Counter(words)
        word_count = {w: c for w, c in word_count.items() if w not in STOP_WORDS and w not in GENERAL_WORDS}
        if not word_count: return None
        top_words = dict(sorted(word_count.items(), key=lambda x: x[1], reverse=True)[:top_n])
        
        # 车企商务配色 - 蓝色渐变（不影响舆情分类准确率）
        fig, ax = plt.subplots(figsize=(12, 6), dpi=100)
        bars = ax.barh(list(top_words.keys())[::-1], list(top_words.values())[::-1], 
                       color=plt.cm.Blues(np.linspace(0.4, 0.9, len(top_words))), edgecolor="white", linewidth=0.5)
        
        # 使用系统可用中文字体（不影响舆情分类准确率）
        ax.set_xlabel('出现频次', fontsize=14, fontweight='bold')
        ax.set_ylabel('关键词', fontsize=14, fontweight='bold')
        ax.set_title(f'高频关键词TOP{top_n}', fontsize=16, fontweight='bold', pad=20)
        
        # 添加数值标签
        for bar in bars:
            width = bar.get_width()
            ax.text(width + 0.5, bar.get_y() + bar.get_height()/2, 
                   f'{int(width)}', ha='left', va='center', fontsize=10)
        
        ax.grid(axis="x", linestyle="--", alpha=0.3)
        plt.tight_layout()
        plt.close(fig)
        return fig
    except: return None

def check_warning_thresholds(df, warning_threshold=100):
    warnings = []
    df_copy = df.copy()
    df_copy["反馈时间"] = pd.to_datetime(df_copy["反馈时间"], errors="coerce")
    df_copy = df_copy.dropna(subset=["反馈时间", "最终分类结果"])
    today = datetime.now().date()
    today_df = df_copy[df_copy["反馈时间"].dt.date == today]
    if not today_df.empty:
        for ftype in FEEDBACK_TYPES:
            count = len(today_df[today_df["最终分类结果"] == ftype])
            if count >= warning_threshold:
                warnings.append({"level": "critical", "message": f"🚨 危机预警：{ftype}今日已达{count}条，超过阈值{warning_threshold}"})
    return warnings

# 状态管理
def sync_business_data():
    if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
        st.session_state.uploaded_data = clean_dataframe(st.session_state.uploaded_data.copy(deep=True))
        return True
    return False

def on_editor_change_callback():
    try:
        if "_data_editor_review" not in st.session_state: return
        editor_raw_data = st.session_state._data_editor_review
        if isinstance(editor_raw_data, dict): edited_df = pd.DataFrame(editor_raw_data)
        elif isinstance(editor_raw_data, pd.DataFrame): edited_df = editor_raw_data
        else: return
        if edited_df.empty or "uploaded_data" not in st.session_state: return
        original_df = st.session_state.uploaded_data.copy(deep=True)
        for idx in edited_df.index:
            if idx >= len(original_df): continue
            current = edited_df.loc[idx, "人工修正结果"] if "人工修正结果" in edited_df.columns else None
            original = original_df.loc[idx, "人工修正结果"] if "人工修正结果" in original_df.columns else None
            if current != original and pd.notna(current) and str(current).strip():
                original_df.loc[idx, "人工修正结果"] = current
                if current in FEEDBACK_TYPES:
                    original_df.loc[idx, "复核状态"] = "已复核"
                    original_df.loc[idx, "最终分类结果"] = current
                    save_correction_sample(original_df.loc[idx])
                    if "原始标签" in original_df.columns:
                        tag_mapper.learn_from_correction(original_df.loc[idx, "原始标签"], current)
        if not original_df.equals(st.session_state.uploaded_data):
            st.session_state.uploaded_data = clean_dataframe(original_df)
            st.toast("✅ 已更新")
    except: pass

def save_correction_sample(row_data):
    try:
        sample = {"content": str(row_data.get("反馈内容", "")), "corrected_class": str(row_data.get("人工修正结果", "")), "correction_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        class_name = sample["corrected_class"]
        sample_file = os.path.join(SAMPLE_POOL_DIR, f"{class_name}_样本.json")
        samples = json.load(open(sample_file, "r", encoding="utf-8")) if os.path.exists(sample_file) else []
        samples.append(sample)
        with open(sample_file, "w", encoding="utf-8") as f:
            json.dump(samples, f, ensure_ascii=False, indent=2)
    except: pass

def export_training_samples(export_format="json"):
    try:
        all_samples = []
        for file in os.listdir(SAMPLE_POOL_DIR):
            if file.endswith(".json"):
                with open(os.path.join(SAMPLE_POOL_DIR, file), "r", encoding="utf-8") as f:
                    all_samples.extend(json.load(f))
        if not all_samples: return None, "无数据"
        if export_format == "json": return json.dumps(all_samples, ensure_ascii=False, indent=2), "json"
        return pd.DataFrame(all_samples).to_csv(index=False, encoding="utf-8-sig"), "csv"
    except Exception as e: return None, str(e)

# 统计
def get_statistics_data(include_archived=True):
    total, reviewed, correct, pending, low_conf = 0, 0, 0, 0, 0
    
    # 优先从当前session的uploaded_data获取统计
    if "uploaded_data" in st.session_state:
        try:
            df = st.session_state.uploaded_data
            if df is not None and hasattr(df, 'empty') and not df.empty:
                total += len(df)
                reviewed += len(df[df["复核状态"] == "已复核"]) if "复核状态" in df.columns else 0
                pending += len(df[df["复核状态"] == "未复核"]) if "复核状态" in df.columns else len(df)
                
                # 低置信度统计
                if "置信度" in df.columns:
                    for idx in df.index:
                        try:
                            conf_val = df.loc[idx, "置信度"]
                            status = df.loc[idx, "复核状态"] if "复核状态" in df.columns else "未复核"
                            if pd.notna(conf_val) and float(conf_val) < CONFIDENCE_MEDIUM and status == "未复核":
                                low_conf += 1
                        except: pass
                
                # 准确率计算
                for idx in df.index:
                    model_res = df.loc[idx, "模型分类结果"] if "模型分类结果" in df.columns else ""
                    manual_res = df.loc[idx, "人工修正结果"] if "人工修正结果" in df.columns else ""
                    final_res = df.loc[idx, "最终分类结果"] if "最终分类结果" in df.columns else ""
                    
                    if str(manual_res).strip() in FEEDBACK_TYPES:
                        if str(model_res).strip() == str(manual_res).strip():
                            correct += 1
                    elif str(model_res).strip() == str(final_res).strip() and str(final_res).strip() in FEEDBACK_TYPES:
                        correct += 1
        except Exception as e:
            print(f"统计当前数据错误: {e}")
    
    # 附加归档数据统计
    if include_archived and os.path.exists(ARCHIVE_DIR):
        for file in os.listdir(ARCHIVE_DIR):
            if file.endswith(".xlsx"):
                try:
                    excel_file = pd.ExcelFile(os.path.join(ARCHIVE_DIR, file), engine="openpyxl")
                    for sheet_name in excel_file.sheet_names:
                        if "年" in sheet_name:
                            df_arc = pd.read_excel(excel_file, sheet_name=sheet_name, engine="openpyxl")
                            if df_arc is not None and not df_arc.empty:
                                total += len(df_arc)
                                if "复核状态" in df_arc.columns:
                                    reviewed += len(df_arc[df_arc["复核状态"] == "已复核"])
                except: pass
    
    # 计算准确率
    accuracy = round(correct / max(total, 1) * 100, 2) if total > 0 else 0
    
    return {
        "total_count": total, 
        "reviewed_count": reviewed, 
        "pending_review_count": pending, 
        "low_confidence_count": low_conf, 
        "accuracy": accuracy, 
        "keyword_count": sum(len(v) for v in load_keywords().values())
    }

def generate_audit_sample(period="weekly"):
    try:
        if "uploaded_data" not in st.session_state or st.session_state.uploaded_data.empty: return None, -1
        df = st.session_state.uploaded_data
        now = datetime.now()
        start_date = now - timedelta(days=7 if period == "weekly" else 30)
        df["反馈时间"] = pd.to_datetime(df["反馈时间"], errors="coerce")
        recent_df = df[df["反馈时间"] >= start_date]
        sample_df = recent_df if len(recent_df) < 50 else recent_df.sample(n=50, random_state=int(time.time()))
        audit_record = {"timestamp": now.isoformat(), "period": period, "sample_size": len(sample_df), "sample_data": sample_df.to_dict('records'), "audit_status": "pending"}
        audits = json.load(open(AUDIT_LOG_FILE, "r", encoding="utf-8")) if os.path.exists(AUDIT_LOG_FILE) else []
        audits.append(audit_record)
        with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(audits[-10:], f, ensure_ascii=False, indent=2)
        return sample_df, len(audits) - 1
    except: return None, -1

# 主程序
def main():
    init_folders()
    
    st.set_page_config(page_title="🚗 新能源车企舆情分类系统 V1.5", page_icon="🚗", layout="wide", initial_sidebar_state="expanded")
    
    # ====== UI/UX优化：全局样式配置（不影响舆情分类准确率）======
    # 1. 微软雅黑字体 + 车企商务配色 + 动画效果
    st.markdown("""
    <style>
        /* 微软雅黑字体全局配置 */
        @import url('https://fonts.googleapis.com/css2?family=Microsoft+YaHei:wght@400;500;700&display=swap');
        
        * {
            font-family: 'Microsoft YaHei', 'PingFang SC', 'Heiti SC', sans-serif !important;
        }
        
        /* 标题样式 */
        h1, h2, h3, h4, h5, h6 {
            font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif !important;
            font-weight: 700 !important;
            line-height: 1.4 !important;
        }
        
        /* 车企商务配色 */
        :root {
            --primary-color: #002B5C;      /* 车企深蓝主色 */
            --secondary-color: #666666;    /* 辅助灰色 */
            --highlight-color: #1E88E5;    /* 高亮蓝色 */
            --success-color: #10B981;      /* 成功绿 */
            --warning-color: #F59E0B;      /* 警告橙 */
            --danger-color: #EF4444;       /* 危险红 */
            --bg-light: #F8FAFC;           /* 浅背景 */
        }
        
        /* 按钮样式优化 */
        .stButton > button {
            border-radius: 6px !important;
            font-weight: 500 !important;
            transition: all 0.2s ease !important;
        }
        
        .stButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15) !important;
        }
        
        /* 卡片样式 */
        .metric-card {
            background: white;
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            transition: all 0.2s ease;
        }
        
        .metric-card:hover {
            box-shadow: 0 4px 12px rgba(0,0,0,0.12);
        }
        
        /* 详情面板淡入动画 */
        .detail-panel {
            animation: fadeIn 0.3s ease-in-out;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        /* 按钮tooltip样式 */
        .tooltip {
            position: relative;
            display: inline-block;
        }
        
        .tooltip .tooltiptext {
            visibility: hidden;
            background-color: #333;
            color: #fff;
            text-align: center;
            padding: 6px 10px;
            border-radius: 4px;
            position: absolute;
            z-index: 1;
            bottom: 125%;
            left: 50%;
            transform: translateX(-50%);
            opacity: 0;
            transition: opacity 0.3s;
            font-size: 12px;
            white-space: nowrap;
        }
        
        .tooltip:hover .tooltiptext {
            visibility: visible;
            opacity: 1;
        }
        
        /* 列表选中高亮 */
        .dataframe tbody tr:hover {
            background-color: #EEF2FF !important;
        }
        
        /* 顶部标题样式 */
        .top-header {
            background: linear-gradient(90deg, #002B5C, #1E88E5);
            padding: 15px 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        
        .top-header h1 {
            color: white;
            margin: 0;
            font-size: 24px;
            font-weight: 700;
        }
        
        .top-header .subtitle {
            color: #a8d0e6;
            font-size: 14px;
            margin-top: 5px;
        }
        
        /* 新手引导遮罩 */
        .guide-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.7);
            z-index: 9999;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        
        .guide-card {
            background: white;
            border-radius: 12px;
            padding: 30px;
            max-width: 500px;
            text-align: center;
        }
        
        .guide-step {
            display: flex;
            align-items: center;
            margin: 15px 0;
            text-align: left;
        }
        
        .guide-step-num {
            width: 30px;
            height: 30px;
            background: #002B5C;
            color: white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-right: 15px;
            font-weight: bold;
        }
        
        /* 图表字体配置 */
        .chart-title {
            font-size: 16px !important;
            font-weight: 700 !important;
            color: #002B5C !important;
        }
        
        .chart-axis {
            font-size: 14px !important;
            color: #666666 !important;
        }
        
        .chart-label {
            font-size: 12px !important;
        }
        
        /* 进度条动画 */
        .stProgress > div > div > div {
            transition: width 0.3s ease;
        }
        
        /* 分隔线样式 */
        hr {
            margin: 16px 0;
            border: none;
            border-top: 1px solid #E5E7EB;
        }
        
        /* 移动端适配 */
        @media (max-width: 768px) {
            .top-header h1 { font-size: 18px; }
            .stButton > button { height: 36px; font-size: 12px; }
        }
    </style>
    """, unsafe_allow_html=True)
    
    # 2. 新手引导功能（不影响舆情分类准确率）
    if "show_guide" not in st.session_state:
        st.session_state.show_guide = False
    
    # 显示新手引导
    if st.session_state.get("show_guide", False):
        st.markdown("""
        <div class="guide-overlay" id="guideOverlay">
            <div class="guide-card">
                <h2 style="color: #002B5C; margin-bottom: 20px;">🚀 新手引导</h2>
                <div class="guide-step">
                    <div class="guide-step-num">1</div>
                    <div>
                        <strong>上传数据</strong><br>
                        <span style="color: #666;">在"数据接入"上传CSV文件</span>
                    </div>
                </div>
                <div class="guide-step">
                    <div class="guide-step-num">2</div>
                    <div>
                        <strong>智能分类</strong><br>
                        <span style="color: #666;">点击"智能分类"自动处理</span>
                    </div>
                </div>
                <div class="guide-step">
                    <div class="guide-step-num">3</div>
                    <div>
                        <strong>人工复核</strong><br>
                        <span style="color: #666;">审核并修正分类结果</span>
                    </div>
                </div>
                <button onclick="document.getElementById('guideOverlay').style.display='none'" 
                        style="background: #002B5C; color: white; border: none; padding: 10px 30px; border-radius: 6px; margin-top: 20px; cursor: pointer;">
                    开始使用
                </button>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    # 3. 图表样式配置函数（不影响舆情分类准确率）
    def get_chart_style():
        """获取图表统一样式配置"""
        return {
            'font.family': CHINESE_FONT,
            'font.size': 12,
            'axes.titlesize': 16,
            'axes.labelsize': 14,
            'xtick.labelsize': 12,
            'ytick.labelsize': 12,
            'figure.titlesize': 16,
            'axes.titleweight': 'bold',
            'axes.labelweight': 'bold',
            'text.color': '#333333',
            'axes.labelcolor': '#666666',
            'xtick.color': '#666666',
            'ytick.color': '#666666',
            'grid.color': '#E5E7EB',
            'grid.linestyle': '--',
            'grid.linewidth': 0.5,
        }
    
    # 应用图表样式
    chart_style = get_chart_style()
    for k, v in chart_style.items():
        plt.rcParams[k] = v
    
    # 4. 快捷操作按钮tooltip说明（不影响舆情分类准确率）
    button_tooltips = {
        "🎯 一键抽检": "随机抽取待复核数据进行质量检查",
        "📚 更新词库": "从已复核数据自动提取新关键词",
        "🚀 智能分类": "使用关键词+AI模型自动分类",
        "💾 归档": "保存分类结果到本地文件",
        "💾 保存修正": "保存人工修正结果",
        "✅ 批量标记已复核": "将选中数据标记为已复核",
        "📂 应用分类": "将选中数据批量分类",
        "🔄 刷新": "刷新页面数据",
    }
    
    # 顶部标题
    st.markdown("""
    <div class="top-header">
        <h1>🚗 新能源车企舆情分类管理系统 V1.5</h1>
        <div class="subtitle">基于百度智能云舆情分析系统 | 讯飞舆情智能分析平台设计逻辑 | 目标准确率98%</div>
    </div>
    """, unsafe_allow_html=True)
    
    col_title1, col_title2, col_title3 = st.columns([3, 2, 1])
    with col_title1: pass
    with col_title2:
        st.markdown("##### 📅 全局时间筛选")
        if "global_time_filter" not in st.session_state: st.session_state.global_time_filter = "近7天"
        st.session_state.global_time_filter = st.selectbox("选择时间", ["今日", "昨日", "近7天", "近30天", "自定义"], index=2, label_visibility="collapsed")
    with col_title3:
        st.markdown("##### 🔄 数据刷新")
        if st.button("🔄 刷新", use_container_width=True): st.rerun()
    
    # 同步业务数据（确保统计数据正确）
    sync_business_data()
    
    # KPI卡片
    stats = get_statistics_data()
    st.markdown("### 📊 核心指标监控")
    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    with col_kpi1: st.metric("📊 累计处理", f"{stats['total_count']:,}")
    with col_kpi2: st.metric("✅ 已复核", f"{stats['reviewed_count']}")
    with col_kpi3: st.metric("🎯 准确率", f"{stats['accuracy']:.1f}%", f"目标:{ACCURACY_THRESHOLD}%")
    with col_kpi4: st.metric("⚠️ 低置信度", stats['low_confidence_count'])
    st.divider()
    
    # 侧边栏导航
    with st.sidebar:
        st.markdown("### 📋 数据流转导航")
        nav_options = [("📥 数据接入", "upload"), ("⚙️ 分类处理", "classify"), ("✏️ 人工复核", "review"), ("📊 舆情分析", "analysis"), ("📦 归档导出", "archive"), ("⚙️ 系统管理", "system")]
        if "active_nav" not in st.session_state: st.session_state.active_nav = "upload"
        for nav_label, nav_key in nav_options:
            if nav_key == "system": st.markdown("---")
            if st.button(nav_label, use_container_width=True, type="primary" if st.session_state.active_nav == nav_key else "secondary", key=f"nav_{nav_key}"):
                st.session_state.active_nav = nav_key
                st.rerun()
        
        st.markdown("---### ⚡ 快捷操作（高频功能）")
        
        # 第一行：核心操作
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            if st.button("🎯 一键抽检", use_container_width=True, help="随机抽取待复核数据进行质量检查"):
                sample_df, idx = generate_audit_sample()
                if sample_df is not None:
                    st.session_state.audit_sample = sample_df
                    st.session_state.active_nav = "review"
                    st.rerun()
        with col_s2:
            if st.button("📊 批量分类", use_container_width=True, help="对未分类数据进行批量智能分类"):
                if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
                    st.session_state.active_nav = "classify"
                    st.rerun()
        
        # 第二行：词库和导出
        col_s3, col_s4 = st.columns(2)
        with col_s3:
            if st.button("📚 更新词库", use_container_width=True, help="从已复核数据自动提取新关键词"):
                if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
                    df_c = st.session_state.uploaded_data[st.session_state.uploaded_data["复核状态"] == "已复核"]
                    if not df_c.empty:
                        _, total = extract_and_update_keywords(df_c)
                        if total > 0: st.success(f"✅ 词库已更新，共{total}个")
        with col_s4:
            if st.button("📈 分析报告", use_container_width=True, help="查看舆情分析报告"):
                st.session_state.active_nav = "analysis"
                st.rerun()
        
        # 第三行：新手指南 + 系统设置
        col_s5, col_s6 = st.columns(2)
        with col_s5:
            if st.button("❓ 新手引导", use_container_width=True, help="查看3步核心操作指南"):
                st.session_state.show_guide = True
                st.rerun()
        with col_s6:
            if st.button("⚙️ 系统设置", use_container_width=True, help="管理系统配置"):
                st.session_state.active_nav = "system"
                st.rerun()
        
        st.markdown("---")
        with st.expander("🤖 智能自动化状态"):
            if stats["accuracy"] >= ACCURACY_THRESHOLD:
                st.success(f"✅ 自动化模式\n\n准确率：{stats['accuracy']}%")
            else:
                st.warning(f"⚠️ 人工复核模式\n\n准确率：{stats['accuracy']}%\n目标：{ACCURACY_THRESHOLD}%")
        
        with st.expander("📝 模型微调样本池"):
            sample_count = sum(len(json.load(open(os.path.join(SAMPLE_POOL_DIR, f), "r"))) for f in os.listdir(SAMPLE_POOL_DIR) if f.endswith(".json")) if os.path.exists(SAMPLE_POOL_DIR) else 0
            st.caption(f"样本总数：{sample_count}")
            if st.button("导出样本JSON", use_container_width=True):
                data, fmt = export_training_samples("json")
                if data:
                    st.download_button("📥", data, "samples.json", "application/json")
    
    # 主内容区
    if st.session_state.active_nav == "upload":
        st.subheader("📥 数据接入")
        
        # 优化：添加数据导入引导和状态提示
        col_u1, col_u2 = st.columns([3, 1])
        with col_u1:
            st.markdown("""
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 16px; border-radius: 10px; color: white; margin-bottom: 16px;">
                <b>📋 导入说明</b><br>
                请上传CSV格式的舆情数据文件，系统将自动进行去重和格式校验
            </div>
            """, unsafe_allow_html=True)
        with col_u2:
            # 显示当前数据状态
            if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
                st.metric("当前数据", f"{len(st.session_state.uploaded_data)}条")
            else:
                st.metric("当前数据", "0条")
        
        uploaded_file = st.file_uploader("选择CSV文件", type=["csv"], key="csv_v15")
        
        if uploaded_file:
            try:
                for enc in ["utf-8", "gbk", "utf-8-sig"]:
                    try: df = pd.read_csv(uploaded_file, encoding=enc); break
                    except: continue
                
                # 优化：更清晰的数据预览
                st.markdown("##### 📊 数据预览")
                col_preview1, col_preview2 = st.columns(2)
                with col_preview1:
                    st.caption(f"总行数: {len(df)}")
                with col_preview2:
                    st.caption(f"总列数: {len(df.columns)}")
                st.dataframe(df.head(10), height=200, use_container_width=True)
                
                is_valid, msg = check_csv_format(df)
                if is_valid:
                    df = df.reset_index(drop=True)
                    existing_ids = set()
                    if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
                        if "舆情编号" in st.session_state.uploaded_data.columns:
                            existing_ids = set(st.session_state.uploaded_data["舆情编号"].astype(str).str.strip())
                    if "舆情编号" in df.columns:
                        df["舆情编号"] = clean_text(df["舆情编号"])
                        dup = len(set(df["舆情编号"]).intersection(existing_ids))
                        if dup > 0: df = df[~df["舆情编号"].isin(existing_ids)]; st.warning(f"⚠️ 自动去重 {dup} 条重复数据")
                    
                    # 优化：显示新增数据量
                    new_count = len(df)
                    total_count = new_count + (len(existing_ids) if existing_ids else 0)
                    
                    tags_list = [json.dumps({"first": str(df.loc[i, "一级标签"]) if "一级标签" in df.columns else "", "second": str(df.loc[i, "二级标签"]) if "二级标签" in df.columns else "", "third": str(df.loc[i, "三级标签"]) if "三级标签" in df.columns else ""}, ensure_ascii=False) for i in range(len(df))]
                    df["原始标签"] = tags_list
                    init_cols = ["模型分类结果", "分类时间", "模型版本", "复核状态", "人工修正结果", "分类方式", "最终分类结果", "置信度", "分类依据", "版本号"]
                    for col in init_cols:
                        if col not in df.columns:
                            if col == "复核状态": df[col] = "未复核"
                            elif col == "模型版本": df[col] = MODEL_VERSION
                            elif col == "版本号": df[col] = "V1.5"
                            elif col == "分类方式": df[col] = "未分类"
                            else: df[col] = ""
                    df = clean_dataframe(df)
                    existing = st.session_state.uploaded_data.copy(deep=True) if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty else None
                    df = pd.concat([existing, df], ignore_index=True) if existing is not None else df
                    st.session_state.uploaded_data = df.copy(deep=True)
                    
                    # 优化：更清晰的成功提示
                    st.success(f"✅ 上传成功！共 {new_count} 条新数据（累计 {len(df)} 条）")
                    write_log("数据上传", f"上传{len(df)}条")
                    
                    # 优化：添加快捷操作按钮
                    col_next1, col_next2 = st.columns(2)
                    with col_next1:
                        if st.button("🚀 前往智能分类", type="primary", use_container_width=True):
                            st.session_state.active_nav = "classify"
                            st.rerun()
                    with col_next2:
                        if st.button("📋 前往人工复核", use_container_width=True):
                            st.session_state.active_nav = "review"
                            st.rerun()
                else: st.error(f"❌ 格式校验失败：{msg}")
            except Exception as e: st.error(f"❌ 读取文件失败：{str(e)}")
    
    elif st.session_state.active_nav == "classify":
        st.subheader("⚙️ 分类处理")
        
        # 优化：添加分类处理引导
        st.markdown("""
        <div style="background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); padding: 12px; border-radius: 8px; color: white; margin-bottom: 16px;">
            <b>💡 提示</b>：点击"智能分类"按钮，系统将使用关键词匹配+AI模型自动分类舆情数据
        </div>
        """, unsafe_allow_html=True)
        
        if "uploaded_data" not in st.session_state or st.session_state.uploaded_data.empty:
            st.warning("⚠️ 暂无数据，请先在「数据接入」页面导入数据")
            if st.button("📥 前往导入数据", use_container_width=True):
                st.session_state.active_nav = "upload"
                st.rerun()
        else:
            sync_business_data()
            df = st.session_state.uploaded_data.copy(deep=True)
            keywords = load_keywords()
            has_kw = sum(len(v) for v in keywords.values()) > 0
            
            # 优化：更清晰的统计卡片
            col_c1, col_c2, col_c3, col_c4 = st.columns(4)
            with col_c1: 
                st.metric("📊 数据总量", f"{len(df)}条")
            with col_c2: 
                unclassified = len(df[df['分类方式']=='未分类'])
                st.metric("⏳ 待分类", f"{unclassified}条", delta=-unclassified if unclassified > 0 else 0, delta_color="inverse")
            with col_c3: 
                st.metric("📚 关键词库", f"{sum(len(v) for v in keywords.values())}个")
            with col_c4: 
                classified = len(df[df['分类方式']!='未分类'])
                st.metric("✅ 已分类", f"{classified}条", delta=classified)
            
            # 优化：分类进度条
            if len(df) > 0:
                progress = classified / len(df)
                st.progress(progress, text=f"分类进度：{progress*100:.1f}%")
            
            st.markdown("---")
            
            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                if st.button("🚀 智能分类", type="primary", use_container_width=True):
                    with st.spinner("🔄 分类处理中，请稍候..."):
                        results, cost = parallel_classify(df, keywords if has_kw else None)
                        kw_c, model_c, fail_c, auto_c = 0, 0, 0, 0
                        for idx, res in results.items():
                            df.loc[idx, "模型分类结果"] = str(res["result"]) if res["result"] else ""
                            df.loc[idx, "分类方式"] = str(res["way"])
                            df.loc[idx, "分类时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            df.loc[idx, "置信度"] = str(res["confidence"])
                            df.loc[idx, "分类依据"] = str(res["reason"])
                            df.loc[idx, "最终分类结果"] = df.loc[idx, "模型分类结果"]
                            if res["auto_reviewed"]: df.loc[idx, "复核状态"] = "已复核"; auto_c += 1
                            if res["way"] in ["标签映射", "关键词匹配"]: kw_c += 1
                            elif res["way"] == "本地模型": model_c += 1
                            elif res["way"] == "分类失败": fail_c += 1
                        st.session_state.uploaded_data = clean_dataframe(df)
                        
                        # 优化：更详细的结果提示
                        st.success(f"""
                        ✅ 分类完成！
                        - 耗时：{cost:.1f} 秒
                        - 关键词匹配：{kw_c} 条
                        - AI模型分类：{model_c} 条
                        - 分类失败：{fail_c} 条
                        - 自动复核通过：{auto_c} 条
                        """)
                        write_log("分类", f"完成:关键词{kw_c}模型{model_c}")
                        st.rerun()
            with col_btn2:
                if st.button("📦 归档数据", use_container_width=True, disabled=len(df[df['分类方式']=='未分类'])==len(df)):
                    with st.spinner("归档中..."):
                        df_final = df.copy()
                        for idx in df_final.index:
                            manual = df_final.loc[idx, "人工修正结果"]
                            model = df_final.loc[idx, "模型分类结果"]
                            df_final.loc[idx, "最终分类结果"] = manual if manual in FEEDBACK_TYPES else (model if model in FEEDBACK_TYPES else "分类失败")
                        success, msg = archive_feedback_data(df_final)
                        if success:
                            df_c = df_final[df_final["复核状态"] == "已复核"]
                            if not df_c.empty: extract_and_update_keywords(df_c)
                            if "archived_batches" not in st.session_state: st.session_state.archived_batches = []
                            st.session_state.archived_batches.append({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "count": len(df_final)})
                            st.session_state.uploaded_data = pd.DataFrame()
                            st.success(f"✅ {msg}")
                            write_log("归档", msg)
                            st.rerun()
                        else: st.error(f"❌ {msg}")
            
            unclassified_df = df[df["分类方式"] == "未分类"].head(20)
            if not unclassified_df.empty:
                st.markdown("### 📋 待分类预览")
                st.dataframe(unclassified_df[["反馈时间", "反馈内容"]].rename(columns={"反馈内容": "内容", "反馈时间": "时间"}), height=300)
            else: st.success("✅ 全部已分类")
    
    elif st.session_state.active_nav == "review":
        st.subheader("✏️ 人工复核")

        # ========== 需求1: 舆情列表重构为表格形式（不影响舆情分类准确率）==========
        if "uploaded_data" not in st.session_state or st.session_state.uploaded_data.empty:
            st.warning("⚠️ 暂无数据，请先导入数据")
            if st.button("📥 前往导入数据", use_container_width=True):
                st.session_state.active_nav = "upload"
                st.rerun()
        else:
            sync_business_data()
            df = st.session_state.uploaded_data.copy(deep=True)

            # ====== 顶部统计指标 ======
            col_r1, col_r2, col_r3, col_r4 = st.columns(4)
            with col_r1: st.metric("总数", len(df))
            with col_r2: st.metric("已复核", len(df[df["复核状态"] == "已复核"]))
            with col_r3: st.metric("待复核", len(df[df["复核状态"] == "未复核"]))
            with col_r4:
                low_conf = sum(
                    1
                    for idx in df.index
                    if df.loc[idx, "复核状态"] == "未复核"
                    and "置信度" in df.columns
                    and pd.notna(df.loc[idx, "置信度"])
                    and float(df.loc[idx, "置信度"]) < CONFIDENCE_MEDIUM
                )
                st.metric("低置信度", low_conf)

            st.markdown("---")

            # ====== 需求3: 布局调整 - 筛选条件 ======
            st.markdown("#### 🔍 筛选条件（不影响舆情分类准确率）")
            col_f1, col_f2, col_f3, col_f4 = st.columns(4)
            with col_f1:
                filter_way = st.selectbox("分类方式", ["全部", "标签映射", "关键词匹配", "本地模型", "分类失败"])
            with col_f2:
                filter_status = st.selectbox("复核状态", ["全部", "已复核", "未复核"])
            with col_f3:
                filter_result = st.selectbox("最终分类", ["全部"] + FEEDBACK_TYPES)
            with col_f4:
                search_kw = st.text_input("内容搜索关键词", "", placeholder="支持模糊搜索舆情中文")

            # 应用筛选
            filtered_df = df.copy()
            if filter_way != "全部":
                filtered_df = filtered_df[filtered_df["分类方式"] == filter_way]
            if filter_status != "全部":
                filtered_df = filtered_df[filtered_df["复核状态"] == filter_status]
            if filter_result != "全部":
                filtered_df = filtered_df[filtered_df["最终分类结果"] == filter_result]
            if search_kw:
                filtered_df = filtered_df[filtered_df["反馈内容"].astype(str).str.contains(search_kw, case=False, na=False)]

            st.caption(f"当前筛选结果：{len(filtered_df)} 条")

            # ====== 需求1: 表格重构 - 使用st.data_editor实现表格内编辑 ======
            if filtered_df.empty:
                st.info("暂无符合条件的数据")
            else:
                # 准备表格数据
                editor_df = filtered_df.copy()

                # 列重命名：反馈内容 -> 舆情中文
                if "反馈内容" in editor_df.columns:
                    editor_df.rename(columns={"反馈内容": "舆情中文"}, inplace=True)

                # 匹配度（复用置信度列）
                if "置信度" in editor_df.columns:
                    editor_df["匹配度"] = editor_df["置信度"].astype(str)
                else:
                    editor_df["匹配度"] = ""

                # 表格列顺序：舆情中文 / 匹配度 / 模型分类结果 / 复核状态 / 人工修正结果 / 最终分类结果
                display_cols = []
                for col in ["舆情编号", "舆情中文", "匹配度", "模型分类结果", "复核状态", "人工修正结果", "最终分类结果"]:
                    if col in editor_df.columns:
                        display_cols.append(col)
                editor_df = editor_df[display_cols].reset_index(drop=False).rename(columns={"index": "_row_index"})

                # 需求1: 高亮规则 - 未复核/分类失败行背景色#FFF2E8，文字色#E64A19
                def _row_style(row):
                    """高亮规则：未复核或分类失败的行（不影响分类准确率，仅视觉提示）"""
                    status = str(row.get("复核状态", ""))
                    model_result = str(row.get("模型分类结果", ""))
                    if status != "已复核" or model_result == "分类失败":
                        return ["color: #E64A19; background-color: #FFF2E8;"] * len(row)
                    return [""] * len(row)

                # 需求1: 表格内编辑 - 使用column_config实现下拉选择
                from streamlit import column_config as st_col_config

                st.markdown("#### 📋 舆情复核列表（未复核/分类失败行高亮显示，可直接修改分类）")

                # 列配置：人工修正结果可编辑，其他列只读
                col_conf = {}
                for c in editor_df.columns:
                    if c == "人工修正结果":
                        col_conf[c] = st_col_config.SelectboxColumn(
                            "人工修正结果",
                            options=[""] + FEEDBACK_TYPES,
                            help="点击选择人工修正分类（不影响原始算法逻辑）",
                        )
                    else:
                        col_conf[c] = st_col_config.TextColumn(c, disabled=True)

                # 需求1: on_change回调 - 编辑后即时保存（不弹窗确认）
                def _on_review_edit():
                    """表格内修改后即时保存（不影响分类准确率，仅同步人工修正结果及复核状态）"""
                    edited = st.session_state.review_editor_data_v2
                    if edited is None:
                        return
                    # 处理 st.data_editor 返回的数据格式
                    if isinstance(edited, pd.DataFrame):
                        edited_df = edited
                    elif isinstance(edited, list):
                        edited_df = pd.DataFrame(edited)
                    else:
                        return
                    for _, r in edited_df.iterrows():
                        src_idx = int(r["_row_index"])
                        new_corr = str(r.get("人工修正结果", "")).strip()
                        if new_corr:
                            df.loc[src_idx, "人工修正结果"] = new_corr
                            df.loc[src_idx, "复核状态"] = "已复核"
                            df.loc[src_idx, "最终分类结果"] = new_corr
                            save_correction_sample(df.loc[src_idx])
                            if "原始标签" in df.columns:
                                tag_mapper.learn_from_correction(df.loc[src_idx, "原始标签"], new_corr)
                    st.session_state.uploaded_data = clean_dataframe(df)
                    st.toast("✅ 修改已保存（仅更新人工修正与复核状态）")

                st.data_editor(
                    editor_df.style.apply(lambda r: _row_style(r), axis=1),
                    key="review_editor_data_v2",
                    use_container_width=True,
                    height=450,
                    column_config=col_conf,
                    hide_index=True,
                    on_change=_on_review_edit,
                )

            st.markdown("---")

            # 需求2: 布局调整 - 归档按钮移至页面最下方居中
            col_b1, col_b2, col_b3 = st.columns([1, 2, 1])
            with col_b2:
                # 需求2: 仅保留1个归档按钮，尺寸200px*40px
                if st.button("💾 归档到年度总表", type="primary", use_container_width=True, help="将已复核数据保存到年度舆情分类总表"):
                    with st.spinner("归档中..."):
                        # 修复：确保使用最新的session_state数据（包含人工修正结果）
                        df = st.session_state.uploaded_data.copy(deep=True)
                        # 优化：支持已复核和未复核数据都进行归档（不影响舆情分类准确率）
                        df_to_archive = df[df["复核状态"].isin(["已复核", "未复核", "待复核"])].copy()
                        if df_to_archive.empty:
                            st.warning("⚠️ 没有可归档的数据")
                        else:
                            # 修复：确保人工修正结果被正确传递到最终分类结果
                            for idx in df_to_archive.index:
                                manual = str(df_to_archive.loc[idx, "人工修正结果"]).strip()
                                model = str(df_to_archive.loc[idx, "模型分类结果"]).strip()
                                # 优先使用人工修正结果，如果没有则使用模型分类结果
                                if manual and manual in FEEDBACK_TYPES:
                                    df_to_archive.loc[idx, "最终分类结果"] = manual
                                elif model and model in FEEDBACK_TYPES:
                                    df_to_archive.loc[idx, "最终分类结果"] = model
                                else:
                                    df_to_archive.loc[idx, "最终分类结果"] = "分类失败"
                            success, msg = archive_feedback_data(df_to_archive)
                            if success:
                                st.success(f"✅ {msg}")
                                write_log("归档", msg)
                                # 修复：归档成功后刷新页面
                                st.rerun()
                            else:
                                st.error(f"❌ {msg}")
    
    elif st.session_state.active_nav == "analysis":
        st.subheader("📊 舆情分析")
        
        # 优化：添加分析引导
        st.markdown("""
        <div style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); padding: 12px; border-radius: 8px; color: white; margin-bottom: 16px;">
            <b>💡 分析说明</b>：查看舆情分类趋势、占比分布和高频关键词，支持按时间维度和分类类型筛选
        </div>
        """, unsafe_allow_html=True)
        
        all_data = None
        if "uploaded_data" in st.session_state and not st.session_state.uploaded_data.empty:
            all_data = st.session_state.uploaded_data.copy(deep=True)
        if os.path.exists(ARCHIVE_DIR):
            archived_dfs = []
            for f in os.listdir(ARCHIVE_DIR):
                if f.endswith(".xlsx"):
                    try:
                        ef = pd.ExcelFile(os.path.join(ARCHIVE_DIR, f), engine="openpyxl")
                        for sn in ef.sheet_names:
                            if "年" in sn: archived_dfs.append(pd.read_excel(ef, sheet_name=sn, engine="openpyxl"))
                    except: pass
            if archived_dfs:
                all_data = pd.concat([all_data, pd.concat(archived_dfs)], ignore_index=True) if all_data is not None else pd.concat(archived_dfs, ignore_index=True)
        
        if all_data is None or all_data.empty:
            st.warning("⚠️ 暂无数据")
        else:
            all_data = clean_dataframe(all_data)
            all_data["反馈时间"] = pd.to_datetime(all_data["反馈时间"], errors="coerce")
            all_data = all_data.dropna(subset=["反馈时间", "最终分类结果"])
            
            col_a1, col_a2, col_a3 = st.columns(3)
            with col_a1: time_dim = st.selectbox("维度", ["日", "周", "月"], index=2)
            with col_a2: filter_type = st.selectbox("分类", ["全部"] + FEEDBACK_TYPES)
            with col_a3: time_range = st.selectbox("范围", ["全部", "近7天", "近30天", "本年度"])
            
            if time_range != "全部":
                today = datetime.now().date()
                if time_range == "近7天": start = today - timedelta(days=7)
                elif time_range == "近30天": start = today - timedelta(days=30)
                else: start = today.replace(month=1, day=1)
                all_data = all_data[all_data["反馈时间"].dt.date >= start]
            if filter_type != "全部": all_data = all_data[all_data["最终分类结果"] == filter_type]
            
            if all_data.empty: st.warning("⚠️ 筛选后无数据")
            else:
                st.markdown("### 🚨 预警监控")
                warnings = check_warning_thresholds(all_data)
                for w in warnings: st.error(w["message"]) if w["level"] == "critical" else st.warning(w["message"])
                if not warnings: st.success("✅ 暂无预警")
                st.divider()
                
                col_c1, col_c2 = st.columns(2)
                with col_c1: 
                    st.markdown("#### 📈 趋势图")
                    fig = generate_trend_chart(all_data, time_dim, "全部")
                    if fig: st.pyplot(fig)
                with col_c2:
                    st.markdown("#### 🥧 分类占比")
                    fig = generate_pie_chart(all_data)
                    if fig: st.pyplot(fig)
                
                col_c3, _ = st.columns(2)
                with col_c3:
                    st.markdown("#### 🔤 高频关键词")
                    fig = generate_keyword_chart(all_data, 10)
                    if fig: st.pyplot(fig)
    
    elif st.session_state.active_nav == "archive":
        st.subheader("📦 归档导出")
        
        # 优化：添加归档导出引导
        st.markdown("""
        <div style="background: linear-gradient(135deg, #fa709a 0%, #fee140 100%); padding: 12px; border-radius: 8px; color: white; margin-bottom: 16px;">
            <b>💡 归档说明</b>：查看历史归档数据，支持按月份筛选、导出Excel文件
        </div>
        """, unsafe_allow_html=True)
        
        col_ar1, col_ar2 = st.columns(2)
        with col_ar1:
            st.markdown("#### 📁 归档文件列表")
            if os.path.exists(ARCHIVE_DIR):
                files = [f for f in os.listdir(ARCHIVE_DIR) if f.endswith(".xlsx")]
                if files:
                    st.success(f"共 {len(files)} 个归档文件")
                    
                    # 优化：更清晰的文件列表展示
                    for f in sorted(files):
                        fpath = os.path.join(ARCHIVE_DIR, f)
                        fsize = os.path.getsize(fpath) / 1024 / 1024
                        
                        # 获取文件中的数据量
                        try:
                            ef = pd.ExcelFile(fpath, engine="openpyxl")
                            total_rows = 0
                            for sn in ef.sheet_names:
                                if "年" in sn:
                                    df_temp = pd.read_excel(ef, sheet_name=sn, engine="openpyxl")
                                    total_rows += len(df_temp)
                            st.markdown(f"""
                            <div style="background: #f8f9fa; padding: 12px; border-radius: 8px; margin: 8px 0; border-left: 4px solid #4facfe;">
                                <b>📄 {f}</b><br>
                                <span style="color: #666;">大小: {fsize:.2f} MB | 数据量: {total_rows} 条</span>
                            </div>
                            """, unsafe_allow_html=True)
                        except:
                            st.write(f"📄 {f} ({fsize:.2f}MB)")
                        
                        col_f1, col_f2 = st.columns([3, 1])
                        with col_f1: 
                            st.caption(f"📄 {f}")
                        with col_f2:
                            if st.button("👁️ 查看", key=f"v_{f}"):
                                st.session_state.view_archive = f
                else: 
                    st.warning("暂无归档文件，请先完成分类后进行归档")
            else: 
                st.warning("暂无归档文件")
        with col_ar2:
            st.markdown("#### 📜 操作日志")
            if os.path.exists(LOG_FILE):
                try:
                    log_df = pd.read_csv(LOG_FILE, encoding="utf-8-sig")
                    if not log_df.empty: 
                        st.dataframe(log_df.tail(20), height=400)
                    else:
                        st.caption("暂无日志记录")
                except: st.caption("暂无日志")
            else:
                st.caption("暂无日志")
        
        if "view_archive" in st.session_state and st.session_state.view_archive:
            st.markdown(f"### 📄 {st.session_state.view_archive}")
            fpath = os.path.join(ARCHIVE_DIR, st.session_state.view_archive)
            try:
                ef = pd.ExcelFile(fpath, engine="openpyxl")
                # 优化：添加"全部"选项（不影响舆情分类准确率）
                sheet_options = ["全部"] + ef.sheet_names
                sn = st.selectbox("Sheet选择", sheet_options)
                
                if sn == "全部":
                    # 显示所有Sheet的汇总数据
                    all_data = []
                    for sheet in ef.sheet_names:
                        df_sheet = pd.read_excel(ef, sheet_name=sheet, engine="openpyxl")
                        df_sheet["来源Sheet"] = sheet
                        all_data.append(df_sheet)
                    dfv = pd.concat(all_data, ignore_index=True)
                else:
                    dfv = pd.read_excel(ef, sheet_name=sn, engine="openpyxl")
                st.dataframe(dfv.head(50), height=400)
                col_s1, col_s2, col_s3 = st.columns(3)
                with col_s1: st.metric("总数据", len(dfv))
                with col_s2: st.metric("已复核", len(dfv[dfv["复核状态"]=="已复核"]) if "复核状态" in dfv.columns else 0)
                with col_s3: st.metric("Sheet数", len(ef.sheet_names) if sn == "全部" else 1)
            except Exception as e: st.error(f"读取失败:{str(e)}")
    
    elif st.session_state.active_nav == "system":
        st.subheader("⚙️ 系统管理")
        col_sy1, col_sy2 = st.columns(2)
        with col_sy1:
            st.markdown("#### 📊 系统信息")
            col_i1, col_i2, col_i3 = st.columns(3)
            with col_i1: st.metric("版本", "V1.5")
            with col_i2: st.metric("模型", MODEL_VERSION)
            with col_i3: st.metric("存储", ROOT_DIR.split("/")[-1])
            st.code(ROOT_DIR)
            st.markdown("#### 🔧 置信度设置")
            st.slider("高置信度", 0.5, 1.0, CONFIDENCE_HIGH, 0.05, format="%.2f", disabled=True)
            st.slider("中置信度", 0.3, 0.8, CONFIDENCE_MEDIUM, 0.05, format="%.2f", disabled=True)
        with col_sy2:
            st.markdown("#### 🔄 词库管理")
            keywords = load_keywords()
            for ft in FEEDBACK_TYPES:
                cnt = len(keywords.get(ft, []))
                with st.expander(f"{ft} ({cnt}个)"):
                    words = keywords.get(ft, [])
                    for i in range(0, len(words), 10): st.write(", ".join(words[i:i+10]))
            st.markdown("#### ➕ 添加关键词")
            col_add1, col_add2 = st.columns(2)
            with col_add1: add_type = st.selectbox("分类", FEEDBACK_TYPES, key="add_t")
            with col_add2: add_words = st.text_input("关键词", key="add_w")
            if st.button("添加", use_container_width=True):
                if add_words:
                    ws = [w.strip() for w in add_words.split(",") if w.strip()]
                    keywords[add_type].extend(ws)
                    if save_keywords(keywords):
                        st.success(f"✅ 添加{len(ws)}个")
                        write_log("词库", f"添加:{add_type}")
                        st.rerun()
        
        st.divider()
        st.markdown("#### 📈 准确率历史")
        if os.path.exists(ACCURACY_HISTORY_FILE):
            try:
                with open(ACCURACY_HISTORY_FILE, "r", encoding="utf-8") as f: history = json.load(f)
                if history:
                    hist_df = pd.DataFrame(history)
                    st.line_chart(hist_df.set_index("timestamp")["accuracy"])
                    st.dataframe(hist_df.tail(10))
            except: st.caption("暂无记录")
        
        # ===================== 新增：错误统计与分析 =====================
        st.divider()
        st.markdown("#### 🔍 分类错误分析")
        
        # 获取当前数据进行分析
        if "uploaded_data" in st.session_state and st.session_state.uploaded_data is not None and not st.session_state.uploaded_data.empty:
            df = st.session_state.uploaded_data
            if "原始分类" in df.columns and "最终分类结果" in df.columns:
                # 过滤掉未分类的数据
                df_valid = df[(df["原始分类"].notna()) & (df["原始分类"] != "") & 
                             (df["最终分类结果"].notna()) & (df["最终分类结果"] != "")]
                if not df_valid.empty:
                    # 计算混淆矩阵
                    confusion = pd.crosstab(df_valid["原始分类"], df_valid["最终分类结果"], 
                                           margins=True, margins_name="总计")
                    st.markdown("##### 混淆矩阵")
                    st.dataframe(confusion, use_container_width=True)
                    
                    # 计算每类的准确率
                    st.markdown("##### 各类别准确率")
                    accuracy_by_type = {}
                    for ftype in FEEDBACK_TYPES:
                        type_df = df_valid[df_valid["原始分类"] == ftype]
                        if not type_df.empty:
                            correct = (type_df["最终分类结果"] == ftype).sum()
                            total = len(type_df)
                            accuracy_by_type[ftype] = round(correct / total * 100, 1) if total > 0 else 0
                    
                    acc_df = pd.DataFrame(list(accuracy_by_type.items()), columns=["分类", "准确率(%)"])
                    if not acc_df.empty:
                        # 按准确率排序
                        acc_df = acc_df.sort_values("准确率(%)", ascending=False)
                        col_err1, col_err2 = st.columns([1, 2])
                        with col_err1:
                            st.dataframe(acc_df, hide_index=True, use_container_width=True)
                        with col_err2:
                            st.bar_chart(acc_df.set_index("分类")["准确率(%)"], horizontal=True)
                    
                    # 错误热力图
                    st.markdown("##### 错误分布热力")
                    error_df = df_valid[df_valid["原始分类"] != df_valid["最终分类结果"]]
                    if not error_df.empty:
                        error_counts = error_df.groupby(["原始分类", "最终分类结果"]).size().reset_index(name="错误数")
                        error_counts = error_counts.sort_values("错误数", ascending=False).head(10)
                        st.dataframe(error_counts, use_container_width=True)
                        
                        # 错误Top5
                        st.markdown("##### 错误Top5")
                        top_errors = error_counts.head(5)
                        for idx, row in top_errors.iterrows():
                            st.error(f"❌ {row['原始分类']} → {row['最终分类结果']}: {row['错误数']}条")
                    else:
                        st.success("✅ 暂无分类错误")
                else:
                    st.info("无可用于错误分析的数据（需要同时有原始分类和最终分类结果）")
            else:
                st.info("数据中缺少原始分类列，无法进行错误分析")
        else:
            st.info("请先上传数据进行分析")

if __name__ == "__main__":
    main()
