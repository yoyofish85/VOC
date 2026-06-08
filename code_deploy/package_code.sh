#!/usr/bin/env bash
#
# VOC_V1.5 — 开发机离线打包脚本（仅打包代码，不含业务数据）
# 用法：在 macOS 上于项目根目录执行：
#   ./code_deploy/package_code.sh
# 或：
#   bash code_deploy/package_code.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ZIP="$SCRIPT_DIR/update.zip"
STAGE="$(mktemp -d)"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

echo "============================================================"
echo " VOC_V1.5 · 离线代码打包（仅 src/，排除数据与依赖）"
echo "============================================================"
echo "项目根: $ROOT"
echo "输出包: $OUT_ZIP"
echo ""

if [[ ! -d "$ROOT/src/frontend" ]] || [[ ! -d "$ROOT/src/backend" ]]; then
  echo "[错误] 未找到 src/frontend 或 src/backend，请确认在项目 VOC_V1.5 下执行。"
  exit 1
fi

echo "[1/4] 正在执行前端生产构建 (npm run build)..."
cd "$ROOT/src/frontend"
if [[ ! -f package.json ]]; then
  echo "[错误] 缺少 src/frontend/package.json"
  exit 1
fi
if [[ ! -d node_modules ]]; then
  echo "  → 首次打包：正在 npm install ..."
  npm install
fi
npm run build
echo "  [✓] 前端构建完成 → src/frontend/dist/"
echo ""

echo "[2/4] 正在组装临时目录（排除 node_modules、缓存、数据库等）..."
mkdir -p "$STAGE/src"

rsync -a \
  --exclude 'node_modules/' \
  --exclude '__pycache__/' \
  --exclude '*.py[cod]' \
  --exclude '.pytest_cache/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude '.vite/' \
  --exclude 'dist/' \
  --exclude '*.db' \
  --exclude '*.db-*' \
  --exclude '*.sqlite' \
  --exclude '*.sqlite3' \
  --exclude 'qwen_inference_cache.json' \
  --exclude '.DS_Store' \
  --exclude '*.log' \
  "$ROOT/src/" "$STAGE/src/"

mkdir -p "$STAGE/src/frontend/dist"
rsync -a "$ROOT/src/frontend/dist/" "$STAGE/src/frontend/dist/"
echo "  [✓] 已写入最新 dist/"
echo ""

echo "[3/4] 正在生成 zip..."
rm -f "$OUT_ZIP"
(
  cd "$STAGE"
  zip -r -q "$OUT_ZIP" src
)
BYTES=$(stat -f%z "$OUT_ZIP" 2>/dev/null || stat -c%s "$OUT_ZIP" 2>/dev/null || echo "?")
echo "  [✓] 已生成 update.zip（约 $BYTES 字节）"
echo ""

echo "[4/4] 校验 zip 内顶层结构..."
if ! unzip -l "$OUT_ZIP" | head -20 | grep -q 'src/'; then
  echo "[警告] zip 内未检测到 src/ 前缀，请检查。"
else
  echo "  [✓] 包含 src/ 目录结构"
fi

echo ""
echo "============================================================"
echo " 打包完成"
echo "============================================================"
echo "请将本目录下的 update.zip 离线拷贝到服务器，然后在服务器上执行："
echo "  ./code_deploy/update_server.sh"
echo ""
echo "安全说明：本包仅含 src/ 源码与前端 dist，不含 data/、*.db、label_project/。"
echo "若 label_project/ 或根目录 app_launcher.py、requirements.txt 有变更，请另行同步。"
echo "============================================================"
