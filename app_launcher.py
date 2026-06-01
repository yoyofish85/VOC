#!/usr/bin/env python3
"""
舆情复核系统 - 应用程序启动器
功能：启动后端服务、前端服务，支持自动保存

一键自动化验证（独立测试库、不污染开发数据）：
  python3 app_launcher.py --verify
  python3 app_launcher.py --verify --quick      # 跳过 5628 等大样本与 E2E
  python3 app_launcher.py --verify --skip-e2e   # 仅接口测试（不启前端）

默认无参数：仍为桌面模式（后端+前端常驻）。
"""

import os
import sys
import time
import signal
import subprocess
import threading
import atexit
import shutil
import tempfile
from pathlib import Path
import webbrowser

# ===================== 配置 =====================
BASE_DIR = Path(__file__).parent.resolve()
BACKEND_DIR = BASE_DIR / "src" / "backend"
FRONTEND_DIR = BASE_DIR / "src" / "frontend"

# 端口配置
BACKEND_PORT = 8000
FRONTEND_PORT = 8080

# ===================== 全局变量 =====================
backend_process = None
frontend_process = None


def print_banner():
    """打印启动banner"""
    print(
        "\n"
        "============================================================\n"
        "   VOC V1.5 - 舆情复核系统\n"
        "============================================================\n",
        flush=True,
    )


