# -*- coding: utf-8 -*-
"""
三级标签映射项目 - 主执行脚本
按顺序执行：数据提取 -> 构建映射 -> 优化 -> 验证
"""

import os
import sys

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入各步骤脚本
import step1_extract
import step2_build_mapping
import step3_optimize
import step4_validate


def main():
    print("=" * 70)
    print("新能源车企舆情三级标签映射项目")
    print("=" * 70)
    print()
    
    # 步骤1：数据提取
    print("\n" + "=" * 70)
    print("【步骤1】数据读取与字段提取")
    print("=" * 70)
    step1_extract.main()
    
    # 步骤2：构建映射
    print("\n" + "=" * 70)
    print("【步骤2】三级标签映射关系梳理")
    print("=" * 70)
    step2_build_mapping.build_hierarchy()
    
    # 步骤3：优化
    print("\n" + "=" * 70)
    print("【步骤3】映射关系优化")
    print("=" * 70)
    step3_optimize.optimize_mapping()
    
    # 步骤4：验证
    print("\n" + "=" * 70)
    print("【步骤4】映射关系验证")
    print("=" * 70)
    match_rate, _ = step4_validate.validate_mapping()
    
    # 输出汇总
    print("\n" + "=" * 70)
    print("项目执行完成！")
    print("=" * 70)
    print("\n📁 输出文件清单：")
    print("  1. label_project/label_raw.csv          - 清洗后原始标签数据")
    print("  2. label_project/label_hierarchy.json   - 初始三级映射")
    print("  3. label_project/label_hierarchy_optimized.json - 优化后映射")
    print("  4. label_project/optimization_report.md - 优化说明")
    print("  5. label_project/validation_report.md   - 验证报告")
    print("  6. label_project/label_hierarchy_final.json   - 最终确认版映射")
    print(f"\n✅ 验证匹配率: {match_rate:.2f}%")


if __name__ == "__main__":
    main()
