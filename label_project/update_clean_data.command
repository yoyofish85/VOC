#!/bin/bash
# Mac 双击运行：在终端中执行一键更新（需已安装 python3，且项目内路径正确）
cd "$(dirname "$0")"
chmod +x run_label_update.sh 2>/dev/null || true
exec ./run_label_update.sh
