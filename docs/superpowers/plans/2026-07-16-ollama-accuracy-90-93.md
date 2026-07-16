# Ollama L1 90–93% Accuracy Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留 Ollama、人工复核回流和现有规则链的前提下，将新批次 L1 从 82.31% 提升到可初步上线的 90–93%，同时将 L2 保持在 82–88% 以上并修复充电域错分。

**Architecture:** 把 O1–O4 实现为可离线重放的确定性上下文规则，并通过 `source`、`vin` 两个现有数据库字段补充分类上下文。所有规则先在近 4–7 天已复核数据上 dry-run，只有净提升和 broken 门禁同时通过才写库或进入新批次验证；人工复核回流继续提供金标、相似样例和下一轮错误池。

**Tech Stack:** Python 3.12、FastAPI、SQLite、Ollama Qwen2.5-14B、pytest、现有 `qwen_ollama.py` 规则链与 `performance_evaluation/` 评估工具。

---

## 1. 当前基线与预计目标

### 1.1 已确认基线（2026-07-16）

| 指标 | 当前值 |
|------|--------|
| 近 4 天 Ollama 批次 | 831 条，L1 **82.31%**（684/831），L2 **85.32%**（186/218） |
| 全库 | 5045 条，L1 **85.43%**，L2 **79.32%** |
| 距近批次 L1 90% | 还需净修正 **64 条** |
| 近 4 天主要 L1 错误 | 服务→非问题 57；质量→非问题 33；服务→质量 24；体验→非问题 10 |
| 全库主要 L2 错误 | 车端充电→LFC **41 条** |
| 旧规则 replay | 仅 7 条 fixable；旧规则 ROI 已见底 |

### 1.2 预计目标（不是保证值）

| 阶段 | 数据门禁 | 预计结果 |
|------|----------|----------|
| Release A：O1 + O3 | 近 7 天 dry-run broken ≤8 | 近批次 L1 **85–88%** |
| Release B：O2 + O4 | L2 dry-run broken ≤3 | 近批次 L2 **87–90%**；全库 L2 **80–82%** |
| Cycle 2：按新错误池再迭代 | 再积累 1000–2000 条复核 | 近批次 L1 **88–90%** |
| MVP 上线门禁 | 连续 2 个独立批次（各 ≥200） | L1 **90–93%**，L2 **≥82%** |

**概率判断：**

- 仅实现 O1–O4，一轮就达到 L1 90%：约 **35–50%**。
- O1–O4 + 每日约 200 条复核 + 2–3 次周迭代，在 **4–8 周 / 3000–6000 条新增复核**内达到 90%：约 **60–75%**。
- 仅持续投喂和回流、不增加规则或模型能力：预计平台仍为 **85–89%**，不应作为 90% 路径。

这些是基于当前错误池覆盖率的工程估计，不是统计置信概率。每一阶段必须以实际 dry-run 和独立新批次结果替代估计。

---

## 2. 设计边界与执行顺序

### 文件职责

| 文件 | 职责 |
|------|------|
| `src/backend/classification_context_rules.py` | 新增：纯函数实现 O1/O2/O3/O4，不读数据库、不调用模型 |
| `src/backend/qwen_ollama.py` | 将上下文规则接入现有 post-rule 链；保留旧规则顺序 |
| `src/backend/voc_classifier_service.py` | 将数据库已有 `source`、`vin` 传入 `classify_text` |
| `performance_evaluation/eval_p1_subset.py` | 离线 replay 同步传递 `source`、`vin` |
| `performance_evaluation/eval_recent_rule_candidate.py` | 新增：近 N 天/指定批次 before-vs-after 的 L1/L2、fixed/broken 门禁 |
| `tests/unit/test_classification_context_rules.py` | 新增：O1–O4 纯函数边界测试 |
| `tests/unit/test_v12_m4b.py` | 增加完整 post-rule 链回归 |
| `tests/unit/test_eval_recent_rule_candidate.py` | 新增：评估口径和门禁测试 |
| `docs/VOC_V1.5_开发记录.md` | 每个门禁通过后记录指标与决策 |

