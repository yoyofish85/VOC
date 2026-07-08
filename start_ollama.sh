#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo '╔══════════════════════════════════════════╗'
echo '║   VOC V1.5 · 启动中                      ║'
echo '║   分类模型：Ollama 14B（原始）            ║'
echo '║   未加载 LoRA adapter                    ║'
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
if ! "$PYTHON" -c "import multipart" 2>/dev/null; then
  echo "⚠ 缺少 python-multipart（CSV 上传需要），正在安装..."
  "$PYTHON" -m pip install python-multipart
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
