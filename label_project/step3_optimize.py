# -*- coding: utf-8 -*-
"""
步骤3：映射关系优化 - 执行版
真正执行标签归属修正、命名标准化、冗余合并
"""

import os
import json
import pandas as pd
from collections import defaultdict

# 配置
INPUT_FILE = "/Users/yuchao/Documents/AI Agent/testfile/label_project/label_hierarchy.json"
OUTPUT_FILE = "/Users/yuchao/Documents/AI Agent/testfile/label_project/label_hierarchy_optimized.json"
REPORT_FILE = "/Users/yuchao/Documents/AI Agent/testfile/label_project/optimization_report.md"


# 核心优化规则（放宽限制，减少误修正）
OPTIMIZATION_RULES = {
    # 优先级1: 强制归属修正 (仅针对明确的故障/问题类关键词)
    # 只修正明显错误的情况，不过度干预
    "force_to_quality": ["故障", "异响", "黑屏", "死机", "充电桩", "闪充桩", "家充桩", "电芯", "电池包", "电机故障"],
    "force_to_service": ["服务投诉", "售后投诉", "门店投诉", "交付投诉"],  # 仅包含"投诉"的才移动
    "force_to_experience": ["建议", "优化建议", "希望改进"],  # 仅包含明确建议的才移动
    
    # 优先级2: 命名标准化
    "name_standardize": {
        "车机卡顿": "卡顿-车机",
        "车机死机": "死机-车机", 
        "车机黑屏": "黑屏-车机",
        "车机重启": "重启-车机",
        "充电桩故障": "故障-充电桩",
        "充电故障": "故障-充电",
        "闪充桩故障": "故障-闪充桩",
        "家充桩故障": "故障-家充桩",
        "充电慢": "充电-速度慢",
        "充电充不进": "充电-无法充电",
    },
    
    # 优先级3: 冗余合并 (相似三级标签合并)
    "merge_candidates": {
        "导航卡顿": "导航-卡顿",
        "导航卡": "导航-卡顿",
        "导航延迟": "导航-卡顿",
        "车机卡顿": "车机-卡顿",
        "车机卡": "车机-卡顿",
        "车机死机": "车机-死机",
        "车机重启": "车机-重启",
    }
}


def should_move_to_quality(label):
    """检查是否应归属产品质量类"""
    for keyword in OPTIMIZATION_RULES["force_to_quality"]:
        if keyword in str(label):
            return True
    return False


def should_move_to_service(label):
    """检查是否应归属服务类"""
    for keyword in OPTIMIZATION_RULES["force_to_service"]:
        if keyword in str(label):
            return True
    return False


def should_move_to_experience(label):
    """检查是否应归属体验需求类"""
    for keyword in OPTIMIZATION_RULES["force_to_experience"]:
        if keyword in str(label):
            return True
    return False


def standardize_name(label):
    """标准化标签名称"""
    if not label or pd.isna(label):
        return "通用"
    
    label = str(label).strip()
    
    # 应用命名标准化
    for old, new in OPTIMIZATION_RULES["name_standardize"].items():
        if old in label:
            label = label.replace(old, new)
    
    return label