### 依赖顺序

```text
上下文纯函数
  → source/vin 贯通
  → O1/O3 L1 规则接入
  → O4 L2 规则
  → O2 VIN 车型规则
  → 近批次离线评估
  → dry-run 门禁
  → 新批次验证
  → MVP 上线门禁
```

O1/O3（L1）与 O2/O4（主要是 L2）属于两个可独立交付的子项目；按上面顺序分两个 release，避免一次改动后无法归因。

---

### Task 1: 建立 O1–O4 纯规则模块

**Files:**
- Create: `src/backend/classification_context_rules.py`
- Create: `tests/unit/test_classification_context_rules.py`

- [ ] **Step 1: 写 O1/O3 失败测试**

```python
from classification_context_rules import (
    assistance_should_be_non_issue,
    app_narrative_should_be_non_issue,
)


def test_assistance_request_is_non_issue():
    assert assistance_should_be_non_issue("车辆扎钉爆胎，需要安排道路救援拖车")
    assert assistance_should_be_non_issue("咨询保养预约，需要协助安排时间")


def test_assistance_with_service_complaint_is_not_captured():
    assert not assistance_should_be_non_issue("爆胎后联系售后两小时无人响应，投诉服务差")
    assert not assistance_should_be_non_issue("保养后车辆故障无法启动，要求维修")


def test_app_narrative_is_non_issue_only_for_app_source():
    text = "周末自驾去了莫干山，一路风景很好，分享一下日常用车体验"
    assert app_narrative_should_be_non_issue(text, source="APP")
    assert not app_narrative_should_be_non_issue(text, source="工单")


def test_app_narrative_with_explicit_fault_is_not_captured():
    assert not app_narrative_should_be_non_issue(
        "自驾途中车机黑屏无法导航，要求尽快解决", source="APP"
    )
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
pytest -q tests/unit/test_classification_context_rules.py
```

Expected: FAIL，提示 `classification_context_rules` 不存在。

- [ ] **Step 3: 实现最小 O1/O3 纯函数**

```python
# src/backend/classification_context_rules.py
from __future__ import annotations

import re

_ASSIST_DOMAIN = re.compile(r"爆胎|扎钉|轮胎没气|交通事故|发生事故|道路救援|拖车|保养")
_ASSIST_INTENT = re.compile(r"咨询|询问|需要|协助|安排|预约|上门|如何处理|怎么办|求助")
_EXPLICIT_ISSUE = re.compile(
    r"投诉|不满|态度差|无人响应|无法联系|未处理|乱收费|收费不合理|"
    r"维修失败|保养后|故障|异常|无法启动|黑屏|失灵|质量问题|受伤"
)
_APP_SOURCE = re.compile(r"app|应用", re.I)
_NARRATIVE = re.compile(r"分享|自驾|游记|旅途|旅程|一路|风景|日常用车|提车日记|打卡")
_DEMAND_OR_FAULT = re.compile(
    r"投诉|建议|希望|要求|故障|异常|无法|不能|黑屏|死机|异响|报错|失灵|维修"
)


def assistance_should_be_non_issue(text: str) -> bool:
    t = (text or "").strip()
    return bool(
        _ASSIST_DOMAIN.search(t)
        and _ASSIST_INTENT.search(t)
        and not _EXPLICIT_ISSUE.search(t)
    )


def app_narrative_should_be_non_issue(text: str, *, source: str = "") -> bool:
    t = (text or "").strip()
    return bool(
        _APP_SOURCE.search(source or "")
        and _NARRATIVE.search(t)
        and not _DEMAND_OR_FAULT.search(t)
    )
```

- [ ] **Step 4: 运行 O1/O3 测试**

Run:

```bash
pytest -q tests/unit/test_classification_context_rules.py
```

Expected: 4 passed。

- [ ] **Step 5: 写 O2/O4 失败测试**

