#!/usr/bin/env bash
# M2 门禁通过后：14B 全量重分写 v3_label_meta
# 用法（在项目根 VOC_V1.5 下）：
#   chmod +x performance_evaluation/run_full_reclassify.sh
#   ./performance_evaluation/run_full_reclassify.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DB="${VOC_DB:-src/backend/opinion_review.db}"
LOG="performance_evaluation/state/reclassify_console.log"
STATE="performance_evaluation/state"

echo "=== VOC 14B 全量重分 ==="
echo "项目: $ROOT"
echo "数据库: $DB"

if [[ ! -f "$DB" ]]; then
  echo "[错误] 数据库不存在: $DB" >&2
  exit 1
fi

if ! curl -sf "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
  echo "[错误] Ollama 未响应 :11434，请先启动 ollama serve" >&2
  exit 1
fi

if ! curl -sf "http://127.0.0.1:11434/api/tags" | grep -q "qwen2.5"; then
  echo "[警告] 未检测到 qwen2.5 模型，请确认: ollama list" >&2
fi

BAK="${DB}.bak_$(date +%Y%m%d_%H%M%S)"
cp "$DB" "$BAK"
echo "[备份] $BAK"

mkdir -p "$STATE"
if [[ -f performance_evaluation/state/reclassify_checkpoint.json ]]; then
  echo "[提示] 将 --all 清空旧 checkpoint 并全量重跑"
fi

nohup python3 performance_evaluation/reclassify_all_with_14b.py --all \
  >"$LOG" 2>&1 &
PID=$!
echo "[启动] PID=$PID"
echo "  日志: tail -f $LOG"
echo "  进度: cat performance_evaluation/state/reclassify_checkpoint.json"
echo "  完成后: python3 performance_evaluation/evaluate_accuracy.py"
