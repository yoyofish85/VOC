# Spec: 基金分析助手（桌面版）

> **状态：** Phase 1 — Specify（待你确认）  
> **目标用户：** 编程小白、个人投资者  
> **版本：** v0.1 草案  
> **最后更新：** 2026-05-21

---

## 我在做的假设（请现在纠正）

在写详细规格前，我先列出默认假设。**若不对，请直接指出，我会改 spec 后再继续。**

1. **应用形态：** 本地桌面应用（不是网页、不是手机 App）
2. **使用场景：** 个人自用，单用户，数据保存在本机（不上云、不需要登录）
3. **基金范围：** 中国公募基金（股票型、混合型、债券型、指数型等），以基金代码（如 `110011`）识别
4. **数据来源：** 第一版通过**免费公开接口或 CSV 导入**获取净值，不接入券商交易账户
5. **「是否投资 / 增购 / 赎回」：** 应用给出**参考建议 + 理由**，不是持牌投顾意见；界面必须有免责声明
6. **操作系统：** 你当前使用 macOS；第一版优先支持 macOS，架构上尽量可扩展到 Windows
7. **技术选型：** 你是编程小白 → 推荐 **Python + PySide6**（界面）+ **SQLite**（本地数据库）+ **pandas**（数据分析），学习曲线相对平缓
8. **决策逻辑：** 第一版用**可配置的规则引擎**（如均线、涨跌幅、回撤阈值），不用复杂 AI 模型
9. **与 VOC_V1.5 无关：** 这是独立新项目，不嵌入现有舆情系统

---

## Objective（目标）

### 我们要做什么

构建一款**基金分析桌面应用**，帮助个人投资者：

1. **查看基金涨跌**：历史净值、阶段涨跌幅、与基准对比
2. **判断是否值得投资**：对新基金或未持仓基金给出「观望 / 可考虑 / 不建议」等参考结论
3. **判断增购或赎回**：对已持仓基金给出「持有 / 增购 / 减仓 / 赎回」等参考结论
4. **记录自己的持仓与操作**：手动录入买入/卖出，自动计算成本、浮盈浮亏

### 用户故事

| 编号 | 作为… | 我希望… | 以便… |
|------|--------|---------|--------|
| US-01 | 新手投资者 | 输入基金代码就能看到最近走势和关键指标 | 快速了解这只基金表现 |
| US-02 | 持仓用户 | 看到系统对我每只基金的建议及理由 | 决定今天要不要加减仓 |
| US-03 | 谨慎用户 | 自定义规则阈值（如回撤超过 15% 提醒赎回） | 按自己的风险偏好决策 |
| US-04 | 记录型用户 | 导入或手动记录买卖记录 | 知道真实成本和收益 |
| US-05 | 任何用户 | 清楚看到「仅供参考，不构成投资建议」 | 避免误把工具当投顾 |

### 成功长什么样

- 打开应用 → 3 秒内看到「我的持仓」列表和今日建议
- 搜索任意基金代码 → 10 秒内展示近 1 年走势图 + 分析结论
- 所有建议都能展开看到**具体依据**（例如：「近 20 日跌 8%，超过你设置的 5% 警戒线」）
- 断网时仍可查看已缓存的历史数据和本地持仓

---

## Tech Stack（技术栈）

| 层级 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.11+ | 数据分析生态成熟，语法相对友好 |
| 桌面 UI | PySide6 (Qt6) | 原生桌面体验，文档和示例较多 |
| 图表 | pyqtgraph 或 matplotlib | 绘制净值曲线 |
| 数据处理 | pandas, numpy | 计算涨跌幅、均线、回撤 |
| 本地存储 | SQLite + SQLAlchemy（可选） | 零配置、单文件数据库 |
| 配置 | YAML 或 JSON | 存储用户规则阈值 |
| 打包分发 | PyInstaller | 打成 `.app` / `.exe`，小白双击即用 |
| 测试 | pytest | 与 Python 生态一致 |

**备选方案（若你更熟悉前端）：** Tauri 2 + Vue 3 + Python 侧车服务 — UI 更现代，但对小白学习成本更高。默认仍推荐纯 Python 方案。

---

## Commands（命令）

```bash
# 创建虚拟环境（首次）
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 开发模式启动
python -m fund_analyzer

# 运行测试
pytest tests/ -v

# 测试覆盖率
pytest tests/ --cov=fund_analyzer --cov-report=term-missing

# 代码格式检查（可选）
ruff check fund_analyzer/
ruff format fund_analyzer/

# 打包 macOS 应用（后续阶段）
pyinstaller fund_analyzer.spec
```

