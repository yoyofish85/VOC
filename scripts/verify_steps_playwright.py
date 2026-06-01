#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐步浏览器验证（Playwright 等效 chrome-devtools take_screenshot）。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "test" / "validation_full_keyword_only_v18.csv"
OUT = ROOT / "reports" / "browser_verify_steps"
FRONTEND = "http://127.0.0.1:5173/review"
REPORT = OUT / "step_results.json"


def shot(page, name: str, results: list) -> str:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    page.screenshot(path=str(p), full_page=True)
    results.append({"screenshot": str(p.relative_to(ROOT))})
    return str(p)


def fail(step: int, msg: str, page, results: list) -> int:
    results.append({"step": step, "status": "FAIL", "detail": msg})
    if page:
        shot(page, f"FAIL_step{step:02d}.png", results)
    REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"步骤 {step} 失败: {msg}")
    return step


def ok(step: int, msg: str, results: list) -> None:
    results.append({"step": step, "status": "OK", "detail": msg})
    print(f"步骤 {step} 完成: {msg}")


def main() -> int:
    results: list = []
    if not CSV.is_file():
        print(f"步骤 4 失败: CSV 不存在 {CSV}")
        return 4

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(300_000)
        console_errors: list = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        # 1 打开页面
        try:
            page.goto(FRONTEND, wait_until="domcontentloaded")
            ok(1, f"已打开 {page.url}", results)
        except Exception as e:
            return fail(1, str(e), page, results)

        # 2 截图
        shot(page, "step02_initial.png", results)
        ok(2, "已截图 step02_initial.png", results)

        # 进入复核工作台（无独立 /review 路由）
        page.get_by_role("tab", name="复核工作台").click()
        page.wait_for_timeout(800)

        # 3 点击上传 CSV（界面文案：上传 CSV 数据）
        try:
            upload_btn = page.get_by_role("button", name="上传 CSV 数据")
            upload_btn.click()
            ok(3, "已点击「上传 CSV 数据」", results)
        except Exception as e:
            return fail(3, str(e), page, results)

        # 4 上传文件
        try:
            page.locator('input[type="file"]').set_input_files(str(CSV))
            ok(4, f"已选择文件 {CSV.name}", results)
        except Exception as e:
            return fail(4, str(e), page, results)

        # 5 等待上传成功
        try:
            page.get_by_text("上传完成", exact=False).first.wait_for(state="visible", timeout=600_000)
            shot(page, "step05_upload_done.png", results)
            ok(5, "出现「上传完成」提示", results)
        except Exception as e:
            return fail(5, str(e), page, results)

        # 6 快速规则分类
        try:
            page.get_by_role("button", name="快速规则分类").click()
            ok(6, "已点击「快速规则分类」", results)
        except Exception as e:
            return fail(6, str(e), page, results)

        # 7 每 5 秒检查直到分类完成
        done = False
        detail = ""
        for i in range(120):  # 最多 10 分钟
            time.sleep(5)
            body = page.content()
            if "分类完成" in body:
                done = True
                detail = f"第 {(i+1)*5} 秒检测到「分类完成」"
                break
            if page.locator(".el-message--success").filter(has_text="分类").count() > 0:
                done = True
                detail = f"第 {(i+1)*5} 秒出现分类成功消息"
                break
            # 异步 job：检查 classifyRunning 结束 + 表格有 v3 标签
            if not page.get_by_role("button", name="快速规则分类").is_disabled():
                rows = page.locator(".data-table .el-table__body tr").count()
                if rows > 0 and i >= 1:
                    done = True
                    detail = f"第 {(i+1)*5} 秒列表已有 {rows} 行且分类按钮可点"
                    break
        shot(page, "step07_classify_done.png", results)
        if not done:
            return fail(7, "超时未检测到「分类完成」", page, results)
        ok(7, detail, results)

        # 8 勾选第一条
        try:
            first_row = page.locator(".data-table .el-table__body tr").first
            first_row.wait_for(state="visible", timeout=60_000)
            cb = first_row.locator(".el-checkbox").first
            cb.click(force=True)
            shot(page, "step08_row_checked.png", results)
            ok(8, "已勾选列表第一条", results)
        except Exception as e:
            return fail(8, str(e), page, results)

        # 填写人工一级（暂存必填）
        try:
            l1 = first_row.locator(".el-select").first
            l1.click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first.click()
            page.wait_for_timeout(400)
        except Exception:
            pass

        # 9 草稿保存 → 界面为「暂存复核结果」
        try:
            page.get_by_role("button", name="暂存复核结果").click()
            page.wait_for_timeout(2000)
            msg_el = page.locator(".el-message--success, .el-message--error").first
            msg_text = ""
            if msg_el.count() > 0:
                msg_text = msg_el.inner_text(timeout=10_000)
            shot(page, "step09_draft_save.png", results)
            if "error" in (msg_el.get_attribute("class") or "") or "失败" in msg_text or "bindings" in msg_text.lower():
                return fail(9, f"暂存失败: {msg_text}", page, results)
            ok(9, f"已点击「暂存复核结果」; 提示: {msg_text or '无 toast'}", results)
        except Exception as e:
            return fail(9, str(e), page, results)

        # 10 刷新
        checked_before = first_row.locator(".el-checkbox input").is_checked()
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("tab", name="复核工作台").click()
        page.wait_for_timeout(3000)
        shot(page, "step10_after_reload.png", results)
        ok(10, "页面已刷新并回到复核工作台", results)

        # 11 检查勾选是否保留
        row_after = page.locator(".data-table .el-table__body tr").first
        checked_after = False
        try:
            checked_after = row_after.locator(".el-checkbox input").is_checked()
        except Exception:
            pass
        persisted = checked_after is True
        ok(11, f"刷新前勾选={checked_before} 刷新后勾选={checked_after} （{'保留' if persisted else '未保留'}）", results)
        results.append({"console_errors": console_errors})

        browser.close()

    REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {REPORT}")
    failed = [r for r in results if isinstance(r, dict) and r.get("status") == "FAIL"]
    return failed[0]["step"] if failed else 0


if __name__ == "__main__":
    sys.exit(main())
