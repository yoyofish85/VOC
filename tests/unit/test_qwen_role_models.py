# -*- coding: utf-8 -*-
"""S10：模型按角色拆分（分类/汇报/仲裁）单测。"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import qwen_ollama  # noqa: E402


def test_report_and_arbiter_default_to_classify_model():
    assert qwen_ollama.QWEN_MODEL_REPORT == qwen_ollama.QWEN_MODEL
    assert qwen_ollama.QWEN_MODEL_ARBITER == qwen_ollama.QWEN_MODEL


def test_cache_key_is_model_aware():
    a = qwen_ollama._cache_key("summary", "text", model="model-a")
    b = qwen_ollama._cache_key("summary", "text", model="model-b")
    assert a != b
    c = qwen_ollama._cache_key("summary", "text")
    d = qwen_ollama._cache_key("summary", "text", model=qwen_ollama.QWEN_MODEL)
    assert c == d


def test_summarize_defaults_to_report_model():
    sig = inspect.signature(qwen_ollama.summarize_opinions)
    assert sig.parameters["model"].default == qwen_ollama.QWEN_MODEL_REPORT
