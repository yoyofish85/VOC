#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2.4 8B模型微调脚本
功能：基于5628条数据微调DeepSeek R1-8B
注意：此脚本生成微调数据，实际微调需在本地执行
"""

import os
import json
import pandas as pd
import random
from collections import Counter

# ===================== 常量配置 =====================
DATA_PATH = "/Users/yuchao/Documents/AI Agent/testfile/testdata3.csv"
DATA2_PATH = "testdata2_fixed.csv"
OUTPUT_PATH = "finetune_v24.jsonl"

# 分类映射
LABEL_MAPPING = {
    "产品质量类": "产品质量问题",
    "服务类": "销售服务问题",
    "体验需求类": "体验需求"
}

def load_and_preprocess():
    """加载并预处理数据"""
    print("📁 加载数据...")
    
    # 加载testdata3
    df3 = pd.read_csv(DATA_PATH, encoding='utf-8')
    print(f"  testdata3: {len(df3)}条")
    
    # 加载testdata2_fixed
    try:
        df2 = pd.read_csv(DATA2_PATH, encoding='utf-8-sig')
        print(f"  testdata2_fixed: {len(df2)}条")
    except:
        df2 = pd.DataFrame()
    
    # 合并数据
    samples = []
    
    # 处理testdata3
    for idx, row in df3.iterrows():
        content = row.get('舆情中文', '')
        original_label = row.get('舆情分类', '')
        
        if pd.isna(content) or pd.isna(original_label):
            continue
        
        label = LABEL_MAPPING.get(original_label, original_label)
        
        samples.append({
            'content': str(content)[:500],
            'label': label
        })
    
    # 处理testdata2_fixed
    if not df2.empty:
        for idx, row in df2.iterrows():
            content = row.get('舆情中文', '')
            original_label = row.get('修正后分类', '')
            
            if pd.isna(content) or pd.isna(original_label):
                continue
            
            label = LABEL_MAPPING.get(original_label, original_label)
            
            samples.append({
                'content': str(content)[:500],
                'label': label
            })
    
    # 去重
    seen = set()
    unique_samples = []
    for s in samples:
        key = (s['content'][:100], s['label'])
        if key not in seen:
            seen.add(key)
            unique_samples.append(s)
    
    print(f"  合并去重后: {len(unique_samples)}条")
    return unique_samples

def analyze_boundary_cases(samples):
    """分析边界案例"""
    print("\n📊 分析边界案例...")
    
    fault_kw = ["故障", "异响", "死机", "黑屏", "问题"]
    suggest_kw = ["建议", "希望", "优化", "改进", "提升"]
    service_kw = ["售后", "服务", "门店", "交付"]
    
    boundary = []
    for s in samples:
        content = s['content'].lower()
        label = s['label']
        
        has_fault = any(kw in content for kw in fault_kw)
        has_suggest = any(kw in content for kw in suggest_kw)
        has_service = any(kw in content for kw in service_kw)
        
        # 边界案例：混合表述
        if label == '体验需求' and has_fault:
            boundary.append({**s, 'type': '体验需求+故障'})
        elif label == '销售服务问题' and has_fault:
            boundary.append({**s, 'type': '销售服务+故障'})
        elif label == '产品质量问题' and has_suggest:
            boundary.append({**s, 'type': '产品质量+建议'})
    
    print(f"  边界案例: {len(boundary)}条")
    return boundary

def generate_finetune_data(samples, boundary_samples, output_path):
    """生成微调数据"""
    print(f"\n📝 生成微调数据...")
    
    # 划分训练/验证集 (8:2)
    random.seed(42)
    random.shuffle(samples)
    
    train_size = int(len(samples) * 0.8)
    train_samples = samples[:train_size]
    val_samples = samples[train_size:]
    
    # 优先包含边界案例
    train_boundary = [s for s in boundary_samples if s in train_samples]
    val_boundary = [s for s in boundary_samples if s in val_samples]
    
    print(f"  训练集: {len(train_samples)}条 (含边界{len(train_boundary)}条)")
    print(f"  验证集: {len(val_samples)}条 (含边界{len(val_boundary)}条)")
    
    # 生成JSONL
    with open(output_path, 'w', encoding='utf-8') as f:
        for s in train_samples:
            prompt = f"用户反馈：{s['content']}"
            completion = s['label']
            
            f.write(json.dumps({
                "prompt": prompt,
                "completion": completion
            }, ensure_ascii=False) + '\n')
    
    print(f"  ✅ 已保存到 {output_path}")
    return train_samples, val_samples

def create_modelfile():
    """创建Ollama Modelfile"""
    system_prompt = """你是新能源车企舆情分类专家。请严格按以下5类分类：
- 产品质量问题：车辆硬件/软件故障、安全问题、性能问题；超充站/充电桩故障；
- 销售服务问题：售后服务、门店服务、交付问题（不含故障）；
- 产品咨询：价格、配置、功能咨询；
- 体验需求：优化建议、功能改进（非故障类）；
- 非问题：表扬、好评、感谢、无故障正面反馈。

关键区分规则：
1. "建议/希望/优化"无故障词 → 体验需求
2. "建议/希望/优化"有故障词 → 产品质量问题
3. "售后/服务/门店"无故障词 → 销售服务问题
4. "售后/服务"有故障词 → 产品质量问题
5. "异响/死机/黑屏/故障" → 产品质量问题
6. "表扬/感谢/好评" → 非问题

只输出分类名称。"""
    
    content = "FROM deepseek-r1:8b\n\n"
    content += "SYSTEM \"\"\"\n" + system_prompt + "\n\"\"\"\n\n"
    content += "PARAMETER temperature 0.1\n"
    content += "PARAMETER top_p 0.9\n"
    content += "PARAMETER num_ctx 4096\n"
    
    with open("Modelfile_v24", 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"  ✅ Modelfile已创建: Modelfile_v24")

def main():
    print("="*60)
    print("🚀 V2.4 8B模型微调数据准备")
    print("="*60)
    
    # 1. 加载数据
    samples = load_and_preprocess()
    
    # 2. 分析边界案例
    boundary_samples = analyze_boundary_cases(samples)
    
    # 3. 生成微调数据
    generate_finetune_data(samples, boundary_samples, OUTPUT_PATH)
    
    # 4. 创建Modelfile
    create_modelfile()
    
    # 5. 统计
    print("\n📊 分类分布:")
    labels = Counter([s['label'] for s in samples])
    for label, count in labels.most_common():
        print(f"  {label}: {count}条")
    
    print("\n" + "="*60)
    print("✅ 微调数据准备完成")
    print("="*60)
    print(f"\n📋 下一步操作:")
    print(f"  1. 将 {OUTPUT_PATH} 用于模型微调")
    print(f"  2. 使用 Modelfile_v24 创建微调模型:")
    print(f"     ollama create deepseek-r1:8b-v24 -f Modelfile_v24")
    print(f"  3. MacBook M4 微调命令（需16GB+内存）:")
    print(f"     ollama run deepseek-r1:8b --train finetune_v24.jsonl")

if __name__ == "__main__":
    main()
