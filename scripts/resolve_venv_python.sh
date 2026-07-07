#!/usr/bin/env bash
# 解析项目虚拟环境 Python（避免 activate 后 python3 仍指向系统解释器）
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  echo "$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/.venv/bin/python3" ]]; then
  echo "$ROOT/.venv/bin/python3"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  echo "$ROOT/venv/bin/python"
elif [[ -x "$ROOT/venv/bin/python3" ]]; then
  echo "$ROOT/venv/bin/python3"
else
  echo ""
fi