```python
from classification_context_rules import charging_l2_target, vehicle_family


def test_vehicle_family_from_vin():
    assert vehicle_family("LJUBMSA14RK008070") == "electric"
    assert vehicle_family("SCCLEKAX1RHA12345") == "emira"
    assert vehicle_family("") == ""


def test_charging_station_and_supplier_are_lfc():
    assert charging_l2_target("蔚来充电桩已下线，站点无法使用") == "LFC问题"
    assert charging_l2_target("浩瀚供应商的闪充站一直不可用") == "LFC问题"


def test_vehicle_side_charging_is_car_charging():
    assert charging_l2_target("车辆充电功率低，只有20kW") == "车端充电问题"
    assert charging_l2_target("预约充电后车辆没有开始充电") == "车端充电问题"
```

- [ ] **Step 6: 实现 O2/O4 纯函数**

```python
_LFC_STATION = re.compile(
    r"充电桩|充电站|闪充站|超充站|极充站|站点|地锁|占位费|桩下线|"
    r"蔚来|浩瀚|第三方桩|供应商"
)
_CAR_CHARGING = re.compile(
    r"充电功率低|功率只有|功率过低|预约充电.{0,10}(不充电|未充电|没有开始)|"
    r"车辆.{0,8}(充不进|无法充电)|车端充电"
)


def vehicle_family(vin: str) -> str:
    value = (vin or "").strip().upper()
    if value.startswith("LJU"):
        return "electric"
    if value.startswith("SCC"):
        return "emira"
    return ""


def charging_l2_target(text: str) -> str:
    t = (text or "").strip()
    if _LFC_STATION.search(t):
        return "LFC问题"
    if _CAR_CHARGING.search(t):
        return "车端充电问题"
    return ""
```

- [ ] **Step 7: 运行全部纯函数测试**

Run:

```bash
pytest -q tests/unit/test_classification_context_rules.py
```

Expected: 7 passed。

- [ ] **Step 8: 提交 Task 1**

```bash
git add src/backend/classification_context_rules.py tests/unit/test_classification_context_rules.py
git commit -m "feat: add contextual classification rules"
```

---

### Task 2: 贯通 source 与 VIN 上下文

**Files:**
- Modify: `src/backend/voc_classifier_service.py:227-231,315-320`
- Modify: `src/backend/qwen_ollama.py:2018-2020,2073-2080,2245`
- Modify: `performance_evaluation/eval_p1_subset.py:62-92,111-124`
- Test: `tests/unit/test_classification_context_rules.py`
- Test: `tests/test_classify_stress.py`

- [ ] **Step 1: 写签名兼容测试**

```python
def test_post_rules_accept_context_without_changing_default_behavior():
    from qwen_ollama import apply_classification_post_rules, load_l2_whitelist

    l2_map = load_l2_whitelist()
    legacy = apply_classification_post_rules("纯表扬", "非问题", "", l2_map)
    contextual = apply_classification_post_rules(
        "纯表扬", "非问题", "", l2_map, source="", vin=""
    )
    assert legacy == contextual
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest -q tests/unit/test_classification_context_rules.py::test_post_rules_accept_context_without_changing_default_behavior
```

Expected: FAIL，`unexpected keyword argument 'source'`。

- [ ] **Step 3: 扩展规则链与 classify_text 签名**

保持向后兼容，新增参数必须是关键字参数且有空字符串默认值：

```python
def apply_classification_post_rules(
    text: str,
    l1: str,
    l2: str,
    l2_map: Dict[str, List[str]],
    *,
    source: str = "",
    vin: str = "",
) -> Tuple[str, str, Dict[str, bool]]:
    ...


def classify_text(
    text: str,
    *,
    db_path: str,
    country: str = "",
    source: str = "",
    vin: str = "",
    model: str = QWEN_MODEL,
    host: str = QWEN_HOST,
) -> Dict[str, Any]:
    ...
```

同时让 `_post_process_classify_result()` 接收并传递 `source`、`vin`。不要把 VIN 写进日志或返回给前端。

