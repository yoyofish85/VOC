#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2.4 数据清洗脚本
功能：修正testdata.csv边界模糊标注，生成review.csv
"""

import pandas as pd
import json
from collections import Counter

# ===================== 常量配置 =====================
INPUT_PATH = "/Users/yuchao/Documents/AI Agent/testfile/testdata.csv"
OUTPUT_CLEANED = "testdata_cleaned.csv"
OUTPUT_REVIEW = "review.csv"

# 分类映射
LABEL_MAPPING = {
    "产品质量类": "产品质量问题",
    "服务类": "销售服务问题",
    "体验需求类": "体验需求",
}

# 故障关键词
FAULT_KEYWORDS = [
    "故障", "异响", "死机", "黑屏", "卡顿", "自燃", "起火", "失控",
    "刹车失灵", "漏电", "漏水", "漏油", "熄火", "无法启动", "打不着",
    "报警", "充不进", "充不了", "无法充电", "不能充电", "充电故障",
    "电机故障", "电池故障", "充电桩故障", "超充站故障", "闪充站故障"
]

# 建议关键词
SUGGEST_KEYWORDS = ["建议", "希望", "期待", "优化", "改进", "提升", "完善", "增加", "添加"]

# 服务关键词
SERVICE_KEYWORDS = ["售后", "服务", "门店", "交付", "客服", "流程", "网点", "保养"]

# 非问题关键词
NON_PROBLEM_KEYWORDS = ["感谢", "谢谢", "好评", "满意", "表扬", "赞", "推荐", "认可"]

def analyze_content(content, label):
    """分析内容特征"""
    if pd.isna(content):
        return {"has_fault": False, "has_suggest": False, "has_service": False, "has_non_prob": False}
    
    text = str(content).lower()
    
    return {
        "has_fault": any(kw in text for kw in FAULT_KEYWORDS),
        "has_suggest": any(kw in text for kw in SUGGEST_KEYWORDS),
        "has_service": any(kw in text for kw in SERVICE_KEYWORDS),
        "has_non_prob": any(kw in text for kw in NON_PROBLEM_KEYWORDS)
    }

def determine_correct_label(content, original_label, features):
    """
    根据内容特征确定正确分类
    
    规则：
    1. 含故障词 → 产品质量问题（最高优先级）
    2. 含非问题词 → 非问题
    3. 含建议词且无故障词 → 体验需求
    4. 含服务词且无故障词 → 销售服务问题
    5. 其他保持原标注
    """
    if features["has_fault"]:
        return "产品质量问题", "含故障词"
    
    if features["has_non_prob"]:
        return "非问题", "含非问题词"
    
    if features["has_suggest"] and not features["has_fault"]:
        return "体验需求", "含建议词且无故障"
    
    if features["has_service"] and not features["has_fault"]:
        return "销售服务问题", "含服务词且无故障"
    
    # 保持原标注
    return original_label, "无边界特征"

def main():
    print("="*60)
    print("🚀 V2.4 数据清洗脚本")
    print("="*60)
    
    # 读取数据
    print(f"\n📁 读取数据: {INPUT_PATH}")
    df = pd.read_csv(INPUT_PATH, encoding='utf-8')
    print(f"  原始数据: {len(df)}条")
    
    # 分析每条数据
    review_records = []
    corrected_count = 0
    
    for idx, row in df.iterrows():
        content = row.get('舆情中文', '')
        original_label = row.get('舆情分类', '')
        
        # 转换为标准标签
        std_label = LABEL_MAPPING.get(original_label, original_label)
        
        # 分析特征
        features = analyze_content(content, std_label)
        
        # 确定正确分类
        correct_label, reason = determine_correct_label(content, std_label, features)
        
        # 记录需要修正的
        if correct_label != std_label:
            corrected_count += 1
            review_records.append({
                'index': idx,
                'content': str(content)[:150],
                'original_label': original_label,
                'corrected_label': correct_label,
                'reason': reason,
                'has_fault': features["has_fault"],
                'has_suggest': features["has_suggest"],
                'has_service': features["has_service"]
            })
    
    print(f"\n📊 清洗结果:")
    print(f"  需要修正: {corrected_count}条")
    
    # 生成修正后的数据
    df_cleaned = df.copy()
    
    for record in review_records:
        idx = record['index']
        new_label = record['corrected_label']
        
        # 映射回原始标签
        reverse_map = {v: k for k, v in LABEL_MAPPING.items()}
        original_new_label = reverse_map.get(new_label, new_label)
        
        df_cleaned.loc[idx, '舆情分类'] = original_new_label
    
    # 保存修正后的数据
    df_cleaned.to_csv(OUTPUT_CLEANED, index=False, encoding='utf-8-sig')
    print(f"  ✅ 已保存修正后数据: {OUTPUT_CLEANED}")
    
    # 保存review.csv
    review_df = pd.DataFrame(review_records)
    if not review_df.empty:
        review_df.to_csv(OUTPUT_REVIEW, index=False, encoding='utf-8-sig')
        print(f"  ✅ 已保存边界案例: {OUTPUT_REVIEW}")
    
    # 生成清洗报表
    print("\n" + "="*60)
    print("📋 清洗前后对比报表")
    print("="*60)
    
    # 原标注分布
    print("\n【清洗前标注分布】")
    orig_dist = df['舆情分类'].value_counts()
    for label, count in orig_dist.items():
        print(f"  {label}: {count}条")
    
    # 修正后分布
    print("\n【清洗后标注分布】")
    new_dist = df_cleaned['舆情分类'].value_counts()
    for label, count in new_dist.items():
        print(f"  {label}: {count}条")
    
    # 修正详情
    if review_records:
        print("\n【修正类型分布】")
        reason_counts = Counter([r['reason'] for r in review_records])
        for reason, count in reason_counts.most_common():
            print(f"  {reason}: {count}条")
        
        print("\n【误判类型分布】")
        error_type = [(r['original_label'], r['corrected_label']) for r in review_records]
        error_counts = Counter(error_type)
        for (orig, corr), count in error_counts.most_common(10):
            print(f"  {orig} → {corr}: {count}条")
    
    # 保存清洗报告
    report = {
        "total_records": len(df),
        "corrected_count": corrected_count,
        "original_distribution": orig_dist.to_dict(),
        "corrected_distribution": new_dist.to_dict() if not new_dist.empty else {},
        "correction_reasons": dict(Counter([r['reason'] for r in review_records])),
        "correction_types": {f"{k[0]}→{k[1]}": v for k, v in Counter([(r['original_label'], r['corrected_label']) for r in review_records]).most_common(10)}
    }
    
    with open('cleaning_report.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 清洗报告已保存: cleaning_report.json")
    print("\n" + "="*60)
    print("🎉 数据清洗完成！")
    print("="*60)

if __name__ == "__main__":
    main()
