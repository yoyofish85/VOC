#!/usr/bin/env bash
#
# VOC_V1.5 — 部署机一键更新（仅覆盖 src/，不碰 data/ 与数据库）
# 用法：
#   ./code_deploy/update_server.sh                    # 默认使用 code_deploy/update.zip
#   ./code_deploy/update_server.sh /path/to/update.zip
#   ./code_deploy/update_server.sh --check-only       # 仅校验 MD5 与包内依赖，不覆盖 src/
#   ./code_deploy/update_server.sh --rollback         # 回滚到上一备份
#
# 可选：更新完成后自动后台启动
#   AUTO_START=1 ./code_deploy/update_server.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHECK_ONLY=0
DO_ROLLBACK=0
REPAIR_MD5=0
ZIP=""

usage() {
  cat <<EOF
用法:
  $0 [选项] [update.zip路径]

选项:
  --check-only   仅校验 MD5 与 zip 内依赖，不修改 src/
  --rollback     自动回滚到最近一次 src 备份
  --repair-md5   根据当前 update.zip 重新生成 .md5（zip 可信、md5 过期时用）
  -h, --help     显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --rollback)
      DO_ROLLBACK=1
      shift
      ;;
    --repair-md5)
      REPAIR_MD5=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      echo "[错误] 未知选项: $1"
      usage
      exit 1
      ;;
    *)
      ZIP="$1"
      shift
      ;;
  esac
done

ZIP="${ZIP:-$SCRIPT_DIR/update.zip}"

if [[ "$DO_ROLLBACK" == "1" ]]; then
  echo "============================================================"
  echo " VOC_V1.5 · 自动回滚"
  echo "============================================================"
  exec "$SCRIPT_DIR/rollback_server.sh" -y
fi

verify_md5() {
  local zip_path="$1"
  local md5_path="${zip_path}.md5"
  if [[ ! -f "$md5_path" ]]; then
    echo "[错误] 未找到 MD5 校验文件: $md5_path"
    echo "  提示: 须与 update.zip 成对拷贝（同一次 package_code.sh 产出）。"
    exit 1
  fi
  local dir base expected actual zip_bytes
  dir="$(cd "$(dirname "$zip_path")" && pwd)"
  base="$(basename "$zip_path")"
  zip_bytes="$(stat -f%z "$zip_path" 2>/dev/null || stat -c%s "$zip_path" 2>/dev/null || echo "?")"
  expected="$(awk '{print $1}' "$md5_path" | tr -d '\r\n')"
  echo "  → 校验 MD5: $md5_path"
  echo "  → zip 大小: ${zip_bytes} 字节"
  if command -v md5sum >/dev/null 2>&1; then
    actual="$(md5sum "$zip_path" | awk '{print $1}')"
    if (cd "$dir" && md5sum -c "${base}.md5" >/dev/null 2>&1); then
      echo "  [✓] MD5 校验通过 (md5sum)"
      return 0
    fi
  else
    actual="$(md5 -q "$zip_path")"
    if [[ "$expected" == "$actual" ]]; then
      echo "  [✓] MD5 校验通过 (macOS md5)"
      return 0
    fi
  fi
  echo "[错误] MD5 校验失败，中止部署。"
  echo "  期望 MD5: $expected"
  echo "  实际 MD5: ${actual:-（未计算）}"
  echo ""
  echo "常见原因:"
  echo "  1. update.zip 与 update.zip.md5 不是同一次打包产出（须两个文件一起拷贝）"
  echo "  2. 只更新了 zip，服务器上的 .md5 仍是旧版本"
  echo "  3. 传输过程中 zip 损坏或不完整"
  echo ""
  echo "修复步骤:"
  echo "  开发机: ./code_deploy/package_code.sh"
  echo "  将 code_deploy/update.zip 与 code_deploy/update.zip.md5 成对拷到服务器"
  echo "  若确认 zip 完整且仅 md5 过期: $0 --repair-md5"
  exit 1
}

write_md5_for_zip() {
  local zip_path="$1"
  local md5_path="${zip_path}.md5"
  local hash base
  base="$(basename "$zip_path")"
  if command -v md5sum >/dev/null 2>&1; then
    hash="$(md5sum "$zip_path" | awk '{print $1}')"
  else
    hash="$(md5 -q "$zip_path")"
  fi
  printf '%s  %s\n' "$hash" "$base" >"$md5_path"
  echo "  [✓] 已写入 $md5_path"
  echo "  MD5: $hash"
}

