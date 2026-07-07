#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo '╔══════════════════════════════════════════╗'
echo '║   VOC V1.5 · 启动中                      ║'
echo '║   分类模型：Ollama 14B（原始）            ║'
echo '║   未加载 LoRA adapter                    ║'
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

unset VOC_USE_MLX VOC_MLX_ADAPTER VOC_MLX_MODEL
export VOC_BACKEND_START_WAIT="${VOC_BACKEND_START_WAIT:-180}"
unset VOC_UVICORN_RELOAD

echo "⏳ 后端启动等待上限: ${VOC_BACKEND_START_WAIT}s"
echo ""
echo "前台启动（Ctrl+C 停止）..."
echo ""

exec python3 app_launcher.py
