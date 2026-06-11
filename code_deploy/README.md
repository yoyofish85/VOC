# VOC_V1.5 离线代码迁移与一键更新工具包

本目录用于 **只更新程序代码（`src/`）**，**绝不覆盖或删除** 业务数据目录、数据库与运行期配置数据。

---

## 目录结构

| 路径 | 说明 |
|------|------|
| `package_code.sh` | **开发机**：构建前端、打 git tag、生成 `update.zip` + MD5 |
| `update_server.sh` | **服务器**：MD5 校验 → 备份 → 覆盖 `src/` → 部署自检 |
| `rollback_server.sh` | **服务器**：从 `backup/` 恢复历史 `src/` |
| `deploy_checklist.py` | **服务器**：部署后健康自检（行数 / WAL / import / API） |
| `update.zip` | 打包产物（勿提交仓库） |
| `update.zip.md5` | zip 的 MD5 校验文件（与 zip 一并传输） |
| `backup/` | 每次更新前的 `src/` 快照 + MD5 归档 |
| `README.md` | 本说明 |

---

## 一、开发机如何打包

### 前置条件

- macOS
- 已安装 Node.js / npm（用于 `npm run build`）
- Git 仓库工作区 **干净**（无未提交修改；测试模式见下文）
- 位于项目根目录 `VOC_V1.5/`

### 命令

```bash
cd "/Users/yuchao/Documents/AI Agent/VOC_V1.5"
chmod +x code_deploy/package_code.sh
./code_deploy/package_code.sh
```

### 产出物

| 文件 | 说明 |
|------|------|
| `code_deploy/update.zip` | 仅含顶层 `src/` 的代码包 |
| `code_deploy/update.zip.md5` | MD5 校验（格式：`hash  update.zip`） |
| git tag `deploy_YYYYMMDD_HHMM` | 打包时刻的版本标记 |
| `src/deploy_manifest.json`（在 zip 内） | deploy tag、commit、变更文件列表 |

### 脚本流程

1. **Git 检查**：工作区有未提交修改时 **退出**（不自动 commit）；创建 tag `deploy_YYYYMMDD_HHMM`。
2. **前端构建**：在 `src/frontend` 执行 `npm run build` → `dist/`。
3. **组装临时目录**：复制 `src/` 并排除数据与依赖（见下表）。
4. **写入 manifest**：`src/deploy_manifest.json`（tag / commit / packed_at / changed_files）。
5. **生成 zip + MD5**：校验 zip 内无数据库/WAL 文件。

### 打包排除项

| 排除 | 原因 |
|------|------|
| `node_modules/`、`__pycache__/`、`.venv/` | 依赖与缓存 |
| `*.db`、`*.db-*`、`*.sqlite` | 业务数据库 |
| `qwen_inference_cache.json` | 本地推理缓存 |
| 项目根 `data/`、`label_project/` | 不在 `src/` 内，需单独同步 |

### 测试 / CI 环境变量

```bash
SKIP_FRONTEND_BUILD=1   # 跳过 npm run build（需已有 dist/index.html）
ALLOW_DIRTY_PACK=1      # 允许脏工作区打包
SKIP_GIT_TAG=1          # 跳过 git tag 创建
```

### 不会打包的内容

- 项目根 `data/`、`label_project/`、`app_launcher.py`、`requirements.txt`
- 若上述文件有变更，请 **另行 rsync / U 盘同步**

---

## 二、如何传输到服务器

**必须一并传输：**

```
code_deploy/update.zip
code_deploy/update.zip.md5
code_deploy/deploy_checklist.py   # 首次部署或脚本有更新时
code_deploy/update_server.sh      # 若服务器脚本版本过旧
code_deploy/rollback_server.sh
```

传输方式：U 盘、AirDrop、内网 scp/smb 等。**不要**把生产库 `data/` 或 `*.db` 打进 zip 传来传去。

---

## 三、服务器如何一键更新

### 前置条件

- 目标机已有完整 VOC 部署目录（含 `data/`、`src/`、`*.db` 等）
- 本脚本 **只覆盖 `src/`**，不触碰数据库文件

### 推荐流程

```bash
cd /path/to/VOC_V1.5

# 1. 先校验，不修改 src/
./code_deploy/update_server.sh --check-only

# 2. 正式部署
./code_deploy/update_server.sh

# 3. 部署后自检（update_server.sh 也会自动尝试运行）
python3 code_deploy/deploy_checklist.py

# 4. 启动
python3 app_launcher.py
# 或：AUTO_START=1 ./code_deploy/update_server.sh
```

### 命令选项

```bash
./code_deploy/update_server.sh                      # 默认 code_deploy/update.zip
./code_deploy/update_server.sh /path/to/update.zip  # 指定 zip
./code_deploy/update_server.sh --check-only         # 仅 MD5 + zip 依赖检查
./code_deploy/update_server.sh --rollback           # 自动回滚到最近备份
./code_deploy/update_server.sh -h                   # 帮助
```

### 脚本流程（正式部署）

1. **MD5 校验**：`md5sum -c update.zip.md5`（macOS 使用 `md5` fallback）；失败则中止。
2. **MD5 归档**：复制到 `backup/YYYYMMDD/update.zip.md5`。
3. **zip 依赖检查**：须含 `src/backend/main.py`、`src/frontend/dist/`，且 **不得** 含 `.db` / `.db-wal` / `.db-shm`。
4. **停止服务**：释放端口 8000 / 8080。
5. **备份**：`src/` → `backup/src_backup_时间戳/`。
6. **覆盖前清理 WAL**：删除目标 `src/` 下 `*.db-shm` / `*.db-wal` 并记录路径。
7. **解压覆盖**：`rsync` 合并到 `src/`（不删 `data/`）。
8. **前端依赖**：无 `node_modules` 时自动 `npm install`。
9. **部署摘要**：打印 manifest 中的 deploy tag、commit、变更文件、`diff -rq` 差异。
10. **自检**：运行 `deploy_checklist.py`（若存在）。

