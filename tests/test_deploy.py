# -*- coding: utf-8 -*-
"""部署打包与 MD5 校验测试（离线，无需启动后端）。"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CODE_DEPLOY = ROOT / "code_deploy"
PACKAGE_SH = CODE_DEPLOY / "package_code.sh"
UPDATE_ZIP = CODE_DEPLOY / "update.zip"
UPDATE_MD5 = CODE_DEPLOY / "update.zip.md5"

DB_ARTIFACT_RE = re.compile(r"\.(db|sqlite|db-shm|db-wal)$")


def _zip_entries(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return zf.namelist()


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def packed_artifacts() -> Path:
    """运行 package_code.sh 生成 update.zip 与 update.zip.md5。"""
    env = os.environ.copy()
    env["SKIP_FRONTEND_BUILD"] = "1"
    env["ALLOW_DIRTY_PACK"] = "1"
    env["SKIP_GIT_TAG"] = "1"
    proc = subprocess.run(
        ["bash", str(PACKAGE_SH)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert UPDATE_ZIP.is_file(), "未生成 update.zip"
    assert UPDATE_MD5.is_file(), "未生成 update.zip.md5"
    return UPDATE_ZIP


def test_package_generates_zip_and_md5(packed_artifacts: Path) -> None:
    assert packed_artifacts.stat().st_size > 0
    assert UPDATE_MD5.read_text(encoding="utf-8").strip()


def test_zip_excludes_database_artifacts(packed_artifacts: Path) -> None:
    bad = [name for name in _zip_entries(packed_artifacts) if DB_ARTIFACT_RE.search(name)]
    assert bad == [], f"zip 含数据库/WAL 文件: {bad}"


def test_zip_contains_required_paths(packed_artifacts: Path) -> None:
    names = _zip_entries(packed_artifacts)
    assert any(n.endswith("src/backend/main.py") for n in names), "缺少 src/backend/main.py"
    assert any("src/frontend/dist/" in n for n in names), "缺少 src/frontend/dist/"


def test_md5_matches_zip(packed_artifacts: Path) -> None:
    line = UPDATE_MD5.read_text(encoding="utf-8").strip().splitlines()[0]
    expected = line.split()[0]
    actual = _file_md5(packed_artifacts)
    assert expected == actual, f"MD5 不匹配: 文件={expected} 计算={actual}"


def test_unzip_list_no_db_grep_empty(packed_artifacts: Path) -> None:
    """等价于: unzip -l update.zip | grep -E '\\.(db|sqlite|db-shm|db-wal)$' 无输出。"""
    proc = subprocess.run(
        ["unzip", "-l", str(packed_artifacts)],
        capture_output=True,
        check=True,
    )
    output = proc.stdout.decode("utf-8", errors="replace")
    matches = [
        ln
        for ln in output.splitlines()
        if DB_ARTIFACT_RE.search(ln.split()[-1] if ln.split() else "")
    ]
    assert matches == [], matches
