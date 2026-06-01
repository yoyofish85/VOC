# -*- coding: utf-8 -*-
"""
单条 Playwright 黄金链路（需前端可访问）：
上传 CSV → 智能分类 → 暂存 → 刷新后仍见「已复核」"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright, expect


FRONTEND = os.environ.get("VOC_FRONTEND_URL", "http://127.0.0.1:5173").rstrip("/")


@pytest.mark.e2e
def test_golden_path_upload_classify_draft_reload(tmp_path: Path) -> None:
    csv_path = tmp_path / "e2e_golden.csv"
    csv_path.write_text(
        "舆情编号,舆情中文,舆情时间,一级标签,渠道\n"
        "E2E_GOLD_001,电池充电异常发热,2025-06-01,产品质量类,Playwright\n",
        encoding="utf-8",
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(120_000)

        page.goto(FRONTEND, wait_until="domcontentloaded")
        page.get_by_text("复核工作台", exact=True).click()

        page.locator('input[type="file"]').set_input_files(str(csv_path))

        page.get_by_text("上传完成", exact=False).first.wait_for(state="visible", timeout=180_000)

        btn_cls = page.get_by_role("button", name="开启智能分类")
        if not btn_cls.is_disabled():
            btn_cls.click()
            page.wait_for_timeout(8000)

        page.get_by_role("button", name="暂存复核结果").click()

        ok = page.locator(".el-message--success")
        ok.first.wait_for(state="visible", timeout=120_000)

        page.reload(wait_until="domcontentloaded")
        page.get_by_text("复核工作台", exact=True).click()
        page.wait_for_timeout(500)
        # 状态列文案可能为「已复核」或「已复核（历史）」；限定在复核工作台内避免匹配到隐藏 Tab
        reviewed_pat = re.compile(r"已复核(?:（历史）)?")
        status_chip = page.locator(".workbench").locator(".el-tag").filter(has_text=reviewed_pat)
        expect(status_chip.first).to_be_visible(timeout=60_000)

        context.close()
        browser.close()
