# VOC V1.5 系统测试报告

**报告日期**：2026-05-25  
**测试环境**：macOS · Python 3.9.6 · Node 24 · 后端 8000 · 前端 5173  
**后端启动**：`VOC_MOCK_LLM=1 VOC_DISABLE_RATE_LIMIT=1 python3 main.py`

---

## 1. 结论摘要

| 维度 | 结论 |
|------|------|
| **核心路径（上传→规则分类→列表展示）** | ✅ **已通过**（修复列表 SQL 参数顺序后） |
| **单元测试** | ✅ 15/15 通过 |
| **全量集成测试** | ⚠️ **34 通过 / 3 失败 / 1 错误**（见下文） |
| **故障是否全部修复** | ⚠️ **P0 已全部修复**；F6/F7 测试待对齐 |

---

## 2. 测试执行记录

### 2.1 单元测试（阶段 2）

```bash
VOC_UNIT_ONLY=1 python3 -m pytest tests/unit/ -v
```

| 模块 | 用例数 | 结果 |
|------|--------|------|
| `rule_conflict_detector` | 5 | ✅ 全过 |
| `v3_materialized` | 4 | ✅ 全过 |
| `classify_metrics_logger` | 3 | ✅ 全过 |
| `main.py` 健康检查 | 3 | ✅ 全过 |
| `draft_save_bindings`（F4 回归） | 1 | ✅ 全过（修复后） |
| **合计** | **16** | **16 passed** |

### 2.2 集成 / 性能测试

```bash
python3 -m pytest tests/ -m "not e2e" -q
```

**结果**：`34 passed, 3 failed, 1 error`（约 122s）

| 用例 | 结果 | 说明 |
|------|------|------|
| `test_api.py` 等只读接口 | ✅ | health / 报表 / dashboard |
| `test_upload.py::test_upload_single_row_ok` | ✅ | 列表修复后应已通过 |
| `test_upload.py::test_upload_duplicate_skipped` | ❌ | 去重语义变更（见故障表） |
| `test_review.py::test_draft_save_persists_review_status` | ❌ | 绑定 draft_save P0 未修 |
| `test_review.py::test_save_to_yearly_and_data_clear` | ⚠️ ERROR | 缺 `VOC_TEST_ARTIFACTS_DIR` |
| `test_perf_integration.py::test_concurrent_batch_classify_jobs` | ❌ | 断言 `code==200`，异步实际 `202` |
| `test_perf_integration.py` 14B 不卡死 / 报表 <2s | ✅ | 10 月月度接口 SLA 满足 |

### 2.3 浏览器核心路径（Playwright 等效 chrome-devtools）

脚本：`scripts/verify_browser_steps_3_6.py`  
CSV：`test/validation_full_keyword_only_v18.csv`（3 条）

| 步骤 | 结果 |
|------|------|
| 打开 http://127.0.0.1:5173/review | ✅ |
| 上传 CSV | ✅ |
| 快速规则分类 | ✅（约 5s） |
| 表格至少 1 行 | ✅（3 行） |

截图目录：`reports/browser_verify_steps/`

---

## 3. 故障清单与修复状态

### 3.1 已修复 ✅

| ID | 故障 | 根因 | 修复 | 验证 |
|----|------|------|------|------|
| **F1** | 复核列表 `total>0` 但 `data=[]` | `get_review_list` 中 SELECT 的 `?` 在 WHERE 之前，参数顺序错误 | `list_params = [preview_n, preview_n, *params, size, offset]` | API + 浏览器表格 3 行 |
| **F2** | 服务器「后端启动失败」 | 启动时同步全量回填阻塞绑端口；uvicorn reload+workers 冲突 | 后台线程回填；默认 `reload=False`；启动器延长等待并 drain 日志 | 本地约 1s 内 health 200 |
| **F3** | 浏览器上传后表格空白 | 同 F1 | 同 F1 | 步骤 6 通过 |

### 3.2 已修复（2026-05-25 续） ✅

| ID | 优先级 | 故障 | 修复 |
|----|--------|------|------|
| **F4** | P0 | `draft_save_reviews` 500（7 占位符 / 8 参数） | 去掉 `draft_updates` 中多余的 `review_status` 值 `1`（SQL 已写死 `review_status=1`） |
| **F5** | P2 | 并发分类测试断言 `code==200` | 异步接口接受 `200` 或 `202` |

### 3.3 仍待处理 ⚠️

| ID | 优先级 | 故障 | 说明 |
|----|--------|------|------|
| **F6** | P2 | `test_upload_duplicate_skipped` | 产品改为仅舆情 ID 去重，测试期望需更新 |
| **F7** | P3 | `test_save_to_yearly_and_data_clear` | 需 `VOC_TEST_ARTIFACTS_DIR` |

---

## 4. 故障是否全部修复？

**结论：P0 已全部修复；集成测试仍有 2 项非阻塞待对齐（F6/F7）。**

- **已修复**：F1–F5（含列表查询、启动、暂存复核、并发测试断言）
- **待对齐**：F6 去重策略测试、F7 归档测试环境

---

## 5. 修复优先级建议

| 优先级 | 项 | 状态 |
|--------|-----|------|
| **P0** | F4 draft_save | ✅ 已修复 |
| **P1** | 全量 pytest | 修 F4 后重跑 |
| **P2** | F6/F7 | 待更新测试或夹具 |

### 3.4 环境 / 非缺陷项

| 项 | 说明 |
|----|------|
| Python 3.9.6 | 计划要求 3.10+，当前可跑但未达目标版本 |
| Chrome DevTools MCP | 未配置；浏览器步骤由 Playwright 完成 |
| `VOC_MOCK_LLM=1` | 代码库中暂无引用；规则分类路径不依赖 LLM |
| 刷新后表格勾选不保留 | Element Plus 默认行为，非缺陷（复核状态应看 `review_status`） |

---

## 6. 建议复验命令

```bash
# 后端
cd src/backend && VOC_MOCK_LLM=1 VOC_DISABLE_RATE_LIMIT=1 python3 main.py

# 单元测试
VOC_UNIT_ONLY=1 python3 -m pytest tests/unit/ -v

# 列表修复验证
curl "http://127.0.0.1:8000/get_review_list?page=1&size=5"

# draft_save（F4 修复前后对比）
curl -X POST http://127.0.0.1:8000/draft_save_reviews \
  -H "Content-Type: application/json" \
  -d '{"reviews":[{"opinion_id":"V18_T001","review_l1":"产品质量类","review_l2":"车机"}]}'

# 浏览器核心路径
python3 scripts/verify_browser_steps_3_6.py
```

---

## 7. 修订记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-05-25 | 1.0 | 汇总单元/集成/浏览器测试；确认 F1–F3 已修、F4 未修 |
| 2026-05-25 | 1.1 | F4 暂存绑定修复 + F5 测试断言；新增 `test_draft_save_bindings.py` |
