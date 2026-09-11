"""Offline compatibility tests; no candidate or judge API calls."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "quality", ROOT / "quality/deepsearchqa",
                  ROOT / "performance", ROOT / "parity"):
    sys.path.insert(0, str(directory))

import agentic_evals
import aime_2025
import benchmark
import eval_utils
import gdpval_eval
import gpqa_diamond
import hle
import quick_evals
import report
import run_astra
import run_deepsearchqa
import run_parity


def response(text="Answer: 42", output=()):
    return SimpleNamespace(
        output_text=text, output=list(output), status="completed",
        usage=SimpleNamespace(input_tokens=100, output_tokens=30,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=20)))


def output_item(**data):
    return SimpleNamespace(**data, model_dump=lambda **kwargs: data.copy())


class AstraTests(unittest.TestCase):
    def test_parameters_and_existing_models(self):
        models = [*eval_utils.ASTRA_MODELS.values(), "global.openai.gpt-6-astra"]
        for model in models:
            with self.subTest(model=model):
                options = eval_utils.response_options(model, temperature=0.6, tools=True)
                self.assertEqual(options["reasoning"], {"effort": "low"})
                self.assertNotIn("temperature", options)
                self.assertEqual(options["service_tier"], "default")
                self.assertIn("reasoning.encrypted_content", options["include"])
                for invalid in ("none", "minimal", "typo"):
                    with self.assertRaises(ValueError):
                        eval_utils.resolve_effort(model, invalid)
                for valid in eval_utils.ASTRA_EFFORTS:
                    self.assertEqual(eval_utils.resolve_effort(model, valid), valid)
        self.assertEqual(eval_utils.response_options("gpt-5.4", temperature=0.6), {"temperature": 0.6})
        self.assertEqual(eval_utils.response_options("gpt-5.6-luna", "none"),
                         {"reasoning": {"effort": "none"}})
        self.assertIsNone(eval_utils.resolve_effort("gpt-5.5", None))

    def test_prices_respect_geography_and_context_threshold(self):
        for backend, model, expected in [
            ("saas", "gpt-6-astra", 0.06),
            ("mantle", "openai.gpt-6-astra", 0.066),
            ("runtime", "us.openai.gpt-6-astra", 0.066),
            ("runtime", "global.openai.gpt-6-astra", 0.06),
        ]:
            self.assertAlmostEqual(quick_evals.call_cost_usd(backend, model, 1000, 1000), expected)
        self.assertAlmostEqual(quick_evals.call_cost_usd("saas", "gpt-6-astra", 272000, 1000), 2.77)
        self.assertAlmostEqual(quick_evals.call_cost_usd("saas", "gpt-6-astra", 272001, 1000), 5.51502)
        self.assertIsNone(quick_evals.call_cost_usd("saas", "unknown-model", 1000, 1000))
        self.assertIsNone(eval_utils.sum_costs([1.0, None]))

    def test_quick_and_gdpval_requests(self):
        for model in eval_utils.ASTRA_MODELS.values():
            client = Mock()
            client.responses.create.return_value = response()
            result = quick_evals.call_one(client, model, None, {"prompt": "2+2"}, 1024)
            self.assertIsNone(result["error"])
            self.assertEqual(client.responses.create.call_args.kwargs["reasoning"], {"effort": "low"})
            self.assertEqual(client.responses.create.call_args.kwargs["max_output_tokens"], 1024)
        with patch.object(gdpval_eval, "make_client", return_value=(client, "test")):
            rows, _ = gdpval_eval.generate("saas", "gpt-6-astra", None,
                [{"task_id": "t", "sector": "s", "occupation": "o", "prompt": "write"}])
        self.assertIsNotNone(rows[0]["cost_usd"])

    def test_streaming_latency_request_and_usage(self):
        client = Mock()
        client.responses.create.return_value = [
            SimpleNamespace(type="response.output_text.delta", delta="4"),
            SimpleNamespace(type="response.completed", response=response("4")),
        ]
        result = benchmark.run_single(client, "gpt-6-astra", "2+2", 1024, None)
        request = client.responses.create.call_args.kwargs
        self.assertTrue(request["stream"])
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertEqual(request["max_output_tokens"], 1024)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reasoning_tokens"], 20)
        self.assertIsNotNone(result["ttft_ms"])

    def test_tool_roundtrips_preserve_reasoning_and_count_cost(self):
        reasoning = output_item(type="reasoning", id="rs_test", summary=[], encrypted_content="test-ciphertext")
        call = output_item(type="function_call", name="lookup", call_id="call_test", arguments="{}")
        snapshots = []
        answers = iter([response(output=[reasoning, call]), response("done")])
        def create(**kwargs):
            snapshots.append(copy.deepcopy(kwargs))
            return next(answers)
        client = Mock()
        client.responses.create.side_effect = create
        task = {"goal": "test", "tools": [], "backend": {"lookup": lambda _: "found"},
                "check": lambda text: text == "done"}
        result = agentic_evals.run_trajectory(client, "saas", "gpt-6-astra", None, task)
        self.assertTrue(result["success"])
        self.assertGreater(result["cost_usd"], 0)
        history = snapshots[1]["input"]
        self.assertEqual([item["type"] for item in history[1:]],
                         ["reasoning", "function_call", "function_call_output"])
        self.assertEqual(history[1]["encrypted_content"], "test-ciphertext")

        snapshots.clear()
        answers = iter([response(output=[reasoning, call]), response("done")])
        row = {"problem": "test", "problem_category": "test", "answer_type": "single", "answer": "done"}
        result = run_deepsearchqa.run_case(client, "runtime", "us.openai.gpt-6-astra", None, row, 0)
        self.assertTrue(result["api_success"])
        self.assertEqual(snapshots[1]["input"][2]["type"], "reasoning")
        self.assertEqual(snapshots[1]["input"][3]["type"], "function_call")
        self.assertEqual(snapshots[1]["input"][4]["type"], "function_call_output")

    def test_unknown_trajectory_cost_stays_unknown(self):
        client = Mock()
        client.responses.create.return_value = response("done")
        task = {"goal": "test", "tools": [], "backend": {}, "check": lambda _: True}
        result = agentic_evals.run_trajectory(client, "saas", "unknown-model", None, task)
        self.assertIsNone(result["cost_usd"])

    def test_legacy_cli_runs_select_astra_and_record_effective_settings(self):
        question = {"Question": "Test?", "Correct Answer": "yes", "Incorrect Answer 1": "no",
                    "Incorrect Answer 2": "maybe", "Incorrect Answer 3": "never"}
        for module, dataset in [
            (gpqa_diamond, [question, question]),
            (aime_2025, [{"Year": 2024, "Question": "Test?", "Answer": 42}] * 2),
            (hle, [{"question": "Test?", "answer": "42", "id": "1", "category": "test"}] * 2),
        ]:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as directory:
                client = Mock()
                client.responses.create.return_value = response()
                argv = ["test", "--backend", "runtime", "--model", "us.openai.gpt-6-astra",
                        "--max-questions", "2"]
                if module is not hle:
                    argv += ["--repeats", "1"]
                with patch.object(module, "shared_client", return_value=(client, "test")), \
                     patch.object(module, "load_dataset", return_value=dataset), \
                     patch.object(module, "RESULTS_DIR", directory), patch.object(sys, "argv", argv), \
                     contextlib.redirect_stdout(io.StringIO()):
                    module.main()
                self.assertEqual(client.responses.create.call_count, 2)
                for request in client.responses.create.call_args_list:
                    self.assertEqual(request.kwargs["model"], "us.openai.gpt-6-astra")
                    self.assertEqual(request.kwargs["reasoning"], {"effort": "low"})
                    self.assertNotIn("temperature", request.kwargs)
                data = json.loads(next(Path(directory).glob("*.json")).read_text())
                self.assertEqual(data["reasoning_effort"], "low")
                self.assertIsNone(data["temperature"])
                self.assertEqual(data["n_questions"], 2)
        with patch.dict(os.environ, {"MANTLE_MODEL": "openai.gpt-5.4"}), \
             patch.object(gpqa_diamond, "shared_client", return_value=(client, "test")):
            self.assertEqual(gpqa_diamond.make_client("mantle", "openai.gpt-6-astra")[1], "openai.gpt-6-astra")

    def test_parity_budget_and_sampling(self):
        client = Mock()
        with patch.object(run_parity, "MODEL", "openai.gpt-6-astra"), \
             patch.object(run_parity, "EFFORT", "low"):
            run_parity.create_response(client, model="openai.gpt-6-astra", max_output_tokens=16)
            self.assertEqual(client.responses.create.call_args.kwargs["max_output_tokens"], 2048)
            run_parity.create_response(client, preserve_budget=True, model="openai.gpt-6-astra",
                                       max_output_tokens=16)
            self.assertEqual(client.responses.create.call_args.kwargs["max_output_tokens"], 16)
            with patch.object(run_parity, "record") as record:
                run_parity.test_temperature()
                self.assertIsNone(record.call_args.args[1])

    def test_all_suites_and_backends_in_plan(self):
        for profile in ("smoke", "full"):
            steps = run_astra.build_plan(list(eval_utils.ASTRA_MODELS), list(run_astra.SUITES),
                                        profile, "low", sys.executable, "us.openai.gpt-6-astra")
            self.assertEqual(len(steps), 27)
            self.assertEqual({(step.backend, step.suite) for step in steps},
                             {(b, s) for b in eval_utils.ASTRA_MODELS for s in run_astra.SUITES})
            for step in steps:
                self.assertTrue((ROOT / step.command[1]).is_file())
                self.assertIn(step.model, step.command)
                self.assertEqual(step.command[step.command.index("--effort") + 1], "low")
            agentic = next(step for step in steps if step.suite == "agentic")
            self.assertIn("all", agentic.command)

    def test_plan_only_makes_no_calls(self):
        with patch.object(sys, "argv", ["run_astra.py", "--region", "us-west-2"]), \
             patch.object(run_astra.subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()):
            run_astra.main()
        run.assert_not_called()

    def test_campaign_stops_on_saved_api_errors_despite_zero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "campaign"
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                if "--list-models" in command:
                    return SimpleNamespace(returncode=0, stdout="gpt-6-astra\n")
                folder = Path(kwargs["env"]["BENCHMARK_RESULTS_DIR"])
                (folder / "quickeval_test.json").write_text(json.dumps({"results": [
                    {"error": {"error_message": "denied"}, "status": "error"}]}))
                return SimpleNamespace(returncode=0)
            with patch.object(sys, "argv", ["run_astra.py", "--backends", "saas", "--suites", "quick,gdpval",
                                           "--execute", "--output-dir", str(output)]), \
                 patch.object(run_astra.subprocess, "run", side_effect=run), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit) as error:
                run_astra.main()
            self.assertEqual(error.exception.code, 1)
            self.assertEqual(len(calls), 2)
            state = json.loads((output / "manifest.json").read_text())
            self.assertEqual(state["steps"][0]["status"], "failed")
            self.assertEqual(state["steps"][0]["api_errors"], 1)

    def test_judge_errors_and_incomplete_results_need_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gdpval_test.json"
            path.write_text(json.dumps({"results": [{"status": "incomplete", "error": None}]}))
            checks = run_astra.inspect_results([path])
            self.assertEqual(checks["incomplete"], 1)
            self.assertEqual(checks["api_errors"], 0)
            judged = path.with_name("gdpval_test_judged.json")
            judged.write_text(json.dumps({"results": [{"judgment": {"error": "judge unavailable"}}]}))
            step = SimpleNamespace(suite="gdpval")
            artifacts, errors = run_astra.inspect_judgments(step, [path])
            self.assertEqual(errors, 1)
            self.assertEqual(artifacts, [judged])

    def test_report_keeps_efforts_separate_and_renders_astra(self):
        fixture = next(json.loads(p.read_text()) for p in (ROOT / "performance/results").glob("*.json")
                       if json.loads(p.read_text()).get("schema_version") == 2)
        with tempfile.TemporaryDirectory() as directory:
            for effort in ("low", "high", None):
                data = copy.deepcopy(fixture)
                data.update(model="openai.gpt-6-astra", backend="bedrock", concurrency=1,
                            reasoning_effort=effort, started_at="2026-09-11T00:00:00Z")
                (Path(directory) / f"results_{effort}.json").write_text(json.dumps(data))
            with patch.object(report, "RESULTS_DIR", directory):
                selected = report.load_results("low")
                self.assertEqual(len(selected), 1)
                self.assertTrue(all(d["reasoning_effort"] == "low" for cell in selected.values() for d in cell.values()))
                with patch.object(report, "build_evals_section", return_value=""), \
                     patch.object(report, "build_deepsearchqa_section", return_value=""), \
                     patch.object(report, "build_gdpval_section", return_value=""):
                    markdown, charts = report.build_markdown(selected)
            self.assertIn("gpt-6-astra", markdown)
            self.assertIn("| Reasoning effort | low |", markdown)
            self.assertEqual(set(charts), {"gpt-6-astra"})
            self.assertTrue((Path(directory) / charts["gpt-6-astra"]).is_file())
            self.assertNotIn("## 3. gpt-5.6-luna", markdown)
        self.assertNotEqual(report._short_model_label("gpt-6-astra (Bedrock, effort=low)"),
                            report._short_model_label("gpt-6-astra (Bedrock, effort=high)"))


if __name__ == "__main__":
    unittest.main()
