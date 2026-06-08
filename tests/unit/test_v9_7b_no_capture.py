# -*- coding: utf-8 -*-
"""v9.7b Phase A：no_capture 扩展捕获。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend"
LABEL = Path(__file__).resolve().parents[2] / "label_project"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(LABEL))

from qwen_ollama import (  # noqa: E402
    apply_classification_post_rules,
    explain_positive_capture_block,
    load_l2_whitelist,
)


def _assert_captures(text: str, model_l1: str, eligible_prefix: str) -> None:
    diag = explain_positive_capture_block(text)
    assert diag == eligible_prefix, f"diag={diag} text={text[:60]}"
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(text, model_l1, "", l2_map)
    assert l1 == "非问题" and l2 == "" and flags.get("pos_captured"), (
        f"l1={l1} flags={flags} text={text[:60]}"
    )


def test_ota_hype_capture():
    _assert_captures("太好了 是ota ！ 我们有救了！", "体验需求类", "eligible:ota_hype")


def test_soft_upgrade_capture():
    _assert_captures("ssss还可以再升级一下", "体验需求类", "eligible:soft_suggestion")


def test_soft_experience_share_capture():
    _assert_captures("驾驶乐趣有，风噪和智能豪华还欠缺", "体验需求类", "eligible:soft_experience_share")


def test_wish_feedback_capture():
    _assert_captures("来源：意见反馈，多点forme配件吧", "服务类", "eligible:wish_feedback")


def test_price_inquiry_capture():
    text = "王先生  18809598088  来源：专属服务群 用户反馈：更换雨刮胶条的费用多少"
    _assert_captures(text, "服务类", "eligible:price_inquiry")


def test_assist_coordination_capture():
    text = "上海-牛群先生-For me  15201928850 用户反馈轮胎扎钉，需要协助处理"
    _assert_captures(text, "服务类", "eligible:assist_coordination")


def test_return_request_not_captured():
    text = "无锡-瞿子伟先生-Emeya反馈：遮阳棚质感太一般，想要退货"
    assert explain_positive_capture_block(text) != "eligible:wish_feedback"
    l2_map = load_l2_whitelist()
    l1, _, _ = apply_classification_post_rules(text, "产品质量类", "", l2_map)
    assert l1 == "产品质量类"