- [ ] **Step 4: 修改批量分类查询和调用**

将两处 SELECT 改为：

```python
SELECT id, opinion_id, original_text, country, source, vin, review_status
```

调用改为：

```python
q = classify_text(
    text,
    db_path=db_path,
    country=row["country"] or "",
    source=row["source"] or "",
    vin=row["vin"] or "",
    host=ollama_host,
)
```

- [ ] **Step 5: 让离线 replay 使用同样上下文**

`eval_p1_subset.py` 的 SELECT 增加 `source, vin`，`_row_dict()` 增加：

```python
"source": str(row["source"] or "").strip(),
"vin": str(row["vin"] or "").strip(),
```

规则重放改为：

```python
nl1, nl2, flags = apply_classification_post_rules(
    d["text"],
    d["model_l1"],
    d["model_l2"],
    l2_map,
    source=d["source"],
    vin=d["vin"],
)
```

- [ ] **Step 6: 运行回归测试**

Run:

```bash
pytest -q tests/unit/test_classification_context_rules.py tests/unit/test_v12_m4b.py
pytest -q tests/test_classify_stress.py
```

Expected: 全部 PASS。

- [ ] **Step 7: 提交 Task 2**

```bash
git add src/backend/qwen_ollama.py src/backend/voc_classifier_service.py \
  performance_evaluation/eval_p1_subset.py tests/unit/test_classification_context_rules.py
git commit -m "feat: pass source and VIN into classification rules"
```

---

### Task 3: 接入 O1 道路协助与 O3 APP 叙事规则

**Files:**
- Modify: `src/backend/qwen_ollama.py:2018-2070`
- Modify: `tests/unit/test_v12_m4b.py`

- [ ] **Step 1: 写完整规则链失败测试**

```python
def test_o1_assistance_overrides_service_to_non_issue():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "车辆扎钉爆胎，需要协助安排道路救援拖车",
        "服务类",
        "售后服务问题",
        l2_map,
    )
    assert (l1, l2) == ("非问题", "")
    assert flags.get("context_non_issue")


def test_o1_does_not_hide_service_complaint():
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(
        "爆胎后联系售后两小时无人响应，投诉服务差",
        "服务类",
        "售后服务问题",
        l2_map,
    )
    assert l1 == "服务类"
    assert not flags.get("context_non_issue")


def test_o3_app_narrative_overrides_experience_to_non_issue():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "周末自驾去了莫干山，一路风景很好，分享日常用车体验",
        "体验需求类",
        "车机智能化",
        l2_map,
        source="APP",
    )
    assert (l1, l2) == ("非问题", "")
    assert flags.get("context_non_issue")
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest -q tests/unit/test_v12_m4b.py -k "o1 or o3"
```

Expected: 3 failed。

- [ ] **Step 3: 在规则链末端接入安全覆盖**

在 `apply_positive_consult_capture()` 之后、`strip_non_issue_l2()` 之前执行：

```python
from classification_context_rules import (
    app_narrative_should_be_non_issue,
    assistance_should_be_non_issue,
)

context_capture = assistance_should_be_non_issue(text) or app_narrative_should_be_non_issue(
    text, source=source
)
if context_capture:
    l1, l2 = "非问题", ""
    flags["context_non_issue"] = True
    flags.pop("charging_guard", None)
    flags.pop("srv_quality_guard", None)
    flags.pop("l1_category_rebalance", None)
```

放在末端是为了覆盖模型输出和旧的服务/充电 guard；安全性由纯函数中的明确负面阻断保证。

- [ ] **Step 4: 运行规则回归**

Run:

```bash
pytest -q tests/unit/test_v12_m4b.py
pytest -q tests/unit/test_v9_*.py
```

Expected: 全部 PASS；现有事故 relay、事故表扬、纯咨询测试不得回退。

- [ ] **Step 5: 提交 Task 3**

