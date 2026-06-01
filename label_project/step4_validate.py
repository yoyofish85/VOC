# -*- coding: utf-8 -*-
"""
步骤4：映射关系验证
随机抽取100条数据验证映射准确性
"""

import os
import json
import random
import pandas as pd

# 配置
INPUT_RAW = "/Users/yuchao/Documents/AI Agent/testfile/label_project/label_raw.csv"
INPUT_MAPPING = "/Users/yuchao/Documents/AI Agent/testfile/label_project/label_hierarchy_optimized.json"
OUTPUT_REPORT = "/Users/yuchao/Documents/AI Agent/testfile/label_project/validation_report.md"
OUTPUT_FINAL = "/Users/yuchao/Documents/AI Agent/testfile/label_project/label_hierarchy_final.json"

# 样本大小
SAMPLE_SIZE = 100


def validate_mapping():
    """验证映射关系"""
    print("=" * 60)
    print("步骤4：映射关系验证")
    print("=" * 60)
    
    # 1. 读取原始数据
    print(f"\n[1/3] 读取原始数据...")
    df = pd.read_csv(INPUT_RAW, encoding='utf-8-sig')
    total_count = len(df)
    print(f"总记录数: {total_count}")
    
    # 2. 读取映射文件
    print(f"\n[2/3] 读取映射文件...")
    with open(INPUT_MAPPING, 'r', encoding='utf-8') as f:
        mapping = json.load(f)
    
    # 3. 随机抽样验证
    print(f"\n[3/3] 随机抽样验证 (样本数: {SAMPLE_SIZE})...")
    
    # 随机抽样
    if total_count > SAMPLE_SIZE:
        sample_df = df.sample(n=SAMPLE_SIZE, random_state=42)
    else:
        sample_df = df
    
    # 验证结果
    match_count = 0
    mismatch_list = []
    
    for idx, row in sample_df.iterrows():
        level1 = str(row["一级标签"]).strip()
        level2 = str(row["二级标签"]).strip()
        level3 = str(row["三级标签"]).strip()
        
        # 检查映射
        if level1 in mapping:
            level2_dict = mapping[level1]
            if level2 in level2_dict:
                level3_data = level2_dict[level2]
                level3_list = level3_data.get("标签列表", [])
                
                if level3 in level3_list:
                    match_count += 1
                else:
                    # 检查是否在详情中
                    details = level3_data.get("详情", {})
                    if level3 in details:
                        match_count += 1
                    else:
                        mismatch_list.append({
                            "一级": level1,
                            "二级": level2,
                            "三级": level3,
                            "原因": "三级标签不在映射列表中"
                        })
            else:
                mismatch_list.append({
                    "一级": level1,
                    "二级": level2,
                    "三级": level3,
                    "原因": "二级标签不在映射中"
                })
        else:
            mismatch_list.append({
                "一级": level1,
                "二级": level2,
                "三级": level3,
                "原因": "一级标签不在映射中"
            })
    
    # 计算匹配率
    match_rate = match_count / len(sample_df) * 100
    
    print(f"\n验证结果:")
    print(f"  - 抽样数: {len(sample_df)}")
    print(f"  - 匹配数: {match_count}")
    print(f"  - 匹配率: {match_rate:.2f}%")
    print(f"  - 不匹配数: {len(mismatch_list)}")
    
    # 生成报告
    print(f"\n生成验证报告...")
    
    report = f"""# 三级标签映射验证报告

## 一、验证概述

| 项目 | 内容 |
|------|------|
| 验证方法 | 随机抽样验证 |
| 原始数据量 | {total_count} 条 |
| 抽样数量 | {len(sample_df)} 条 |
| 匹配数量 | {match_count} 条 |
| 匹配率 | {match_rate:.2f}% |

## 二、验证结果

### 2.1 整体匹配情况

| 指标 | 数值 |
|------|------|
| 匹配数 | {match_count} |
| 不匹配数 | {len(mismatch_list)} |
| 匹配率 | {match_rate:.2f}% |

### 2.2 不匹配案例分析

"""
    
    if mismatch_list:
        report += "| 一级标签 | 二级标签 | 三级标签 | 不匹配原因 |\n"
        report += "|---------|---------|---------|-----------|\n"
        
        for item in mismatch_list[:20]:
            report += f"| {item['一级']} | {item['二级']} | {item['三级']} | {item['原因']} |\n"
    
    # 统计不匹配原因
    reason_stats = {}
    for item in mismatch_list:
        reason = item["原因"]
        reason_stats[reason] = reason_stats.get(reason, 0) + 1
    
    report += f"""
### 2.3 不匹配原因统计

| 不匹配原因 | 数量 |
|-----------|------|
"""
    
    for reason, count in reason_stats.items():
        report += f"| {reason} | {count} |\n"
    
    # 调整建议
    report += f"""
## 三、调整建议

"""
    
    if match_rate >= 95:
        report += "✅ 映射关系验证通过，匹配率 ≥ 95%\n"
    elif match_rate >= 80:
        report += "⚠️ 映射关系基本可用，建议针对不匹配案例进行优化\n"
    else:
        report += "❌ 映射关系存在较大问题，需要重新梳理\n"
    
    # 具体建议
    if "一级标签不在映射中" in reason_stats:
        report += f"\n1. **一级标签缺失**：{reason_stats.get('一级标签不在映射中', 0)}条，建议补充\n"
    if "二级标签不在映射中" in reason_stats:
        report += f"\n2. **二级标签缺失**：{reason_stats.get('二级标签不在映射中', 0)}条，建议补充\n"
    
    report += f"""
## 四、验证样本（前20条）

| 序号 | 一级标签 | 二级标签 | 三级标签 | 验证结果 |
|------|---------|---------|---------|---------|
"""
    
    for i, (idx, row) in enumerate(sample_df.head(20).iterrows()):
        level1 = row["一级标签"]
        level2 = row["二级标签"]
        level3 = row["三级标签"]
        
        # 检查是否匹配
        matched = "✅" if {
            "一级": str(level1),
            "二级": str(level2),
            "三级": str(level3)
        } not in mismatch_list else "❌"
        
        report += f"| {i+1} | {level1} | {level2} | {level3} | {matched} |\n"
    
    report += f"""
## 五、最终确认

基于验证结果，确认当前映射关系：

- **匹配率**: {match_rate:.2f}%
- **状态**: {"✅ 通过" if match_rate >= 95 else "⚠️ 需优化" if match_rate >= 80 else "❌ 需重审"}

---
*验证时间：2026-03-04*
*验证工具：step4_validate.py*
"""
    
    # 保存报告
    with open(OUTPUT_REPORT, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n✅ 验证报告: {OUTPUT_REPORT}")
    
    # 复制最终映射文件
    with open(INPUT_MAPPING, 'r', encoding='utf-8') as f:
        final_mapping = json.load(f)
    
    # 添加验证信息
    final_mapping["_验证信息"] = {
        "验证时间": "2026-03-04",
        "抽样数量": len(sample_df),
        "匹配数量": match_count,
        "匹配率": f"{match_rate:.2f}%",
        "状态": "✅ 通过" if match_rate >= 95 else "⚠️ 需优化"
    }
    
    with open(OUTPUT_FINAL, 'w', encoding='utf-8') as f:
        json.dump(final_mapping, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 最终映射文件: {OUTPUT_FINAL}")
    
    return match_rate, mismatch_list


if __name__ == "__main__":
    validate_mapping()
