import unittest

from quality.compare_quick_evals import aggregate, paired_outcomes, wilson_interval


def payload(task, rows, total_cost):
    return {
        "task": task,
        "results": rows,
        "summary": {"total_cost_usd": total_cost},
    }


class QualityCompareTests(unittest.TestCase):
    def test_wilson_interval_contains_observed_rate(self):
        low, high = wilson_interval(9, 10)
        self.assertLess(low, 0.9)
        self.assertGreater(high, 0.9)

    def test_aggregate_includes_errors_in_attempted_accuracy(self):
        rows = [
            {
                "id": "a",
                "correct": True,
                "error": None,
                "latency_ms": 10,
                "output_tokens": 20,
                "reasoning_tokens": 5,
            },
            {
                "id": "b",
                "correct": False,
                "error": {"message": "failed"},
                "latency_ms": None,
                "output_tokens": 0,
                "reasoning_tokens": 0,
            },
        ]
        result = aggregate([payload("task", rows, 0.2)])
        self.assertEqual(result["accuracy"], 0.5)
        self.assertEqual(result["success_only_accuracy"], 1.0)
        self.assertEqual(result["cost_per_success_usd"], 0.2)

    def test_paired_outcomes(self):
        rows_a = [
            {"id": "1", "correct": True, "error": None},
            {"id": "2", "correct": False, "error": None},
        ]
        rows_b = [
            {"id": "1", "correct": True, "error": None},
            {"id": "2", "correct": True, "error": None},
        ]
        result = paired_outcomes(
            payload("task", rows_a, 0),
            payload("task", rows_b, 0),
        )
        self.assertEqual(
            result,
            {"both": 1, "a_only": 0, "b_only": 1, "neither": 0},
        )


if __name__ == "__main__":
    unittest.main()
