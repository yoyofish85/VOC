#!/usr/bin/env bash
#
# LoRA 效果验证：单条 smoke + holdout A/B 对比
# 用法（项目根目录）:
#   bash scripts/verify_lora.sh
#   SKIP_AB=1 bash scripts/verify_lora.sh   # 仅跑 5 条 smoke，跳过 holdout
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PYTHON="${VOC_PYTHON:-python3}"
DB_PATH="${VOC_DB_PATH:-src/backend/opinion_review.db}"
MODEL="${VOC_MLX_MODEL:-mlx-community/Qwen2.5-14B-Instruct-4bit}"
ADAPTER_PATH="${VOC_MLX_ADAPTER:-$HOME/lora_adapter_14b_v2}"
MIN_DELTA_PP="${VOC_LORA_MIN_DELTA:-2.0}"

export VOC_USE_MLX=1
export VOC_MLX_MODEL="$MODEL"
export VOC_MLX_ADAPTER="$ADAPTER_PATH"

echo "============================================================"
echo " LoRA 效果验证"
echo "============================================================"
echo "MLX 模型:   $VOC_MLX_MODEL"
echo "Adapter:    $VOC_MLX_ADAPTER"
echo "数据库:     $DB_PATH"
echo ""

if [[ ! -f "$ADAPTER_PATH/adapters.safetensors" && ! -f "$ADAPTER_PATH/adapter_config.json" ]]; then
  echo "[错误] Adapter 不存在或未完成训练: $ADAPTER_PATH"
  echo "  请先运行: bash scripts/train_lora_14b.sh"
  exit 1
fi

echo "[1/3] 单条推理 smoke（5 条）..."
SMOKE_OK=$("$PYTHON" - <<PY
import sys, time
sys.path.insert(0, "src/backend")
from qwen_ollama import classify_text

tests = [
    ("车机黑屏了，重启了好几次都没用", "产品质量类"),
    ("销售顾问态度很好非常满意", "非问题"),
    ("建议增加哨兵模式", "体验需求类"),
    ("充电枪拔不出来客服电话打不通", "产品质量类"),
    ("售后维修等了一个月没消息", "服务类"),
]
correct = 0
for text, exp in tests:
    t0 = time.time()
    r = classify_text(text, db_path="${DB_PATH}", country="CN")
    act = r.get("l1") or ""
    mt = r.get("match_type") or ""
    ok = act == exp
    if ok:
        correct += 1
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] exp={exp} act={act}/{r.get('l2','')} type={mt} [{time.time()-t0:.0f}s]")
    if mt == "mlx_load_failed":
        print("  MLX 加载失败，终止验证")
        sys.exit(2)
print(f"smoke: {correct}/{len(tests)}")
sys.exit(0 if correct >= 3 else 1)
PY
) || SMOKE_RC=$?
SMOKE_RC=${SMOKE_RC:-0}
if [[ "$SMOKE_RC" -ne 0 ]]; then
  echo "[失败] smoke 正确率不足 3/5 或 MLX 加载失败"
  exit "$SMOKE_RC"
fi

if [[ "${SKIP_AB:-0}" == "1" ]]; then
  echo ""
  echo "[跳过] SKIP_AB=1，未执行 holdout A/B"
  exit 0
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "[警告] 数据库不存在，跳过 holdout A/B: $DB_PATH"
  exit 0
fi

if [[ ! -f "data/holdout_opinion_ids.json" ]]; then
  echo "[警告] 缺少 data/holdout_opinion_ids.json，跳过 holdout A/B"
  exit 0
fi

echo ""
echo "[2/3] Holdout A/B 对比（baseline → candidate → compare）..."
"$PYTHON" performance_evaluation/ab_compare_models.py --db-path "$DB_PATH" --baseline
"$PYTHON" performance_evaluation/ab_compare_models.py \
  --db-path "$DB_PATH" \
  --candidate "$MODEL+lora"

echo ""
echo "[3/3] 读取对比结果..."
VERIFY_RC=$("$PYTHON" - <<PY
import json, sys
from pathlib import Path

report_path = Path("performance_evaluation/state/ab_comparison_report.json")
if not report_path.is_file():
    print("缺少 ab_comparison_report.json")
    sys.exit(1)
d = json.loads(report_path.read_text(encoding="utf-8"))
b = d.get("baseline") or {}
print(f"基线:  {b.get('l1_accuracy_pct')}% ({b.get('l1_correct')}/{b.get('l1_total')})")
c = d.get("candidate") or {}
print(f"候选:  {c.get('l1_accuracy_pct')}% ({c.get('l1_correct')}/{c.get('l1_total')})")
cmp = d.get("comparison") or {}
delta = float(cmp.get("delta_pp") or 0)
print(f"Delta: {delta:+.2f}pp")
min_delta = float("${MIN_DELTA_PP}")
if delta >= min_delta:
    print(f"结论: PASS — LoRA 优于基线 ≥ {min_delta}pp")
    sys.exit(0)
elif delta >= -1:
    print("结论: WARN — LoRA 与基线持平，建议继续优化后再部署")
    sys.exit(0)
else:
    print("结论: FAIL — LoRA 不如基线，禁止部署")
    sys.exit(1)
PY
) || VERIFY_RC=$?

exit "${VERIFY_RC:-0}"
