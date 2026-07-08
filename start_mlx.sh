#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo '╔══════════════════════════════════════════╗'
echo '║   VOC V1.5 · 启动中                      ║'
echo '║   分类模型：MLX + LoRA (14B v2)           ║'
echo '║   Adapter: lora_adapter_14b_v2           ║'
echo '╚══════════════════════════════════════════╝'

PYTHON="$(bash scripts/resolve_venv_python.sh || true)"
if [[ -z "$PYTHON" ]]; then
  echo "❌ 未找到兼容的 .venv（需要 Python 3.11–3.13，不能用 3.14）"
  echo "   请执行: bash scripts/setup_server_venv.sh"
  exit 1
fi

if ! "$PYTHON" -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "❌ 虚拟环境缺少 fastapi/uvicorn: $PYTHON"
  echo "   请执行: $PYTHON -m pip install -r requirements.txt"
  exit 1
fi
if ! bash scripts/ensure_mlx_deps.sh "$PYTHON"; then
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

echo "✅ Python: $PYTHON"
echo "✅ Adapter: $VOC_MLX_ADAPTER"
echo "⏳ 后端启动等待上限: ${VOC_BACKEND_START_WAIT}s（大库迁移可能较慢）"
echo "⚠  MLX 模型在首次分类时加载（约 90s），启动阶段不会预加载"
echo ""
echo "前台启动（Ctrl+C 停止）..."
echo ""

exec "$PYTHON" app_launcher.py