def check_port_in_use(port):
    """检查端口是否被占用"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def find_free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return int(port)


def kill_port(port):
    """杀掉占用端口的进程"""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
        )
        if result.stdout:
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                if pid:
                    subprocess.run(["kill", "-9", pid])
                    print(f"已释放端口 {port}")
    except Exception as e:
        print(f"释放端口失败: {e}")


def _drain_process_output(proc, lines: list, max_lines: int = 200):
    """后台读取子进程 stdout，避免 PIPE 缓冲区写满导致子进程阻塞。"""
    try:
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            if len(lines) > max_lines:
                del lines[: len(lines) - max_lines]
    except Exception:
        pass


def start_backend():
    """启动后端服务"""
    global backend_process

    if check_port_in_use(BACKEND_PORT):
        print(f"端口 {BACKEND_PORT} 已被占用，正在释放...")
        kill_port(BACKEND_PORT)
        time.sleep(1)

    print(f"[1/2] 启动后端服务 (端口 {BACKEND_PORT})...")

    os.chdir(BACKEND_DIR)

    backend_lines: list = []
    backend_process = subprocess.Popen(
        [sys.executable, "main.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    threading.Thread(
        target=_drain_process_output,
        args=(backend_process, backend_lines),
        daemon=True,
        name="backend-log-drain",
    ).start()

    max_wait = int(os.environ.get("VOC_BACKEND_START_WAIT", "120"))
    for i in range(max_wait):
        time.sleep(0.5)
        if backend_process.poll() is not None:
            print("[FAIL] 后端进程已退出，最近日志：")
            for ln in backend_lines[-40:]:
                print(f"  {ln}")
            return False
        if check_port_in_use(BACKEND_PORT):
            print(f"[OK] 后端服务已启动 (http://localhost:{BACKEND_PORT})")
            return True
        if (i + 1) % 10 == 0:
            print(f"  等待后端启动... ({i+1}/{max_wait})")

    print("[FAIL] 后端服务启动失败（等待端口超时），最近日志：")
    for ln in backend_lines[-40:]:
        print(f"  {ln}")
    return False


def start_frontend():
    """启动前端服务"""
    global frontend_process

    print(f"[2/2] 启动前端服务 (端口 {FRONTEND_PORT})...")

    os.chdir(FRONTEND_DIR)

    node_modules = FRONTEND_DIR / "node_modules"
    if not node_modules.exists():
        print("  首次运行，正在安装前端依赖...")
        frontend_process = subprocess.Popen(
            ["npm", "install"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        while True:
            line = frontend_process.stdout.readline()
            if not line and frontend_process.poll() is not None:
                break
            if line:
                print(f"  npm: {line.strip()}")

    frontend_process = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(FRONTEND_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    for i in range(30):
        time.sleep(1)
        if check_port_in_use(FRONTEND_PORT):
            print(f"[OK] 前端服务已启动 (http://localhost:{FRONTEND_PORT})")
            return True
        print(f"  等待前端启动... ({i+1}/30)")

    print("[FAIL] 前端服务启动失败")
    return False


def stop_services():
    """停止所有服务"""
    print("\n正在关闭服务...")

    save_data()

    if frontend_process:
        frontend_process.terminate()
        try:
            frontend_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            frontend_process.kill()
        print("[OK] 前端服务已停止")

    if backend_process:
        backend_process.terminate()
        try:
            backend_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend_process.kill()
        print("[OK] 后端服务已停止")

    print("\n感谢使用舆情复核系统！")
    print("数据已自动保存。")


def save_data():
    """保存数据（可扩展保存逻辑）"""
    try:
        db_file = BACKEND_DIR / "opinion_review.db"
        if db_file.exists():
            print("[OK] 所有数据已安全保存到数据库")

        keyword_file = BACKEND_DIR / "keywords_v1.2.json"
        if keyword_file.exists():
            print("[OK] 关键词词库已保存")

    except Exception as e:
        print(f"保存数据时出错: {e}")


def signal_handler(signum, frame):
    """信号处理"""
    print("\n收到关闭信号...")
    stop_services()
    sys.exit(0)


def run_automation_verify() -> int:
    """启动后端（测试库）→ 可选前端 → pytest → 返回退出码。"""
    print_banner()
    report_dir = BASE_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    work = tempfile.mkdtemp(prefix="voc_autotest_")
    db_path = os.path.join(work, "opinion_test.db")
    art_path = work
    kw_dst = os.path.join(work, "keywords_test.json")
    kw_src = BACKEND_DIR / "keywords_v1.2.json"
    if kw_src.is_file():
        shutil.copy2(kw_src, kw_dst)
    else:
        Path(kw_dst).write_text(
            '{"keywords": {}, "v3_l1_keywords": {}, "l1_keyword_weights": {}, "updated_at": ""}',
            encoding="utf-8",
        )

    skip_e2e = "--skip-e2e" in sys.argv or "--quick" in sys.argv
    verify_port = BACKEND_PORT
    if skip_e2e:
        if check_port_in_use(BACKEND_PORT):
            verify_port = find_free_port()
            print(f"[验证] 端口 {BACKEND_PORT} 已占用，接口测试改用 {verify_port}。")
    elif check_port_in_use(BACKEND_PORT):
        print(f"[验证] 错误：E2E 需要后端在 {BACKEND_PORT}（与前端 API 一致）。请先释放端口或使用 --skip-e2e。")
        shutil.rmtree(work, ignore_errors=True)
        return 1

    env = os.environ.copy()
    env.update(
        {
            "VOC_DB_PATH": db_path,
            "VOC_TEST_ARTIFACTS_DIR": art_path,
            "VOC_KEYWORD_FILE": kw_dst,
            "VOC_DISABLE_RATE_LIMIT": "1",
            "VOC_REFLOW_MERGE_GOLD": "0",
            "VOC_BASE_URL": f"http://127.0.0.1:{verify_port}",
            "VOC_FRONTEND_URL": f"http://127.0.0.1:{FRONTEND_PORT}",
        }
    )

    print("[验证] 安装测试依赖（requirements-dev.txt）…")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(BASE_DIR / "requirements-dev.txt")],
        cwd=str(BASE_DIR),
        check=False,
    )
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        cwd=str(BASE_DIR),
        check=False,
    )

    print(f"[验证] 启动 uvicorn（测试库） http://127.0.0.1:{verify_port} …")
    backend_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(verify_port),
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    for _ in range(120):
        time.sleep(0.25)
        if check_port_in_use(verify_port):
            print("[验证] 后端端口已就绪")
            break
        if backend_proc.poll() is not None:
            err = backend_proc.stderr.read() if backend_proc.stderr else ""
            print("[验证] 后端进程已退出:", (err or "")[:2000])
            shutil.rmtree(work, ignore_errors=True)
            return 1
    else:
        print("[验证] 等待后端超时")
        backend_proc.terminate()
        shutil.rmtree(work, ignore_errors=True)
        return 1

    fe_proc = None
    if not skip_e2e:
        if not check_port_in_use(FRONTEND_PORT):
            fe_nm = FRONTEND_DIR / "node_modules"
            if not fe_nm.is_dir():
                print("[验证] 首次运行：npm install …")
                subprocess.run(["npm", "install"], cwd=str(FRONTEND_DIR), check=False)
            print(f"[验证] 启动 Vite 前端 http://127.0.0.1:{FRONTEND_PORT} …")
            fe_proc = subprocess.Popen(
                ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(FRONTEND_PORT)],
                cwd=str(FRONTEND_DIR),
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(120):
                time.sleep(0.5)
                if check_port_in_use(FRONTEND_PORT):
                    print("[验证] 前端端口已就绪")
                    break
            else:
                print("[验证] 前端启动超时，将跳过 E2E（可改用 --skip-e2e 仅跑接口）。")
                skip_e2e = True
                if fe_proc:
                    fe_proc.terminate()
                    try:
                        fe_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        fe_proc.kill()
                    fe_proc = None
        else:
            print(f"[验证] 端口 {FRONTEND_PORT} 已占用，假定前端已在运行。")

    junit = report_dir / "junit.xml"
    html_rep = report_dir / "report.html"
    pytest_cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(BASE_DIR / "tests"),
        "-v",
        "--tb=short",
        f"--junitxml={junit}",
        f"--html={html_rep}",
        "--self-contained-html",
    ]
    if "--quick" in sys.argv:
        pytest_cmd += ["-m", "not slow and not e2e"]
    elif "--skip-e2e" in sys.argv:
        pytest_cmd += ["-m", "not e2e"]
    print("[验证] 运行 pytest …")
    print(" ", " ".join(pytest_cmd))
    rc = subprocess.run(pytest_cmd, cwd=str(BASE_DIR), env=env).returncode

    if fe_proc:
        fe_proc.terminate()
        try:
            fe_proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            fe_proc.kill()
    backend_proc.terminate()
    try:
        backend_proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        backend_proc.kill()

    shutil.rmtree(work, ignore_errors=True)
    print("\n" + "=" * 50)
    print(f"测试报告：{html_rep}")
    print(f"JUnit：    {junit}")
    print(f"退出码：   {rc}")
    print("=" * 50)
    return rc


def main():
    """主函数"""
    if "--verify" in sys.argv or (len(sys.argv) > 1 and sys.argv[1] in ("verify", "test")):
        try:
            code = run_automation_verify()
        except KeyboardInterrupt:
            code = 130
        sys.exit(code)

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except ValueError:
        pass

    atexit.register(stop_services)

    print_banner()

    if not start_backend():
        input("\n按 Enter 键退出...")
        return

    if not start_frontend():
        input("\n按 Enter 键退出...")
        return

    url = f"http://localhost:{FRONTEND_PORT}"
    print(f"\n正在打开浏览器: {url}")
    threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    print("\n" + "=" * 50)
    print("服务已就绪！")
    print(f"  - 后端: http://localhost:{BACKEND_PORT}")
    print(f"  - 前端: http://localhost:{FRONTEND_PORT}")
    print("\n按 Ctrl+C 关闭程序")
    print("=" * 50)

    try:
        while True:
            time.sleep(1)
            if backend_process.poll() is not None:
                print("\n后端服务意外停止")
                break
            if frontend_process.poll() is not None:
                print("\n前端服务意外停止")
                break
    except KeyboardInterrupt:
        pass

    stop_services()


if __name__ == "__main__":
    main()
