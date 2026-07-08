#!/usr/bin/env bash
# 服务器/开发机一键创建 .venv 并安装依赖（需 Python 3.11–3.13）
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== VOC V1.5 · 虚拟环境安装 ==="

PY="$(bash scripts/find_python_for_venv.sh)"
echo "选用解释器: $PY ($("$PY" --version))"

if [[ -x .venv/bin/python ]]; then
  VENV_MINOR="$(.venv/bin/python -c 'import sys; print(sys.version_info.minor)')"
  if (( VENV_MINOR >= 14 || VENV_MINOR < 11 )); then
    echo "删除不兼容的旧 .venv (Python 3.$VENV_MINOR)..."
    rm -rf .venv
  fi
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "创建 .venv ..."
  "$PY" -m venv .venv
fi

echo "升级 pip ..."
.venv/bin/python -m pip install -U pip

echo "安装 requirements.txt ..."
.venv/bin/pip install -r requirements.txt

echo "安装 mlx-lm（MLX+LoRA 推理）..."
.venv/bin/pip install mlx-lm

echo ""
echo "✅ 完成: .venv/bin/python ($(.venv/bin/python --version))"
echo "   启动 Ollama: ./start_ollama.sh"
echo "   启动 MLX:    ./start_mlx.sh"