---

## Project Structure（项目结构）

```
fund-analyzer/                    # 独立仓库（建议与 VOC 分开）
├── fund_analyzer/                # 主包
│   ├── __main__.py               # 入口：python -m fund_analyzer
│   ├── app.py                    # Qt 应用初始化
│   ├── ui/                       # 界面层
│   │   ├── main_window.py        # 主窗口（持仓 + 搜索）
│   │   ├── fund_detail.py        # 基金详情页（图表 + 建议）
│   │   ├── settings_dialog.py    # 规则阈值设置
│   │   └── widgets/              # 可复用小组件
│   ├── services/                 # 业务逻辑
│   │   ├── data_fetcher.py       # 拉取/缓存净值
│   │   ├── analyzer.py           # 指标计算
│   │   ├── advisor.py            # 投资建议规则引擎
│   │   └── portfolio.py          # 持仓与成本计算
│   ├── models/                   # 数据模型
│   │   ├── fund.py
│   │   ├── holding.py
│   │   └── transaction.py
│   ├── db/                       # SQLite 持久化
│   │   ├── schema.sql
│   │   └── repository.py
│   └── config/                   # 默认规则配置
│       └── default_rules.yaml
├── tests/
│   ├── test_analyzer.py
│   ├── test_advisor.py
│   └── test_portfolio.py
├── docs/
│   └── spec-基金分析桌面应用.md    # 本文件
├── requirements.txt
├── requirements-dev.txt
├── fund_analyzer.spec            # PyInstaller 配置
└── README.md
```

---

## Code Style（代码风格）

面向小白的约定：**命名直白、函数短小、类型标注、中文注释只解释「为什么」**。

```python
# fund_analyzer/services/advisor.py

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    HOLD = "持有"
    BUY = "增购"
    REDUCE = "减仓"
    REDEEM = "赎回"
    WATCH = "观望"
    CONSIDER = "可考虑"
    AVOID = "不建议"


@dataclass(frozen=True)
class Advice:
    action: Action
    confidence: str          # "低" | "中" | "高"
    reasons: list[str]       # 用户可读的中文理由


def advise_holding(
    return_20d_pct: float,
    max_drawdown_pct: float,
    *,
    loss_alert_pct: float = -5.0,
    drawdown_alert_pct: float = -15.0,
) -> Advice:
    """对已持仓基金给出增购/赎回建议（规则可配置）。"""
    reasons: list[str] = []

    if return_20d_pct <= loss_alert_pct:
        reasons.append(f"近 20 日跌 {abs(return_20d_pct):.1f}%，超过警戒线 {abs(loss_alert_pct):.1f}%")
        return Advice(Action.REDUCE, "中", reasons)

    if max_drawdown_pct <= drawdown_alert_pct:
        reasons.append(f"最大回撤 {abs(max_drawdown_pct):.1f}%，风险偏高")
        return Advice(Action.REDEEM, "中", reasons)

    reasons.append("未触发任何风险规则，建议继续持有")
    return Advice(Action.HOLD, "低", reasons)
```

**约定摘要：**

- 文件名、函数名：`snake_case`
- 类名：`PascalCase`
- 用户可见文案：简体中文
- 单函数不超过 ~40 行；复杂逻辑拆到 `services/`
- UI 与业务逻辑分离：界面不直接写计算公式

---

## 功能范围（MVP vs 后续）

### MVP（第一版必做）

| 模块 | 功能 |
|------|------|
| 基金查询 | 输入代码，展示名称、类型、近 1 年净值曲线 |
| 指标计算 | 近 7/30/90/365 日涨跌幅、最大回撤、简单均线（MA20/MA60） |
| 投资建议 | 对新基金：观望/可考虑/不建议；对持仓：持有/增购/减仓/赎回 |
| 持仓管理 | 手动添加持仓、录入买卖记录、显示成本与浮盈 |
| 规则配置 | 可修改阈值（跌多少提醒、回撤多少赎回） |
| 数据缓存 | 本地 SQLite 缓存净值，减少重复请求 |
| 免责声明 | 启动页 + 建议卡片固定展示 |

### v1.1（第二版可选）

- CSV 批量导入持仓
- 基金对比（最多 3 只并排）
- 定时自动刷新净值
- 导出分析报告（PDF / Markdown）

### 明确不做（第一版）

- 直连券商下单
- 多用户 / 云同步
- 复杂量化回测
- 付费数据源对接

