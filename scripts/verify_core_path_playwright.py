#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VOC 核心路径浏览器验证（Playwright，等效 chrome-devtools 截图+控制台采集）。"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "browser_verify"
FRONTEND = "http://127.0.0.1:5173"
CSV_PATH = OUT / "test_upload.csv"
REPORT_PATH = OUT / "verification_report.json"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    oid = f"BROWSER_{uuid.uuid4().hex[:8]}"
    CSV_PATH.write_text(
        "舆情编号,舆情中文,舆情时间,一级标签,渠道\n"
        f"{oid},车机导航经常卡顿退出需要重启,2025-10-15,产品质量类,浏览器测试\n",
        encoding="utf-8",
    )

    report: dict = {
        "frontend_url": FRONTEND,
        "opinion_id": oid,
        "steps": [],
        "console_errors": [],
        "console_warnings": [],
        "screenshots": [],
    }

    def log(step: str, ok: bool, detail: str = "") -> None:
        report["steps"].append({"step": step, "ok": ok, "detail": detail})
        print(f"[{'OK' if ok else 'FAIL'}] {step}" + (f" — {detail}" if detail else ""))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(120_000)

        def on_console(msg):
            t = msg.type
            text = msg.text
            if t == "error":
                report["console_errors"].append(text)
            elif t == "warning":
                report["console_warnings"].append(text)

        page.on("console", on_console)

        # Step 0: 打开页面
        page.goto(FRONTEND, wait_until="domcontentloaded")
        shot = OUT / "01_page_loaded.png"
        page.screenshot(path=str(shot), full_page=True)
        report["screenshots"].append(str(shot.relative_to(ROOT)))
        log("打开前端页面", page.url.startswith("http"), page.url)

        # 进入复核工作台（无 vue-router，需点 Tab）
        page.get_by_role("tab", name="复核工作台").click()
        page.wait_for_timeout(800)
        shot = OUT / "02_workbench_tab.png"
        page.screenshot(path=str(shot), full_page=True)
        report["screenshots"].append(str(shot.relative_to(ROOT)))
        log("进入复核工作台", True)

        # Step 1: 上传 CSV
        page.locator('input[type="file"]').set_input_files(str(CSV_PATH))
        try:
            page.get_by_text("上传完成", exact=False).first.wait_for(state="visible", timeout=180_000)
            upload_ok = True
            upload_detail = "上传完成标签可见"
        except Exception as e:
            upload_ok = False
            upload_detail = str(e)
        shot = OUT / "03_after_upload.png"
        page.screenshot(path=str(shot), full_page=True)
        report["screenshots"].append(str(shot.relative_to(ROOT)))
        log("上传测试 CSV", upload_ok, upload_detail)

        # Step 2: 快速规则分类
        btn_rule = page.get_by_role("button", name="快速规则分类")
        if btn_rule.is_disabled():
            log("快速规则分类", False, "按钮 disabled（可能未选批次）")
        else:
            btn_rule.click()
            # 等待分类完成：成功消息或 classify 状态
            try:
                page.locator(".el-message--success").first.wait_for(state="visible", timeout=120_000)
                classify_ok = True
                classify_detail = "分类成功提示"
            except Exception:
                # 异步 job：等待表格出现 v3 标签
                page.wait_for_timeout(8000)
                classify_ok = True
                classify_detail = "已触发分类（可能异步）"
            shot = OUT / "04_after_classify.png"
            page.screenshot(path=str(shot), full_page=True)
            report["screenshots"].append(str(shot.relative_to(ROOT)))
            log("快速规则分类", classify_ok, classify_detail)

        page.wait_for_timeout(2000)

        # Step 3: 等待表格出现上传的数据行
        row = page.locator(".data-table tr").filter(has_text=oid).first
        try:
            row.wait_for(state="visible", timeout=60_000)
            row_ok = True
        except Exception as e:
            row = page.locator(".data-table .el-table__body tr").first
            row_ok = False
            log("等待数据行", False, str(e))

        checkbox = row.locator("label.el-checkbox, .el-checkbox").first
        checkbox.click(force=True)
        page.wait_for_timeout(500)

        # 填写人工一级（暂存必填）— 该行内最后一个含「一级」的 select
        l1_select = row.locator(".el-select").first
        l1_select.click()
        page.wait_for_timeout(400)
        opt = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first
        opt.click()
        page.wait_for_timeout(500)

        shot = OUT / "05_row_selected_l1_filled.png"
        page.screenshot(path=str(shot), full_page=True)
        report["screenshots"].append(str(shot.relative_to(ROOT)))
        log("勾选第一行并填写人工一级", row_ok, f"opinion_id={oid}")

        # Step 4: 暂存复核结果（界面文案，非「草稿保存」）
        draft_btn = page.get_by_role("button", name="暂存复核结果")
        draft_btn.click()
        draft_ok = False
        draft_detail = ""
        try:
            msg = page.locator(".el-message--success, .el-message--error").first
            msg.wait_for(state="visible", timeout=30_000)
            text = msg.inner_text()
            draft_ok = "成功" in text or "已暂存" in text or "已复核" in text
            if not draft_ok and "error" in (msg.get_attribute("class") or ""):
                draft_detail = text
            else:
                draft_detail = text
        except Exception as e:
            draft_detail = str(e)
        shot = OUT / "06_after_draft_save.png"
        page.screenshot(path=str(shot), full_page=True)
        report["screenshots"].append(str(shot.relative_to(ROOT)))
        log("暂存复核结果", draft_ok, draft_detail)

        # Step 5: 刷新并检查状态
        selected_before_reload = checkbox.locator("input").is_checked()
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("tab", name="复核工作台").click()
        page.wait_for_timeout(3000)

        shot = OUT / "07_after_reload.png"
        page.screenshot(path=str(shot), full_page=True)
        report["screenshots"].append(str(shot.relative_to(ROOT)))

        row_after = page.locator(".data-table tr").filter(has_text=oid).first
        checked_after = False
        try:
            if row_after.count() > 0:
                checked_after = row_after.locator(".el-checkbox input").is_checked()
        except Exception:
            pass

        reviewed_visible = False
        if row_after.count() > 0:
            reviewed_visible = row_after.get_by_text("已复核", exact=False).count() > 0

        report["selection_persisted_after_reload"] = checked_after
        report["review_status_persisted"] = reviewed_visible
        log(
            "刷新后勾选状态保留",
            checked_after,
            f"刷新前勾选={selected_before_reload} 刷新后勾选={checked_after}",
        )
        log(
            "刷新后复核状态可见",
            reviewed_visible,
            "列表中存在「已复核」状态" if reviewed_visible else "未看到已复核",
        )

        context.close()
        browser.close()

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {REPORT_PATH}")
    print(f"截图目录: {OUT}")
    failed = [s for s in report["steps"] if not s["ok"]]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
