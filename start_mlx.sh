#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo '╔══════════════════════════════════════════╗'
echo '║   VOC V1.5 · 启动中                      ║'
echo '║   分类模型：MLX + LoRA (14B v2)           ║'
echo '║   Adapter: lora_adapter_14b_v2           ║'
echo '╚══════════════════════════════════════════╝'

source .venv/bin/activate

# 加载 MLX 环境变量
export VOC_USE_MLX=1
export VOC_MLX_ADAPTER="$HOME/lora_adapter_14b_v2"
export VOC_MLX_MODEL=mlx-community/Qwen2.5-14B-Instruct-4bit

# 验证 adapter 存在
if [ ! -f "$VOC_MLX_ADAPTER/adapters.safetensors" ]; then
  echo "❌ LoRA adapter 不存在: $VOC_MLX_ADAPTER/adapters.safetensors"
  echo "请确认 adapter 路径正确或先运行训练脚本"
  exit 1
fi
echo "✅ Adapter 验证通过: $VOC_MLX_ADAPTER"

# 通过 app_launcher.py 启动
python3 app_launcher.py &

# 等待后端启动
echo "等待后端..."
for i in $(seq 1 30); do
  sleep 1
  if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "✅ 后端已就绪 (http://localhost:8000)"
    break
  fi
  [ $i -lt 30 ] && echo -n "." || echo "❌ 后端启动超时"
done

# 显示模型信息
echo ""
python3 -c "import sys;sys.path.insert(0,'src/backend');from qwen_ollama import classify_text;r=classify_text('测试',db_path='src/backend/opinion_review.db');print(f'模型: {r[\"match_type\"]}')" 2>/dev/null || echo "模型: mlx_14b_lora (MLX+LoRA)"

echo ""
echo "前端: http://localhost:8080"
echo "⚠ 首次分类请求约需 90 秒加载 MLX 模型"
echo ""
