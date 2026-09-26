"""Offline reporting, isolation, and installed-package checks for the explorer."""

import contextlib
import copy
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/bedrock-bench"
sys.path.insert(0, str(PLUGIN / "scripts"))

from bedrock_bench.cli import demo_experiment, main
from bedrock_bench.engine import execute
from bedrock_bench.explorer import (
    PREVIEW_BYTES, explorer_data, inspect_attempt, list_runs, load_run, render_explorer,
)
from bedrock_bench.report import compare, write_report


class ReportParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.in_data = False
        self.data = ""
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "script" and dict(attrs).get("id") == "bb-data":
            self.in_data = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_data = False

    def handle_data(self, data):
        if self.in_data:
            self.data += data


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root, self.run = execute(demo_experiment(), self.directory / "results")
        self.source = self.root / "run.json"
        self.failed = next(row for row in self.run["attempts"] if not row["success"])

    def save(self, run=None):
        self.source.write_text(json.dumps(self.run if run is None else run))

    def test_completed_run_writes_a_self_contained_explorer_with_the_original_accounting(self):
        html = (self.root / "REPORT.html").read_text()
        parsed = ReportParser(html)
        data = json.loads(parsed.data)
        self.assertIn("html", parsed.tags)
        self.assertTrue(data["synthetic"])
        self.assertFalse(data["inline"])
        self.assertEqual(len(data["attempts"]), 6)
        imperfect = next(row for row in data["targets"] if row["target"]["id"] == "fixture-imperfect")
        self.assertEqual(imperfect["successes"], 2)
        self.assertEqual(imperfect["attempts"], 3)
        self.assertAlmostEqual(imperfect["cost_per_success_usd"], 0.006)
        self.assertAlmostEqual(imperfect["total_cost_usd"], 0.012)
        self.assertEqual(imperfect["cost_coverage"], 1)
        self.assertEqual(data["runs"][0]["source"], str(self.source))
        self.assertEqual(len([tag for tag in parsed.tags if tag == "script"]), 2)
        self.assertNotIn("<script src=", html)
        self.assertNotIn("<link ", html)
        self.assertTrue((self.root / "REPORT.md").is_file())
        self.assertTrue((self.root / "comparison.json").is_file())

    def test_missing_cost_and_zero_successes_do_not_become_a_free_target(self):
        self.failed["cost_usd"] = None
        self.failed["accounting_complete"] = False
        for row in self.run["attempts"]:
            if row["target"]["id"] == "fixture-imperfect":
                row["success"] = False
        summary = compare([self.run])
        data = explorer_data([self.run], summary, self.root, [self.source])
        imperfect = next(row for row in data["targets"] if row["target"]["id"] == "fixture-imperfect")
        self.assertIsNone(imperfect["cost_per_success_usd"])
        self.assertIsNone(imperfect["total_cost_usd"])
        self.assertAlmostEqual(imperfect["cost_coverage"], 2 / 3)
        self.assertEqual(imperfect["successes"], 0)
        task = next(row for row in data["tasks"] if row["target_id"] == "fixture-imperfect" and row["task_id"] == "inference-triage")
        self.assertIsNone(task["cost_per_success_usd"])
        self.assertEqual(task["attempts"], 1)

    def test_labels_cannot_escape_embedded_json_or_create_html_elements(self):
        hostile = '</script><img src=x onerror="alert(1)"><script>'
        self.run["name"] = hostile
        self.failed["grading"]["reason"] = hostile
        self.failed["target"]["id"] = hostile
        html = render_explorer([self.run], compare([self.run]), self.root, [self.source])
        parsed = ReportParser(html)
        data = json.loads(parsed.data)
        self.assertNotIn("img", parsed.tags)
        self.assertEqual(parsed.tags.count("script"), 2)
        self.assertEqual(data["runs"][0]["name"], hostile)
        self.assertEqual(next(row for row in data["attempts"] if not row["success"])["grading"]["reason"], hostile)
        self.assertNotIn(hostile, html)

    def test_attempt_keys_and_target_versions_remain_distinct_across_runs(self):
        second = copy.deepcopy(self.run)
        second["run_id"] = "second-run"
        for row in second["attempts"]:
            row["runner_version"] = "different-runner-version"
        runs = [self.run, second]
        data = explorer_data(runs, compare(runs), self.root, [self.source, None])
        self.assertEqual(len(data["targets"]), 4)
        self.assertEqual(len({row["key"] for row in data["attempts"]}), 12)
        keys = {row["key"] for row in data["targets"]}
        self.assertEqual({row["target_key"] for row in data["attempts"]}, keys)
        self.assertTrue(all(row["target_key"] in keys for row in data["tasks"]))

    def test_inspection_preserves_failed_attempts_and_bounds_redacted_previews(self):
        trace = self.root / self.failed["trace"]
        secret = "test-credential-that-must-be-redacted"
        trace.write_text(secret + "\n" + "x" * (PREVIEW_BYTES + 100))
        with patch.dict(os.environ, {"BENCH_TEST_TOKEN": secret}):
            value = inspect_attempt(self.source, self.failed["attempt_id"])
        self.assertFalse(value["attempt"]["success"])
        evidence = next(item for item in value["evidence"] if item["label"] == "Trace")
        self.assertTrue(evidence["truncated"])
        self.assertIn("[REDACTED]", evidence["preview"])
        self.assertNotIn(secret, evidence["preview"])
        self.assertLessEqual(len(evidence["preview"]), PREVIEW_BYTES)
        workspace = next(item for item in value["evidence"] if item["label"] == "Workspace")
        self.assertEqual(workspace["kind"], "directory")
        self.assertNotIn("preview", workspace)

    def test_evidence_paths_cannot_read_outside_the_run_directory(self):
        outside = self.directory / "outside.txt"
        outside.write_text("private-content")
        symlink = self.root / "escape.txt"
        try:
            symlink.symlink_to(outside)
        except OSError:
            symlink = None
        paths = ["../outside.txt", str(outside), "javascript:alert(1)", "folder\\outside.txt"]
        if symlink:
            paths.append(symlink.name)
        for path in paths:
            with self.subTest(path=path):
                self.failed["trace"] = path
                self.save()
                value = inspect_attempt(self.source, self.failed["attempt_id"])
                evidence = next(item for item in value["evidence"] if item["label"] == "Trace")
                self.assertEqual(evidence["status"], "unavailable")
                self.assertNotIn("preview", evidence)
                self.assertNotIn("private-content", json.dumps(value))
                report = explorer_data([self.run], compare([self.run]), self.root, [self.source])
                selected = next(row for row in report["attempts"] if not row["success"])
                self.assertNotIn("href", next(item for item in selected["evidence"] if item["label"] == "Trace"))

    def test_missing_evidence_stays_visible_and_valid_links_are_encoded(self):
        renamed = self.root / "events ?#<.jsonl"
        renamed.write_text("{}")
        self.failed["trace"] = renamed.name
        self.save()
        data = explorer_data([self.run], compare([self.run]), self.directory / "comparison", [self.source])
        selected = next(row for row in data["attempts"] if not row["success"])
        evidence = next(item for item in selected["evidence"] if item["label"] == "Trace")
        self.assertTrue(evidence["href"].startswith("./"))
        self.assertIn("%3F%23%3C", evidence["href"])
        renamed.unlink()
        value = inspect_attempt(self.source, self.failed["attempt_id"])
        self.assertFalse(value["attempt"]["success"])
        self.assertEqual(value["evidence"][0]["status"], "missing")

    def test_run_discovery_includes_interrupted_runs_and_reports_corrupt_files(self):
        parent = self.root.parent
        interrupted = parent / "interrupted"
        interrupted.mkdir()
        run = {**self.run, "run_id": "interrupted-run", "status": "interrupted", "attempts": []}
        (interrupted / "run.json").write_text(json.dumps(run))
        corrupt = parent / "corrupt"
        corrupt.mkdir()
        (corrupt / "run.json").write_text("{bad json")
        archive = parent / "archive" / "previous"
        archive.mkdir(parents=True)
        (archive / "run.json").write_text(json.dumps(self.run))
        result = list_runs(parent)
        self.assertEqual(len(result["runs"]), 2)
        self.assertEqual(len(result["unreadable"]), 1)
        self.assertEqual(next(row for row in result["runs"] if row["run_id"] == "interrupted-run")["recorded_attempts"], 0)
        self.assertEqual(len(list_runs(self.root)["runs"]), 1)

    def test_reference_validation_is_labeled_and_cannot_mix_with_model_results(self):
        reference = copy.deepcopy(self.run)
        reference.update(synthetic=False, validation_only=True)
        data = explorer_data([reference], compare([reference]), self.root, [self.source])
        self.assertTrue(data["validation_only"])
        self.assertFalse(data["synthetic"])
        with self.assertRaises(ValueError):
            write_report([self.run, reference], self.directory / "mixed")

    def test_cli_inspection_and_inline_reporting_do_not_execute_a_benchmark(self):
        output = self.directory / "inline"
        with patch("bedrock_bench.cli.execute", side_effect=AssertionError("must not run")), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(["report", str(self.source), "--out", str(output), "--format", "inline"]), 0)
        path = Path(stdout.getvalue().strip())
        parsed = ReportParser(path.read_text())
        self.assertNotIn("html", parsed.tags)
        self.assertTrue(json.loads(parsed.data)["inline"])
        self.assertTrue((output / "REPORT.html").is_file())
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(["inspect", str(self.source), "--attempt", self.failed["attempt_id"]]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["attempt"]["attempt_id"], self.failed["attempt_id"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["inspect", str(self.source), "--attempt", "missing"]), 2)

    def test_large_reports_have_an_explicit_inline_fallback(self):
        self.failed["grading"]["reason"] = "x" * 1_000_000
        summary = compare([self.run])
        with self.assertRaisesRegex(ValueError, "--format html"):
            render_explorer([self.run], summary, self.root, [self.source], inline=True)
        self.assertIn("<!doctype html>", render_explorer([self.run], summary, self.root, [self.source]))

    def test_installed_package_generates_html_without_a_neighboring_checkout(self):
        package = self.directory / "installed-plugin"
        shutil.copytree(PLUGIN, package, ignore=shutil.ignore_patterns("__pycache__"))
        output = self.directory / "standalone"
        process = subprocess.run(
            [sys.executable, "-B", str(package / "scripts/bench.py"), "report", str(self.source),
             "--out", str(output), "--format", "html"],
            cwd=self.directory, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(Path(process.stdout.strip()), (output / "REPORT.html").resolve())
        data = json.loads(ReportParser((output / "REPORT.html").read_text()).data)
        self.assertEqual(len(data["attempts"]), 6)


if __name__ == "__main__":
    unittest.main()
