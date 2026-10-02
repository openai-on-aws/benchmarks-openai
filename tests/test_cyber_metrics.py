import unittest
from pathlib import Path

from quality.cyber.cybersoc_eval import (
    bedrock_output_config,
    bedrock_response_fields,
    estimate_cost,
    is_refusal,
    option_letters,
    parse_answer,
    report_path_for,
    safe_error,
    selection_metrics,
    stratified_sample,
)
from quality.cyber.compare_cybersoc import compare_payloads
from quality.cyber.summarize_inspect import (
    classify_sample,
    estimate_usage_cost,
    render_markdown,
    summarize_payloads,
)


class CyberMetricTests(unittest.TestCase):
    def test_exact_multi_select(self):
        metrics = selection_metrics(["B", "A"], ["A", "B"])
        self.assertTrue(metrics["exact"])
        self.assertEqual(metrics["jaccard"], 1.0)
        self.assertEqual(metrics["f1"], 1.0)

    def test_partial_credit(self):
        metrics = selection_metrics(["A", "C"], ["A", "B"])
        self.assertFalse(metrics["exact"])
        self.assertEqual(metrics["jaccard"], 1 / 3)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)

    def test_strict_json_parser(self):
        self.assertEqual(
            parse_answer('{"correct_answers":["A","C"]}', ["A", "B", "C"]),
            ["A", "C"],
        )
        self.assertIsNone(parse_answer("A and C", ["A", "B", "C"]))
        self.assertIsNone(
            parse_answer('{"correct_answers":["A","A"]}', ["A", "B", "C"])
        )

    def test_option_letters(self):
        self.assertEqual(option_letters(["A. one", "B) two"]), ["A", "B"])

    def test_sampling_is_seeded_and_bounded(self):
        items = [
            {"id": index, "attack": f"a{index % 2}", "difficulty": "medium"}
            for index in range(20)
        ]
        first = stratified_sample(items, 7, 42)
        second = stratified_sample(items, 7, 42)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 7)

    def test_long_context_price_switch(self):
        short = estimate_cost(
            "mantle", "openai.gpt-daybreak-blue-5.6-sol", 100_000, 0, 0
        )
        long = estimate_cost(
            "mantle", "openai.gpt-daybreak-blue-5.6-sol", 300_000, 0, 0
        )
        self.assertEqual(short, 0.55)
        self.assertEqual(long, 3.3)

    def test_errors_redact_api_keys(self):
        error = RuntimeError("authorization: Bearer token-value sk-secret")
        message = safe_error(error)["error_message"]
        self.assertNotIn("token-value", message)
        self.assertNotIn("sk-secret", message)

    def test_bedrock_output_schema_uses_valid_letters(self):
        config = bedrock_output_config(["A", "C"])
        schema = config["textFormat"]["structure"]["jsonSchema"]["schema"]
        self.assertIn('"enum":["A","C"]', schema)

    def test_bedrock_response_normalization(self):
        response = {
            "output": {
                "message": {
                    "content": [{"text": '{"correct_answers":["B"]}'}]
                }
            },
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": 10,
                "outputTokens": 3,
                "cacheReadInputTokens": 5,
                "cacheWriteInputTokens": 2,
            },
            "ResponseMetadata": {
                "HTTPHeaders": {
                    "x-amzn-requestid": "request-id",
                    "authorization": "must-not-be-copied",
                }
            },
        }
        result = bedrock_response_fields(response, ["A", "B"])
        self.assertEqual(result["predicted"], ["B"])
        self.assertEqual(result["input_tokens"], 17)
        self.assertEqual(result["cached_tokens"], 5)
        self.assertEqual(result["cache_write_tokens"], 2)
        self.assertEqual(
            result["provider_details"]["headers"],
            {"x-amzn-requestid": "request-id"},
        )

    def test_refusal_detection_uses_policy_or_text(self):
        self.assertTrue(is_refusal("", "content_filtered"))
        self.assertTrue(is_refusal("I cannot assist with that request."))
        self.assertFalse(is_refusal('{"correct_answers":["A"]}', "end_turn"))

    def test_runtime_claude_prices(self):
        fable = estimate_cost(
            "runtime", "us.anthropic.claude-fable-5", 1_000_000, 0, 0
        )
        opus = estimate_cost(
            "runtime", "us.anthropic.claude-opus-4-8", 1_000_000, 0, 0
        )
        self.assertEqual(fable, 11.0)
        self.assertEqual(opus, 5.5)

    def test_cache_write_cost_is_not_counted_as_uncached_input(self):
        cost = estimate_cost(
            "mantle",
            "openai.gpt-5.6-cyber",
            input_tokens=1_000_000,
            cached_tokens=200_000,
            output_tokens=0,
            cache_write_tokens=300_000,
        )
        self.assertEqual(cost, 12.30625)

    def test_report_path_rejects_traversal(self):
        with self.assertRaises(ValueError):
            report_path_for(
                Path("/tmp/reports"),
                {"attack": "../outside", "sha256": "a" * 64},
            )


