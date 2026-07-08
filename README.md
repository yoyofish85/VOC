# VOC V1.5 舆情分类复核系统

## 目录结构

- `app_launcher.py`：一键启动（后端 + 前端）与 `--verify` 自动化验证
- `src/backend/`：FastAPI 后端（`python main.py` 工作目录应为此目录或使用 uvicorn）
- `src/frontend/`：Vue 3 + Vite 前端
- `src/config/`：全局路径（`VOC_PROJECT_ROOT` 可覆盖项目根）
- `data/annual/`：年度归档 CSV（按年 `YYYY.csv` + `年度数据总和.CSV`）
- `data/labeled/`：清洗库 `data_clear.csv`
- `data/uploaded/`、`data/temp/`：上传缓冲与临时文件
- `label_project/`：标签体系与白名单 JSON

## 环境变量（部署）

| 变量 | 说明 |
|------|------|
| `VOC_PROJECT_ROOT` | 项目根绝对路径（可选，默认从 `src/config/paths.py` 推断） |
| `VOC_DB_PATH` | SQLite 数据库文件路径 |
| `VOC_KEYWORD_FILE` | 关键词 JSON |
| `VOC_ANNUAL_DIR` | 年度归档目录（默认 `data/annual`） |
| `VOC_USE_MLX` | `1` 启用 MLX 推理（见 `start_mlx.sh`） |
| `VOC_MLX_ADAPTER` | LoRA adapter 目录（默认 `~/lora_adapter_14b_v2`） |

## 启动

### 开发机（默认 Ollama）

```bash
python3 app_launcher.py
```

### 服务器（推荐双启动脚本）

```bash
bash scripts/setup_server_venv.sh   # 首次
./start_mlx.sh                      # MLX + LoRA
./start_ollama.sh                   # 切换回 Ollama 14B
```

同一时间只运行一个实例；切换前先 Ctrl+C。

### 仅后端

```bash
cd src/backend && python3 main.py
```

### 仅前端

```bash
cd src/frontend && npm run dev -- --port 8080
```

## 自动化测试

```bash
python3 app_launcher.py --verify
python3 app_launcher.py --verify --quick --skip-e2e
```

## Linux 后台示例

```bash
cd src/backend
export VOC_PROJECT_ROOT="/opt/VOC_V1.5"
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 >> ../../logs/backend.log 2>&1 &
```

## Streamlit 辅助工具（可选）

```bash
python3 -m streamlit run src/utils/feedback_manage_system_v1.5.py
```

## 数据汇报页（多条件筛选可视化）

- 入口：前端标签 **「数据汇报 / Report」**（`src/frontend/src/views/DataReport.vue`）。
- 后端接口（相对路径，无磁盘硬编码）：
  - `GET /api/get_monthly_overview?date_from=&date_to=&region=` — `region`: `all` | `cn` | `rest`
  - `GET /api/get_monthly_subtag_trend?...&l1=&top_secondary=`
  - `GET /api/get_top_subtag_monthly?...&top_n=`
- 统计口径与仪表盘一致：已复核优先 `review_l1`/`review_l2`，否则 `v3_label_meta`；界面「销售服务类」对应库内规范一级 **服务类**。
- 实现：`src/backend/report_aggregator.py`（约 45s TTL 内存缓存）。

### 本地验证

1. 启动后打开 **数据汇报 / Report**，选日期与区域，点 **刷新**；三张图应渲染；**导出 PNG** 保存深色底图。
2. 冒烟：`cd src/backend && python3 -c "from fastapi.testclient import TestClient; import main; print(TestClient(main.app).get('/api/get_monthly_overview', params={'date_from':'2025-01-01','date_to':'2025-01-31'}).json()['code'])"`

### 仅替换 `src`、保留 `data`（不停写数据目录）

1. 停服后备份；覆盖 **`src/`**；**勿删 `data/`**（`annual`、`labeled` 等）。
2. 重启；`src/frontend` 按需 `npm install`。默认 DB 仍为 `src/backend/opinion_review.db`（未改 `VOC_DB_PATH` 则数据保留）。

### 大模型解读（扩展）

后续可将本页返回的 `months`/`series` JSON 作为 prompt，经 `OLLAMA_HOST` 调用 Qwen2.5-14B 生成归因说明（与现有 `batch_classify` 的 Ollama 配置一致）。
