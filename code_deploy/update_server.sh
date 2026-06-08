#!/usr/bin/env bash
#
# VOC_V1.5 — 部署机一键更新（仅覆盖 src/，不碰 data/ 与数据库）
# 用法：
#   ./code_deploy/update_server.sh                    # 默认使用 code_deploy/update.zip
#   ./code_deploy/update_server.sh /path/to/update.zip
#
# 可选：更新完成后自动后台启动
#   AUTO_START=1 ./code_deploy/update_server.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ZIP="${1:-$SCRIPT_DIR/update.zip}"

echo "============================================================"
echo " VOC_V1.5 · 服务器一键更新（仅 src/）"
echo "============================================================"
echo "项目根: $VOC_ROOT"
echo "更新包: $ZIP"
echo ""

if [[ ! -f "$ZIP" ]]; then
  echo "[错误] 未找到更新包: $ZIP"
  echo "请将开发机生成的 update.zip 放到 code_deploy/ 下或传入绝对路径。"
  exit 1
fi

if [[ ! -d "$VOC_ROOT/src/backend" ]]; then
  echo "[错误] $VOC_ROOT 下缺少 src/backend，请确认 VOC 项目路径正确。"
  exit 1
fi

stop_ports() {
  local port pid
  for port in 8000 8080; do
    pid=$(lsof -ti ":$port" 2>/dev/null || true)
    if [[ -n "${pid:-}" ]]; then
      echo "  → 释放端口 $port (PID: $pid)"
      kill $pid 2>/dev/null || true
    fi
  done
}

echo "[1/6] 正在停止本机 VOC 相关进程（8000 / 8080）..."
stop_ports
sleep 2
stop_ports || true
echo "  [✓] 端口处理完成（若仍占用请手动结束进程）"
echo ""

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$SCRIPT_DIR/backup"
BACKUP_PATH="$BACKUP_DIR/src_backup_$STAMP"
echo "[2/6] 正在备份当前 src/ → $BACKUP_PATH"
mkdir -p "$BACKUP_DIR"
cp -R "$VOC_ROOT/src" "$BACKUP_PATH"
echo "  [✓] 备份完成（可回滚）"
echo ""

echo "[3/6] 正在解压更新包..."
TMP="$(mktemp -d)"
unzip -q -o "$ZIP" -d "$TMP"
if [[ ! -d "$TMP/src" ]]; then
  echo "[错误] zip 内缺少顶层目录 src/，请使用本仓库提供的 package_code.sh 打包。"
  rm -rf "$TMP"
  exit 1
fi
echo "  [✓] 解压完成"
echo ""

echo "[4/6] 正在合并覆盖 src/（不删除 data/、不覆盖项目根下配置）..."
# 仅将包内 src 同步到项目 src：不删除目标中多余文件，降低误删风险
rsync -a "$TMP/src/" "$VOC_ROOT/src/"
rm -rf "$TMP"

# 清理 SQLite WAL 残留（不应存在于部署包；若存在说明前次部署带入，可能损坏数据库）
WAL_REMOVED=0
while IFS= read -r -d '' f; do
  rm -f "$f"
  WAL_REMOVED=$((WAL_REMOVED + 1))
done < <(find "$VOC_ROOT/src" \( -name '*.db-shm' -o -name '*.db-wal' \) -print0 2>/dev/null)
if [[ "$WAL_REMOVED" -gt 0 ]]; then
  echo "  → 已清理 $WAL_REMOVED 个 SQLite WAL 残留文件 (*.db-shm / *.db-wal)"
fi

echo "  [✓] src/ 已更新"
echo ""

echo "[5/6] 前端依赖检查（若无 node_modules 则安装）..."
FE="$VOC_ROOT/src/frontend"
if [[ -f "$FE/package.json" ]] && [[ ! -d "$FE/node_modules" ]]; then
  echo "  → 正在 npm install ..."
  (cd "$FE" && npm install)
fi
echo "  [✓] 前端依赖就绪"
echo ""

echo "[6/6] 更新流程结束"
echo "============================================================"
echo " 更新成功 — 历史数据未修改（未触碰 data/、*.db、label_project/）"
echo "============================================================"
echo "备份位置: $BACKUP_PATH"
echo ""

if [[ "${AUTO_START:-0}" == "1" ]]; then
  LOG="$SCRIPT_DIR/voc_launcher_${STAMP}.log"
  echo "AUTO_START=1 → 后台启动 app_launcher.py，日志: $LOG"
  cd "$VOC_ROOT"
  nohup python3 app_launcher.py >>"$LOG" 2>&1 &
  echo "  PID: $!"
else
  echo "请手动启动系统："
  echo "  cd \"$VOC_ROOT\""
  echo "  python3 app_launcher.py"
  echo ""
  echo "（后台启动可设置环境变量：AUTO_START=1 ./code_deploy/update_server.sh）"
fi
echo "============================================================"
