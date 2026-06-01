# VOC V1.5 系统全面评估 — 测试计划

**文档版本**：1.0  
**制定日期**：2026-05-25  
**评估范围**：后端单元/集成/性能、前端界面验收、部署健康

---

## 1. 目标

| 维度 | 目标 |
|------|------|
| 功能正确性 | 冲突检测、物化列、路由指标、健康检查、复核/分类/报表主链路可用 |
| 性能 | 报表接口（10 月数据）< 2s；10 并发 batch_classify 不卡死 |
| 稳定性 | 后端 60s 内可启动；14B 异步不阻塞主线程 |
| 体验 | 14B 范围弹窗、数据汇报按需加载、控制台无阻断性 error |

---

## 2. 测试环境要求

| 项 | 要求 | 验证命令 |
|----|------|----------|
| Python | **3.10+**（推荐；3.9 可跑但非目标） | `python3 --version` |
| Node.js | 18+ | `node --version` |
| 数据库 | `VOC_DB_PATH` 指向可写 SQLite | 启动后 `GET /health` |
| 后端 | 8000 端口 | `curl http://127.0.0.1:8000/health` |
| 前端 | 8080（Vite 默认）或 5173 | 浏览器 / Playwright |
| 依赖 | `requirements-dev.txt` + `pytest-cov` | `pip install -r requirements-dev.txt pytest-cov` |

**环境变量（集成/性能测试）**

```bash
export VOC_DB_PATH=/path/to/writable/opinion_review.db
export VOC_DISABLE_RATE_LIMIT=1
export VOC_BASE_URL=http://127.0.0.1:8000
export VOC_FRONTEND_URL=http://127.0.0.1:8080
```

---

## 3. 测试分层

```
        E2E / UI 验收（阶段 4）
              │
        集成 & 性能（阶段 3）
              │
        单元测试（阶段 2）
              │
        环境 & 计划（阶段 1）
```

---

## 4. 阶段 1 — 测试准备

- [x] 生成本文档 `docs/test_plan.md`
- [ ] 确认 Python / Node / DB 可写
- [ ] 安装 `pytest-cov`
- [ ] 启动后端（`python src/backend/main.py` 或 `app_launcher.py`）

---

## 5. 阶段 2 — 后端单元测试

### 5.1 覆盖模块

| 模块 | 文件 | 测试文件 | 重点用例 |
|------|------|----------|----------|
| 冲突检测 | `rule_conflict_detector.py` | `tests/unit/test_rule_conflict_detector.py` | C1/C2/C3 命中、空文本、JSON 热加载 |
| 物化列 | `v3_materialized.py` | `tests/unit/test_v3_materialized.py` | schema、回填、物化字段解析 |
| 路由指标 | `classify_metrics_logger.py` | `tests/unit/test_classify_metrics_logger.py` | record_row、finalize、read JSONL |
| 健康检查 | `main.py` | `tests/unit/test_main_health.py` | `/health`、`/api/health` TestClient |

### 5.2 执行命令

```bash
cd VOC_V1.5
VOC_UNIT_ONLY=1 python3 -m pytest tests/unit/ -v

# 全量（需后端已启动）
python3 -m pytest tests/ -v --cov=src/backend --cov-report=term-missing
```

### 5.3 通过标准

- 单元测试 100% 通过
- 集成测试（tests/ 除 unit）在后端就绪时通过
- 新增模块行覆盖率 ≥ 70%（目标）

---

## 6. 阶段 3 — 集成 & 性能

### 6.1 并发 batch_classify

| 步骤 | 说明 |
|------|------|
| 准备 | 上传 1 个测试批次（≥10 条） |
| 执行 | 同时 POST 10 个 `async=true` 的 batch_classify（use_llm=false） |
| 断言 | 全部返回 200 + job_id；60s 内全部 completed；`/health` 仍 200 |

### 6.2 14B 卡死检查

| 步骤 | 说明 |
|------|------|
| 执行 | 提交 1 个 use_llm=true 小批量（2 条）async job |
| 断言 | 提交后立即返回；轮询 status 有进度；health 不超时 |

> 无 Ollama 时 14B 可能 failed，但不应对 HTTP 层造成卡死。

### 6.3 报表响应时间

| 接口 | 参数 | SLA |
|------|------|-----|
| `GET /api/get_monthly_overview` | `date_from=2025-10-01&date_to=2025-10-31&region=all` | < 2s |
| `GET /api/get_monthly_subtag_trend` | 同上 + top_secondary=1 | < 2s |

> 计划中的 `/api/report/daily` 在当前代码库中**不存在**，以月度报表 API 替代验收。

### 6.4 执行

```bash
python3 -m pytest tests/test_perf_integration.py -v -m perf
```

---

## 7. 阶段 4 — 前端界面验收

**入口**：`http://localhost:8080`（项目 Vite 默认；5173 为备用）

| # | 步骤 | 预期 |
|---|------|------|
| 1 | 打开复核工作台 | 页面加载，无阻断 error |
| 2 | 快速规则分类（use_llm=false） | 任务完成，列表有 v3 标签 |
| 3 | 勾选 2 条 → 14B | 弹出范围确认（仅所选 / 整批） |
| 4 | 数据汇报 → 切换日期 | 图表渲染，无全页卡死 |
| 5 | 控制台 | 收集 error/warn |

**工具**：Chrome DevTools MCP 或 Playwright E2E（`tests/test_e2e.py`）

---

## 8. 阶段 5 — 评估报告

输出：`docs/VOC_V1.5_系统评估报告_YYYY-MM-DD.md`

包含：

1. 测试通过率汇总
2. 未通过场景与根因
3. 修复建议 P0/P1/P2
4. 环境偏差说明（如 Python 3.9 vs 3.10+）

---

## 9. 风险与禁改区

**不在此次评估中修改**：LabelMatcher 规则、联防语义、14B 提示词、咨询黑名单、页面布局。

**已知风险**：

- 生产库过大时后台回填仍在进行，物化列筛选可能不完整（启动后数分钟内）
- 无 Ollama 时 14B 相关用例只能验证「不卡死」，不能验证准确率

---

## 10. 修订记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-05-25 | 1.0 | 初版，对应全面评估五阶段计划 |
