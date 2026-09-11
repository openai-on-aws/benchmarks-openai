"""Scoring, data isolation, budgets, and agent-state regression checks."""

import copy
from enum import IntEnum
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "quality"))

import arc_agi2
import arc_agi3
from arc_common import BedrockSession, RunLimit, TokenBudget, model_cost
import run_bedrock_arc


def result(text, output=None, status="completed"):
    return {"text": text, "status": status, "output": output or [],
            "estimated_cost_usd": 0.001}


class ArcTests(unittest.TestCase):
    def test_exact_grid_parsing(self):
        self.assertEqual(arc_agi2.parse_grid("```json\n[[1,2],[3,4]]\n```"), [[1, 2], [3, 4]])
        for text in ("[[true]]", "[[1],[2,3]]", "[[10]]", "[[1.0]]", "[]", "[[]]",
                     '{"grid":[[1]]}', "Answer: [[1]]"):
            self.assertIsNone(arc_agi2.parse_grid(text), text)

    def test_test_answers_cannot_leak_into_prompt(self):
        task = {"train": [{"input": [[1]], "output": [[2]]}],
                "test": [{"input": [[3]], "output": [[7, 8, 9]]},
                         {"input": [[4]], "output": [[9, 8, 7]]}],
                "solution": "secret-grading-only"}
        prompt = arc_agi2.prompt_for(task, 0)
        self.assertIn('"test_input":[[3]]', prompt)
        self.assertNotIn("[[7,8,9]]", prompt)
        self.assertNotIn("[[9,8,7]]", prompt)
        self.assertNotIn("secret-grading-only", prompt)

    def test_every_test_input_must_be_solved_without_feedback(self):
        task = {"train": [{"input": [[0]], "output": [[1]]}],
                "test": [{"input": [[2]], "output": [[3]]},
                         {"input": [[4]], "output": [[5]]}]}
        session = Mock()
        session.call.side_effect = [result("[[3]]"), result("[[9]]"),
                                    result("[[0]]"), result("[[5]]")]
        row = arc_agi2.evaluate_task(session, "example", task, 2, lambda _: None)
        self.assertTrue(row["solved"])
        self.assertFalse(row["solved_first_attempt"])
        self.assertEqual(session.call.call_count, 4)  # No early stop on gold match.
        requests = session.call.call_args_list
        self.assertEqual(requests[0].args, requests[1].args)
        self.assertEqual(requests[2].args, requests[3].args)
        session.call.side_effect = [result("[[3]]"), result("[[3]]"),
                                    result("[[0]]"), result("[[0]]")]
        row = arc_agi2.evaluate_task(session, "example", task, 2, lambda _: None)
        self.assertFalse(row["solved"])

    def test_partial_campaign_has_no_overstated_accuracy(self):
        rows = [{"status": "completed", "solved": True, "solved_first_attempt": True}]
        summary = arc_agi2.summarize(rows, 20)
        self.assertEqual(summary["completed_tasks"], 1)
        self.assertIsNone(summary["task_accuracy"])
        self.assertEqual(arc_agi2.summarize(rows, 1)["task_accuracy"], 1.0)

    def test_budget_checks_before_calls_and_preserves_unknown_cost(self):
        budget = TokenBudget(1, lambda i, o: (i + o) / 1000)
        budget.reserve(400, 400)
        self.assertAlmostEqual(budget.settle(100, 100), 0.2)
        with self.assertRaises(RunLimit):
            budget.reserve(500, 500)
        with self.assertRaises(RunLimit):
            TokenBudget(1, lambda i, o: None).reserve(1, 1)
        budget.reserve(100, 100)
        with self.assertRaises(RunLimit):
            budget.reserve(1, 1)

    def test_model_prices_are_region_and_route_specific(self):
        self.assertAlmostEqual(model_cost("mantle", "openai.gpt-oss-20b", "us-west-2", 1000, 1000),
                               0.00037)
        self.assertAlmostEqual(model_cost("mantle", "openai.gpt-5.5-2026-04-23", "us-east-1",
                                         1000, 1000), 0.0385)
        self.assertIsNone(model_cost("mantle", "openai.gpt-oss-20b", "ap-south-1", 1000, 1000))
        self.assertIsNone(model_cost("runtime", "global.openai.gpt-5.6-sol", "us-west-2", 1000, 1000))
        self.assertIsNone(model_cost("mantle", "openai.future-model", "us-west-2", 1000, 1000))

    def test_request_preserves_reasoning_and_explicit_compaction(self):
        args = SimpleNamespace(region="us-west-2", backend="mantle",
            model="openai.gpt-6-astra", effort="low", budget_usd=10,
            input_rate=None, output_rate=None, max_output_tokens=4096, max_input_bytes=100_000)
        usage = SimpleNamespace(input_tokens=100, output_tokens=20,
                                model_dump=lambda **_: {"input_tokens": 100, "output_tokens": 20})
        reasoning = {"type": "reasoning", "id": "rs_test", "encrypted_content": "opaque"}
        output = SimpleNamespace(model_dump=lambda **_: reasoning)
        response = SimpleNamespace(id="resp_test", output_text='{"action":1}',
                                   status="completed", usage=usage, output=[output])
        client = Mock()
        client.with_options.return_value = client
        client.responses.create.return_value = response
        with patch("quick_evals.make_client", return_value=(client, "bedrock")):
            session = BedrockSession(args)
        answer = session.call([{"role": "user", "content": "test"}], compaction_threshold=10000)
        request = client.responses.create.call_args.kwargs
        self.assertFalse(request["store"])
        self.assertIn("reasoning.encrypted_content", request["include"])
        self.assertEqual(request["extra_body"]["context_management"][0]["compact_threshold"], 10000)
        self.assertEqual(answer["output"], [reasoning])
        client.responses.create.reset_mock()
        session.budget.limit = 0.000001
        with self.assertRaises(RunLimit):
            session.call([{"role": "user", "content": "test"}])
        client.responses.create.assert_not_called()

    def test_safeguard_uses_bedrock_chat_and_labels_default_effort(self):
        args = SimpleNamespace(region="us-west-2", backend="mantle",
            model="openai.gpt-oss-safeguard-20b", effort="low", budget_usd=1,
            input_rate=None, output_rate=None, max_output_tokens=4096, max_input_bytes=100_000)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20,
                                model_dump=lambda **_: {"prompt_tokens": 100, "completion_tokens": 20})
        message = SimpleNamespace(content="[[1]]", model_dump=lambda **_: {"content": "[[1]]"})
        response = SimpleNamespace(id="chat_test", usage=usage,
            choices=[SimpleNamespace(message=message, finish_reason="stop")])
        client = Mock()
        client.with_options.return_value = client
        client.chat.completions.create.return_value = response
        with patch("quick_evals.make_client", return_value=(client, "https://bedrock/openai/v1")):
            session = BedrockSession(args)
        answer = session.call([{"role": "user", "content": "test"}])
        self.assertEqual(session.api, "chat_completions")
        self.assertEqual(session.effective_effort, "model_default")
        self.assertEqual(answer["usage"], {"input_tokens": 100, "output_tokens": 20})
        self.assertEqual(answer["status"], "completed")
        request = client.chat.completions.create.call_args.kwargs
        self.assertNotIn("reasoning_effort", request)
        client.responses.create.assert_not_called()
        with self.assertRaises(RunLimit):
            session.call([], compaction_threshold=10000)

    def test_context_only_pruned_at_server_compaction(self):
        history = [{"role": "user", "content": "old"}]
        reasoning = {"type": "reasoning", "encrypted_content": "opaque"}
        arc_agi3.retain_output(history, [reasoning])
        self.assertEqual(history[-1], reasoning)
        self.assertEqual(len(history), 2)
        compact = {"type": "compaction", "encrypted_content": "compact-opaque"}
        message = {"type": "message", "role": "assistant", "content": []}
        arc_agi3.retain_output(history, [compact, message])
        self.assertEqual(history, [compact, {"role": "assistant", "content": ""}])

    def test_mantle_oss_message_normalization_keeps_reasoning(self):
        reasoning = {"type": "reasoning", "summary": []}
        message = {"type": "message", "role": "assistant",
                   "id": "msg_test", "status": "completed",
                   "content": [{"type": "output_text", "text": '{"action":1}', "annotations": []}]}
        history = []
        arc_agi3.retain_output(history, [reasoning, message], plain_messages=True)
        self.assertEqual(history, [reasoning, {"role": "assistant", "content": '{"action":1}'}])
        self.assertEqual(message["id"], "msg_test")  # Original raw evidence is unchanged.

    def test_action_validation_and_public_observation(self):
        frame = SimpleNamespace(frame=[[[0, 1], [2, 3]]],
            state=SimpleNamespace(value="NOT_FINISHED"), levels_completed=0,
            available_actions=[1, 6], solution="never expose this", win_levels=99)
        obs = arc_agi3.observation(frame)
        self.assertNotIn("solution", obs)
        self.assertNotIn("win_levels", obs)
        self.assertEqual(arc_agi3.parse_action('{"action":6,"x":1,"y":0}', obs)["x"], 1)
        for text in ('{"action":true}', '{"action":2}', '{"action":6,"x":-1,"y":0}',
                     '{"action":6,"x":0}', '{"action":1,"x":0}', '[]', 'not json'):
            with self.assertRaises(ValueError, msg=text):
                arc_agi3.parse_action(text, obs)

    def test_play_stops_at_action_limit_and_replays_reasoning(self):
        class Action(IntEnum):
            ACTION1 = 1
            @classmethod
            def from_id(cls, value):
                return cls(value)
        frame = SimpleNamespace(frame=[[[0]]], state=SimpleNamespace(value="NOT_FINISHED"),
                                levels_completed=0, available_actions=[1])
        env = Mock()
        env.environment_info.game_id = "test-123"
        env.reset.return_value = env.step.return_value = frame
        snapshots = []
        session = Mock()
        def call(history, **kwargs):
            snapshots.append(copy.deepcopy(history))
            return result('{"action":1}', [{"type": "reasoning", "encrypted_content": "opaque"}])
        session.call.side_effect = call
        with patch.dict(sys.modules, {"arcengine": SimpleNamespace(GameAction=Action)}):
            row = arc_agi3.play_game(session, env, 2, None, lambda _: None)
        self.assertEqual(row["status"], "action_limit")
        self.assertEqual(row["actions"], 2)
        self.assertEqual(env.step.call_count, 2)
        self.assertTrue(any(item.get("type") == "reasoning" for item in snapshots[1]))

    def test_catalog_deduplicates_routes_without_dropping_models(self):
        catalog = {"regions": ["us-west-2", "us-east-1"], "models": [
            {"backend": "runtime", "region": "us-east-1", "model": "global.openai.gpt-6-astra"},
            {"backend": "runtime", "region": "us-east-1", "model": "us.openai.gpt-6-astra"},
            {"backend": "mantle", "region": "us-west-2", "model": "openai.gpt-6-astra"},
            {"backend": "runtime", "region": "us-west-2", "model": "us.openai.gpt-5.6-sol"},
            {"backend": "mantle", "region": "us-west-2", "model": "openai.gpt-oss-safeguard-20b"},
        ]}
        selected = run_bedrock_arc.select_models(catalog)
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[0]["backend"], "mantle")
        self.assertEqual(len(run_bedrock_arc.select_models(catalog, all_routes=True)), 5)
        self.assertEqual(run_bedrock_arc.select_models(catalog, "us.openai.gpt-6-astra")[0]["backend"],
                         "runtime")
        with self.assertRaises(ValueError):
            run_bedrock_arc.select_models(catalog, "missing")


if __name__ == "__main__":
    unittest.main()
