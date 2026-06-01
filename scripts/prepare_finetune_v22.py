#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2.2 LoRA微调脚本
功能：基于5628条数据微调Ollama模型
注意：本地7b模型微调需要GPU支持，此脚本生成微调数据格式
"""

import os
import json
import pandas as pd
import random
from datetime import datetime, timedelta

# ===================== 常量配置 =====================
DATA_PATH = "/Users/yuchao/Documents/AI Agent/testfile/testdata3.csv"
OUTPUT_PATH = "finetune_v22.jsonl"

# 分类映射
LABEL_MAPPING = {
    "产品质量类": "产品质量问题",
    "服务类": "销售服务问题",
    "体验需求类": "体验需求"
}

# ===================== 数据预处理 =====================
def load_and_preprocess():
    """加载并预处理数据"""
    print("📁 加载数据...")
    df = pd.read_csv(DATA_PATH, encoding='utf-8')
    print(f"  原始数据: {len(df)}条")
    
    # 清洗数据
    samples = []
    for idx, row in df.iterrows():
        content = row.get('舆情中文', '')
        original_label = row.get('舆情分类', '')
        
        if pd.isna(content) or pd.isna(original_label):
            continue
        
        # 转换标签
        label = LABEL_MAPPING.get(original_label, original_label)
        
        # 提取问题细分作为补充信息
        problem_detail = row.get('问题细分', '')
        core_issue = str(problem_detail) if pd.notna(problem_detail) else ''
        
        samples.append({
            'content': str(content)[:500],  # 截断过长文本
            'label': label,
            'core_issue': core_issue
        })
    
    print(f"  有效样本: {len(samples)}条")
    return samples

def analyze_mixed_expressions(samples):
    """分析混合表述"""
    print("\n📊 分析混合表述...")
    
    fault_kw = ["故障", "异响", "死机", "黑屏", "卡顿", "问题", "损坏", "质量"]
    exp_kw = ["建议", "希望", "优化", "提升", "增加", "改进", "完善"]
    service_kw = ["售后", "服务", "门店", "交付", "客服"]
    
    mixed = []
    for s in samples:
        content = s['content']
        label = s['label']
        
        has_fault = any(kw in content for kw in fault_kw)
        has_exp = any(kw in content for kw in exp_kw)
        has_service = any(kw in content for kw in service_kw)
        
        # 提取混合表述
        if label == '体验需求' and has_fault:
            mixed.append({**s, 'type': '体验需求+故障'})
        elif label == '销售服务问题' and has_fault:
            mixed.append({**s, 'type': '销售服务+故障'})
        elif label == '产品质量问题' and has_exp:
            mixed.append({**s, 'type': '产品质量+建议'})
    
    print(f"  混合表述样本: {len(mixed)}条")
    return mixed

def generate_finetune_jsonl(samples, mixed_samples, output_path):
    """生成微调JSONL格式"""
    print(f"\n📝 生成微调数据...")
    
    # 划分训练/验证集 (8:2)
    random.seed(42)
    random.shuffle(samples)
    
    train_size = int(len(samples) * 0.8)
    train_samples = samples[:train_size]
    val_samples = samples[train_size:]
    
    # 优先包含混合样本
    train_mixed = [s for s in mixed_samples if s in train_samples]
    val_mixed = [s for s in mixed_samples if s in val_samples]
    
    print(f"  训练集: {len(train_samples)}条 (含混合{len(train_mixed)}条)")
    print(f"  验证集: {len(val_samples)}条 (含混合{len(val_mixed)}条)")
    
    # 生成JSONL
    with open(output_path, 'w', encoding='utf-8') as f:
        for s in train_samples:
            # 构建prompt
            prompt = f"""用户反馈：{s['content']}"""
            if s.get('core_issue'):
                prompt += f"\n问题细分：{s['core_issue']}"
            
            # 构建completion
            completion = s['label']
            
            # 写入JSONL
            f.write(json.dumps({
                "prompt": prompt,
                "completion": completion
            }, ensure_ascii=False) + '\n')
    
    print(f"  ✅ 已保存到 {output_path}")
    return train_samples, val_samples

def generate_system_prompt():
    """生成系统提示"""
    system_prompt = """你是新能源车企舆情分类专家。请严格按以下5类分类：
- 产品质量问题：车辆硬件/软件故障、安全问题、性能问题；超充站/充电桩故障；
- 销售服务问题：售后服务、门店服务、交付问题（不含故障）；
- 产品咨询：价格、配置、功能咨询；
- 体验需求：优化建议、功能改进（非故障类）；
- 非问题：表扬、好评、感谢、无故障正面反馈。

关键区分规则：
1. "建议/希望/优化" + 无故障词 → 体验需求
2. "建议/希望/优化" + 有故障词 → 产品质量问题
3. "售后/服务/门店" + 无故障词 → 销售服务问题
4. "售后/服务" + 有故障词 → 产品质量问题
5. "异响/死机/黑屏/故障" → 产品质量问题
6. "表扬/感谢/好评" → 非问题

只输出分类名称，不要输出其他内容。"""
    
    return system_prompt

def create_modelfile(system_prompt, model_path):
    """创建Ollama Modelfile"""
    # 使用字符串拼接避免语法错误
    content = "FROM deepseek-r1:7b\n\n"
    content += "SYSTEM \"\"\"\n" + system_prompt + "\n\"\"\"\n\n"
    content += "PARAMETER temperature 0.1\n"
    content += "PARAMETER top_p 0.9\n"
    content += "PARAMETER num_ctx 4096\n"
    
    with open(model_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"  ✅ Modelfile已创建: {model_path}")

def main():
    print("="*60)
    print("🚀 V2.2 数据预处理 + 微调准备")
    print("="*60)
    
    # 1. 加载数据
    samples = load_and_preprocess()
    
    # 2. 分析混合表述
    mixed_samples = analyze_mixed_expressions(samples)
    
    # 3. 生成微调数据
    generate_finetune_jsonl(samples, mixed_samples, OUTPUT_PATH)
    
    # 4. 生成系统提示
    system_prompt = generate_system_prompt()
    create_modelfile(system_prompt, "Modelfile_v22")
    
    # 5. 统计各分类
    print("\n📊 分类分布:")
    from collections import Counter
    labels = Counter([s['label'] for s in samples])
    for label, count in labels.most_common():
        print(f"  {label}: {count}条")
    
    print("\n" + "="*60)
    print("✅ 微调数据准备完成")
    print("="*60)
    print(f"\n📋 下一步操作:")
    print(f"  1. 将 {OUTPUT_PATH} 用于模型微调")
    print(f"  2. 使用 Modelfile_v22 创建微调模型:")
    print(f"     ollama create deepseek-r1-v22 -f Modelfile_v22")
    print(f"  3. 运行 stress_test_v22.py 进行测试")

if __name__ == "__main__":
    main()
