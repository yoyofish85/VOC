#!/usr/bin/env bash
#
# 14B LoRA 训练（均衡数据 mlx_finetune_balanced/）
# 用法（项目根目录）:
#   bash scripts/train_lora_14b.sh
#
# 可选环境变量:
#   VOC_MLX_MODEL      默认 mlx-community/Qwen2.5-14B-Instruct-4bit
#   VOC_MLX_ADAPTER    默认 ~/lora_adapter_14b_v2
#   VOC_LORA_DATA_DIR  默认 ./mlx_finetune_balanced
#   VOC_LORA_ITERS     默认 1000
#   VOC_PYTHON         默认 python3
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PYTHON="${VOC_PYTHON:-python3}"
MODEL="${VOC_MLX_MODEL:-mlx-community/Qwen2.5-14B-Instruct-4bit}"
ADAPTER_PATH="${VOC_MLX_ADAPTER:-$HOME/lora_adapter_14b_v2}"
DATA_DIR="${VOC_LORA_DATA_DIR:-mlx_finetune_balanced}"
ITERS="${VOC_LORA_ITERS:-1000}"

echo "============================================================"
echo " 14B LoRA 均衡数据训练"
echo "============================================================"
echo "项目根:     $ROOT"
echo "数据目录:   $DATA_DIR"
echo "基座模型:   $MODEL"
echo "Adapter:    $ADAPTER_PATH"
echo "迭代次数:   $ITERS"
echo ""

for split in train valid test; do
  f="$DATA_DIR/${split}.jsonl"
  if [[ ! -s "$f" ]]; then
    echo "[错误] 缺少或为空: $f"
    echo "  请先运行: python3 scripts/balance_finetune_data.py"
    exit 1
  fi
done

echo "[1/3] 数据行数:"
wc -l "$DATA_DIR"/*.jsonl

echo ""
echo "[2/3] 检查 mlx-lm 与基座模型..."
"$PYTHON" - <<PY
from mlx_lm import load
print("mlx-lm OK, 预加载基座模型（首次会下载）...")
load("${MODEL}")
print("模型加载成功")
PY

echo ""
echo "[3/3] 开始 LoRA 训练..."
"$PYTHON" -m mlx_lm lora \
  --model "$MODEL" \
  --data "$DATA_DIR" \
  --adapter-path "$ADAPTER_PATH" \
  --train \
  --iters "$ITERS" \
  --batch-size 2 \
  --learning-rate 2e-05 \
  --num-layers 16 \
  --max-seq-length 1024 \
  --save-every 200 \
  --steps-per-eval 200 \
  --steps-per-report 20 \
  --val-batches 50 \
  --grad-checkpoint

echo ""
echo "============================================================"
echo " 训练完成"
echo " Adapter: $ADAPTER_PATH"
echo " 下一步:  bash scripts/verify_lora.sh"
echo "============================================================"
