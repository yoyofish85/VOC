#!/usr/bin/env bash
#
# VOC_V1.5 — 开发机离线打包脚本（仅打包代码，不含业务数据）
# 用法：在 macOS 上于项目根目录执行：
#   ./code_deploy/package_code.sh
# 或：
#   bash code_deploy/package_code.sh
#
# 测试/CI 可选：
#   SKIP_FRONTEND_BUILD=1 ALLOW_DIRTY_PACK=1 ./code_deploy/package_code.sh
#   AUTO_COMMIT_PACK=0  — 禁止打包前自动 commit（默认开启）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ZIP="$SCRIPT_DIR/update.zip"
OUT_MD5="$SCRIPT_DIR/update.zip.md5"
STAGE="$(mktemp -d)"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

write_md5_file() {
  local zip_path="$1"
  local md5_path="$2"
  local hash base
  base="$(basename "$zip_path")"
  if command -v md5sum >/dev/null 2>&1; then
    hash="$(md5sum "$zip_path" | awk '{print $1}')"
  else
    hash="$(md5 -q "$zip_path")"
  fi
  printf '%s  %s\n' "$hash" "$base" >"$md5_path"
}

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

DEPLOY_TAG=""
GIT_COMMIT=""
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [[ "${ALLOW_DIRTY_PACK:-0}" != "1" ]] && ! git -C "$ROOT" diff-index --quiet HEAD --; then
    if [[ "${AUTO_COMMIT_PACK:-1}" == "1" ]]; then
      PACK_TS="$(date +%Y%m%d_%H%M%S)"
      echo "[0/5] 工作区有未提交修改，自动 commit 后继续打包..."
      git -C "$ROOT" status --short
      git -C "$ROOT" add -A
      if git -C "$ROOT" diff-index --quiet HEAD --cached; then
        echo "[警告] 仅有未跟踪且被 .gitignore 忽略的文件，跳过 commit。"
      else
        git -C "$ROOT" commit -m "chore(deploy): auto-commit before pack ${PACK_TS}"
        echo "  [✓] 已自动 commit: chore(deploy): auto-commit before pack ${PACK_TS}"
      fi
    else
      echo "[错误] 工作区有未提交修改，请先 commit 或 stash 后再打包。"
      echo "  提示: AUTO_COMMIT_PACK=1（默认）可自动 commit；ALLOW_DIRTY_PACK=1 可跳过检查。"
      echo ""
      git -C "$ROOT" status --short
      exit 1
    fi
  fi
  DEPLOY_TAG="deploy_$(date +%Y%m%d_%H%M)"
  GIT_COMMIT="$(git -C "$ROOT" rev-parse --short HEAD)"
  if [[ "${SKIP_GIT_TAG:-0}" == "1" ]]; then
    echo "[0/5] SKIP_GIT_TAG=1 — 跳过 git tag（测试模式）"
  else
    if git -C "$ROOT" tag -l "$DEPLOY_TAG" | grep -qx "$DEPLOY_TAG"; then
      echo "[错误] git tag 已存在: $DEPLOY_TAG，请等待 1 分钟后重试。"
      exit 1
    fi
    git -C "$ROOT" tag "$DEPLOY_TAG"
    echo "[0/5] git tag: $DEPLOY_TAG (commit: $GIT_COMMIT)"
  fi
else
  DEPLOY_TAG="deploy_$(date +%Y%m%d_%H%M)_nogit"
  echo "[0/5] 非 git 仓库，跳过 tag（manifest 使用 $DEPLOY_TAG）"
fi
echo ""

echo "[1/5] 正在执行前端生产构建 (npm run build)..."
cd "$ROOT/src/frontend"
if [[ ! -f package.json ]]; then
  echo "[错误] 缺少 src/frontend/package.json"
  exit 1
fi
if [[ "${SKIP_FRONTEND_BUILD:-0}" == "1" ]]; then
  echo "  → SKIP_FRONTEND_BUILD=1：跳过 npm run build"
  mkdir -p "$ROOT/src/frontend/dist"
  if [[ ! -f "$ROOT/src/frontend/dist/index.html" ]]; then
    echo '<!DOCTYPE html><html><head><title>VOC</title></head><body></body></html>' \
      >"$ROOT/src/frontend/dist/index.html"
    echo "  → 已写入占位 dist/index.html"
  fi
else
  if [[ ! -d node_modules ]]; then
    echo "  → 首次打包：正在 npm install ..."
    npm install
  fi
  npm run build
fi
echo "  [✓] 前端构建完成 → src/frontend/dist/"
echo ""

echo "[2/5] 正在组装临时目录（排除 node_modules、缓存、数据库等）..."
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

# S5–S8 依赖：label_project + 评估脚本（不含 exports / 数据库）
echo "  → 附带 label_project/ 与 performance_evaluation/（工具脚本）..."
mkdir -p "$STAGE/label_project" "$STAGE/performance_evaluation"
rsync -a \
  --exclude '__pycache__/' \
  --exclude '*.py[cod]' \
  --exclude '.pytest_cache/' \
  --exclude '.DS_Store' \
  --exclude '*.db' \
  --exclude '*.db-*' \
  "$ROOT/label_project/" "$STAGE/label_project/"