### 数据安全承诺

- **不** 修改、删除 `data/` 目录
- **不** 用 zip 覆盖 `*.db`（打包端已排除）
- **不** 删除服务器上已有的数据库文件

---

## 四、如何回滚

```bash
# 查看备份
ls -lt code_deploy/backup

# 交互式回滚（默认最新 src_backup_*）
./code_deploy/rollback_server.sh

# 指定备份
./code_deploy/rollback_server.sh src_backup_20260108_143022

# 非交互（update_server.sh --rollback 内部使用）
./code_deploy/rollback_server.sh -y
```

回滚后手动启动：`python3 app_launcher.py`

---

## 五、部署后自检（deploy_checklist.py）

```bash
python3 code_deploy/deploy_checklist.py
```

检查项（7 项，全部通过即输出 `自检全部通过 (7/7)`）：

| 检查 | 说明 |
|------|------|
| `main.py` 行数 | 预期 2800–4500 行 |
| `reflow_service.py` 行数 | 预期 150–500 行 |
| SQLite WAL | `PRAGMA journal_mode` = `wal` |
| import | `from reflow_service import reflow_batch_rows` |
| 前端 dist | `src/frontend/dist/index.html` 存在 |
| `/api/health` | 返回 200（需后端已启动） |
| `/api/health/detail` | 返回 200（需后端已启动） |

环境变量：

```bash
VOC_BASE_URL=http://127.0.0.1:8000   # API 地址
VOC_DB_PATH=/path/to/opinion_review.db # 数据库路径
```

---

## 六、服务器部署后验证命令

```bash
# 1. 确认关键文件
ls -la src/backend/main.py src/backend/reflow_service.py \
  src/frontend/dist/index.html code_deploy/deploy_checklist.py

# 2. 确认 WAL 模式
python3 -c "
import sqlite3
c = sqlite3.connect('src/backend/opinion_review.db')
print('journal_mode:', c.execute('PRAGMA journal_mode').fetchone()[0])
print('integrity_check:', c.execute('PRAGMA integrity_check').fetchone()[0])
c.close()
"

# 3. 部署自检
python3 code_deploy/deploy_checklist.py

# 4. API 健康
curl -s http://localhost:8000/api/health \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['status'])"

curl -s http://localhost:8000/api/health/detail \
  | python3 -c "
import sys,json; d=json.load(sys.stdin)['data']
print('WAL:', d.get('db',{}).get('journal_mode'))
print('stats:', d.get('stats'))
"

# 5. 前端
curl -s -o /dev/null -w '%{http_code}' http://localhost:8080

# 6. 确认 zip 无数据库（开发机侧）
unzip -l code_deploy/update.zip | grep -E '\.(db|sqlite|db-shm|db-wal)$'
# 预期：无输出
```

---

## 七、开发机测试

```bash
cd "/Users/yuchao/Documents/AI Agent/VOC_V1.5"

# 安装依赖（venv 示例）
./venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt python-multipart 'httpx==0.27.2'

# 单元测试
./venv/bin/python -m pytest \
  tests/unit/test_reflow_resilience.py \
  tests/unit/test_pending_reflow.py \
  tests/unit/test_main_health.py \
  tests/unit/test_draft_save_bindings.py \
  -v

# 打包测试
./venv/bin/python -m pytest tests/test_deploy.py -v
```

> **注意**：`httpx 0.28.x` 与 `fastapi 0.104` 的 `TestClient` 不兼容，测试环境请固定 `httpx==0.27.2`。

---

## 八、注意事项

1. **数据优先**：更新前另行备份 `data/` 与 `*.db`（Time Machine / 手工拷贝）；本工具不代替数据库灾备。
2. **仅信本仓库脚本**：`update.zip` 应只来自本仓库 `package_code.sh`。
3. **Python 依赖**：`requirements.txt` 有新增时，服务器需 `pip install -r requirements.txt`。
4. **label_project**：金标 JSON 不在 zip 内，有变更须单独同步。
5. **Qwen 模型**：模型由 Ollama 管理，不进入 zip；服务器需提前 `ollama pull qwen2.5:14b-instruct-q4_K_M`。
6. **SQLite WAL**：后端启动时会启用 WAL 模式；部署后应看到 `opinion_review.db-wal` / `.db-shm` 文件。

---

## 九、快速检查清单

| 步骤 | 开发机 | 服务器 |
|------|--------|--------|
| 打包 | `./code_deploy/package_code.sh` | — |
| 传输 | 拷贝 `update.zip` + `update.zip.md5` | 收到文件 |
| 校验 | — | `./code_deploy/update_server.sh --check-only` |
| 更新 | — | `./code_deploy/update_server.sh` |
| 自检 | — | `python3 code_deploy/deploy_checklist.py` |
| 启动 | — | `python3 app_launcher.py` 或 `AUTO_START=1` |
| 回滚 | — | `./code_deploy/rollback_server.sh` 或 `--rollback` |

---

**版本**：与 VOC_V1.5 仓库同步维护。脚本适用于 **macOS**（依赖 `bash`、`zip`、`unzip`、`rsync`、`lsof`、`python3`）。
