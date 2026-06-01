# -*- coding: utf-8 -*-
"""
步骤2：三级标签映射关系梳理
基于清洗后的标签数据，构建层级映射结构
"""

import os
import json
import pandas as pd
from collections import defaultdict

# 配置
INPUT_FILE = "/Users/yuchao/Documents/AI Agent/testfile/label_project/label_raw.csv"
OUTPUT_FILE = "/Users/yuchao/Documents/AI Agent/testfile/label_project/label_hierarchy.json"


def normalize_label(label):
    """标准化标签"""
    if pd.isna(label) or label == "":
        return "通用"
    return str(label).strip()


def build_hierarchy():
    """构建三级标签映射"""
    print("=" * 60)
    print("步骤2：三级标签映射关系梳理")
    print("=" * 60)
    
    # 读取数据
    print(f"\n[1/2] 读取数据: {INPUT_FILE}")
    df = pd.read_csv(INPUT_FILE, encoding='utf-8-sig')
    print(f"记录数: {len(df)}")
    
    # 构建层级结构
    # 结构: {一级分类: {二级分类: [三级分类列表]}}
    hierarchy = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    stats = defaultdict(lambda: defaultdict(int))
    
    for _, row in df.iterrows():
        level1 = normalize_label(row["一级标签"])
        level2 = normalize_label(row["二级标签"])
        level3 = normalize_label(row["三级标签"])
        freq = int(row.get("频次", 1))
        
        # 统计
        hierarchy[level1][level2][level3] += freq
        stats[level1]["count"] += freq
        stats[level1][level2] += freq
    
    # 转换为标准JSON格式
    result = {}
    for level1, level2_dict in hierarchy.items():
        result[level1] = {}
        for level2, level3_dict in level2_dict.items():
            # 按频次排序
            sorted_level3 = sorted(level3_dict.items(), key=lambda x: x[1], reverse=True)
            result[level1][level2] = {
                "标签列表": [item[0] for item in sorted_level3],
                "频次": sum(item[1] for item in sorted_level3),
                "详情": {item[0]: item[1] for item in sorted_level3}
            }
    
    # 添加统计信息
    result["_统计信息"] = {
        level1: {
            "总频次": data["count"],
            "二级标签数": len(data) - 1  # 减去count键
        }
        for level1, data in stats.items()
    }
    
    # 保存JSON
    print(f"\n[2/2] 保存映射文件...")
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 输出文件: {OUTPUT_FILE}")
    
    # 输出统计
    print(f"\n映射统计:")
    print(f"  - 一级标签: {len([k for k in result.keys() if not k.startswith('_')])}")
    for level1 in result.keys():
        if level1.startswith('_'):
            continue
        level2_count = len([k for k in result[level1].keys()])
        level3_count = sum(len(v.get("标签列表", [])) for v in result[level1].values())
        print(f"  - {level1}: {level2_count}个二级, {level3_count}个三级")
    
    return result


if __name__ == "__main__":
    build_hierarchy()
