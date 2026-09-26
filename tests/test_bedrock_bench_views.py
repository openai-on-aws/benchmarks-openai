"""Saved-run library and public tool replay; no model calls or external services."""

import contextlib
import copy
import hashlib
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

from bedrock_bench.activity import activity_data, replay_fragment
from bedrock_bench.cli import demo_experiment, main
from bedrock_bench.engine import execute
from bedrock_bench.library import library_data, reviewed_source, write_library
from bedrock_bench.report import compare


class ViewParser(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.data = {}
        self.current = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attr = dict(attrs)
        if tag == "script" and attr.get("type") == "application/json":
            self.current = attr["id"]
            self.data[self.current] = ""

    def handle_data(self, value):
        if self.current:
            self.data[self.current] += value

    def handle_endtag(self, tag):
        if tag == "script":
            self.current = None


class SavedViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.results = self.directory / "results"
        self.run_dir, self.run = execute(demo_experiment(), self.results)
        self.source = self.run_dir / "run.json"

    def copy_run(self, name, **changes):
        directory = self.results / name
        directory.mkdir()
        run = copy.deepcopy(self.run)
        run.update(run_id=name, **changes)
        source = directory / "run.json"
        source.write_text(json.dumps(run))
        return source, run

    def review(self, mutate=None):
        run = copy.deepcopy(self.run)
        run["name"] += " (reviewed)"
        run["accounting_review"] = {
            "original_run": "run.json", "source": "audit.json",
            "original_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
        }
        run["attempts"][0]["cost_usd"] = .25
        run["attempts"][0]["known_cost_subtotal_usd"] = .25
        run["attempts"][0]["cost_basis"] = ["rate_card_estimate"]
        if mutate:
            mutate(run)
        (self.run_dir / "audit.json").write_text('{"note":"test review"}')
        reviewed = self.run_dir / "run-accounting-reviewed.json"
        reviewed.write_text(json.dumps(run))
        return reviewed

    def trace(self, name="trace-run"):
        source, run = self.copy_run(
            name, synthetic=False, started_at="2026-09-25T00:00:00Z",
            finished_at="2026-09-25T00:00:25Z",
        )
        run["experiment"]["suite"] = "aws-cdk-smoke"
        run["experiment"]["limits"]["timeout_seconds"] = 600
        row = run["attempts"][0]
        run["attempts"] = [row]
        row["attempt_id"] = "attempt-1"
        row["agent_wall_seconds"] = 10
        row["target"].update(id="test-sol", runner="codex", model="openai.gpt-6-sol",
                             provider="amazon-bedrock", region="us-east-1")
        relative = "attempts/attempt-1/upstream/job/trial"
        trial_dir = source.parent / relative
        sessions = trial_dir / "agent/sessions/2026/09/25"
        sessions.mkdir(parents=True)
        result = trial_dir / "result.json"
        result.write_text(json.dumps({
            "agent_execution": {"started_at": "2026-09-25T00:00:10Z",
                                "finished_at": "2026-09-25T00:00:20Z"},
        }))
        row["trace"] = relative + "/result.json"
        row["trace_sha256"] = hashlib.sha256(result.read_bytes()).hexdigest()
        row["upstream"] = {"harness": "harbor", "result": "upstream/job/trial/result.json"}
        row["workspace"] = "attempts/attempt-1"

        def event(second, payload, kind="response_item"):
            return {"type": kind, "timestamp": f"2026-09-25T00:00:{second:02}Z", "payload": payload}

        records = [
            event(10, {"type": "message", "role": "system", "content": [
                {"type": "input_text", "text": "PRIVATE_SYSTEM_CONTENT"}]}),
            event(10, {"type": "reasoning", "text": "PRIVATE_REASONING_CONTENT"}),
            event(10, {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "<environment_context>internal context</environment_context>"}]}),
            event(10, {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Repair the CDK application. Do not deploy resources."}]}),
            event(11, {"type": "function_call", "name": "exec_command", "call_id": "first",
                       "arguments": json.dumps({"cmd": "cat stack.ts"})}),
            event(12, {"type": "function_call_output", "call_id": "first",
                       "output": "Process exited with code 0\nOutput:\nsaved file content"}),
            event(13, {"type": "custom_tool_call", "name": "apply_patch", "call_id": "patch",
                       "input": "*** Begin Patch\n*** End Patch"}),
            event(14, {"type": "custom_tool_call_output", "call_id": "patch", "output": "Patch applied"}),
            event(15, {"type": "function_call", "name": "write_stdin", "call_id": "pending",
                       "arguments": json.dumps({"session_id": 123, "chars": ""})}),
        ]
        session = sessions / "rollout.jsonl"
        session.write_text("\n".join(json.dumps(item) for item in records) + "\n")
        source.write_text(json.dumps(run))
        return source, run, session, records

    def test_library_preserves_accounting_and_compatible_groups(self):
        _, second = self.copy_run("matching")
        self.copy_run("reference", synthetic=False, validation_only=True)
        self.copy_run("different", protocol_hash="different-protocol")
        self.copy_run("interrupted", status="interrupted", attempts=[])
        data = library_data(self.results, replay_limit=0)
        indexed = {run["id"]: run for run in data["runs"]}
        first = indexed[self.run["run_id"]]
        self.assertEqual(len(indexed), 5)
        self.assertEqual(first["comparisonGroup"], indexed["matching"]["comparisonGroup"])
        compare([self.run, second])
        self.assertNotEqual(first["comparisonGroup"], indexed["different"]["comparisonGroup"])
        self.assertNotEqual(first["comparisonGroup"], indexed["reference"]["comparisonGroup"])
        self.assertIsNone(indexed["interrupted"]["comparisonGroup"])
        self.assertIsNone(indexed["interrupted"]["total"]["total_cost_usd"])
        self.assertEqual(indexed["interrupted"]["total"]["attempts"], 0)
        imperfect = next(t for t in first["targets"] if t["target"]["id"] == "fixture-imperfect")
        self.assertEqual(imperfect["successes"], 2)
        self.assertAlmostEqual(imperfect["cost_per_success_usd"], .006)
        self.assertAlmostEqual(imperfect["total_cost_usd"], .012)

    def test_unknown_cost_stays_unknown_in_library(self):
        row = self.run["attempts"][0]
        row.update(cost_usd=None, accounting_complete=False)
        self.source.write_text(json.dumps(self.run))
        total = library_data(self.results, replay_limit=0)["runs"][0]["total"]
        self.assertIsNone(total["total_cost_usd"])
        self.assertIsNone(total["cost_per_success_usd"])
        self.assertAlmostEqual(total["cost_coverage"], 5 / 6)

    def test_partial_attempts_remain_visible_but_not_comparable(self):
        source, _ = self.copy_run("partial", status="interrupted", attempts=self.run["attempts"][:1])
        data = library_data(self.results, replay_limit=0)
        partial = next(run for run in data["runs"] if run["source"] == str(source))
        self.assertEqual(partial["total"]["attempts"], 1)
        self.assertEqual(len(partial["targets"]), 1)
        self.assertIsNone(partial["comparisonGroup"])

    def test_duplicate_and_malformed_runs_do_not_hide_valid_runs(self):
        source, copy_run = self.copy_run("duplicate")
        copy_run["run_id"] = self.run["run_id"]
        source.write_text(json.dumps(copy_run))
        broken = self.results / "broken"
        broken.mkdir()
        (broken / "run.json").write_text("{bad")
        self.copy_run("bad-timestamp", started_at={"invalid": "timestamp"})
        data = library_data(self.results, replay_limit=0)
        self.assertEqual(len(data["runs"]), 1)
        self.assertEqual(len(data["unreadable"]), 3)
        self.assertTrue(any("Duplicate run ID" in item["error"] for item in data["unreadable"]))

    def test_reviewed_accounting_is_used_only_with_matching_provenance(self):
        original = self.source.read_bytes()
        reviewed = self.review()
        data = library_data(self.results, replay_limit=0)
        self.assertFalse(data["warnings"])
        self.assertEqual(data["runs"][0]["source"], str(reviewed))
        self.assertTrue(data["runs"][0]["reviewedAccounting"])
        self.assertEqual(self.source.read_bytes(), original)
        first = next(row for row in data["runs"][0]["attempts"] if row["id"] == self.run["attempts"][0]["attempt_id"])
        self.assertEqual(first["cost"], .25)

        reviewed_data = json.loads(reviewed.read_text())
        reviewed_data["accounting_review"]["original_sha256"] = "incorrect"
        reviewed.write_text(json.dumps(reviewed_data))
        data = library_data(self.results, replay_limit=0)
        self.assertEqual(data["runs"][0]["source"], str(self.source))
        self.assertIn("checksum", data["warnings"][0]["message"])

    def test_review_cannot_change_outcomes_settings_timings_or_audit_path(self):
        mutations = [
            lambda run: run["attempts"][0].update(success=False),
            lambda run: run["attempts"][0].update(wall_seconds=999),
            lambda run: run["attempts"][0]["target"].update(region="elsewhere"),
            lambda run: run.update(protocol_hash="changed"),
            lambda run: run["accounting_review"].update(source="../private.json"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.review(mutate)
                source, _, warning = reviewed_source(self.source)
                self.assertEqual(source, self.source)
                self.assertIsNotNone(warning)

    def test_replay_reads_public_task_tools_and_exact_agent_intervals(self):
        source, _, session, _ = self.trace()
        data = activity_data(source)
        self.assertEqual(len(data["models"]), 1)
        model = data["models"][0]
        self.assertEqual(model["name"], "Sol")
        self.assertEqual(model["agent"], {"start": 10, "end": 20})
        self.assertEqual(len(model["calls"]), 3)
        self.assertEqual(model["prompt"], "Repair the CDK application. Do not deploy resources.")
        self.assertEqual(model["calls"][0]["recordedCallId"], "first")
        self.assertEqual(model["calls"][0]["output"], "saved file content")
        self.assertEqual(model["calls"][0]["exitCode"], 0)
        self.assertEqual(model["calls"][1]["tool"], "apply_patch")
        self.assertFalse(model["calls"][2]["replyRecorded"])
        self.assertIsNone(model["calls"][2]["exitCode"])
        self.assertEqual(model["sessions"][0]["sha256"], hashlib.sha256(session.read_bytes()).hexdigest())
        rendered = json.dumps(data)
        for private in ("PRIVATE_REASONING_CONTENT", "PRIVATE_SYSTEM_CONTENT", "<environment_context>"):
            self.assertNotIn(private, rendered)

    def test_replay_bounds_and_redacts_evidence_before_embedding(self):
        source, _, session, records = self.trace()
        secret = "test-secret-not-for-the-view"
        records[4]["payload"]["arguments"] = json.dumps({"cmd": secret + "x" * 9_000})
        records[5]["payload"]["output"] = secret + "y" * 7_000
        session.write_text("\n".join(json.dumps(item) for item in records) + "\n{malformed\n")
        with patch.dict(os.environ, {"BENCH_TEST_TOKEN": secret}):
            data = activity_data(source)
        call = data["models"][0]["calls"][0]
        self.assertTrue(call["commandClipped"])
        self.assertTrue(call["outputClipped"])
        self.assertIn("[REDACTED]", call["command"])
        self.assertNotIn(secret, json.dumps(data))
        self.assertLessEqual(len(call["command"]), 8_000)
        self.assertLessEqual(len(call["output"]), 6_000)
        self.assertTrue(data["models"][0]["warnings"])
        with patch("bedrock_bench.activity.MAX_SESSION_BYTES", 20):
            bounded = activity_data(source)
        self.assertFalse(bounded["models"])
        self.assertIn("read limit", bounded["unavailable"][0]["reason"])

    def test_missing_or_changed_evidence_never_becomes_a_fake_replay(self):
        source, run, session, _ = self.trace()
        session.unlink()
        data = activity_data(source)
        self.assertFalse(data["models"])
        self.assertIn("No saved Codex sessions", data["unavailable"][0]["reason"])
        result = source.parent / run["attempts"][0]["trace"]
        result.write_text("{}")
        data = activity_data(source)
        self.assertIn("checksum", data["unavailable"][0]["reason"])
        with self.assertRaisesRegex(ValueError, "Expected one attempt"):
            activity_data(source, attempt="not-recorded")

    def test_session_and_result_paths_cannot_escape_run_directory(self):
        source, run, session, _ = self.trace()
        outside = self.directory / "outside.jsonl"
        outside.write_text(session.read_text())
        session.unlink()
        try:
            session.symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks unavailable")
        data = activity_data(source)
        self.assertFalse(data["models"])
        self.assertIn("outside", data["unavailable"][0]["reason"])
        run["attempts"][0]["upstream"]["result"] = "../../../../../outside.jsonl"
        source.write_text(json.dumps(run))
        data = activity_data(source)
        self.assertFalse(data["models"])
        self.assertIn("inside", data["unavailable"][0]["reason"])

    def test_library_embeds_several_runs_with_distinct_replay_context(self):
        first, _, _, _ = self.trace("trace-first")
        second, _, _, _ = self.trace("trace-second")
        data = library_data(self.results)
        self.assertEqual(len(data["activities"]), 2)
        self.assertEqual(data["activities"]["trace-first"]["source"], str(first))
        self.assertEqual(data["activities"]["trace-second"]["source"], str(second))
        self.assertEqual(len(library_data(self.results, replay_limit=1)["activities"]), 1)
        self.assertEqual(len(library_data(self.results, limit=1)["runs"]), 1)
        with self.assertRaises(ValueError):
            library_data(self.results, limit=0)

    def test_hostile_saved_text_cannot_break_library_or_replay_markup(self):
        source, run, session, records = self.trace()
        hostile = '</script><img src=x onerror="alert(1)"><script>'
        run["name"] = hostile
        source.write_text(json.dumps(run))
        records[5]["payload"]["output"] = hostile
        session.write_text("\n".join(json.dumps(item) for item in records))
        data = activity_data(source)
        fragment = replay_fragment(data)
        parsed = ViewParser(fragment)
        self.assertNotIn("img", parsed.tags)
        self.assertEqual(parsed.tags.count("script"), 2)
        self.assertEqual(json.loads(parsed.data["mt-data"])["name"], hostile)
        path = write_library(self.results, self.directory / "library", inline=True)
        library = ViewParser(path.read_text())
        self.assertNotIn("img", library.tags)
        self.assertEqual(library.tags.count("script"), 4)
        self.assertNotIn("<script src=", path.read_text())

    def test_cli_views_never_execute_and_inline_overflow_keeps_full_files(self):
        source, _, _, _ = self.trace()
        for command in (["library", str(self.results)], ["replay", str(source)]):
            with self.subTest(command=command):
                out = self.directory / command[0]
                with patch("bedrock_bench.cli.execute", side_effect=AssertionError("no execution")), \
                        contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(main([*command, "--out", str(out), "--format", "inline"]), 0)
                self.assertTrue(Path(stdout.getvalue().strip()).is_file())
        out = self.directory / "oversize"
        with patch("bedrock_bench.views.MAX_INLINE_BYTES", 10), self.assertRaisesRegex(ValueError, "--format html"):
            write_library(self.results, out, inline=True)
        self.assertTrue((out / "LIBRARY.html").is_file())
        self.assertTrue((out / "LIBRARY.json").is_file())

    def test_installed_package_can_build_library_and_replay_in_isolation(self):
        source, _, _, _ = self.trace()
        installed = self.directory / "installed"
        shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__"))
        for command in (["library", str(self.results)], ["replay", str(source)]):
            process = subprocess.run(
                [sys.executable, "-B", str(installed / "scripts/bench.py"), *command,
                 "--out", str(self.directory / ("installed-" + command[0]))],
                cwd=self.directory, text=True, capture_output=True, timeout=15,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(Path(process.stdout.strip()).is_file())


if __name__ == "__main__":
    unittest.main()