archive_md5() {
  local zip_path="$1"
  local md5_path="${zip_path}.md5"
  local day_dir="$SCRIPT_DIR/backup/$(date +%Y%m%d)"
  mkdir -p "$day_dir"
  if [[ -f "$md5_path" ]]; then
    cp -f "$md5_path" "$day_dir/"
    echo "  [✓] MD5 已归档 → $day_dir/$(basename "$md5_path")"
  fi
}

check_zip_dependencies() {
  local zip_path="$1"
  echo "  → 检查 zip 内关键路径..."
  python3 - "$zip_path" <<'PY'
import sys, zipfile
zpath = sys.argv[1]
with zipfile.ZipFile(zpath) as zf:
    names = zf.namelist()
bad = [n for n in names if n.endswith((".db", ".sqlite", ".db-shm", ".db-wal"))]
if bad:
    print("[错误] zip 内包含数据库/WAL 文件:", bad[:5])
    sys.exit(1)
if not any(n.endswith("src/backend/main.py") for n in names):
    print("[错误] zip 内缺少 src/backend/main.py")
    sys.exit(1)
if not any("src/frontend/dist/" in n for n in names):
    print("[错误] zip 内缺少 src/frontend/dist/")
    sys.exit(1)
print("  [✓] zip 依赖检查通过")
PY
}

clean_wal_before_overwrite() {
  echo "  → 覆盖前清理目标 src/ 下 SQLite WAL 残留..."
  local removed=0
  while IFS= read -r -d '' f; do
    echo "  → 删除 WAL 文件: $f"
    rm -f "$f"
    removed=$((removed + 1))
  done < <(find "$VOC_ROOT/src" \( -name '*.db-shm' -o -name '*.db-wal' \) -print0 2>/dev/null)
  if [[ "$removed" -eq 0 ]]; then
    echo "  [✓] 未发现 WAL 残留"
  else
    echo "  [✓] 已清理 $removed 个 WAL 文件"
  fi
}

print_deploy_summary() {
  local manifest="$VOC_ROOT/src/deploy_manifest.json"
  local backup_path="${1:-}"
  echo ""
  echo "—— 部署摘要 ——"
  if [[ -f "$manifest" ]]; then
    python3 - "$manifest" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    m = json.load(f)
print(f"  deploy tag : {m.get('deploy_tag', '—')}")
print(f"  git commit : {m.get('git_commit', '—')}")
print(f"  packed_at  : {m.get('packed_at', '—')}")
changed = m.get("changed_files") or []
if changed:
    print("  打包变更文件:")
    for p in changed[:30]:
        print(f"    - {p}")
    if len(changed) > 30:
        print(f"    ... 共 {len(changed)} 个文件")
PY
  else
    echo "  deploy tag : （zip 内无 deploy_manifest.json）"
  fi
  if [[ -n "$backup_path" ]] && [[ -d "$backup_path" ]]; then
    echo "  本次覆盖差异（相对备份）:"
    diff -rq "$backup_path" "$VOC_ROOT/src" 2>/dev/null | head -40 || true
  fi
}

echo "============================================================"
echo " VOC_V1.5 · 服务器一键更新（仅 src/）"
echo "============================================================"
echo "项目根: $VOC_ROOT"
echo "更新包: $ZIP"
[[ "$CHECK_ONLY" == "1" ]] && echo "模式  : --check-only（不修改 src/）"
echo ""

if [[ ! -f "$ZIP" ]]; then
  echo "[错误] 未找到更新包: $ZIP"
  echo "请将开发机生成的 update.zip 放到 code_deploy/ 下或传入绝对路径。"
  exit 1
fi

if [[ "$REPAIR_MD5" == "1" ]]; then
  echo "[repair-md5] 根据当前 zip 重新生成 MD5..."
  write_md5_for_zip "$ZIP"
  echo ""
  echo "请再次运行: $0 --check-only"
  exit 0
fi

if [[ ! -d "$VOC_ROOT/src/backend" ]]; then
  echo "[错误] $VOC_ROOT 下缺少 src/backend，请确认 VOC 项目路径正确。"
  exit 1
