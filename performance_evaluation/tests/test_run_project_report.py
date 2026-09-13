from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = PROJECT_ROOT / "performance_evaluation" / "run_project_report.sh"


class RunProjectReportTest(unittest.TestCase):
    def test_rejects_python_3_14_before_running_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            perf_dir = root / "performance_evaluation"
            perf_dir.mkdir()
            shutil.copy2(RUN_SCRIPT, perf_dir / RUN_SCRIPT.name)

            log_path = root / "python_calls.log"
            fake_python = root / "fake-python"
            fake_python.write_text(
                """#!/usr/bin/env bash
if [[ "${1:-}" == "-c" ]]; then
  if [[ "${2:-}" == *"print"* ]]; then
    echo "3.14"
    exit 0
  fi
  exit 1
fi
printf '%s\\n' "$*" >> "$VOC_TEST_LOG"
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            env = os.environ.copy()
            env["VOC_PYTHON"] = str(fake_python)
            env["VOC_TEST_LOG"] = str(log_path)
            completed = subprocess.run(
                ["bash", str(perf_dir / RUN_SCRIPT.name), "7"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertIn("必须为 3.11–3.13，当前为 3.14", completed.stderr)
            self.assertFalse(log_path.exists(), "版本不兼容时不应运行任何周报脚本")

    def test_one_click_report_exports_accuracy_timeline_with_project_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            perf_dir = root / "performance_evaluation"
            perf_dir.mkdir()
            shutil.copy2(RUN_SCRIPT, perf_dir / RUN_SCRIPT.name)

            script_names = [
                "health_check.py",
                "evaluate_accuracy.py",
                "eval_batch_accuracy.py",
                "eval_recent_rule_candidate.py",
                "export_l1_errors.py",
                "weekly_evolution_report.py",
                "report_project_stages.py",
                "export_accuracy_timeline.py",
            ]
            for name in script_names:
                (perf_dir / name).touch()

            manual_seed = perf_dir / "exports" / "accuracy_timeline_manual_seed.csv"
            manual_seed.parent.mkdir()
            manual_seed.touch()

            log_path = root / "python_calls.log"
            fake_python = root / "fake-python"
            fake_python.write_text(
                """#!/usr/bin/env bash
if [[ "${1:-}" == "-c" ]]; then
  echo "3.12"
  exit 0
fi
printf '%s\\n' "$*" >> "$VOC_TEST_LOG"
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            env = os.environ.copy()
            env["VOC_PYTHON"] = str(fake_python)
            env["VOC_TEST_LOG"] = str(log_path)
            completed = subprocess.run(
                ["bash", str(perf_dir / RUN_SCRIPT.name), "7"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            calls = log_path.read_text(encoding="utf-8")
            self.assertIn(
                "performance_evaluation/export_accuracy_timeline.py "
                "--manual-csv "
                "performance_evaluation/exports/accuracy_timeline_manual_seed.csv",
                calls,
            )


if __name__ == "__main__":
    unittest.main()