```bash
git add src/backend/qwen_ollama.py tests/unit/test_v12_m4b.py
git commit -m "feat: classify assistance and APP narratives as non-issues"
```

---

### Task 4: 收紧 O4 车端充电与 LFC 边界

**Files:**
- Modify: `src/backend/qwen_ollama.py:1512-1540,1679-1700`
- Modify: `tests/unit/test_v9_2_lfc_soft_capture.py`
- Modify: `tests/unit/test_v12_m4b.py`

- [ ] **Step 1: 写 O4 失败测试**

```python
def test_supplier_station_issue_maps_to_lfc():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "国内蔚来充电桩下线，站点无法使用",
        "产品质量类",
        "车端充电问题",
        l2_map,
    )
    assert l1 == "产品质量类"
    assert l2 == "LFC问题"
    assert flags.get("charging_l2_boundary")


def test_vehicle_power_and_scheduled_charge_map_to_car_charging():
    l2_map = load_l2_whitelist()
    for text in ("车辆充电功率低，只有20kW", "预约充电后车辆没有开始充电"):
        l1, l2, flags = apply_classification_post_rules(
            text, "产品质量类", "LFC问题", l2_map
        )
        assert l1 == "产品质量类"
        assert l2 == "车端充电问题"
        assert flags.get("charging_l2_boundary")
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest -q tests/unit/test_v12_m4b.py -k "supplier_station or vehicle_power"
```

Expected: FAIL，当前 `_pick_lfc_l2()` 将“功率”优先映射到 LFC。

- [ ] **Step 3: 实现唯一 O4 决策入口**

在 `_pick_lfc_l2()` 和 `_pick_l2_after_remap()` 中都优先调用：

```python
target = charging_l2_target(text)
if target in opts:
    return target
```

并在 `apply_classification_post_rules()` 中，在 L1 已确定后执行：

```python
if canonicalize_l1_label(l1) == "产品质量类":
    target_l2 = charging_l2_target(text)
    if target_l2 and target_l2 in (l2_map.get("产品质量类") or []) and target_l2 != l2:
        l2 = target_l2
        flags["charging_l2_boundary"] = True
```

- [ ] **Step 4: 运行充电域全量测试**

Run:

```bash
pytest -q tests/unit/test_v9_1_praise_capture.py \
  tests/unit/test_v9_2_lfc_soft_capture.py \
  tests/unit/test_v9_3_neutral_capture.py \
  tests/unit/test_v9_4_capture_expand.py \
  tests/unit/test_v9_5_capture_refine.py \
  tests/unit/test_v9_6_phase2_guard.py \
  tests/unit/test_v12_m4b.py
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交 Task 4**

```bash
git add src/backend/qwen_ollama.py tests/unit/test_v9_2_lfc_soft_capture.py \
  tests/unit/test_v12_m4b.py
git commit -m "fix: distinguish vehicle charging from LFC station issues"
```

---

### Task 5: 接入 O2 VIN 车型先验

**Files:**
- Modify: `src/backend/qwen_ollama.py:1679-1700,2018-2070`
- Modify: `tests/unit/test_v12_m4b.py`

- [ ] **Step 1: 写 VIN L2 失败测试**

```python
def test_scc_vin_maps_generic_product_issue_to_emira():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "用户反馈车辆故障灯亮",
        "产品质量类",
        "故障-通用",
        l2_map,
        vin="SCCLEKAX1RHA12345",
    )
    assert l2 == "Emira问题"
    assert flags.get("vin_vehicle_hint")


def test_lju_vin_prevents_emira_label():
    l2_map = load_l2_whitelist()
    _, l2, flags = apply_classification_post_rules(
        "用户反馈车辆故障灯亮",
        "产品质量类",
        "Emira问题",
        l2_map,
        vin="LJUBMSA14RK008070",
    )
    assert l2 != "Emira问题"
    assert flags.get("vin_vehicle_hint")
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest -q tests/unit/test_v12_m4b.py -k "scc_vin or lju_vin"
```

Expected: 2 failed。

- [ ] **Step 3: 实现保守 VIN 覆盖**

规则仅在 L1 已是“产品质量类”时生效：

```python
family = vehicle_family(vin)
product_opts = l2_map.get("产品质量类") or []
if family == "emira" and "Emira问题" in product_opts and l2 in ("", "故障-通用"):
    l2 = "Emira问题"
    flags["vin_vehicle_hint"] = True
