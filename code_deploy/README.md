# VOC_V1.5 离线代码迁移与一键更新工具包

本目录用于 **只更新程序代码（`src/`）**，**绝不覆盖或删除** 业务数据目录、数据库与运行期配置数据。

---

## 目录结构

| 路径 | 说明 |
|------|------|
| `package_code.sh` | **开发机**：构建前端并生成 `update.zip` |
| `update_server.sh` | **服务器**：停止服务 → 备份 `src/` → 解压覆盖 `src/` → 依赖检查 |
| `rollback_server.sh` | **服务器**：从 `backup/` 恢复历史 `src/` |
| `update.zip` | 打包产物（勿提交仓库，已加入 `.gitignore`） |
| `backup/` | 每次更新前自动备份的 `src/` 快照 |
| `README.md` | 本说明 |

---

## 一、开发机如何打包

### 前置条件

- macOS
- 已安装 Node.js / npm（用于 `npm run build`）
- 位于克隆后的项目根目录 `VOC_V1.5/`

### 命令

```bash
cd /path/to/VOC_V1.5
chmod +x code_deploy/package_code.sh
./code_deploy/package_code.sh
```

### 脚本会做什么

1. 在 `src/frontend` 执行 **`npm run build`**，生成最新 `dist/`。
2. 将 **`src/`** 复制到临时目录，并 **排除**：
   - `node_modules/`
   - `__pycache__/`、`*.pyc`、`.pytest_cache/`
   - 前端 `dist/`（随后单独拷入刚构建的产物）
   - `*.db` / `*.sqlite`（防止误打进包）
   - `qwen_inference_cache.json`（本地推理缓存，不随代码包迁移）
   - `.vite/`、虚拟环境目录等
3. 在 **`code_deploy/update.zip`** 中生成 **仅包含顶层 `src/`** 的 zip。

### 不会做什么

- 不修改任何业务源码（除构建产生的 `dist` 外）。
- **不包含** 项目根下的 `data/`、`label_project/`、`*.db`、`app_launcher.py`、`requirements.txt`。

> **注意**：若本次上线包含 `label_project/` 标签 JSON、或根目录 `app_launcher.py` / `requirements.txt` 的变更，请 **另行用 U 盘/rsync 单独同步** 这些路径，或自行扩展打包脚本白名单。

### Qwen2.5-14B 本地模型配置

模型本体由服务器 Ollama 管理，不进入 `update.zip`。服务器需提前准备：

```bash
ollama pull qwen2.5:14b-instruct-q4_K_M
```

可选环境变量（默认已适配 M3 Max 36GB）：

```bash
export VOC_QWEN_MODEL=qwen2.5:14b-instruct-q4_K_M
export VOC_QWEN_TEMPERATURE=0.1
export VOC_QWEN_TOP_P=0.3
export VOC_QWEN_NUM_CTX=131072
export VOC_QWEN_CONCURRENCY=2
```

---

## 二、如何传输到服务器

1. 将 **`code_deploy/update.zip`** 拷贝到目标 Mac（如 M3 Max）上的同一项目路径，例如：
   - `…/VOC_V1.5/code_deploy/update.zip`
2. 可使用 U 盘、AirDrop、内网 scp/smb 等 **离线或半离线** 方式；**不要**把生产库 `data/` 当作打包内容传来传去。

---

## 三、服务器如何一键更新

### 前置条件

- 目标机上已存在完整 VOC 部署目录（含 `data/`、`src/` 等），本脚本 **只覆盖 `src/`**。

### 命令

```bash
cd /path/to/VOC_V1.5
chmod +x code_deploy/update_server.sh
./code_deploy/update_server.sh
```

或指定 zip 路径：

```bash
./code_deploy/update_server.sh /Users/you/Downloads/update.zip
```

### 可选：更新后自动后台启动

```bash
AUTO_START=1 ./code_deploy/update_server.sh
```

日志默认写入 `code_deploy/voc_launcher_时间戳.log`。

### 脚本会做什么

1. 尝试释放 **8000、8080** 端口（与默认后端/前端端口一致）。
2. 将当前 **`src/`** 完整复制到 **`code_deploy/backup/src_backup_时间戳`**。
3. 解压 `update.zip`，要求包内必须有 **`src/`** 顶层目录。
4. 使用 `rsync` **合并覆盖** 到项目根下 `src/`（**不会**删除 `data/`、不会动项目根下数据库文件路径约定之外的文件）。
5. 若 `src/frontend` 无 `node_modules`，自动执行 **`npm install`**。

### 不会做什么（数据安全）

- **不** 修改、删除、覆盖 **`data/`** 目录。
- **不** 在更新脚本中操作 **`*.csv`、关键词 JSON、环境专有配置**（这些不在 `src/` 打包范围内；若你曾把库文件放在 `src/` 下，打包脚本已排除 `*.db`）。
- 数据库默认在 `src/backend/opinion_review.db`：本更新 **不会** 用 zip 覆盖 `.db`（打包端已排除）；服务器端 **不会** 执行删除 `.db` 的步骤。

---

## 四、如何回滚

1. 查看备份：

   ```bash
   ls -lt code_deploy/backup
   ```

2. 交互式回滚（默认使用 **最新** 一次 `src_backup_*`）：

   ```bash
   chmod +x code_deploy/rollback_server.sh
   ./code_deploy/rollback_server.sh
   ```

3. 或指定备份目录名：

   ```bash
   ./code_deploy/rollback_server.sh src_backup_20260108_143022
   ```

4. 回滚后手动启动：

   ```bash
   python3 app_launcher.py
   ```

---

## 五、注意事项（务必阅读）

1. **数据优先**：生产环境更新前，建议另行对 `data/` 与 `*.db` 做一次 **Time Machine / 手工拷贝** 备份；本工具不代替数据库灾备。
2. **仅信本仓库脚本**：`update.zip` 应只来自 **本仓库的 `package_code.sh`**，勿使用来源不明的压缩包，以免 `src/` 被植入异常代码。
3. **Python 依赖**：若 `requirements.txt` 有新增依赖，服务器需 **手动** `pip install -r requirements.txt`（本更新包不包含根目录依赖文件）。
4. **端口与启动方式**：若生产环境不用 `app_launcher.py` 而用 systemd/其他方式启动，请自行改用你们的启停命令，并仍建议在更新前停止旧进程。
5. **label_project**：分类/金标相关 JSON 在仓库 `label_project/`，**不在** `update.zip` 内；若有变更必须单独同步。

---

## 六、快速检查清单

| 步骤 | 开发机 | 服务器 |
|------|--------|--------|
| 打包 | `./code_deploy/package_code.sh` | — |
| 传输 | 拷贝 `update.zip` | 收到 zip |
| 更新 | — | `./code_deploy/update_server.sh` |
| 启动 | — | `python3 app_launcher.py` 或 `AUTO_START=1` |
| 回滚 | — | `./code_deploy/rollback_server.sh` |

---

**版本**：与 VOC_V1.5 仓库同步维护。脚本适用于 **macOS**（依赖系统自带 `bash`、`zip`、`unzip`、`rsync`、`lsof`）。
