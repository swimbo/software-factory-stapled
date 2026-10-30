"""Focused tests for gitleaks temporary report cleanup."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline  # noqa: E402


class GitleaksCleanupTests(unittest.TestCase):
    def _fake_run(self, reports, payload):
        def run(cmd, **_kwargs):
            report_path = cmd[cmd.index("--report-path") + 1]
            reports.append(report_path)
            Path(report_path).write_text(payload)
            return subprocess.CompletedProcess(cmd, 1)
        return run

    def _run_gitleaks(self, payload):
        reports = []
        run = self._fake_run(reports, payload)
        with mock.patch.object(pipeline, "_which", return_value="/usr/bin/gitleaks"), \
                mock.patch.object(pipeline.subprocess, "run", side_effect=run):
            result = pipeline.run_gitleaks("synthetic-target")
        return result, reports

    def test_findings_report_is_removed(self):
        payload = (
            '[{"RuleID": "example-rule", "File": "example.txt", '
            '"Description": "synthetic example finding"}]'
        )
        result, reports = self._run_gitleaks(payload)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].severity, "high")
        self.assertEqual(result[0].category, "secret")
        self.assertEqual(result[0].rule, "example-rule")
        self.assertEqual(result[0].location, "example.txt")
        self.assertEqual(len(reports), 1)
        self.assertFalse(Path(reports[0]).exists())

    def test_clean_report_is_removed(self):
        result, reports = self._run_gitleaks("[]")

        self.assertEqual(result, [])
        self.assertFalse(Path(reports[0]).exists())

    def test_malformed_report_is_removed_and_falls_back(self):
        result, reports = self._run_gitleaks("{not valid json")

        self.assertIsNone(result)
        self.assertFalse(Path(reports[0]).exists())

    def test_launch_error_propagates_and_report_is_removed(self):
        reports = []

        def run(cmd, **_kwargs):
            reports.append(cmd[cmd.index("--report-path") + 1])
            raise FileNotFoundError("gitleaks")

        with mock.patch.object(pipeline, "_which", return_value="/usr/bin/gitleaks"), \
                mock.patch.object(pipeline.subprocess, "run", side_effect=run):
            with self.assertRaises(FileNotFoundError):
                pipeline.run_gitleaks("synthetic-target")

        self.assertEqual(len(reports), 1)
        self.assertFalse(Path(reports[0]).exists())

    def test_cleanup_failure_is_visible_without_masking_scanner_failure(self):
        original_unlink = Path.unlink
        scanner_error = OSError("synthetic scanner failure")
        for failure in (None, scanner_error):
            with self.subTest(scanner_failed=failure is not None):
                reports = []
                def run(cmd, **_kwargs):
                    path = Path(cmd[cmd.index("--report-path") + 1])
                    reports.append(path)
                    if failure is not None:
                        raise failure
                    path.write_text("[]")
                    return subprocess.CompletedProcess(cmd, 0)
                try:
                    with mock.patch.object(pipeline, "_which", return_value="fixture"), \
                            mock.patch.object(pipeline.subprocess, "run", side_effect=run), \
                            mock.patch.object(Path, "unlink", side_effect=PermissionError("synthetic cleanup failure")):
                        with self.assertRaises(OSError) as caught:
                            pipeline.run_gitleaks("synthetic-target")
                    if failure is None:
                        self.assertIsInstance(caught.exception, PermissionError)
                    else:
                        self.assertIs(caught.exception, scanner_error)
                        self.assertIn("Gitleaks temporary report cleanup also failed", getattr(caught.exception, "__notes__", []))
                finally:
                    for path in reports:
                        original_unlink(path, missing_ok=True)

    def test_cleanup_failure_does_not_borrow_handled_caller_exception(self):
        reports = []
        original_unlink = Path.unlink
        try:
            try:
                raise RuntimeError("unrelated handled caller failure")
            except RuntimeError as caller_error:
                with mock.patch.object(pipeline, "_which", return_value="fixture"), \
                        mock.patch.object(pipeline.subprocess, "run", side_effect=self._fake_run(reports, "[]")), \
                        mock.patch.object(Path, "unlink", side_effect=PermissionError("synthetic cleanup failure")):
                    with self.assertRaises(PermissionError):
                        pipeline.run_gitleaks("synthetic-target")
                self.assertFalse(getattr(caller_error, "__notes__", []))
        finally:
            for path in reports:
                original_unlink(Path(path), missing_ok=True)

    def test_absent_binary_returns_none_without_running(self):
        with mock.patch.object(pipeline, "_which", return_value=None), \
                mock.patch.object(pipeline.subprocess, "run") as run:
            result = pipeline.run_gitleaks("synthetic-target")

        self.assertIsNone(result)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
