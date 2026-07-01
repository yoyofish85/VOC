# -*- coding: utf-8 -*-
"""MLX 后端集成测试（mock MLX 调用，不依赖真实模型）。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def reset_mlx_state(monkeypatch):
    """每个测试前重置 MLX 缓存状态和 env var。"""
    import qwen_ollama as qo

    qo._MLX_LOADED = False
    qo._MLX_MODEL = None
    qo._MLX_TOKENIZER = None
    qo._MLX_MODEL_PATH = None
    monkeypatch.delenv("VOC_USE_MLX", raising=False)
    monkeypatch.delenv("VOC_MLX_ADAPTER", raising=False)
    monkeypatch.delenv("VOC_MLX_MODEL", raising=False)
    yield
    qo._MLX_LOADED = False
    qo._MLX_MODEL = None
    qo._MLX_TOKENIZER = None
    qo._MLX_MODEL_PATH = None


def test_post_process_valid_result():
    """正常 LLM 输出通过后处理返回结构化结果。"""
    from qwen_ollama import _post_process_classify_result

    obj = {"l1": "产品质量类", "l2": "车机问题", "confidence": 0.85, "keywords": ["黑屏"]}
    text = "车机黑屏了"
    l2_map = {"产品质量类": ["车机问题", "LFC问题"]}
    result = _post_process_classify_result(obj, text, l2_map)
    assert result is not None
    assert result["l1"] == "产品质量类"
    assert result["l2"] == "车机问题"
    assert result["confidence"] >= 0.75
    assert result["match_type"] == "qwen14b_structured"


def test_post_process_whitelist_reject():
    """白名单外的 L2 应被拒绝。"""
    from qwen_ollama import _post_process_classify_result

    obj = {"l1": "产品质量类", "l2": "不存在的二级"}
    l2_map = {"产品质量类": ["车机问题", "LFC问题"]}
    result = _post_process_classify_result(obj, "测试", l2_map)
    assert result is None


@patch("qwen_ollama._extract_l3_phrases")
def test_post_process_mlx_adds_l3_phrases(mock_extract, monkeypatch):
    """MLX 模式下后处理结果应包含 L3 具体问题短语。"""
    from qwen_ollama import _post_process_classify_result

    monkeypatch.setenv("VOC_USE_MLX", "1")
    mock_extract.return_value = "车机黑屏,重启无效"
    obj = {"l1": "产品质量类", "l2": "车机问题"}
    l2_map = {"产品质量类": ["车机问题"]}
    result = _post_process_classify_result(obj, "车机黑屏了，重启也不行", l2_map, match_type="mlx_14b_lora")
    assert result is not None
    assert result["l3_phrases"] == "车机黑屏,重启无效"
    mock_extract.assert_called_once()


@patch("qwen_ollama._extract_l3_phrases")
@patch("qwen_ollama._mlx_model_load")
@patch("qwen_ollama._mlx_generate")
def test_classify_text_mlx_success(mock_gen, mock_load, mock_extract, monkeypatch):
    """classify_text 在 VOC_USE_MLX=1 时走 MLX 路径并返回正确结果。"""
    import qwen_ollama as qo

    monkeypatch.setenv("VOC_USE_MLX", "1")
    monkeypatch.setenv("VOC_MLX_MODEL", "mlx-test-model")
    mock_load.return_value = True
    mock_gen.return_value = '{"l1": "产品质量类", "l2": "车机问题"}'
    mock_extract.return_value = "车机黑屏"
    with (
        patch("qwen_ollama.load_l2_whitelist", return_value={"产品质量类": ["车机问题"]}),
        patch("qwen_ollama.historical_examples", return_value=[]),
    ):
        result = qo.classify_text("车机黑屏了，重启也不行", db_path=":memory:", country="CN")

    assert result.get("l1") == "产品质量类"
    assert result.get("l2") == "车机问题"
    assert result.get("match_type") == "mlx_14b_lora"
    assert result.get("model") == "mlx-test-model"
    assert result.get("l3_phrases") == "车机黑屏"
    assert result.get("cache_hit") is False
    mock_load.assert_called_once()
    mock_gen.assert_called_once()


@patch("qwen_ollama._mlx_model_load")
def test_classify_text_mlx_load_failure(mock_load, monkeypatch):
    """MLX 模型加载失败时返回错误标记。"""
    import qwen_ollama as qo

    monkeypatch.setenv("VOC_USE_MLX", "1")
    mock_load.return_value = False
    with (
        patch("qwen_ollama.load_l2_whitelist", return_value={"产品质量类": ["车机问题"]}),
        patch("qwen_ollama.historical_examples", return_value=[]),
    ):
        result = qo.classify_text("测试", db_path=":memory:")
    assert result.get("match_type") == "mlx_load_failed"
    assert result.get("needs_review") is True


@patch("qwen_ollama._mlx_model_load")
@patch("qwen_ollama._mlx_generate")
def test_classify_text_mlx_whitelist_reject(mock_gen, mock_load, monkeypatch):
    """MLX 路径的白名单拒绝行为。"""
    import qwen_ollama as qo

    monkeypatch.setenv("VOC_USE_MLX", "1")
    mock_load.return_value = True
    mock_gen.return_value = '{"l1": "产品质量类", "l2": "不存在的二级"}'
    with (
        patch("qwen_ollama.load_l2_whitelist", return_value={"产品质量类": ["车机问题"]}),
        patch("qwen_ollama.historical_examples", return_value=[]),
    ):
        result = qo.classify_text("测试", db_path=":memory:")
    assert result.get("match_type") == "mlx_rejected"
    assert result.get("needs_review") is True


@patch("qwen_ollama.ollama_generate")
def test_classify_text_ollama_path_still_uses_shared_post_process(mock_gen):
    """默认不启用 MLX 时仍走 Ollama，并保留原 match_type。"""
    import qwen_ollama as qo

    mock_gen.return_value = '{"l1": "产品质量类", "l2": "车机问题"}'
    with (
        patch("qwen_ollama.load_l2_whitelist", return_value={"产品质量类": ["车机问题"]}),
        patch("qwen_ollama.historical_examples", return_value=[]),
        patch("qwen_ollama._cache_get", return_value=None),
        patch("qwen_ollama._cache_set", return_value=None),
    ):
        result = qo.classify_text("车机黑屏了", db_path=":memory:")

    assert result["match_type"] == "qwen14b_structured"
    assert result["l1"] == "产品质量类"
    assert result["l2"] == "车机问题"
    mock_gen.assert_called_once()
