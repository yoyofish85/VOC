# -*- coding: utf-8 -*-
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "label_project"))

from taxonomy_normalize import (  # noqa: E402
    L2_EVAL_L1,
    is_non_issue_l1,
    l2_required_for_l1,
    strip_non_issue_l2,
)


def test_strip_non_issue_l2_clears_l2():
    l1, l2 = strip_non_issue_l2("非问题", "咨询与表扬")
    assert l1 == "非问题"
    assert l2 == ""


def test_strip_non_issue_l2_keeps_business_l2():
    l1, l2 = strip_non_issue_l2("产品质量类", "LFC问题")
    assert l1 == "产品质量类"
    assert l2 == "LFC问题"


def test_l2_required_for_l1():
    assert not l2_required_for_l1("非问题")
    assert l2_required_for_l1("服务类")
    assert "产品质量类" in L2_EVAL_L1
    assert is_non_issue_l1("咨询")
