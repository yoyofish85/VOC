#!/bin/bash
# 一键更新：备份 → 可选合并清洗库 → 重建二级白名单与三级聚类 → 导出标注用标准表
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
cd "$DIR"
mkdir -p "$DIR/backup"
TS=$(date +%Y%m%d_%H%M%S)

for f in gold_l2_whitelist_v3.json gold_l3_clusters_v3.json; do
  if [[ -f "$DIR/$f" ]]; then
    cp "$DIR/$f" "$DIR/backup/${f%.json}_${TS}.json"
    echo "已备份: backup/${f%.json}_${TS}.json"
  fi
done

GOLD_CSV="${LABEL_GOLD_CSV:-$ROOT/testdata3.csv}"
MAP_JSON="${LABEL_MAP_JSON:-$DIR/label_hierarchy_final.json}"

if [[ ! -f "$GOLD_CSV" ]]; then
  echo "⚠️ 未找到金标 CSV: $GOLD_CSV"
  echo "   可设置环境变量 LABEL_GOLD_CSV 指向 5628 数据文件后重试。"
  exit 1
fi

echo "📂 金标: $GOLD_CSV"
python3 "$DIR/build_gold_taxonomy_v3.py" -i "$GOLD_CSV" -m "$MAP_JSON" \
  --out-l2 "$DIR/gold_l2_whitelist_v3.json" \
  --out-l3 "$DIR/gold_l3_clusters_v3.json"

if [[ -f "$DIR/merge_clean_data_to_hierarchy.py" ]]; then
  echo "📂 可选：合并清洗数据到 hierarchy …"
  python3 "$DIR/merge_clean_data_to_hierarchy.py" || true
fi

python3 "$DIR/export_standard_label_system.py" --out-dir "$ROOT/output" || true

echo "✅ 标签白名单/聚类/标准导出已完成。"
