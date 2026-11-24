"""Evidence summaries must describe saved, matched attempts without inferring causes.

All fixtures are synthetic and local. These tests exercise the public dashboard
builder, rather than reproducing the implementation's aggregation algorithm.
"""
import copy
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dashboard = load_module("dashboard_evidence_under_test", PLUGIN / "scripts/dashboard.py")
existing_tests = load_module("dashboard_evidence_browser_support", Path(__file__).with_name("test_dashboard_metrics.py"))


def fixtures():
    """Six attempts, two requests each; optional failure never changes success."""
    checks = [
        {"id": "actions", "question": "Were all required actions included?", "required": True},
        {"id": "amount", "question": "Was the amount correct?", "required": True},
        {"id": "tone", "question": "Was the optional tone preference met?", "required": False},
    ]
    config = {
        "document": {"checks": checks, "datasets": [{"provenance": "synthetic", "label_status": "unreviewed"}],
                     "runner": {"selection": {"cost_metric": "cost_usd_per_success"}}},
        "grading": {
            "actions": {"type": "json_set_equals", "path": "actions", "expected_key": "answer.actions"},
            "amount": {"type": "json_equals", "path": "amount", "expected_key": "answer.amount"},
            "tone": {"type": "json_equals", "path": "tone", "expected_key": "answer.tone"}},
        "datasets": {"heldout": [{"id": case, "expected": {"answer": {
            "actions": ["explain", "reconcile"], "amount": 100, "tone": "brief"}},
            "metadata": {"label_status": "unreviewed"}} for case in ("case-a", "case-b", "case-c")]},
        "splits": {"holdout": ["heldout"]}, "prompt": "Return actions and amount.",
    }
    groups = {}
    for name, revised in (("target_original", False), ("target_selected", True)):
        rows = []
        for index in range(6):
            case, repeat = ("case-a", "case-b", "case-c")[index // 2], index % 2
            action_pass = revised or index not in (0, 1)
            amount_pass = index != 2
            answer = {"actions": ["explain", "reconcile"] if action_pass else ["reconcile"],
                      "amount": 100 if amount_pass else 90, "tone": "wordy" if index == 4 else "brief"}
            grades = {
                "actions": {"pass": action_pass, "reason": "Action set matches" if action_pass else "Missing required action",
                            "path": "actions", "missing_values": [] if action_pass else ["explain"],
                            "missing_count": 0 if action_pass else 1},
                "amount": {"pass": amount_pass, "reason": "Amount matches" if amount_pass else "Amount differs"},
                "tone": {"pass": index != 4, "reason": "Optional preference"},
            }
            def call(n, input_tokens, output_tokens, reasoning_tokens):
                return {"status": "completed", "model_requested": "model-sol", "model_returned": "model-sol",
                        "request_id": f"{name}-{index}-{n}", "usage": {
                            "input_tokens": input_tokens, "output_tokens": output_tokens,
                            "total_tokens": input_tokens + output_tokens,
                            "output_tokens_details": {"reasoning_tokens": reasoning_tokens}}}
            rows.append({"id": f"{name}-{case}-{repeat}", "case_id": case, "repeat": repeat,
                         "model_id": "target", "checks": grades, "result": {
                             "status": "completed", "model_requested": "model-sol", "outputs": [json.dumps(answer)],
                             "turn_records": [{"user": f"Evaluate complete input for {case}", "output": json.dumps(answer)}],
                             "timing": {"conversation_ms": 1000 + index},
                             "route_calls": [call(0, 100 + index, (8 if revised else 10) + index, 3 + index),
                                             call(1, 20, 5, 2)]}})
        groups[name] = {"trials": rows, "score": {
            "prompt": "Compute actions independently; return actions and amount." if revised else config["prompt"],
            "accuracy": (5 if revised else 3) / 6, "n_sessions": 6,
            "cost_usd_per_session": .012 if revised else .01,
            "cost_usd_per_success": .0144 if revised else .02,
            "unpriced_requests": 0, "latency_scope": "conversation", "latency_p50_s": 1, "latency_p95_s": 1.1}}
    source = copy.deepcopy(groups["target_original"])
    for row in source["trials"]:
        row["id"] = "source-" + row["id"]
        row["model_id"] = "source"
        row["result"]["model_requested"] = "model-astra"
        for call in row["result"]["route_calls"]:
            call["model_requested"] = call["model_returned"] = "model-astra"
    groups = {"source_original": source, **groups}
    report = {"backend": "agentcore", "simulated": False, "evaluation_status": "complete",
              "unresolved_attempts": [], "grading_error_records": 0, "holdout": groups,
              "optimization": {"generations": []}, "selected_prompt": {
                  "prompt": groups["target_selected"]["score"]["prompt"], "selection": "validation only",
                  "reason": "Selected on validation", "policy": {"cost_metric": "cost_usd_per_success"}}}
    return report, config


class PromptEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="prompt-evidence-")
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)

    def build(self, report=None, config=None, *, with_config=True):
        if report is None:
            report, config = fixtures()
        if config is not None:
            config = copy.deepcopy(config)
            config.pop("fingerprint", None)
            config["fingerprint"] = hashlib.sha256(json.dumps(
                config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            report["fingerprint"] = config["fingerprint"]
        path = self.root / "report.json"
        path.write_text(json.dumps(report))
        frozen = self.root / "run-input.json"
        if with_config:
            frozen.write_text(json.dumps(config))
        elif frozen.exists():
            frozen.unlink()
        original = path.read_bytes()
        result = dashboard.build_data(self.root)
        self.assertEqual(path.read_bytes(), original, "A display build must not rewrite run evidence")
        return result

    def evidence(self, data):
        pairs = data["reports"][0]["prompt_evidence"]
        return next(p for p in pairs if p["before_id"] == "target_original" and p["after_id"] in ("target_selected", "target_challenger"))

    def browser(self, data, actions=""):
        # Reuse the established DOM stub without changing another agent's file.
        capture = '\n$("insight").innerHTML = $("prompt-evidence").innerHTML + $("prompt-example-body-report-1-0").innerHTML;'
        return existing_tests.DashboardMetricTests.browser_probe(self, data, actions + capture)

    def test_complete_usage_sums_requests_before_averaging_attempts(self):
        evidence = self.evidence(self.build())
        self.assertTrue(evidence["available"], evidence["issues"])
        before, after = evidence["usage"]["before"], evidence["usage"]["after"]
        self.assertEqual(before["attempts"], 6)
        self.assertEqual(before["input_tokens"], {"total": 735, "mean": 122.5, "covered_attempts": 6})
        self.assertEqual(before["output_tokens"], {"total": 105, "mean": 17.5, "covered_attempts": 6})
        self.assertEqual(before["reasoning_tokens"], {"total": 45, "mean": 7.5, "covered_attempts": 6})
        self.assertEqual(after["output_tokens"]["mean"], 15.5)
        self.assertNotEqual(before["output_tokens"]["mean"], 25, "Reasoning is already part of output, not an extra charge")

    def test_missing_usage_never_becomes_zero_or_partial_mean(self):
        for mutation in ("missing_counter", "missing_call_usage", "no_calls", "invalid_counter"):
            with self.subTest(mutation=mutation):
                report, config = fixtures();result = report["holdout"]["target_selected"]["trials"][0]["result"]
                if mutation == "missing_counter":result["route_calls"][1]["usage"].pop("output_tokens")
                elif mutation == "missing_call_usage":result["route_calls"][1].pop("usage")
                elif mutation == "no_calls":result["route_calls"] = []
                else:result["route_calls"][1]["usage"]["output_tokens"] = -1
                counts = self.evidence(self.build(report, config))["usage"]["after"]["output_tokens"]
                self.assertIsNone(counts["mean"])
                self.assertIsNone(counts["total"])
                self.assertEqual(counts["covered_attempts"], 5)

    def test_required_and_optional_checks_keep_separate_denominators(self):
        evidence = self.evidence(self.build());criteria = {c["id"]: c for c in evidence["criteria"]}
        self.assertEqual(evidence["required_check_count"], 2)
        self.assertTrue(criteria["actions"]["required"])
        self.assertFalse(criteria["tone"]["required"])
        self.assertEqual(criteria["actions"]["before"], {"passed": 4, "failed": 2, "unknown": 0, "total": 6})
        self.assertEqual(criteria["actions"]["after"], {"passed": 6, "failed": 0, "unknown": 0, "total": 6})
        self.assertEqual(criteria["actions"]["failure_change"], 2)
        self.assertEqual(evidence["outcomes"], {"before_passes": 3, "after_passes": 5, "attempts": 6,
                                               "improved": 2, "regressed": 0, "both_passed": 3, "both_failed": 1})

    def test_missing_optional_grade_is_visible_without_blocking_required_comparison(self):
        report, config = fixtures();report["holdout"]["target_selected"]["trials"][0]["checks"].pop("tone")
        evidence = self.evidence(self.build(report, config))
        self.assertTrue(evidence["available"], evidence["issues"])
        tone = next(c for c in evidence["criteria"] if c["id"] == "tone")
        self.assertEqual(tone["after"], {"passed": 4, "failed": 1, "unknown": 1, "total": 6})
        self.assertEqual(evidence["outcomes"]["after_passes"], 5)

    def test_missing_or_nonboolean_required_grade_blocks_improvement_claim(self):
        for bad in (None, "true", 1):
            with self.subTest(bad=bad):
                report, config = fixtures();row = report["holdout"]["target_selected"]["trials"][0]
                row["checks"]["actions"]["pass"] = bad
                evidence = self.evidence(self.build(report, config))
                self.assertFalse(evidence["available"])
                actions = next(c for c in evidence["criteria"] if c["id"] == "actions")
                self.assertEqual(actions["after"]["unknown"], 1)
                self.assertTrue(evidence["issues"])

    def test_matching_requires_cases_repeats_models_inputs_and_distinct_attempts(self):
        for mutation in ("case", "repeat", "model", "input", "duplicate", "count", "missing_prompt", "identical_prompt"):
            with self.subTest(mutation=mutation):
                report, config = fixtures();group = report["holdout"]["target_selected"];row = group["trials"][0]
                if mutation == "case":row["case_id"] = "another-case"
                elif mutation == "repeat":row["repeat"] = 7
                elif mutation == "model":row["result"]["route_calls"][0]["model_requested"] = "other-model"
                elif mutation == "input":row["result"]["turn_records"][0]["user"] = "Different customer request"
                elif mutation == "duplicate":group["trials"][1] = copy.deepcopy(row)
                elif mutation == "count":group["score"]["n_sessions"] = 7
                elif mutation == "missing_prompt":group["score"].pop("prompt")
                else:group["score"]["prompt"] = report["holdout"]["target_original"]["score"]["prompt"]
                pairs = self.build(report, config)["reports"][0]["prompt_evidence"]
                self.assertFalse(any(p["available"] for p in pairs))

    def test_report_score_must_match_required_check_outcomes(self):
        report, config = fixtures();report["holdout"]["target_selected"]["score"]["accuracy"] = 1
        evidence = self.evidence(self.build(report, config))
        self.assertFalse(evidence["available"])
        self.assertTrue(evidence["issues"])

    def test_unverified_required_definitions_cannot_support_a_claim(self):
        for mutation in ("missing_manifest", "duplicate_check", "no_required", "missing_definition"):
            with self.subTest(mutation=mutation):
                report, config = fixtures()
                if mutation == "duplicate_check":config["document"]["checks"].append(copy.deepcopy(config["document"]["checks"][0]))
                elif mutation == "no_required":
                    for c in config["document"]["checks"]:c["required"] = False
                elif mutation == "missing_definition":config["document"].pop("checks")
                evidence = self.evidence(self.build(report, config, with_config=mutation != "missing_manifest"))
                self.assertFalse(evidence["available"])

    def test_example_is_deterministic_and_points_to_actual_failure_and_success(self):
        report, config = fixtures();data = self.build(report, config);evidence = self.evidence(data)
        example = evidence["example"]
        self.assertEqual((example["case_id"], example["repeat"], example["focus_check_id"]), ("case-a", 0, "actions"))
        self.assertEqual(example["before_trial_id"], "target_original-case-a-0")
        self.assertEqual(example["after_trial_id"], "target_selected-case-a-0")
        self.assertEqual(len(evidence["examples"]), 6)
        for group in report["holdout"].values():group["trials"].reverse()
        self.assertEqual(self.evidence(self.build(report, config))["example"], example)
        self.assertEqual(evidence["prompt_diff"]["format"], "unified")
        self.assertIn("-Return actions and amount.", evidence["prompt_diff"]["text"])
        self.assertIn("+Compute actions independently", evidence["prompt_diff"]["text"])

    def test_regressions_remain_counted_alongside_improvements(self):
        report, config = fixtures();g = report["holdout"]["target_selected"]
        g["trials"][5]["checks"]["amount"]["pass"] = False;g["score"]["accuracy"] = 4 / 6
        evidence = self.evidence(self.build(report, config))
        self.assertTrue(evidence["available"], evidence["issues"])
        self.assertEqual(evidence["outcomes"]["improved"], 2)
        self.assertEqual(evidence["outcomes"]["regressed"], 1)
        self.assertEqual(sum(evidence["outcomes"][k] for k in ("improved", "regressed", "both_passed", "both_failed")), 6)
        self.assertIn("regressed", {e["outcome"] for e in evidence["examples"]})

    def test_no_improvement_uses_a_truthful_fallback_example(self):
        report, config = fixtures();before = report["holdout"]["target_original"];after = report["holdout"]["target_selected"]
        for a, b in zip(before["trials"], after["trials"]):b["checks"] = copy.deepcopy(a["checks"])
        after["score"]["accuracy"] = before["score"]["accuracy"]
        evidence = self.evidence(self.build(report, config))
        self.assertTrue(evidence["available"], evidence["issues"])
        self.assertEqual(evidence["outcomes"]["improved"], 0)
        selected = evidence["example"]
        outcome = next(e["outcome"] for e in evidence["examples"] if e["before_trial_id"] == selected["before_trial_id"])
        self.assertIn(outcome, ("both_failed", "both_passed"))

    def test_retained_original_can_show_measured_unselected_candidate(self):
        report, config = fixtures();h = report["holdout"]
        h["target_challenger"] = h.pop("target_selected")
        h["target_selected"] = dict(copy.deepcopy(h["target_original"]), reused_from="target_original")
        report["selected_prompt"].update(prompt=config["prompt"], reason="Tradeoff; original retained")
        evidence = self.evidence(self.build(report, config))
        self.assertTrue(evidence["available"], evidence["issues"])
        self.assertEqual(evidence["after_id"], "target_challenger")
        self.assertEqual(len(self.build(report, config)["reports"][0]["prompt_evidence"]), 1)

    def test_fixed_prompt_does_not_invent_optimization_evidence(self):
        report, config = fixtures();report.pop("optimization")
        report["holdout"]["target_selected"] = dict(copy.deepcopy(report["holdout"]["target_original"]), reused_from="target_original")
        report["selected_prompt"].update(prompt=config["prompt"], reason="Baseline only")
        self.assertEqual(self.build(report, config)["reports"][0]["prompt_evidence"], [])

    def test_group_level_model_sets_cannot_hide_different_models_on_a_pair(self):
        report, config = fixtures()
        before = report['holdout']['target_original']['trials']
        after = report['holdout']['target_selected']['trials']
        before[0]['result']['route_calls'][1]['model_requested'] = 'other-model'
        after[2]['result']['route_calls'][1]['model_requested'] = 'other-model'
        evidence = self.evidence(self.build(report, config))
        self.assertFalse(evidence['available'], 'Equal group-wide model lists do not prove that each case used the same model')

    def test_absent_input_histories_cannot_be_treated_as_matching_empty_lists(self):
        for turns in ([], [{'user': None}], [{'output': 'User input was not recorded'}]):
            with self.subTest(turns=turns):
                report, config = fixtures()
                for group in ('target_original', 'target_selected'):
                    report['holdout'][group]['trials'][0]['result']['turn_records'] = copy.deepcopy(turns)
                evidence = self.evidence(self.build(report, config))
                self.assertFalse(evidence['available'], 'Missing input evidence is not proof that the requests matched')

    def test_ui_shows_both_costs_tokens_and_required_check_denominators(self):
        data = self.build()
        view = self.browser(data)
        text = view['insight']
        self.assertEqual(data['reports'][0]['dataset_summary'], 'Results from synthetic demo cases.')
        self.assertIn('Results from synthetic demo cases.', view['dataset_notice'])
        self.assertEqual(view['provenance'], data['provenance'])
        self.assertNotIn(data['reports'][0]['provenance'], text)
        for expected in ('Mean output tokens (includes reasoning)', '17.5', '15.5',
                         'Model cost per attempt', '$0.010000', '$0.012000',
                         'Model cost per successful result', '$0.020000', '$0.014400',
                         '3/6 (50.0%)', '5/6 (83.3%)', '(required)', '(optional)',
                         '2 paired attempts changed from failing to passing',
                         '0 changed from passing to failing', '6/6 attempts'):
            self.assertIn(expected, text)
        switched = self.browser(data, '$("cost-metric").onchange({target:{value:"cost_usd_per_session"}});')
        self.assertEqual(switched['insight'], text, 'Changing chart cost units must not hide either prompt cost')
        self.assertNotIn('Evaluate complete input for case-a', text, 'Case packets should be lazy, not repeated in the initial DOM')

    def test_ui_lazy_example_is_actual_saved_pair_and_all_pairs_remain_browsable(self):
        data = self.build()
        action = '$("prompt-example-report-1-0").open = true; $("prompt-example-report-1-0").ontoggle();'
        first = self.browser(data, action)['insight']
        for expected in ('target_original-case-a-0', 'target_selected-case-a-0',
                         'Missing required action', 'Missing values', 'explain',
                         'Expected answer', 'unreviewed', 'Full input shared by both attempts',
                         'not an explanation of what caused the aggregate change'):
            self.assertIn(expected, first)
        self.assertIn('value="5"', first)
        last = self.browser(data, action + '$("prompt-example-select-report-1-0").onchange({target:{value:"5"}});')['insight']
        self.assertIn('target_selected-case-c-1', last)
        self.assertNotIn('Attempt target_selected-case-a-0', last)

    def test_ui_default_example_explains_why_that_pair_was_chosen(self):
        data = self.build()
        view = self.browser(data, '$("prompt-example-report-1-0").open=true; $("prompt-example-report-1-0").ontoggle();')
        self.assertIn('required check with the largest reduction in failures', view['insight'])
        self.assertIn('does not establish which prompt edit caused the change', view['insight'])

    def test_ui_prompt_links_open_full_prompt_from_summary_and_worked_example(self):
        data = self.build()
        action = '$("prompt-evidence").querySelectorAll("[data-prompt-link]").find(link => link.dataset.promptLink.endsWith("target_selected")).click();'
        view = self.browser(data, action)
        self.assertEqual(view['visible_views'], ['integrity'])
        self.assertEqual(view['scrolled_to'], 'prompt-report-1-target_selected')
        self.assertEqual(view['focused'], 'prompt-report-1-target_selected')
        action = ('$("prompt-example-report-1-0").open=true; $("prompt-example-report-1-0").ontoggle();'
                  '$("prompt-example-body-report-1-0").querySelectorAll("[data-prompt-link]")'
                  '.find(link => link.dataset.promptLink.endsWith("source_original")).click();')
        view = self.browser(data, action)
        self.assertEqual(view['visible_views'], ['integrity'])
        self.assertEqual(view['scrolled_to'], 'prompt-report-1-source_original')

    def test_ui_escapes_case_prompt_reference_output_and_grader_text(self):
        report, config = fixtures()
        attack = '</script><img src=x onerror="unsafe()">'
        for case in config['datasets']['heldout']:
            case['expected']['answer']['note'] = attack
            case['id'] += attack
        config['document']['checks'][0]['question'] += attack
        for name, group in report['holdout'].items():
            group['score']['prompt'] += attack
            for row in group['trials']:
                row['case_id'] += attack
                row['result']['turn_records'][0]['user'] += attack
                row['result']['outputs'] = [attack]
                row['checks']['actions']['reason'] += attack
        data = self.build(report, config)
        action = '$("prompt-example-report-1-0").open=true; $("prompt-example-report-1-0").ontoggle();'
        view = self.browser(data, action)
        self.assertNotIn(attack, view['insight'])
        self.assertNotIn('<img src=x', view['insight'])
        self.assertIn('&lt;/script&gt;&lt;img', view['insight'])
        output = self.root / 'dashboard.html'
        response = dashboard.render_dashboard(self.root, output)
        self.assertEqual(response['fresh_model_calls'], 0)
        self.assertEqual(response['fresh_aws_calls'], 0)
        self.assertNotIn(attack, output.read_text())
        self.assertIn('\\u003c/script\\u003e', output.read_text())

    def test_ui_unselected_candidate_and_regressions_are_not_hidden(self):
        report, config = fixtures();h = report['holdout'];h['target_challenger'] = h.pop('target_selected')
        h['target_challenger']['trials'][5]['checks']['amount']['pass'] = False
        h['target_challenger']['score']['accuracy'] = 4/6
        h['target_selected'] = dict(copy.deepcopy(h['target_original']), reused_from='target_original')
        report['selected_prompt'].update(prompt=config['prompt'], reason='Tradeoff; original retained')
        text = self.browser(self.build(report, config))['insight']
        self.assertIn('candidate was evaluated but was not selected', text)
        self.assertIn('1 changed from passing to failing', text)
        self.assertNotIn('optimizer chose this revised prompt', text)

    def test_ui_legacy_and_unavailable_evidence_keep_existing_summary(self):
        data = self.build()
        for value in (None, []):
            with self.subTest(value=value):
                legacy = copy.deepcopy(data)
                if value is None:legacy['reports'][0].pop('prompt_evidence')
                else:legacy['reports'][0]['prompt_evidence'] = value
                self.assertEqual(self.browser(legacy)['insight'], '')
                original_view = existing_tests.DashboardMetricTests.browser_probe(self, legacy)
                self.assertIn('Prompt optimization', original_view['insight'])
        report, config = fixtures();report['holdout']['target_selected']['trials'][0]['checks'].pop('actions')
        incomplete = self.build(report, config)
        self.assertEqual(self.browser(incomplete)['insight'], '')
        self.assertIn('Prompt optimization', existing_tests.DashboardMetricTests.browser_probe(self, incomplete)['insight'])

    def test_ui_missing_output_usage_is_unknown_with_explicit_coverage(self):
        report, config = fixtures();report['holdout']['target_selected']['trials'][0]['result']['route_calls'][0]['usage'].pop('output_tokens')
        text = self.browser(self.build(report, config))['insight']
        self.assertIn('revised prompt 5/6 attempts', text)
        self.assertIn('Missing usage is unknown, not zero', text)
        self.assertNotIn('<td>15.5</td>', text)


    def test_incomplete_or_simulated_run_cannot_claim_verified_improvement(self):
        for mutation in ('unfinished', 'simulated', 'unresolved', 'missing_ledger', 'grading_errors'):
            with self.subTest(mutation=mutation):
                report, config = fixtures()
                if mutation == 'unfinished':report['evaluation_status'] = 'running'
                elif mutation == 'simulated':report['simulated'] = True
                elif mutation == 'unresolved':report['unresolved_attempts'] = [{'id': 'pending'}]
                elif mutation == 'missing_ledger':report.pop('unresolved_attempts')
                else:report['grading_error_records'] = 1
                self.assertFalse(self.evidence(self.build(report, config))['available'])

    def test_grading_error_is_unknown_not_a_failed_application_answer(self):
        report, config = fixtures()
        report['holdout']['target_selected']['trials'][0]['checks']['actions'].update(
            {'pass': False, 'error': 'grader unavailable'})
        evidence = self.evidence(self.build(report, config))
        actions = next(c for c in evidence['criteria'] if c['id'] == 'actions')
        self.assertEqual(actions['after'], {'passed': 5, 'failed': 0, 'unknown': 1, 'total': 6})
        self.assertIsNone(actions['failure_change'])
        self.assertFalse(evidence['available'])

    def test_missing_reasoning_counter_does_not_invent_zero_or_hide_complete_output(self):
        report, config = fixtures()
        report['holdout']['target_original']['trials'][0]['result']['route_calls'][0]['usage'].pop('output_tokens_details')
        usage = self.evidence(self.build(report, config))['usage']['before']
        self.assertEqual(usage['output_tokens']['mean'], 17.5)
        self.assertIsNone(usage['reasoning_tokens']['mean'])
        self.assertIsNone(usage['reasoning_tokens']['total'])
        self.assertEqual(usage['reasoning_tokens']['covered_attempts'], 5)

    def test_ui_incomplete_latency_and_unknown_cost_stay_unknown(self):
        report, config = fixtures()
        score = report['holdout']['target_selected']['score']
        score['timing_metrics'] = {'conversation_ms': {'observations': 5}}
        score['unpriced_requests'] = 1
        text = self.browser(self.build(report, config))['insight']
        self.assertIn('P95 conversation time</th><td>1.100 s</td><td>Unknown</td>', text)
        self.assertIn('Model cost per attempt</th><td>$0.010000</td><td>Unknown</td>', text)
        self.assertIn('Model cost per successful result</th><td>$0.020000</td><td>Unknown</td>', text)

    def test_ui_selection_survives_close_reopen_and_rendered_prompt_links_rebind(self):
        data = self.build()
        actions = ('$("prompt-example-report-1-0").open=true; $("prompt-example-report-1-0").ontoggle();'
                   '$("prompt-example-select-report-1-0").onchange({target:{value:"5"}});'
                   '$("prompt-example-report-1-0").open=false; $("prompt-example-report-1-0").ontoggle();'
                   '$("cost-metric").onchange({target:{value:"cost_usd_per_session"}});'
                   '$("prompt-example-report-1-0").open=true; $("prompt-example-report-1-0").ontoggle();')
        view = self.browser(data, actions)
        self.assertIn('Attempt target_selected-case-c-1', view['insight'])
        view = self.browser(data, actions + '$("prompt-example-body-report-1-0").querySelectorAll("[data-prompt-link]").find(link => link.dataset.promptLink.endsWith("target_selected")).click();')
        self.assertEqual(view['visible_views'], ['integrity'])
        self.assertEqual(view['focused'], 'prompt-report-1-target_selected')


    def test_tampered_manifest_cannot_promote_unverified_evidence(self):
        self.build()
        frozen = self.root / 'run-input.json'
        config = json.loads(frozen.read_text())
        config['document']['checks'][0]['question'] = 'Changed after the run'
        frozen.write_text(json.dumps(config))
        data = dashboard.build_data(self.root)
        self.assertEqual(data['reports'][0]['frozen_input_status'], 'unverified')
        evidence = self.evidence(data)
        self.assertFalse(evidence['available'])
        self.assertIsNone(evidence['example'])
        self.assertEqual(evidence['examples'], [])


    def test_returned_model_contradictions_block_comparison_but_legacy_absence_is_allowed(self):
        for mutation in ('contradictory_returned_model', 'legacy_missing_returned_model'):
            with self.subTest(mutation=mutation):
                report, config = fixtures()
                rows = report['holdout']['target_selected']['trials']
                if mutation == 'contradictory_returned_model':
                    rows[0]['result']['route_calls'][0]['model_returned'] = 'different-model-version'
                else:
                    for row in rows:
                        for call in row['result']['route_calls']:call.pop('model_returned')
                evidence = self.evidence(self.build(report, config))
                self.assertEqual(evidence['available'], mutation == 'legacy_missing_returned_model', evidence['issues'])

    def test_ui_null_or_errored_optional_checks_are_unknown_in_paired_and_ordinary_detail(self):
        report, config = fixtures()
        report['holdout']['source_original']['trials'][0]['checks']['tone'] = None
        report['holdout']['target_original']['trials'][0]['checks']['tone'] = None
        report['holdout']['target_selected']['trials'][0]['checks']['tone'] = {
            'pass': True, 'error': 'grader unavailable', 'reason': 'Optional grader did not finish'}
        data = self.build(report, config)
        self.assertTrue(self.evidence(data)['available'])
        action = '$("prompt-example-report-1-0").open=true; $("prompt-example-report-1-0").ontoggle();'
        view = self.browser(data, action)
        self.assertEqual(len(re.findall(r'Unknown</span>\s*<strong>Tone</strong>', view['insight'])), 2)
        self.assertIn('grader unavailable', view['insight'])
        self.assertRegex(view['detail'], r'Unknown</span>\s*<strong>tone</strong>')


if __name__ == '__main__':
    unittest.main()