elif family == "electric" and l2 == "Emira问题":
    candidates = [x for x in product_opts if x != "Emira问题"]
    l2 = _nearest("故障-通用", candidates) if candidates else ""
    flags["vin_vehicle_hint"] = True
```

不要仅凭 VIN 改 L1；VIN 只降低车型相关 L2 互串风险。

- [ ] **Step 4: 运行测试**

Run:

```bash
pytest -q tests/unit/test_v12_m4b.py tests/unit/test_classification_context_rules.py
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交 Task 5**

```bash
git add src/backend/qwen_ollama.py tests/unit/test_v12_m4b.py
git commit -m "feat: use VIN prefix as conservative vehicle hint"
```

---

### Task 6: 新增近批次规则候选评估器

**Files:**
- Create: `performance_evaluation/eval_recent_rule_candidate.py`
- Create: `tests/unit/test_eval_recent_rule_candidate.py`

- [ ] **Step 1: 写指标失败测试**

```python
from eval_recent_rule_candidate import compare_rows


def test_compare_rows_reports_fixed_broken_and_net():
    rows = [
        {"human_l1": "非问题", "model_l1": "服务类", "replay_l1": "非问题",
         "human_l2": "", "model_l2": "售后服务问题", "replay_l2": ""},
        {"human_l1": "产品质量类", "model_l1": "产品质量类", "replay_l1": "非问题",
         "human_l2": "LFC问题", "model_l2": "LFC问题", "replay_l2": ""},
    ]
    result = compare_rows(rows)
    assert result["l1_fixed"] == 1
    assert result["l1_broken"] == 1
    assert result["l1_net"] == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest -q tests/unit/test_eval_recent_rule_candidate.py
```

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现评估器核心**

`compare_rows()` 必须输出：

```python
{
    "l1_total": int,
    "l1_before_correct": int,
    "l1_after_correct": int,
    "l1_before_pct": float,
    "l1_after_pct": float,
    "l1_fixed": int,
    "l1_broken": int,
    "l1_net": int,
    "l2_total": int,
    "l2_before_correct": int,
    "l2_after_correct": int,
    "l2_fixed": int,
    "l2_broken": int,
}
```

CLI 参数：

```python
parser.add_argument("--db", type=Path, default=DEFAULT_DB)
parser.add_argument("--since-days", type=int, default=7)
parser.add_argument("--upload-batch", action="append", default=[])
parser.add_argument("--export-csv", type=Path)
```

数据库 SELECT 必须包含：

```sql
opinion_id, original_text, source, vin, review_l1, review_l2,
v3_label_meta, model_class, model_keyword, reviewed_at, create_time, upload_batch
```

对每行调用更新后的 `apply_classification_post_rules(..., source=..., vin=...)`，不写数据库。

- [ ] **Step 4: 增加门禁返回码**

CLI 增加：

```python
parser.add_argument("--max-l1-broken", type=int, default=8)
parser.add_argument("--max-l2-broken", type=int, default=3)
```

当 broken 超门禁时返回 2，并打印：

```text
FAIL: broken gate exceeded
```

否则返回 0，并打印 before/after、fixed/broken/net。

- [ ] **Step 5: 运行测试**

Run:

```bash
pytest -q tests/unit/test_eval_recent_rule_candidate.py
```

Expected: PASS。

- [ ] **Step 6: 提交 Task 6**

```bash
git add performance_evaluation/eval_recent_rule_candidate.py \
  tests/unit/test_eval_recent_rule_candidate.py
git commit -m "feat: add recent rule candidate accuracy gate"
```

---

### Task 7: Release A（O1/O3）离线门禁