---

## 核心决策规则（v0.1 草案）

> 规则应全部可配置；以下为**默认参数**，用户可在设置中修改。

### 新基金「是否投资」

| 条件 | 建议 | 置信度 |
|------|------|--------|
| 近 90 日涨幅 > 0 且 MA20 > MA60 | 可考虑 | 中 |
| 近 90 日涨幅 ≤ 0 但最大回撤 < 10% | 观望 | 低 |
| 近 90 日跌幅 > 15% 或 MA20 < MA60 且趋势下行 | 不建议 | 中 |

### 已持仓「增购 / 赎回」

| 条件 | 建议 |
|------|------|
| 近 20 日跌幅超过用户设定阈值（默认 -5%） | 减仓 |
| 最大回撤超过用户设定阈值（默认 -15%） | 赎回 |
| 近 20 日涨幅 > 3% 且 MA20 > MA60 且浮盈 > 0 | 增购 |
| 其他 | 持有 |

每条建议必须附带 `reasons[]`，界面以列表展示。

---

## Testing Strategy（测试策略）

| 层级 | 工具 | 测什么 |
|------|------|--------|
| 单元测试 | pytest | `analyzer` 指标计算、`advisor` 规则、`portfolio` 成本 |
| 集成测试 | pytest + 临时 SQLite | 数据写入/读取、缓存过期逻辑 |
| UI 测试 | 手动 / 后续 pytest-qt | 第一版以手动验收为主 |
| 覆盖率目标 | ≥ 80% 针对 `services/` 和 `models/` | UI 层不要求高覆盖 |

**示例验收测试：**

```python
def test_advise_holding_triggers_reduce_on_loss():
    advice = advise_holding(return_20d_pct=-6.0, max_drawdown_pct=-8.0)
    assert advice.action == Action.REDUCE
    assert any("20 日" in r for r in advice.reasons)
```

---

## Boundaries（边界）

### Always（始终遵守）

- 任何建议界面展示免责声明
- 合并代码前运行 `pytest`
- 用户输入（基金代码、金额）必须校验格式
- 敏感配置（若有 API Key）放 `.env`，不入库

### Ask first（先问你再做）

- 新增第三方依赖
- 更换数据源或数据库 schema
- 修改默认决策规则的业务含义
- 打包发布到应用商店

### Never（绝不做）

- 声称「保证收益」或替代 licensed 投顾
- 把 `.env`、数据库文件提交到 Git
- 静默删除用户持仓/交易记录
- 在未确认的数据源上自动下单

---

## Success Criteria（验收标准）

Phase 1（MVP 完成）需满足：

- [ ] 双击/命令行可启动桌面窗口，无 Python 环境的用户可通过打包版启动
- [ ] 输入有效基金代码后，10 秒内展示净值曲线和至少 4 个关键指标
- [ ] 对任意基金能输出建议动作 + 至少 1 条中文理由
- [ ] 能添加 ≥1 条持仓并正确计算浮盈浮亏
- [ ] 用户修改规则阈值后，建议结果随之变化（有测试覆盖）
- [ ] 断网时，已缓存基金仍可查看（显示「数据可能过期」提示）
- [ ] `pytest tests/` 全部通过
- [ ] README 含安装步骤，编程小白可按文档跑起来

---

## Open Questions（待你确认）

请逐条回复或纠正，确认后进入 **Phase 2: Plan**。

1. **基金范围：** 只做中国公募基金？是否需要 QDII / 场内 ETF？
2. **数据来源：** 能否接受第一版用免费接口（可能有频率限制）？还是你更愿意**手动导入 CSV**？
3. **技术栈：** 同意 Python + PySide6 吗？还是你更想用 Electron / 网页套壳？
4. **决策风格：** 默认规则偏**保守**（少建议增购）还是**积极**（趋势好就建议加）？
5. **持仓录入：** 第一版只支持手动录入可以吗？
6. **项目位置：** 在 VOC_V1.5 仓库里新建目录，还是单独建 `fund-analyzer` 仓库？
7. **是否需要：** 基金筛选器（按类型、近一年收益排序）放进 MVP？

---

## 下一步（Spec 确认后）

```
你确认 Spec ──→ Phase 2: 技术实施计划
              ──→ Phase 3: 任务拆分（每项 ≤ 单次会议可完成）
              ──→ Phase 4: 逐任务实现
```

**请你先回复 Open Questions，或直接说「假设都对，继续 Plan」。**
