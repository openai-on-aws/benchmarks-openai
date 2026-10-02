import unittest
from types import SimpleNamespace

from performance.benchmark import percentile, run_single, summarize


class PerformanceMetricTests(unittest.TestCase):
    def test_percentile_interpolates(self):
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5)

    def test_summarize_ignores_none(self):
        result = summarize([1, None, 3])
        self.assertEqual(result["mean"], 2.0)
        self.assertEqual(result["p50"], 2.0)

    def test_empty_summary(self):
        self.assertIsNone(summarize([None]))

    def test_reasoning_tokens_are_excluded_from_visible_throughput(self):
        usage = SimpleNamespace(
            input_tokens=20,
            output_tokens=15,
            output_tokens_details=SimpleNamespace(reasoning_tokens=5),
            input_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        response = SimpleNamespace(status="completed", usage=usage)
        events = [
            SimpleNamespace(type="response.created"),
            SimpleNamespace(type="response.output_text.delta", delta="one"),
            SimpleNamespace(type="response.output_text.delta", delta=" two"),
            SimpleNamespace(type="response.completed", response=response),
        ]
        client = SimpleNamespace(
            responses=SimpleNamespace(create=lambda **kwargs: iter(events))
        )

        result = run_single(client, "model", "prompt", 20, None)

        self.assertEqual(result["visible_output_tokens"], 10)
        self.assertEqual(result["text_delta_count"], 2)
        self.assertIsNotNone(result["ttfe_ms"])
        self.assertIsNotNone(result["ttft_ms"])
        self.assertIsNotNone(result["reasoning_wait_ms"])
        self.assertIsNotNone(result["otps"])


if __name__ == "__main__":
    unittest.main()