**Files:**
- No code changes unless a broken sample requires narrowing `classification_context_rules.py`
- Update after pass: `docs/VOC_V1.5_开发记录.md`

- [ ] **Step 1: 跑近 7 天 dry-run**

Run:

```bash
python3 performance_evaluation/eval_recent_rule_candidate.py \
  --since-days 7 \
  --max-l1-broken 8 \
  --max-l2-broken 3 \
  --export-csv performance_evaluation/exports/o1_o3_candidate_20260716.csv
```

Expected release gate:

```text
L1 fixed >= 20
L1 broken <= 8
L1 net >= 15
```

- [ ] **Step 2: 若未达标，仅按 broken CSV 收窄规则**

规则调整只能增加明确阻断词，不能把宽泛的“反馈”“需要”“车辆”作为正向条件。每次调整后重复 Step 1，直到通过或判定 O1/O3 ROI 不足。

- [ ] **Step 3: 跑全量单元测试**

Run:

```bash
pytest -q tests/unit/test_classification_context_rules.py \
  tests/unit/test_v12_m4b.py tests/unit/test_v9_*.py
```

Expected: 全部 PASS。

- [ ] **Step 4: 记录门禁结果并提交**

```bash
git add src/backend/classification_context_rules.py src/backend/qwen_ollama.py \
  docs/VOC_V1.5_开发记录.md
git commit -m "docs: record O1 and O3 accuracy gate results"
```

---

### Task 8: Release B（O2/O4）离线门禁与历史 L2 patch

**Files:**
- No code changes unless broken 样本要求收窄
- Existing: `performance_evaluation/patch_v3_l2_replay.py`
- Update after pass: `docs/VOC_V1.5_开发记录.md`

- [ ] **Step 1: 跑 O2/O4 dry-run**

Run:

```bash
python3 performance_evaluation/eval_recent_rule_candidate.py \
  --since-days 30 \
  --max-l1-broken 8 \
  --max-l2-broken 3 \
  --export-csv performance_evaluation/exports/o2_o4_candidate_20260716.csv
```

Expected release gate:

```text
L2 fixed >= 25
L2 broken <= 3
车端充电问题→LFC问题覆盖率 >= 70%
L1 net >= 0
```

- [ ] **Step 2: 跑历史 L2 patch dry-run**

Run:

```bash
python3 performance_evaluation/patch_v3_l2_replay.py
```

Expected: 输出可写 L2 纠偏数量，不修改数据库。

- [ ] **Step 3: 人工抽查前 40 条**

必须确认：

- 桩下线、蔚来、浩瀚、站点不可用 → `LFC问题`
- 充电功率低、预约后未充 → `车端充电问题`
- 无充电含义的文本未被改动

- [ ] **Step 4: 备份数据库后写入**

```bash
cp src/backend/opinion_review.db \
  "src/backend/opinion_review_before_o4_$(date +%Y%m%d_%H%M%S).db"
python3 performance_evaluation/patch_v3_l2_replay.py --write
python3 performance_evaluation/evaluate_accuracy.py
```

Expected: 全库 L1 不下降；L2 上升；报告生成成功。

- [ ] **Step 5: 提交门禁记录**

```bash
git add docs/VOC_V1.5_开发记录.md
git commit -m "docs: record VIN and charging boundary gate results"
```

---

### Task 9: 独立新批次验证

**Files:**
- Existing: `performance_evaluation/eval_batch_accuracy.py`
- Update: `docs/VOC_V1.5_开发记录.md`
- Update: `docs/plans/mlx-accuracy-evolution-plan.md`

- [ ] **Step 1: 部署代码并重启 Ollama 路径**

```bash
./start_ollama.sh
```

确认分类日志中不出现 `mlx_14b_lora`。

- [ ] **Step 2: 完成第一个独立批次**

要求：

```text
样本数 >= 200
全部分类发生在规则发布后
人工 L1 全复核
业务三类人工 L2 尽量完整
```

