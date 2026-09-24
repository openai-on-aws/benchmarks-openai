"""Offline behavioral tests for task economics. No model calls or account access."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/bedrock-bench"
sys.path.insert(0, str(PLUGIN / "scripts"))

from bedrock_bench.cli import demo_experiment, main
from bedrock_bench.config import Experiment, Limits, Target
from bedrock_bench.costs import charge, estimate, normalize_usage, validate_rate_card
from bedrock_bench.engine import execute
from bedrock_bench.native import openrouter_loop, responses_loop
from bedrock_bench.report import aggregate, compare
from bedrock_bench.runners import command_for, parse_result, run_process
from bedrock_bench.tasks import TASK_IDS, execute_tool, make_task


def rate(**changes):
    return {
        "provider": "openai", "model": "test-model", "region": None,
        "service_tier": "default", "as_of": "2026-09-24",
        "source": "https://example.com/fictional-test-rates",
        "input_usd_per_million": 2, "cached_input_usd_per_million": 0.5,
        "cache_write_input_usd_per_million": 3, "output_usd_per_million": 10,
        **changes,
    }


def events(*rows, code=0, timed_out=False):
    return {"stdout": "".join(json.dumps(row) + "\n" for row in rows), "stderr": "",
            "exit_code": code, "timed_out": timed_out, "wall_seconds": 2.0}


def codex_usage():
    return {"input_tokens": 1000, "cached_input_tokens": 400,
            "output_tokens": 100, "reasoning_output_tokens": 60}


class AccountingTests(unittest.TestCase):
    def test_cached_and_reasoning_tokens_are_not_double_charged(self):
        usage = normalize_usage("codex", codex_usage())
        self.assertAlmostEqual(estimate(usage, rate()), 0.0024)
        self.assertEqual(usage["output_tokens"], 100)
        self.assertEqual(usage["reasoning_output_tokens"], 60)

    def test_opencode_subsets_are_normalized_before_pricing(self):
        raw = {"input": 600, "output": 40, "reasoning": 60, "cache": {"read": 400, "write": 50}}
        usage = normalize_usage("opencode", raw)
        self.assertEqual(usage["input_tokens"], 1050)
        self.assertEqual(usage["output_tokens"], 100)
        self.assertAlmostEqual(estimate(usage, rate()), 0.00255)
        self.assertEqual(charge("opencode", raw, usage, rate(), emitted_cost=0.07),
                         {"usd": 0.07, "basis": "runner_estimate"})

    def test_openrouter_account_charge_is_not_added_to_upstream_cost(self):
        raw = {"prompt_tokens": 1000, "completion_tokens": 100,
               "prompt_tokens_details": {"cached_tokens": 400},
               "cost": 0.009, "cost_details": {"upstream_inference_cost": 0.02}}
        usage = normalize_usage("openrouter", raw)
        self.assertEqual(charge("openrouter", raw, usage, rate())["usd"], 0.009)
        raw["cost"] = 0
        self.assertEqual(charge("openrouter", raw, usage, rate())["usd"], 0)

    def test_zero_opencode_catalog_estimate_requires_explicit_prices(self):
        raw = {"input": 600, "output": 40, "reasoning": 60, "cache": {"read": 400, "write": 0}}
        usage = normalize_usage("opencode", raw)
        self.assertIsNone(charge("opencode", raw, usage, None, emitted_cost=0)["usd"])
        self.assertAlmostEqual(charge("opencode", raw, usage, rate(), emitted_cost=0)["usd"], 0.0024)
        free = rate(input_usd_per_million=0, cached_input_usd_per_million=0, output_usd_per_million=0)
        self.assertEqual(charge("opencode", raw, usage, free, emitted_cost=0),
                         {"usd": 0, "basis": "rate_card_estimate"})

    def test_missing_cost_and_unsupported_price_band_are_unknown(self):
        usage = normalize_usage("codex", codex_usage())
        self.assertIsNone(estimate(usage, None))
        self.assertIsNone(estimate(usage, rate(max_input_tokens=900)))
        no_cache_rate = rate()
        del no_cache_rate["cached_input_usd_per_million"]
        self.assertIsNone(estimate(usage, no_cache_rate))
        self.assertIsNone(estimate(normalize_usage("responses", {"input_tokens": 100, "output_tokens": 10}), rate()))
        self.assertIsNone(charge("responses", {}, usage, rate(), served_tier="priority")["usd"])

    def test_invalid_usage_and_rates_do_not_create_savings(self):
        for raw in [
            {"input_tokens": -1, "cached_input_tokens": 0, "output_tokens": 5},
            {"input_tokens": 10, "cached_input_tokens": 20, "output_tokens": 5},
            {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": float("nan")},
        ]:
            self.assertIsNone(estimate(normalize_usage("codex", raw), rate()))
        for change in [{"input_usd_per_million": -1}, {"output_usd_per_million": float("inf")},
                       {"source": "http://example.com"}, {"as_of": "invalid"}]:
            with self.assertRaises((ValueError, TypeError)):
                validate_rate_card(rate(**change))

    def test_failed_attempt_spend_is_included_in_cost_per_success(self):
        rows = [
            {"success": True, "cost_usd": 1, "known_cost_subtotal_usd": 1, "wall_seconds": 2,
             "cost_basis": ["rate_card_estimate"], "status": "completed"},
            {"success": False, "cost_usd": 3, "known_cost_subtotal_usd": 3, "wall_seconds": 4,
             "cost_basis": ["rate_card_estimate"], "status": "task_failed"},
        ]
        result = aggregate(rows)
        self.assertEqual(result["cost_per_success_usd"], 4)
        self.assertEqual(result["cost_per_attempt_usd"], 2)
        rows[1]["cost_usd"] = None
        result = aggregate(rows)
        self.assertIsNone(result["cost_per_success_usd"])
        self.assertEqual(result["known_cost_subtotal_usd"], 4)
        self.assertEqual(result["cost_coverage"], 0.5)
        rows[0]["success"] = False
        self.assertIsNone(aggregate(rows)["cost_per_success_usd"])


class TaskTests(unittest.TestCase):
    def test_every_task_grades_correct_artifacts_and_rejects_wrong_ones(self):
        for task_id in TASK_IDS:
            with self.subTest(task_id=task_id), tempfile.TemporaryDirectory() as temp:
                task = make_task(task_id, 42)
                workspace = Path(temp) / "workspace"
                task.prepare(workspace)
                (workspace / "answer.json").write_text(json.dumps(task.demonstration_answer()))
                self.assertTrue(task.grade(workspace)["success"])
                (workspace / "answer.json").write_text("{}")
                self.assertFalse(task.grade(workspace)["success"])

    def test_fixture_seed_and_expected_data_are_reproducible(self):
        task = make_task("invoice-reconciliation", 7)
        self.assertEqual(task.files, make_task(task.id, 7).files)
        self.assertNotEqual(task.files, make_task(task.id, 8).files)
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "task"
            task.prepare(workspace)
            (workspace / "invoices.json").write_text("[]")
            (workspace / "answer.json").write_text('{"total_outstanding_cents":0}')
            self.assertFalse(task.grade(workspace)["success"])

    def test_workspace_boundaries_and_artifact_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            task = make_task("inference-triage", 0)
            workspace = base / "workspace"
            task.prepare(workspace)
            outside = base / "outside.json"
            outside.write_text(json.dumps(task.expected))
            self.assertIn("error", execute_tool(workspace, "read_file", {"path": "../outside.json"}))
            self.assertIn("error", execute_tool(workspace, "read_file", {"path": str(outside)}))
            (workspace / "answer.json").symlink_to(outside)
            self.assertFalse(task.grade(workspace)["success"])
            self.assertIn("error", execute_tool(workspace, "write_file", {"path": "answer.json", "content": "{}"}))
            self.assertEqual(json.loads(outside.read_text()), task.expected)
            self.assertIn("error", execute_tool(workspace, "write_file", {"path": "attempts.json", "content": "[]"}))

    def test_all_valid_deployment_orders_are_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            task = make_task("deployment-order", 0)
            workspace = Path(temp) / "task"
            task.prepare(workspace)
            valid = {"deployment_order": ["identity", "network", "queue", "database", "worker", "api", "frontend"]}
            (workspace / "answer.json").write_text(json.dumps(valid))
            self.assertTrue(task.grade(workspace)["success"])
            valid["deployment_order"].reverse()
            (workspace / "answer.json").write_text(json.dumps(valid))
            self.assertFalse(task.grade(workspace)["success"])


class AdapterTests(unittest.TestCase):
    def test_codex_success_and_partial_timeout(self):
        process = events(
            {"type": "item.completed", "item": {"type": "command_execution"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
            {"type": "turn.completed", "usage": codex_usage()},
        )
        row = parse_result("codex", process, rate())
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["reported_tool_events"], 1)
        self.assertEqual(row["final_text"], "done")
        self.assertAlmostEqual(row["cost_usd"], 0.0024)
        process["timed_out"] = True
        partial = parse_result("codex", process, rate())
        self.assertEqual(partial["status"], "timeout")
        self.assertIsNone(partial["cost_usd"])
        self.assertAlmostEqual(partial["known_cost_subtotal_usd"], 0.0024)

    def test_opencode_deduplicates_part_events_and_marks_catalog_estimate(self):
        step = {"type": "step_finish", "part": {"id": "s1", "reason": "stop", "cost": 0.015,
                "tokens": {"input": 300, "output": 10, "reasoning": 20, "cache": {"read": 200, "write": 0}}}}
        row = parse_result("opencode", events(step, step, {"type": "text", "part": {"id": "text1", "text": "done"}}))
        self.assertEqual(row["cost_usd"], 0.015)
        self.assertEqual(row["usage"]["input_tokens"], 500)
        self.assertEqual(row["usage"]["output_tokens"], 30)
        self.assertEqual(row["reported_usage_events"], 1)
        self.assertEqual(row["cost_basis"], ["runner_estimate"])

    def test_missing_terminal_and_provider_error_preserve_unknown_spend(self):
        self.assertEqual(parse_result("codex", events())["status"], "incomplete")
        process = events({"type": "step", "source": "openrouter",
                          "usage": {"cost": 0.01, "prompt_tokens": 5, "completion_tokens": 2}},
                         {"type": "error", "error": "request timed out"}, code=1)
        row = parse_result("native", process)
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(row["known_cost_subtotal_usd"], 0.01)
        self.assertEqual(row["status"], "runner_error")

    def test_native_turn_limit_retains_complete_observed_charge(self):
        row = parse_result("native", events(
            {"type": "step", "source": "openrouter", "usage": {"cost": 0.02}},
            {"type": "error", "error": "Turn limit", "accounting_complete": True},
        ))
        self.assertEqual(row["status"], "runner_error")
        self.assertEqual(row["cost_usd"], 0.02)

    def test_commands_use_specific_model_and_task_permissions(self):
        workspace = Path("/tmp/task")
        command, _ = command_for(Target("a", "codex", "amazon-bedrock", "exact-model",
                                        region="us-west-2"), Limits(), workspace, Path("/tmp/worker.json"))
        self.assertIn("--ignore-user-config", command)
        self.assertIn("workspace-write", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertIn('model_provider="amazon-bedrock"', command)
        command, env = command_for(Target("a", "opencode", "openrouter", "openai/exact-model"),
                                   Limits(max_turns=4), workspace, Path("/tmp/worker.json"))
        self.assertIn("openrouter/openai/exact-model", command)
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        agent = config["agent"]["bedrock-bench"]
        self.assertEqual(agent["steps"], 4)
        self.assertEqual(agent["permission"]["*"], "deny")
        self.assertEqual(agent["permission"]["edit"], "allow")

    def test_process_timeout_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as temp:
            code = 'import time; print("started",flush=True); time.sleep(5)'
            row = run_process([sys.executable, "-c", code], os.environ.copy(), temp, "", 0.15)
            self.assertTrue(row["timed_out"])
            self.assertIn("started", row["stdout"])
            self.assertLess(row["wall_seconds"], 3)
            with patch.dict(os.environ, {"BENCH_TEST_API_KEY": "a-secret-for-test-only"}):
                code = 'import os; print(os.environ["BENCH_TEST_API_KEY"])'
                row = run_process([sys.executable, "-c", code], os.environ.copy(), temp, "", 2)
            self.assertEqual(row["stdout"].strip(), "[REDACTED]")


def item(**data):
    return SimpleNamespace(**data, model_dump=lambda **kwargs: data.copy())


class NativeLoopTests(unittest.TestCase):
    def test_responses_loop_replays_reasoning_and_executes_real_artifact_tool(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            usage = item(input_tokens=100, output_tokens=30, input_tokens_details={"cached_tokens": 0})
            thought = item(type="reasoning", id="r1", encrypted_content="opaque")
            call = item(type="function_call", name="write_file", call_id="c1",
                        arguments=json.dumps({"path": "answer.json", "content": '{"done":true}'}))
            response1 = SimpleNamespace(id="response1", model="test", usage=usage, status="completed",
                                        output=[thought, call], output_text="")
            response2 = SimpleNamespace(id="response2", model="test", usage=usage, status="completed",
                                        output=[], output_text="done")
            requests = []
            def create(**kwargs):
                requests.append(copy.deepcopy(kwargs))
                return response1 if len(requests) == 1 else response2
            client = SimpleNamespace(responses=SimpleNamespace(create=create))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                responses_loop(client, Target("a", "native", "openai", "test", reasoning_effort="low"),
                               Limits(), "do task", workspace)
            self.assertEqual(json.loads((workspace / "answer.json").read_text()), {"done": True})
            self.assertTrue(any(row.get("encrypted_content") == "opaque" for row in requests[1]["input"]))
            self.assertTrue(any(row.get("type") == "function_call_output" for row in requests[1]["input"]))
            self.assertEqual(requests[0]["max_output_tokens"], 2048)
            self.assertFalse(requests[0]["store"])
            self.assertIn('"type": "completed"', stream.getvalue())

    def test_openrouter_tool_history_and_reported_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            fn = SimpleNamespace(name="write_file", arguments='{"path":"answer.json","content":"{}"}')
            call = SimpleNamespace(id="tc1", function=fn)
            first = SimpleNamespace(tool_calls=[call], content=None, model_dump=lambda **kw: {
                "role": "assistant", "tool_calls": [{"id": "tc1", "type": "function",
                    "function": {"name": fn.name, "arguments": fn.arguments}}], "reasoning_details": [{"id": "r"}]})
            last = SimpleNamespace(tool_calls=[], content="done", model_dump=lambda **kw: {"role": "assistant", "content": "done"})
            responses = [
                SimpleNamespace(id="1", model="test", provider="provider-a", usage=item(cost=0.01),
                                choices=[SimpleNamespace(message=first, finish_reason="tool_calls")]),
                SimpleNamespace(id="2", model="test", provider="provider-a", usage=item(cost=0.02),
                                choices=[SimpleNamespace(message=last, finish_reason="stop")]),
            ]
            requests = []
            def create(**kwargs):
                requests.append(copy.deepcopy(kwargs))
                return responses[len(requests) - 1]
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                openrouter_loop(client, Target("a", "native", "openrouter", "test", routing=["provider-a"]),
                                Limits(), "do task", Path(temp))
            self.assertFalse(requests[0]["extra_body"]["provider"]["allow_fallbacks"])
            self.assertIn("reasoning_details", requests[1]["messages"][2])
            process = {"stdout": stream.getvalue(), "stderr": "", "exit_code": 0, "timed_out": False, "wall_seconds": 1}
            row = parse_result("native", process)
            self.assertAlmostEqual(row["cost_usd"], 0.03)
            self.assertEqual(row["upstream_providers"], ["provider-a"])


class WorkflowTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Fake CLI executable uses a POSIX shebang")
    def test_controller_runs_cli_process_and_grades_written_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            bin_dir = base / "bin"
            bin_dir.mkdir()
            expected = make_task("deployment-order", 42).demonstration_answer()
            event = {"type": "turn.completed", "usage": codex_usage()}
            executable = bin_dir / "codex"
            executable.write_text(
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                "if '--version' in sys.argv:\n"
                "    print('codex fixture-1'); sys.exit(0)\n"
                "prompt = sys.stdin.read()\n"
                "assert 'services.json' in prompt\n"
                f"pathlib.Path('answer.json').write_text({json.dumps(json.dumps(expected))})\n"
                f"print({json.dumps(json.dumps(event))})\n"
            )
            executable.chmod(0o755)
            experiment = Experiment(
                name="CLI fixture integration", targets=[Target("a", "codex", "openai", "test-model")],
                tasks=["deployment-order"], rate_cards=[rate()],
            )
            with patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}):
                root, run = execute(experiment, base / "runs", allow_live=True)
            row = run["attempts"][0]
            self.assertTrue(row["success"])
            self.assertEqual(row["runner_version"], "codex fixture-1")
            self.assertAlmostEqual(row["cost_usd"], 0.0024)
            self.assertTrue((root / row["trace"]).read_text().strip())

    def test_demo_produces_traceable_results_and_rejects_mismatched_comparisons(self):
        with tempfile.TemporaryDirectory() as temp:
            root, run = execute(demo_experiment(), temp)
            self.assertEqual(len(run["attempts"]), 6)
            self.assertEqual(sum(row["success"] for row in run["attempts"]), 5)
            self.assertTrue((root / "REPORT.md").is_file())
            self.assertIn("Synthetic demonstration", (root / "REPORT.md").read_text())
            for row in run["attempts"]:
                self.assertTrue((root / row["trace"]).is_file())
            result = compare([run])
            imperfect = next(row for row in result["targets"] if row["target"]["id"] == "fixture-imperfect")
            self.assertAlmostEqual(imperfect["cost_per_success_usd"], 0.006)
            for change in [{"run_id": run["run_id"]}, {"run_id": "other", "protocol_hash": "wrong"},
                           {"run_id": "other", "synthetic": False}, {"run_id": "other", "status": "interrupted"}]:
                with self.assertRaises(ValueError):
                    compare([run, {**run, **change}])

    def test_live_execution_gate_and_invalid_configuration(self):
        data = demo_experiment().to_dict()
        data["targets"] = [{"id": "a", "runner": "native", "provider": "openai", "model": "test"}]
        experiment = Experiment.from_dict(data)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "--execute"):
                execute(experiment, temp)
            config = Path(temp) / "experiment.json"
            config.write_text(json.dumps(data))
            with contextlib.redirect_stdout(io.StringIO()), patch("bedrock_bench.cli.execute") as execution:
                self.assertEqual(main(["run", str(config)]), 0)
                execution.assert_not_called()
        with self.assertRaises(ValueError):
            Limits(timeout_seconds=float("nan"))
        with self.assertRaises(ValueError):
            Target("a", "native", "bedrock-runtime", "test")
        with self.assertRaises(ValueError):
            Target("a", "codex", "openrouter", "test")
        data["rate_cards"] = [rate(), rate()]
        with self.assertRaises(ValueError):
            Experiment.from_dict(data)

    def test_packaged_cli_works_without_neighboring_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "plugin"
            shutil.copytree(PLUGIN, package, ignore=shutil.ignore_patterns("__pycache__"))
            process = subprocess.run(
                [sys.executable, str(package / "scripts/bench.py"), "demo", "--out", str(Path(temp) / "results")],
                cwd=temp, text=True, capture_output=True, timeout=15,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(Path(process.stdout.strip()).is_file())


if __name__ == "__main__":
    unittest.main()
