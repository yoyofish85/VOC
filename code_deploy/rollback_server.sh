#!/usr/bin/env bash
#
# VOC_V1.5 — 使用 code_deploy/backup 下最近一次备份回滚 src/
# 用法：
#   ./code_deploy/rollback_server.sh              # 使用最新备份（交互确认）
#   ./code_deploy/rollback_server.sh src_backup_20250108_143022
#   ./code_deploy/rollback_server.sh -y           # 非交互，使用最新备份
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_ROOT="$SCRIPT_DIR/backup"
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y | --yes)
      ASSUME_YES=1
      shift
      ;;
    -h | --help)
      echo "用法: $0 [-y] [src_backup_YYYYMMDD_HHMMSS]"
      exit 0
      ;;
    -*)
      echo "[错误] 未知选项: $1"
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

echo "============================================================"
echo " VOC_V1.5 · 回滚 src/（从 code_deploy/backup）"
echo "============================================================"

if [[ ! -d "$BACKUP_ROOT" ]] || [[ -z "$(ls -A "$BACKUP_ROOT" 2>/dev/null)" ]]; then
  echo "[错误] 没有可用备份目录: $BACKUP_ROOT"
  exit 1
fi

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  TARGET="$(ls -1t "$BACKUP_ROOT" | grep '^src_backup_' | head -1)"
fi

if [[ -z "$TARGET" ]]; then
  echo "[错误] 未找到 src_backup_* 备份"
  exit 1
fi

if [[ -d "$TARGET" ]]; then
  SNAP="$TARGET"
elif [[ -d "$BACKUP_ROOT/$TARGET" ]]; then
  SNAP="$BACKUP_ROOT/$TARGET"
else
  echo "[错误] 找不到备份: $TARGET"
  exit 1
fi

echo "将使用的备份: $SNAP"
if [[ "$ASSUME_YES" != "1" ]]; then
  read -r -p "确认用该备份替换当前 $VOC_ROOT/src ? [y/N] " ok
  if [[ "${ok:-}" != "y" ]] && [[ "${ok:-}" != "Y" ]]; then
    echo "已取消。"
    exit 0
  fi
else
  echo "  (-y) 已跳过交互确认"
fi

echo "[1/3] 停止端口 8000 / 8080 ..."
for port in 8000 8080; do
  pid=$(lsof -ti ":$port" 2>/dev/null || true)
  [[ -n "${pid:-}" ]] && kill $pid 2>/dev/null || true
done
sleep 1
echo "[2/3] 替换 src/ ..."
rm -rf "$VOC_ROOT/src"
cp -R "$SNAP" "$VOC_ROOT/src"
echo "[3/3] 完成。"
echo "请手动启动: cd \"$VOC_ROOT\" && python3 app_launcher.py"
echo "============================================================"