- [ ] **Step 3: 评估第一个批次**

```bash
python3 performance_evaluation/eval_batch_accuracy.py \
  --upload-batch <实际批次名>
```

门禁：

```text
L1 >= 90%
L2 >= 82%
服务类→非问题 相对 4 天基线占比下降 >= 50%
车端充电→LFC 错误相对基线下降 >= 70%
```

- [ ] **Step 4: 完成并评估第二个独立批次**

使用另一天、另一个 `upload_batch`，同样 ≥200 条。不能复用第一批样本。

- [ ] **Step 5: 运行 7 天汇总**

```bash
python3 performance_evaluation/eval_batch_accuracy.py --since-days 7
python3 performance_evaluation/evaluate_accuracy.py
python3 performance_evaluation/export_l1_errors.py --since 7d
```

- [ ] **Step 6: 判定 MVP 上线**

必须同时满足：

```text
连续两个独立批次 L1 均 >= 90%
两个批次合计 L1 在 90–93% 或更高
两个批次 L2 均 >= 82%
近 7 天 L1 >= 90%
无单个主要错误模式连续两批增长
```

若只达到 88–90%，不宣称上线达标；进入 Cycle 2，按最新 Top1/Top2 错误再做一轮同样的 TDD + dry-run。

- [ ] **Step 7: 更新文档并提交**

```bash
git add docs/VOC_V1.5_开发记录.md \
  docs/plans/mlx-accuracy-evolution-plan.md
git commit -m "docs: record Ollama MVP accuracy gate"
```

---

## 3. 运营与回流要求

实现期间继续每天约 100–200 条测试投喂，但区分用途：

| 数据 | 用途 |
|------|------|
| 规则发布前数据 | 训练错误归因，不作为发布后上线证明 |
| 规则发布后第 1 批 ≥200 | 第一次独立门禁 |
| 规则发布后第 2 批 ≥200 | 重复性门禁 |
| 所有人工复核 | 回流到 gold review、historical examples、后续错误池 |

回流不会自动修改 Ollama 权重；其即时收益主要是原文金标命中和最多 4 条相似 few-shot。每周必须把回流结果转化为评估和规则决策：

```bash
python3 performance_evaluation/evaluate_accuracy.py
python3 performance_evaluation/export_l1_errors.py --since 7d
python3 performance_evaluation/eval_p1_subset.py --compare \
  --export-regression-fixable auto \
  --export-other-fixable auto \
  --export-fixable auto
python3 performance_evaluation/weekly_evolution_report.py
```

---

## 4. 风险与停止条件

| 风险 | 门禁/缓解 |
|------|-----------|
| O1 把真实售后投诉误归非问题 | 必须同时命中协助域 + 协助意图；明确投诉/故障阻断；L1 broken ≤8 |
| O3 把 APP 故障游记误归非问题 | 必须 source=APP + 叙事词；故障/诉求阻断 |
| O4 继续混淆车端与设施端 | 唯一 `charging_l2_target()` 决策入口；站点/供应商优先 LFC |
| VIN 误改 L1 | VIN 只影响产品质量类内部 L2；永不单独改 L1 |
| 全库 patch 产生不可逆回退 | 先 dry-run、数据库备份、仅用专用 patch；禁止 `apply_post_rules_to_v3.py --write` |
| 规则越加越复杂但 L1 不升 | 若两个 release 的 L1 净提升合计 <2pp，停止扩规则，重新评估 prompt/模型路线 |

---

## 5. 计划自审

- 规格覆盖：O1 道路协助、O2 VIN、O3 APP 叙事、O4 充电/LFC、回流运营和 90–93% 门禁均有对应任务。
- 无占位实现：所有新增函数签名、核心正则、调用链、测试命令和门禁均已给出。
- 类型一致：`source`、`vin` 均为关键字字符串参数；离线 replay 与线上批量分类使用同一规则接口。
- 范围控制：不重构现有大文件，不修改前端，不恢复 MLX LoRA，不改变数据库 schema。

