#!/usr/bin/env bash
# 选择可用于 VOC 的 Python（3.11–3.13；排除 3.14+，fastapi/pydantic 尚未兼容）
set -euo pipefail

_is_supported() {
  local py="$1"
  [[ -x "$py" ]] || return 1
  "$py" - <<'PY'
import sys
m, n = sys.version_info[:2]
raise SystemExit(0 if (m, n) >= (3, 11) and (m, n) < (3, 14) else 1)
PY
}

_candidates=(
  python3.12 python3.13 python3.11
  /opt/homebrew/bin/python3.12
  /opt/homebrew/bin/python3.13
  /opt/homebrew/bin/python3.11
  /usr/local/bin/python3.12
  /usr/local/bin/python3.13
  /usr/local/bin/python3.11
)

_seen=""
for cmd in "${_candidates[@]}"; do
  if [[ " $_seen " == *" $cmd "* ]]; then
    continue
  fi
  _seen+=" $cmd"
  if _is_supported "$cmd" 2>/dev/null; then
    if [[ "$cmd" == /* ]]; then
      echo "$cmd"
    else
      command -v "$cmd"
    fi
    exit 0
  fi
done

echo "未找到 Python 3.11–3.13。当前默认 python3 若为 3.14，请安装 python@3.12：" >&2
echo "  brew install python@3.12" >&2
exit 1
