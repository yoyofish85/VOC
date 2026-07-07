#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo '╔══════════════════════════════════════════╗'
echo '║   VOC V1.5 · 启动中                      ║'
echo '║   分类模型：Ollama 14B（原始）            ║'
echo '║   未加载 LoRA adapter                    ║'
echo '╚══════════════════════════════════════════╝'

PYTHON="$(bash scripts/resolve_venv_python.sh)"
if [[ -z "$PYTHON" ]]; then
  echo "❌ 未找到 .venv 或 venv，请先创建虚拟环境并安装依赖："
  echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if ! "$PYTHON" -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "❌ 虚拟环境缺少 fastapi/uvicorn: $PYTHON"
  echo "   请执行: $PYTHON -m pip install -r requirements.txt"
  exit 1
fi

unset VOC_USE_MLX VOC_MLX_ADAPTER VOC_MLX_MODEL
export VOC_BACKEND_START_WAIT="${VOC_BACKEND_START_WAIT:-180}"
unset VOC_UVICORN_RELOAD

echo "✅ Python: $PYTHON"
echo "⏳ 后端启动等待上限: ${VOC_BACKEND_START_WAIT}s"
echo ""
echo "前台启动（Ctrl+C 停止）..."
echo ""

exec "$PYTHON" app_launcher.py
