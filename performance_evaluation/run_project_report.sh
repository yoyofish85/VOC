#!/usr/bin/env bash
# 项目汇报：各阶段识别准确率（只读 DB，不写库）
# 用法（服务器项目根）:
#   bash performance_evaluation/run_project_report.sh
#   bash performance_evaluation/run_project_report.sh 7
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
DAYS="${1:-7}"
STAMP="$(date +%Y%m%d)"
EXPORT="performance_evaluation/exports/project_stages_${STAMP}.md"
PYTHON="${VOC_PYTHON:-$ROOT/.venv/bin/python3}"

if [[ ! -x "$PYTHON" ]]; then
  echo "错误：未找到可执行的项目 Python：$PYTHON" >&2
  echo "请先运行 bash scripts/setup_server_venv.sh，或设置 VOC_PYTHON。" >&2
  exit 1
fi

echo "========== VOC 项目汇报数据采集（近 ${DAYS} 天）=========="
echo "项目根: $ROOT"
echo ""

echo ">>> [健康检查]"
"$PYTHON" performance_evaluation/health_check.py || true
echo ""

echo ">>> [A] 全库已复核（线上 v3）"
"$PYTHON" performance_evaluation/evaluate_accuracy.py
echo ""

echo ">>> [B] 近 ${DAYS} 天已复核"
"$PYTHON" performance_evaluation/eval_batch_accuracy.py --since-days "$DAYS"
echo ""

echo ">>> [B-批次] 近 ${DAYS} 天 upload_batch 列表"
"$PYTHON" performance_evaluation/eval_batch_accuracy.py --list-batches "$DAYS"
echo ""

echo ">>> [C] 规则 dry-run（模型基线 → 当前规则重放）"
"$PYTHON" performance_evaluation/eval_recent_rule_candidate.py \
  --since-days "$DAYS" \
  --max-l1-broken 8 \
  --max-l2-broken 3 \
  --export-csv "performance_evaluation/exports/week_prod_check_${STAMP}.csv"
echo ""

echo ">>> [D] 近 ${DAYS} 天 L1 错误 Top"
"$PYTHON" performance_evaluation/export_l1_errors.py --since "${DAYS}d" --snapshot \
  --out "performance_evaluation/exports/l1_errors_${DAYS}d_${STAMP}.csv"
echo ""

echo ">>> [E] 进化闭环周报"
"$PYTHON" performance_evaluation/weekly_evolution_report.py --days "$DAYS"
echo ""

if [[ -f performance_evaluation/report_project_stages.py ]]; then
  echo ">>> [汇总 Markdown]"
  "$PYTHON" performance_evaluation/report_project_stages.py \
    --days "$DAYS" \
    --export-md "$EXPORT"
  echo ""
  echo "已导出: $EXPORT"
fi

echo "========== 完成 =========="
