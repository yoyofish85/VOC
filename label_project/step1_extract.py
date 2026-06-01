# -*- coding: utf-8 -*-
"""
步骤1：数据读取与字段提取
读取testdata3.csv，提取核心标签字段，生成清洗后的原始标签数据
"""

import os
import pandas as pd
import json
from datetime import datetime

# 配置
INPUT_FILE = "/Users/yuchao/Documents/AI Agent/testfile/testdata3.csv"
OUTPUT_DIR = "/Users/yuchao/Documents/AI Agent/testfile/label_project"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "label_raw.csv")

# 字段映射
FIELD_MAP = {
    "舆情编号": "舆情编号",
    "一级标签": "舆情分类",
    "二级标签": "问题简述", 
    "三级标签": "问题细分"
}


def clean_text(text):
    """清洗文本"""
    if pd.isna(text):
        return ""
    return str(text).strip()


def main():
    print("=" * 60)
    print("步骤1：数据读取与字段提取")
    print("=" * 60)
    
    # 1. 读取数据
    print(f"\n[1/4] 读取数据: {INPUT_FILE}")
    df = pd.read_csv(INPUT_FILE, encoding='utf-8-sig')
    print(f"原始数据量: {len(df)} 条")
    
    # 2. 提取核心字段
    print(f"\n[2/4] 提取核心字段...")
    
    # 使用舆情分类作为一级，问题简述作为二级，问题细分作为三级
    result = pd.DataFrame()
    result["舆情编号"] = df["舆情编号"].apply(clean_text)
    result["一级标签"] = df["舆情分类"].apply(clean_text)  # 使用舆情分类
    result["二级标签"] = df["问题简述"].apply(clean_text)
    result["三级标签"] = df["问题细分"].apply(clean_text)
    
    # 3. 数据清洗
    print(f"\n[3/4] 数据清洗...")
    
    # 剔除字段为空的记录
    original_count = len(result)
    result = result[result["一级标签"] != ""]
    result = result.dropna(subset=["二级标签", "三级标签"])
    result = result[result["二级标签"] != ""]
    cleaned_count = len(result)
    
    print(f"  - 剔除空记录: {original_count} → {cleaned_count} 条")
    
    # 4. 去重（按一级+二级+三级组合去重）
    print(f"\n[4/4] 去重处理...")
    result_dedup = result.drop_duplicates(subset=["一级标签", "二级标签", "三级标签"], keep="first")
    print(f"  - 去重后: {len(result_dedup)} 条唯一组合")
    
    # 5. 统计频次
    print(f"\n[5/4] 统计频次...")
    freq = result.groupby(["一级标签", "二级标签", "三级标签"]).size().reset_index(name="频次")
    result_final = result_dedup.merge(freq, on=["一级标签", "二级标签", "三级标签"], how="left")
    result_final = result_final.sort_values("频次", ascending=False)
    
    # 保存
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    result_final.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
    print(f"\n✅ 输出文件: {OUTPUT_FILE}")
    print(f"   总记录数: {len(result_final)}")
    
    # 统计信息
    print(f"\n标签统计:")
    print(f"  - 一级标签种类: {result_final['一级标签'].nunique()}")
    print(f"  - 二级标签种类: {result_final['二级标签'].nunique()}")
    print(f"  - 三级标签种类: {result_final['三级标签'].nunique()}")
    
    print(f"\n一级标签分布:")
    for label, count in result_final['一级标签'].value_counts().head(10).items():
        print(f"  {label}: {count}")
    
    return result_final


if __name__ == "__main__":
    main()
