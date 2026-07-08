#!/usr/bin/env bash
# 确保当前 venv 已安装 mlx-lm；成功返回 0
set -euo pipefail

PYTHON="${1:-}"
if [[ -z "$PYTHON" ]]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  PYTHON="$(bash "$ROOT/scripts/resolve_venv_python.sh" || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "❌ 未找到 .venv Python，请先运行: bash scripts/setup_server_venv.sh" >&2
  exit 1
fi

if "$PYTHON" -c "import mlx_lm" 2>/dev/null; then
  "$PYTHON" -c "import mlx_lm; print('mlx_lm:', getattr(mlx_lm, '__version__', 'ok'))"
  exit 0
fi

echo "⚠ 未检测到 mlx-lm，正在安装 requirements-mlx.txt ..."
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! "$PYTHON" -m pip install -r "$ROOT/requirements-mlx.txt"; then
  echo "❌ mlx-lm 安装失败。请检查网络后重试：" >&2
  echo "   $PYTHON -m pip install -r requirements-mlx.txt" >&2
  exit 1
fi

if ! "$PYTHON" -c "import mlx_lm" 2>/dev/null; then
  echo "❌ mlx-lm 安装后仍无法 import mlx_lm：" >&2
  "$PYTHON" -c "import mlx_lm" 2>&1 || true
  exit 1
fi

"$PYTHON" -c "import mlx_lm; print('✅ mlx_lm:', getattr(mlx_lm, '__version__', 'ok'))"
