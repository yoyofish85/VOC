# -*- coding: utf-8 -*-
"""summarize_opinions 汇报文案生成单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def mock_rows():
    """生成 15 条模拟已复核数据用于测试。"""
    rows = []
    for i in range(5):
        rows.append({
            "opinion_id": f"T{i:03d}",
            "original_text": f"车机黑屏了，重启也不行，去售后说要换屏，等了一个月没消息，太差劲了 #{i}",
            "create_time": "2026-06-10",
            "reviewed_at": "2026-06-11",
            "country": "CN",
            "review_l1": "产品质量类",
            "review_l2": "车机问题",
            "model_class": "产品质量类",
            "model_keyword": "车机",
            "v3_label_meta": '{"l1":"产品质量类","l2":"车机问题","confidence":0.85}',
            "source": "APP",
        })
    for i in range(5):
        rows.append({
            "opinion_id": f"S{i:03d}",
            "original_text": f"销售顾问态度很好，但提车时发现车门有划痕，交付说正常现象，真是无语 #{i}",
            "create_time": "2026-06-10",
            "reviewed_at": "2026-06-11",
            "country": "CN",
            "review_l1": "服务类",
            "review_l2": "交付问题",
            "model_class": "服务类",
            "model_keyword": "交付",
            "v3_label_meta": '{"l1":"服务类","l2":"交付问题","confidence":0.78}',
            "source": "APP",
        })
    for i in range(5):
        rows.append({
            "opinion_id": f"C{i:03d}",
            "original_text": f"充电桩地锁一直识别不到车牌，打了三次客服都不了了之，体验极差 #{i}",
            "create_time": "2026-06-10",
            "reviewed_at": "2026-06-11",
            "country": "CN",
            "review_l1": "产品质量类",
            "review_l2": "车端充电问题",
            "model_class": "产品质量类",
            "model_keyword": "充电",
            "v3_label_meta": '{"l1":"产品质量类","l2":"车端充电问题","confidence":0.9}',
            "source": "小程序",
        })
    return rows


def test_pick_representative_quotes_returns_expected(mock_rows):
    """summarize_opinions 输出包含 volume 环比统计。"""
    from qwen_ollama import summarize_opinions

    with patch("qwen_ollama.ollama_generate") as mock_gen:
        mock_gen.return_value = (
            '{"summary":"测试","top_issues":[],"risks":[],"actions":[],"ppt_text":"测试",'
            '"trends":[],"representative_quotes":[]}'
        )
        result = summarize_opinions(
            mock_rows,
            period="week:2026-06-10~2026-06-16",
            region="all",
            prev_period_stats={"total": 10, "by_l1": {"产品质量类": 6}},
        )
    assert "volume" in result
    assert result["volume"]["total"] == 15
    assert result["volume"]["prev_total"] == 10
    assert result["volume"]["change_pct"] == 50.0
    assert len(result["volume"]["by_l1"]) >= 2


def test_summarize_opinions_no_prev_data(mock_rows):
    """无上期数据时 change_pct 为 0。"""
    from qwen_ollama import summarize_opinions

    with patch("qwen_ollama.ollama_generate") as mock_gen:
        mock_gen.return_value = '{"summary":"测试","top_issues":[],"risks":[],"actions":[],"ppt_text":"测试"}'
        result = summarize_opinions(
            mock_rows,
            period="week:2026-06-10~2026-06-16",
            region="all",
        )
    assert result["volume"]["prev_total"] == 0
    assert result["volume"]["change_pct"] == 0


def test_summarize_opinions_representative_quotes(mock_rows):
    """输出中包含 representative_quotes。"""
    from qwen_ollama import summarize_opinions

    with patch("qwen_ollama.ollama_generate") as mock_gen:
        mock_gen.return_value = (
            '{"summary":"测试","top_issues":[],"risks":[],"actions":[],"ppt_text":"测试",'
            '"representative_quotes":[]}'
        )
        result = summarize_opinions(
            mock_rows,
            period="week:2026-06-10~2026-06-16",
            region="all",
        )
    assert "representative_quotes" in result


def test_volume_by_l1_distribution(mock_rows):
    """L1 分布统计正确。"""
    from qwen_ollama import summarize_opinions

    with patch("qwen_ollama.ollama_generate") as mock_gen:
        mock_gen.return_value = '{"summary":"测试汇总","top_issues":[],"risks":[],"actions":[],"ppt_text":"测试"}'
        result = summarize_opinions(
            mock_rows,
            period="week:2026-06-10~2026-06-16",
            region="all",
            prev_period_stats={"total": 10, "by_l1": {"产品质量类": 6, "服务类": 3, "体验需求类": 1}},
        )
    vol = result.get("volume", {})
    assert "产品质量类" in vol.get("by_l1", {})
    assert "服务类" in vol.get("by_l1", {})
    assert vol["by_l1"]["产品质量类"] == 10
    assert vol["by_l1"]["服务类"] == 5


def test_ppt_text_fallback(mock_rows):
    """当 LLM 返回的 ppt_text 过短时，用程序组装降级版本。"""
    from qwen_ollama import summarize_opinions

    with patch("qwen_ollama.ollama_generate") as mock_gen:
        mock_gen.return_value = '{"summary":"简短","top_issues":[],"risks":[],"actions":[],"ppt_text":"短"}'
        result = summarize_opinions(
            mock_rows,
            period="week:2026-06-10~2026-06-16",
            region="all",
            prev_period_stats={"total": 10, "by_l1": {"产品质量类": 6}},
        )
    ppt = result.get("ppt_text", "")
    assert len(ppt) > 5
    assert "VOC" in ppt
    assert "15" in ppt
