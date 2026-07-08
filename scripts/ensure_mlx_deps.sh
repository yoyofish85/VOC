#!/usr/bin/env bash
# 确保当前 venv 已安装且可 import mlx_lm；成功返回 0
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${1:-}"
if [[ -z "$PYTHON" ]]; then
  PYTHON="$(bash "$ROOT/scripts/resolve_venv_python.sh" || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "❌ 未找到 .venv Python，请先运行: bash scripts/setup_server_venv.sh" >&2
  exit 1
fi

_import_ok() {
  "$PYTHON" -c "import mlx_lm" 2>/dev/null
}

_show_import_error() {
  echo "mlx_lm 导入失败，详细错误：" >&2
  "$PYTHON" -c "import mlx_lm" 2>&1 || true
}

if _import_ok; then
  "$PYTHON" -c "import mlx_lm, transformers; print('mlx_lm:', getattr(mlx_lm,'__version__','ok'), '| transformers:', transformers.__version__)"
  exit 0
fi

echo "⚠ mlx_lm 不可用，按 requirements-mlx.txt 安装/修复依赖 ..."
if ! "$PYTHON" -m pip install -r "$ROOT/requirements-mlx.txt"; then
  echo "❌ pip install 失败" >&2
  _show_import_error
  exit 1
fi

if ! _import_ok; then
  echo "⚠ 仍无法 import，尝试将 transformers 限制到 <5.13 ..."
  if ! "$PYTHON" -m pip install 'transformers>=5.0.0,<5.13.0' --force-reinstall; then
    echo "❌ transformers 降级失败" >&2
    _show_import_error
    exit 1
  fi
fi

if ! _import_ok; then
  echo "❌ mlx_lm 仍无法导入。常见原因: transformers>=5.13 与 mlx-lm 不兼容" >&2
  _show_import_error
  echo "手动修复: $PYTHON -m pip install 'transformers>=5.0.0,<5.13.0' --force-reinstall" >&2
  exit 1
fi

"$PYTHON" -c "import mlx_lm, transformers; print('✅ mlx_lm:', getattr(mlx_lm,'__version__','ok'), '| transformers:', transformers.__version__)"