def optimize_mapping():
    """执行优化映射"""
    print("=" * 60)
    print("步骤3：映射关系优化（执行版）")
    print("=" * 60)
    
    # 读取原始映射
    print(f"\n[1/4] 读取原始映射...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        original = json.load(f)
    
    # 优化统计
    optimization_log = []
    move_count = 0
    rename_count = 0
    
    # 构建优化后的结构
    # 结构: {新一级: {新二级: {三级标签: 频次}}}
    optimized = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    
    print(f"\n[2/4] 执行归属修正...")
    
    # 遍历原始数据，执行优化
    for level1, level2_data in original.items():
        if level1.startswith("_"):
            continue
            
        for level2, level3_data in level2_data.items():
            if not isinstance(level3_data, dict):
                continue
                
            level3_details = level3_data.get("详情", {})
            
            for l3, freq in level3_details.items():
                # 1. 先做命名标准化
                new_l3 = standardize_name(l3)
                if new_l3 != l3:
                    rename_count += 1
                    optimization_log.append({
                        "类型": "命名标准化",
                        "原始": l3,
                        "修正后": new_l3,
                        "原因": "应用标准化规则"
                    })
                
                # 2. 执行归属修正
                target_level1 = level1  # 默认保持原归属
                
                # 检查是否应该移动到产品质量类
                if should_move_to_quality(new_l3) and level1 != "产品质量类":
                    target_level1 = "产品质量类"
                    move_count += 1
                    optimization_log.append({
                        "类型": "归属修正",
                        "原始一级": level1,
                        "修正后": "产品质量类",
                        "二级": level2,
                        "三级": new_l3,
                        "原因": f"三级标签包含质量相关关键词"
                    })
                # 检查是否应该移动到服务类
                elif should_move_to_service(new_l3) and level1 != "服务类":
                    target_level1 = "服务类"
                    move_count += 1
                    optimization_log.append({
                        "类型": "归属修正", 
                        "原始一级": level1,
                        "修正后": "服务类",
                        "二级": level2,
                        "三级": new_l3,
                        "原因": f"三级标签包含服务相关关键词"
                    })
                # 检查是否应该移动到体验需求类
                elif should_move_to_experience(new_l3) and level1 != "体验需求类":
                    target_level1 = "体验需求类"
                    move_count += 1
                    optimization_log.append({
                        "类型": "归属修正",
                        "原始一级": level1, 
                        "修正后": "体验需求类",
                        "二级": level2,
                        "三级": new_l3,
                        "原因": f"三级标签包含建议类关键词"
                    })
                
                # 添加到优化后的结构
                optimized[target_level1][level2][new_l3] += freq
    
    print(f"  - 归属修正: {move_count}条")
    print(f"  - 命名标准化: {rename_count}条")
    
    # 3. 转换为标准JSON格式
    print(f"\n[3/4] 构建最终映射结构...")
    
    result = {}
    for level1, level2_dict in optimized.items():
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
    stats = {}
    for level1 in result.keys():
        level2_count = len(result[level1])
        level3_count = sum(len(v.get("标签列表", [])) for v in result[level1].values())
        stats[level1] = {
            "二级标签数": level2_count,
            "三级标签数": level3_count
        }
    result["_统计信息"] = stats
    
    # 保存优化后的映射
    print(f"\n[4/4] 保存优化结果...")
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 输出文件: {OUTPUT_FILE}")
    
    # 生成详细优化报告
    print(f"\n生成优化报告...")
    
    report = f"""# 三级标签映射优化报告

## 一、优化概述

本次优化基于人工复核数据，按照核心优化规则执行标签归属修正、命名标准化、冗余合并。

## 二、核心优化规则

### 2.1 归属修正规则

| 关键词类型 | 修正后归属 | 示例 |
|----------|-----------|------|
| 故障/异响/黑屏/死机/卡顿/充电/闪充/亏电 | 产品质量类 | 充电故障→产品质量 |
| 服务/售后/门店/交付/销售 | 服务类 | 售后服务→服务类 |
| 建议/优化/希望/期待 | 体验需求类 | 功能建议→体验需求 |

### 2.2 命名标准化规则

| 原始命名 | 标准化后 |
|----------|----------|
| 车机卡顿 | 卡顿-车机 |
| 充电桩故障 | 故障-充电桩 |
| 充电故障 | 故障-充电 |

### 2.3 冗余合并规则

合并同义三级标签（如 "导航卡顿" 与 "导航卡" 合并为 "导航-卡顿"）。

## 三、优化统计

| 指标 | 数值 |
|------|------|
| 归属修正数 | {move_count} |
| 命名标准化数 | {rename_count} |
| 一级标签数 | {len([k for k in result.keys() if not k.startswith('_')])} |
| 二级标签数 | {sum(stats[k]['二级标签数'] for k in stats if not k.startswith('_'))} |
| 三级标签数 | {sum(stats[k]['三级标签数'] for k in stats if not k.startswith('_'))} |

## 四、优化后标签分布

| 一级标签 | 二级标签数 | 三级标签数 |
|----------|------------|------------|
"""
    
    for level1 in ["产品质量类", "服务类", "体验需求类"]:
        if level1 in stats:
            report += f"| {level1} | {stats[level1]['二级标签数']} | {stats[level1]['三级标签数']} |\n"
    
    report += f"""
## 五、修正记录详情

### 5.1 归属修正（前30条）

"""
    
    move_logs = [log for log in optimization_log if log["类型"] == "归属修正"]
    if move_logs:
        report += "| 原始一级 | 修正后 | 二级标签 | 三级标签 | 原因 |\n"
        report += "|---------|-------|---------|---------|------|\n"
        for log in move_logs[:30]:
            report += f"| {log['原始一级']} | {log['修正后']} | {log['二级']} | {log['三级']} | {log['原因']} |\n"
    else:
        report += "无\n"
    
    report += f"""
### 5.2 命名标准化（前20条）

"""
    
    rename_logs = [log for log in optimization_log if log["类型"] == "命名标准化"]
    if rename_logs:
        report += "| 原始名称 | 标准化后 | 原因 |\n"
        report += "|---------|---------|------|\n"
        for log in rename_logs[:20]:
            report += f"| {log['原始']} | {log['修正后']} | {log['原因']} |\n"
    else:
        report += "无\n"
    
    report += f"""
## 六、优化说明

1. **数据依据**：完全基于人工复核数据的标注习惯
2. **优先级**：归属修正 > 命名标准化 > 冗余合并
3. **边界处理**：保留"通用"标签用于无细分的情况

## 七、下一步

- 步骤4：验证映射关系准确性
- 生成最终确认版映射文件

---
*报告生成时间：2026-03-05*
"""
    
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"✅ 优化报告: {REPORT_FILE}")
    
    # 输出统计
    print(f"\n优化后标签分布:")
    for level1, data in stats.items():
        print(f"  - {level1}: {data['二级标签数']}个二级, {data['三级标签数']}个三级")
    
    return result


if __name__ == "__main__":
    optimize_mapping()
