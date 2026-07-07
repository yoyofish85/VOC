#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo '╔══════════════════════════════════════════╗'
echo '║   VOC V1.5 · 启动中                      ║'
echo '║   分类模型：Ollama 14B（原始）            ║'
echo '║   未加载 LoRA adapter                    ║'
echo '╚══════════════════════════════════════════╝'

source .venv/bin/activate

# 清除任何 MLX 变量，确保走 Ollama 路径
unset VOC_USE_MLX VOC_MLX_ADAPTER VOC_MLX_MODEL

# 通过 app_launcher.py 启动
python3 app_launcher.py &

# 等待后端启动
echo "等待后端..."
for i in $(seq 1 30); do
  sleep 1
  if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "✅ 后端已就绪 (http://localhost:8000)"
    break
  fi
  [ $i -lt 30 ] && echo -n "." || echo "❌ 后端启动超时"
done

# 显示模型信息
echo ""
python3 -c "import sys;sys.path.insert(0,'src/backend');from qwen_ollama import classify_text;r=classify_text('测试',db_path='src/backend/opinion_review.db');print(f'模型: {r[\"match_type\"]}')" 2>/dev/null || echo "模型: qwen14b_structured (Ollama)"

echo ""
echo "前端: http://localhost:8080"
echo ""
