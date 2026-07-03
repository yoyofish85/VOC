# -*- coding: utf-8 -*-
"""balance_finetune_data.py 单元测试。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "balance_finetune_data.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("balance_finetune_data", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(ROOT / "label_project"))
    spec.loader.exec_module(mod)
    return mod


def _rec(l1: str, l2: str = "") -> dict:
    return {
        "prompt": "待分类原文",
        "completion": json.dumps({"l1": l1, "l2": l2}, ensure_ascii=False),
    }


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def test_balance_caps_non_issue_and_upsamples_experience(mod, tmp_path):
    records = []
    for _ in range(100):
        records.append(_rec("非问题"))
    for _ in range(20):
        records.append(_rec("产品质量类"))
    for _ in range(15):
        records.append(_rec("服务类"))
    for _ in range(5):
        records.append(_rec("体验需求类"))

    balanced, counts = mod.balance_records(records, non_issue_max_ratio=0.30, seed=42)
    assert counts["非问题"] <= 42  # 140 * 0.30
    assert counts["体验需求类"] == counts["产品质量类"] == 20
    assert len(balanced) == sum(counts.values())


def test_write_splits_produces_three_files(mod, tmp_path):
    records = [_rec("产品质量类"), _rec("服务类"), _rec("非问题"), _rec("体验需求类")] * 25
    out = tmp_path / "balanced"
    written = mod.write_splits(records, out, seed=42)
    assert written["train"] + written["valid"] + written["test"] == len(records)
    for name in ("train", "valid", "test"):
        path = out / f"{name}.jsonl"
        assert path.is_file()
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == written[name]


def test_main_rejects_empty_input(mod, tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert mod.main(["--input", str(empty), "--output-dir", str(tmp_path / "out")]) == 1


def test_main_dry_run(mod, tmp_path):
    src = tmp_path / "finetune.jsonl"
    rows = [_rec("非问题")] * 50 + [_rec("产品质量类")] * 10
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    rc = mod.main(["--input", str(src), "--output-dir", str(tmp_path / "out"), "--dry-run"])
    assert rc == 0
    assert not (tmp_path / "out").exists()


def test_mlx_match_type_with_and_without_adapter(monkeypatch):
    sys.path.insert(0, str(ROOT / "src" / "backend"))
    import qwen_ollama as qo

    monkeypatch.delenv("VOC_MLX_ADAPTER", raising=False)
    assert qo._mlx_match_type() == "mlx_14b_base"
    monkeypatch.setenv("VOC_MLX_ADAPTER", "/tmp/lora")
    assert qo._mlx_match_type() == "mlx_14b_lora"
