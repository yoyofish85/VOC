#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo '╔══════════════════════════════════════════╗'
echo '║   VOC V1.5 · 启动中                      ║'
echo '║   分类模型：MLX + LoRA (14B v2)           ║'
echo '║   Adapter: lora_adapter_14b_v2           ║'
echo '╚══════════════════════════════════════════╝'

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [[ -f venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
else
  echo "❌ 未找到 .venv 或 venv，请先创建虚拟环境"
  exit 1
fi

export VOC_USE_MLX=1
export VOC_MLX_ADAPTER="${VOC_MLX_ADAPTER:-$HOME/lora_adapter_14b_v2}"
export VOC_MLX_MODEL="${VOC_MLX_MODEL:-mlx-community/Qwen2.5-14B-Instruct-4bit}"
export VOC_BACKEND_START_WAIT="${VOC_BACKEND_START_WAIT:-240}"
export VOC_UVICORN_WORKERS=1
unset VOC_UVICORN_RELOAD

if [[ ! -f "$VOC_MLX_ADAPTER/adapters.safetensors" ]]; then
  echo "❌ LoRA adapter 不存在: $VOC_MLX_ADAPTER/adapters.safetensors"
  echo "请确认 adapter 路径正确或先运行训练脚本"
  exit 1
fi
echo "✅ Adapter 验证通过: $VOC_MLX_ADAPTER"
echo "⏳ 后端启动等待上限: ${VOC_BACKEND_START_WAIT}s（大库迁移可能较慢）"
echo "⚠  MLX 模型在首次分类时加载（约 90s），启动阶段不会预加载"
echo ""
echo "前台启动（Ctrl+C 停止）..."
echo ""

exec python3 app_launcher.py
