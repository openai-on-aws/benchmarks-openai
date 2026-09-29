"""Report-size failures preserve the complete, offline fallback outputs."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/bedrock-bench/scripts"))

from bedrock_bench.cli import demo_experiment, main
from bedrock_bench.engine import execute
from bedrock_bench.report import compare


class ReportOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.run_dir, self.run = execute(demo_experiment(), self.directory / "results")
        failed = next(row for row in self.run["attempts"] if not row["success"])
        failed["grading"]["reason"] = "oversized saved evidence " * 50_000
        self.source = self.run_dir / "run.json"
        self.source.write_text(json.dumps(self.run))
        self.original_bytes = self.source.read_bytes()

    def assert_full_outputs(self, output):
        self.assertGreater((output / "REPORT.html").stat().st_size, 1_000_000)
        self.assertIn("<!doctype html>", (output / "REPORT.html").read_text())
        self.assertIn("Cost per success includes spend on failed attempts",
                      (output / "REPORT.md").read_text())
        self.assertEqual(json.loads((output / "comparison.json").read_text()), compare([self.run]))
        self.assertEqual(self.source.read_bytes(), self.original_bytes)

    def test_inline_overflow_retains_full_outputs_and_reports_their_location(self):
        output = self.directory / "inline"
        with patch("bedrock_bench.cli.execute", side_effect=AssertionError("must not run")), \
                contextlib.redirect_stdout(io.StringIO()) as stdout, \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            status = main(["report", str(self.source), "--out", str(output), "--format", "inline"])
        self.assertEqual(status, 2)
        self.assertIn("--format html", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assert_full_outputs(output)
        self.assertIn(str(output.resolve()), stderr.getvalue())
        self.assertFalse((output / "REPORT.inline.html").exists())

    def test_html_reporting_accepts_the_same_oversized_saved_evidence(self):
        output = self.directory / "html"
        with patch("bedrock_bench.cli.execute", side_effect=AssertionError("must not run")), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            status = main(["report", str(self.source), "--out", str(output), "--format", "html"])
        self.assertEqual(status, 0)
        self.assertEqual(Path(stdout.getvalue().strip()), (output / "REPORT.html").resolve())
        self.assert_full_outputs(output)


if __name__ == "__main__":
    unittest.main()