class InspectSummaryTests(unittest.TestCase):
    def sample(self, *, score="I", events=None, error=None, usage=None):
        return {
            "id": "task-1",
            "epoch": 1,
            "scores": {"includes": {"value": score}} if score else {},
            "events": events or [],
            "error": error,
            "turn_count": 2,
            "total_time": 10.0,
            "working_time": 8.0,
            "model_usage": usage or {},
        }

    def payload(self, model, samples):
        return {
            "eval": {
                "model": model,
                "task": "inspect_evals/cybench",
                "task_args": {"variant_names": "hard"},
            },
            "stats": {
                "started_at": "2026-09-01T00:00:00Z",
                "completed_at": "2026-09-01T00:01:00Z",
            },
            "samples": samples,
        }

    def test_inspect_cost_includes_all_four_token_categories(self):
        usage = {
            "input_tokens": 100,
            "input_tokens_cache_read": 200,
            "input_tokens_cache_write": 300,
            "output_tokens": 400,
        }
        cost = estimate_usage_cost(
            "openai/bedrock/gpt-5.6-cyber",
            usage,
        )
        expected = (
            100 * 13.75
            + 200 * 1.375
            + 300 * 17.1875
            + 400 * 82.5
        ) / 1_000_000
        self.assertEqual(cost, round(expected, 8))

    def test_setup_and_limit_failures_are_distinct(self):
        setup = self.sample(
            score=None,
            error={"message": "validation error"},
        )
        setup["turn_count"] = 0
        limited = self.sample(
            events=[
                {"event": "model"},
                {"event": "sample_limit", "type": "turn"},
            ]
        )
        self.assertEqual(classify_sample(setup), "setup_error")
        self.assertEqual(classify_sample(limited), "sample_limit")

    def test_summary_excludes_setup_error_from_capability_denominator(self):
        model = "openai/bedrock/gpt-daybreak-blue-5.6-sol"
        setup = self.sample(
            score=None,
            error={"message": "bad endpoint"},
        )
        setup["turn_count"] = 0
        success = self.sample(
            score="C",
            events=[{"event": "model"}, {"event": "tool"}],
            usage={
                model: {
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "input_tokens_cache_read": 0,
                    "input_tokens_cache_write": 0,
                }
            },
        )
        report = summarize_payloads([self.payload(model, [setup, success])])
        arm = report["arms"][0]
        self.assertEqual(arm["n_attempted"], 2)
        self.assertEqual(arm["n_capability_evaluable"], 1)
        self.assertEqual(arm["capability_success_rate"], 1.0)
        self.assertEqual(arm["failure_taxonomy"]["setup_error"], 1)
        self.assertIn("Cost/success", render_markdown(report))


class CyberSOCComparisonTests(unittest.TestCase):
    def payload(self, model, jaccard, sample_id="sample-1"):
        return {
            "benchmark": "CyberSOCEval malware_analysis",
            "backend": "mantle",
            "model": model,
            "input_profile": "full",
            "max_output_tokens": 2048,
            "structured_output": False,
            "seed": 42,
            "dataset": {"commit": "commit"},
            "summary": {"estimated_total_cost_usd": 1.0},
            "results": [
                {
                    "id": sample_id,
                    "exact": jaccard == 1.0,
                    "jaccard": jaccard,
                    "f1": jaccard,
                    "latency_ms": 100,
                    "error": None,
                    "refusal": False,
                    "status": "completed",
                }
            ],
        }

    def test_comparison_is_paired_and_protocol_checked(self):
        report = compare_payloads(
            [
                self.payload("model-a", 1.0),
                self.payload("model-b", 0.5),
            ]
        )
        self.assertEqual(report["pairwise"][0]["a_jaccard_wins"], 1)
        self.assertEqual(report["arms"][0]["estimated_cost_per_exact_usd"], 1.0)

        incompatible = self.payload("model-c", 0.0, sample_id="different")
        with self.assertRaisesRegex(ValueError, "sample_ids"):
            compare_payloads([self.payload("model-a", 1.0), incompatible])


if __name__ == "__main__":
    unittest.main()