fi

echo "[1] MD5 校验..."
verify_md5 "$ZIP"
archive_md5 "$ZIP"
echo ""

echo "[2] zip 依赖检查..."
check_zip_dependencies "$ZIP"
echo ""

if [[ "$CHECK_ONLY" == "1" ]]; then
  echo "[3] 本地前端依赖检查..."
  FE="$VOC_ROOT/src/frontend"
  if [[ -f "$FE/package.json" ]] && [[ ! -d "$FE/node_modules" ]]; then
    echo "  [提示] src/frontend 无 node_modules，部署后需 npm install"
  else
    echo "  [✓] 前端 node_modules 就绪或无需安装"
  fi
  echo ""
  echo "============================================================"
  echo " --check-only 完成：未修改 src/"
  echo "============================================================"
  exit 0
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

echo "[3/7] 正在停止本机 VOC 相关进程（8000 / 8080）..."
stop_ports
sleep 2
stop_ports || true
echo "  [✓] 端口处理完成（若仍占用请手动结束进程）"
echo ""

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$SCRIPT_DIR/backup"
BACKUP_PATH="$BACKUP_DIR/src_backup_$STAMP"
echo "[4/7] 正在备份当前 src/ → $BACKUP_PATH"
mkdir -p "$BACKUP_DIR"
cp -R "$VOC_ROOT/src" "$BACKUP_PATH"
echo "  [✓] 备份完成（可回滚）"
echo ""

echo "[5/7] 覆盖前清理 WAL + 解压更新包..."
clean_wal_before_overwrite
TMP="$(mktemp -d)"
unzip -q -o "$ZIP" -d "$TMP"
if [[ ! -d "$TMP/src" ]]; then
  echo "[错误] zip 内缺少顶层目录 src/，请使用本仓库提供的 package_code.sh 打包。"
  rm -rf "$TMP"
  exit 1
fi
echo "  [✓] 解压完成"
echo ""

echo "[6/7] 正在合并覆盖 src/（不删除 data/、不覆盖项目根下配置）..."
rsync -a "$TMP/src/" "$VOC_ROOT/src/"
rm -rf "$TMP"
echo "  [✓] src/ 已更新"
echo ""

echo "[7/7] 前端依赖检查（若无 node_modules 则安装）..."
FE="$VOC_ROOT/src/frontend"
if [[ -f "$FE/package.json" ]] && [[ ! -d "$FE/node_modules" ]]; then
  echo "  → 正在 npm install ..."
  (cd "$FE" && npm install)
fi
echo "  [✓] 前端依赖就绪"
echo ""

echo "============================================================"
echo " 更新成功 — 历史数据未修改（未触碰 data/、*.db、label_project/）"
echo "============================================================"
echo "备份位置: $BACKUP_PATH"
print_deploy_summary "$BACKUP_PATH"
echo ""

if [[ -f "$SCRIPT_DIR/deploy_checklist.py" ]]; then
  echo "运行部署自检（离线项，API 需启动后复查）: python3 code_deploy/deploy_checklist.py"
  python3 "$SCRIPT_DIR/deploy_checklist.py" --offline || echo "  [警告] 部署自检未全部通过，请查看上方输出"
  echo ""
fi

if [[ "${AUTO_START:-0}" == "1" ]]; then
  LOG="$SCRIPT_DIR/voc_launcher_${STAMP}.log"
  echo "AUTO_START=1 → 后台启动 app_launcher.py，日志: $LOG"
  cd "$VOC_ROOT"
  nohup python3 app_launcher.py >>"$LOG" 2>&1 &
  echo "  PID: $!"
  sleep 3
  if [[ -f "$SCRIPT_DIR/deploy_checklist.py" ]]; then
    echo "启动后 API 自检:"
    python3 "$SCRIPT_DIR/deploy_checklist.py" || echo "  [警告] API 自检未通过"
    echo ""
  fi
else
  echo "请手动启动系统："
  echo "  cd \"$VOC_ROOT\""
  echo "  python3 app_launcher.py"
  echo ""
  echo "启动后运行完整自检："
  echo "  python3 code_deploy/deploy_checklist.py"
  echo ""
  echo "（后台启动可设置环境变量：AUTO_START=1 ./code_deploy/update_server.sh）"
fi
echo "============================================================"
