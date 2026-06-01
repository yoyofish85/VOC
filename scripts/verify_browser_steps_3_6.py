#!/usr/bin/env python3
"""步骤 3-6：浏览器上传 + 规则分类 + 验表格行数。"""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "test" / "validation_full_keyword_only_v18.csv"
OUT = ROOT / "reports" / "browser_verify_steps"
OUT.mkdir(parents=True, exist_ok=True)

results = []

def report(step, ok, detail=""):
    results.append({"step": step, "ok": ok, "detail": detail})
    print(f"[{'OK' if ok else 'FAIL'}] 步骤{step}: {detail}")

with sync_playwright() as p:
    page = p.chromium.launch(headless=True).new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(180_000)

    page.goto("http://127.0.0.1:5173/review", wait_until="domcontentloaded")
    page.screenshot(path=str(OUT / "step3_page.png"), full_page=True)
    report(3, True, f"已打开 {page.url}")

    page.get_by_role("tab", name="复核工作台").click()
    page.wait_for_timeout(500)

    page.get_by_role("button", name="上传 CSV 数据").click()
    page.locator('input[type="file"]').set_input_files(str(CSV))
    page.get_by_text("上传完成", exact=False).first.wait_for(state="visible", timeout=120_000)
    page.screenshot(path=str(OUT / "step4_upload.png"), full_page=True)
    report(4, True, f"已上传 {CSV.name}")

    page.get_by_role("button", name="快速规则分类").click()
    for i in range(24):
        time.sleep(5)
        rows = page.locator(".data-table .el-table__body tr").count()
        disabled = page.get_by_role("button", name="快速规则分类").is_disabled()
        if rows >= 1 and not disabled:
            report(5, True, f"约 {(i+1)*5}s 后分类结束，按钮可点")
            break
    else:
        report(5, False, "等待分类超时")
        page.screenshot(path=str(OUT / "FAIL_step5.png"), full_page=True)
        (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(5)

    page.screenshot(path=str(OUT / "step5_classify.png"), full_page=True)
    row_count = page.locator(".data-table .el-table__body tr").count()
    ok6 = row_count >= 1
    page.screenshot(path=str(OUT / "step6_table.png"), full_page=True)
    report(6, ok6, f"表格可见行数={row_count}（规则分类成功需 >=1）")

(OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
sys.exit(0 if all(r["ok"] for r in results) else 1)
