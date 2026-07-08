#!/usr/bin/env bash
# 解析项目虚拟环境 Python（避免 activate 后 python3 仍指向系统解释器）
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_pick() {
  local p="$1"
  [[ -x "$p" ]] || return 1
  "$p" - <<'PY' >/dev/null 2>&1
import sys
m, n = sys.version_info[:2]
raise SystemExit(0 if (m, n) >= (3, 11) and (m, n) < (3, 14) else 1)
PY
  echo "$p"
}

for candidate in \
  "$ROOT/.venv/bin/python" \
  "$ROOT/.venv/bin/python3" \
  "$ROOT/venv/bin/python" \
  "$ROOT/venv/bin/python3"
do
  if picked="$(_pick "$candidate")"; then
    echo "$picked"
    exit 0
  fi
done
