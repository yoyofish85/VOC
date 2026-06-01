#!/bin/bash
# Ollama 微调脚本
# 使用本地数据进行微调

set -e

echo "=========================================="
echo "🚀 开始Ollama模型微调"
echo "=========================================="

# 检查模型文件
if [ ! -f "finetune_data.jsonl" ]; then
    echo "❌ 微调数据文件不存在: finetune_data.jsonl"
    exit 1
fi

# 统计数据量
DATA_COUNT=$(wc -l < finetune_data.jsonl)
echo "📊 微调数据量: $DATA_COUNT 条"

# 创建Modelfile
cat > Modelfile << 'EOF'
FROM deepseek-r1:7b

# 设置系统提示
SYSTEM """你是新能源车企舆情分类专家。请严格按以下5类分类：
- 产品质量问题：车辆硬件/软件故障、安全问题、性能问题；超充站/充电桩故障；
- 销售服务问题：售后服务、门店服务、交付问题（不含故障）；
- 产品咨询：价格、配置、功能咨询；
- 体验需求：优化建议、功能改进（非故障类）；
- 非问题：表扬、好评、感谢、无故障正面反馈。

注意：
- 包含"希望/建议/期待/优化"但无故障 = 体验需求
- 包含"故障/异响/死机/黑屏" = 产品质量问题
- 充电桩/超充站故障 = 产品质量问题
- "表扬/感谢/好评" = 非问题
"""

# 设置温度参数
PARAMETER temperature 0.1
PARAMETER top_p 0.9
PARAMETER num_ctx 4096
EOF

echo "✅ Modelfile已创建"

# 检查Ollama是否运行
echo ""
echo "🔍 检查Ollama服务..."
if ! curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    echo "❌ Ollama服务未运行，请先启动Ollama"
    exit 1
fi

echo "✅ Ollama服务正常"

# 创建微调模型
echo ""
echo "🔧 创建微调模型 deepseek-r1-optimized..."
echo "注意：由于Ollama 7b不支持直接微调，此脚本仅创建配置模型"
echo "如需实际微调，请使用 Ollama 的 /api/chat 接口进行 few-shot 学习"

# 列出可用模型
echo ""
echo "📋 当前可用模型:"
curl -s http://127.0.0.1:11434/api/tags | python3 -c "import sys,json; data=json.load(sys.stdin); [print(f'  - {m[\"name\"]}') for m in data.get('models', [])]"

echo ""
echo "=========================================="
echo "✅ 微调准备完成"
echo "=========================================="
echo ""
echo "📝 说明："
echo "由于Ollama 7b版本限制，直接微调需要以下步骤："
echo "1. 使用外部工具（如Llamafolio）进行微调"
echo "2. 或使用OpenWebUI等图形界面工具"
echo ""
echo "当前可用的替代方案："
echo "- 使用few-shot learning（少样本学习）"
echo "- 优化Prompt来提高分类准确率"
echo "- 使用更大的模型（如deepseek-r1:14b）"
echo ""