rsync -a \
  --exclude '__pycache__/' \
  --exclude '*.py[cod]' \
  --exclude '.pytest_cache/' \
  --exclude '.DS_Store' \
  --exclude 'exports/' \
  --exclude 'state/' \
  --exclude '*.db' \
  --exclude '*.csv' \
  --exclude '*.jsonl' \
  "$ROOT/performance_evaluation/" "$STAGE/performance_evaluation/"

PACKED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
FULL_SHA=""
BRANCH=""
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  FULL_SHA="$(git -C "$ROOT" rev-parse HEAD)"
  BRANCH="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
fi
# 离线协议 VERSION_MANIFEST（与 update.zip 同传）
{
  echo "package=VOC_offline_update"
  echo "branch=${BRANCH}"
  echo "git_commit_short=${GIT_COMMIT}"
  echo "git_commit=${FULL_SHA}"
  echo "deploy_tag=${DEPLOY_TAG}"
  echo "packed_at=${PACKED_AT}"
  echo "includes=src,label_project,performance_evaluation"
  echo "excludes=data,*.db,exports,node_modules,venv"
  echo "note=Server has no Git; deploy only this paired zip+md5."
} >"$STAGE/VERSION_MANIFEST.txt"
cp -f "$STAGE/VERSION_MANIFEST.txt" "$SCRIPT_DIR/VERSION_MANIFEST.txt"

python3 - "$STAGE/src/deploy_manifest.json" "$DEPLOY_TAG" "$GIT_COMMIT" "$PACKED_AT" "$ROOT" <<'PY'
import json, subprocess, sys
from pathlib import Path

manifest_path, tag, commit, packed_at, root = sys.argv[1:6]
changed = []
if Path(root, ".git").exists():
    for args in (["diff", "--name-only", "HEAD~1", "HEAD"], ["diff", "--name-only", "HEAD"]):
        r = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            changed = r.stdout.strip().splitlines()[:200]
            break
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(
        {
            "deploy_tag": tag,
            "git_commit": commit,
            "packed_at": packed_at,
            "changed_files": changed,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
PY
echo "  [✓] 已写入最新 dist/ 与 deploy_manifest.json"
echo ""

echo "[3/5] 正在生成 zip..."
rm -f "$OUT_ZIP" "$OUT_MD5"
(
  cd "$STAGE"
  zip -r -q "$OUT_ZIP" src label_project performance_evaluation VERSION_MANIFEST.txt
)
BYTES=$(stat -f%z "$OUT_ZIP" 2>/dev/null || stat -c%s "$OUT_ZIP" 2>/dev/null || echo "?")
echo "  [✓] 已生成 update.zip（约 $BYTES 字节）"
# 按顺序执行计划命名归档副本（便于线下传递留档）
if [[ -n "${GIT_COMMIT}" ]]; then
  PACK_LABEL="${PACK_LABEL:-update}"
  ARCHIVE_NAME="VOC_${PACK_LABEL}_${GIT_COMMIT}_$(date +%Y%m%d_%H%M%S).zip"
  cp -f "$OUT_ZIP" "$SCRIPT_DIR/$ARCHIVE_NAME"
  write_md5_file "$SCRIPT_DIR/$ARCHIVE_NAME" "$SCRIPT_DIR/${ARCHIVE_NAME}.md5"
  echo "  [✓] 归档副本: $SCRIPT_DIR/$ARCHIVE_NAME"
fi
echo ""

echo "[4/5] 生成 MD5 校验文件..."
write_md5_file "$OUT_ZIP" "$OUT_MD5"
echo "  [✓] $OUT_MD5"
echo ""

echo "[5/5] 校验 zip 内顶层结构..."
if ! unzip -l "$OUT_ZIP" | grep -q 'src/'; then
  echo "[警告] zip 内未检测到 src/ 前缀，请检查。"
else
  echo "  [✓] 包含 src/ 目录结构"
fi
python3 - "$OUT_ZIP" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    bad = [n for n in zf.namelist() if n.endswith((".db", ".sqlite", ".db-shm", ".db-wal"))]
if bad:
    print("[错误] zip 内检测到数据库/WAL 文件:", bad[:5])
    sys.exit(1)
print("  [✓] 未包含 .db / .sqlite / .db-shm / .db-wal")
PY

echo ""
echo "============================================================"
echo " 打包完成"
echo "============================================================"
echo "deploy tag : $DEPLOY_TAG"
echo "输出文件   : $OUT_ZIP"
echo "MD5 校验   : $OUT_MD5"
echo "zip MD5    : $(awk '{print $1}' "$OUT_MD5")"
echo ""
echo "⚠️  请将 update.zip 与 update.zip.md5 成对拷贝到服务器（同一次打包，缺一不可）。"
echo "请将 update.zip 与 update.zip.md5 离线拷贝到服务器，然后执行："
echo "  ./code_deploy/update_server.sh"
echo ""
echo "安全说明：本包含 src/ + label_project/ + performance_evaluation/（无 data/、*.db、exports/）。"
echo "另附 VERSION_MANIFEST.txt（zip 内 + code_deploy/ 各一份）。"
echo "============================================================"
