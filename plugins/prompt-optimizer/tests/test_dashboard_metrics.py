"""Saved cost preferences and frozen reference evidence reach the actual dashboard."""
import hashlib
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_metrics", PLUGIN / "scripts/dashboard.py")
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


def example_report(backend="local", metric="cost_usd_per_success"):
    groups = {}
    for key, correct, price in (("source_original", 8, .02), ("target_original", 10, .022)):
        groups[key] = {
            "score": {"accuracy": correct / 10, "n_sessions": 10,
                      "cost_usd_per_session": price, "cost_usd_per_success": price * 10 / correct,
                      "unpriced_requests": 0, "latency_scope": "conversation",
                      "latency_p50_s": 3, "latency_p95_s": 4},
            "trials": [{"id": key + str(i), "case_id": "invoice-1", "repeat": i,
                        "model_id": key, "result": {"status": "completed", "outputs": ['{"total":10}'],
                            "turn_records": [{"user": "Extract the invoice"}],
                            "timing": {"conversation_ms": 3000}},
                        "checks": {"total": {"pass": i < correct}}} for i in range(10)]}
    return {"backend": backend, "simulated": False, "fingerprint": "matching-study",
            "evaluation_status": "complete", "unresolved_attempts": [], "grading_error_records": 0,
            "holdout": groups, "selected_prompt": {"policy": {"cost_metric": metric}}}


def optimization_report(backend="local", *, original_correct=9, selected_correct=10,
                        original_cost=.02, selected_cost=.015,
                        original_p95=4, selected_p95=3):
    value = example_report(backend)
    source = value['holdout']['source_original']
    source['score']['prompt'] = 'original instructions'
    original = value['holdout']['target_original']
    original['score'].update(prompt='original instructions', accuracy=original_correct / 10,
                             cost_usd_per_session=original_cost,
                             cost_usd_per_success=original_cost * 10 / original_correct if original_correct else None,
                             latency_p95_s=original_p95)
    selected = copy.deepcopy(original)
    selected['score'].update(prompt='selected instructions', accuracy=selected_correct / 10,
                             cost_usd_per_session=selected_cost,
                             cost_usd_per_success=selected_cost * 10 / selected_correct if selected_correct else None,
                             latency_p95_s=selected_p95)
    for group, correct, identity in [(source, 8, 'source-model'),
                                      (original, original_correct, 'target-model'),
                                      (selected, selected_correct, 'target-model')]:
        for index, row in enumerate(group['trials']):
            row['id'] = group['score']['prompt'] + identity + str(index)
            row['model_id'] = identity
            row['result']['model_requested'] = identity
            row['checks']['total']['pass'] = index < correct
    value['holdout']['target_selected'] = selected
    value['selected_prompt'].update(prompt='selected instructions', selection='validation only',
                                    reason='max_generations reached')
    return value


def challenger_report(backend="local", **scores):
    """Actual engine layout when validation retains the starting prompt."""
    value = optimization_report(backend, **scores)
    candidate = value['holdout'].pop('target_selected')
    candidate['score']['prompt'] = 'candidate instructions'
    value['holdout']['target_challenger'] = candidate
    value['holdout']['target_selected'] = dict(copy.deepcopy(value['holdout']['target_original']),
                                             reused_from='target_original')
    value['optimization'] = {'generations': []}
    value['selected_prompt'].update(prompt='original instructions', selection='validation only',
        reason='Tradeoffs need customer review; incumbent retained', challenger={
            'prompt': 'candidate instructions', 'sha256': hashlib.sha256(b'candidate instructions').hexdigest(),
            'selected': False, 'selection': 'Final validation generation only; no holdout outcomes used',
            'reason': 'Lowest-cost quality-eligible candidate retained for tradeoff reporting; not promoted over the incumbent.'})
    return value


class DashboardMetricTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dashboard-metrics-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def save(self, value):
        path = self.root / value["backend"] / "report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def freeze(self, value, *, metric="cost_usd_per_success", expected=None):
        if expected is None:
            expected = {"answer": {"total": 10}, "reference": '{"total":10}'}
        config = {"document": {"runner": {"selection": {"cost_metric": metric}},
                    "datasets": [{"provenance": "customer", "label_status": "unreviewed"}]},
                  "datasets": {"holdout": [{"id": "invoice-1", "expected": expected,
                    "metadata": {"label_status": "unreviewed"}}]}}
        config["fingerprint"] = hashlib.sha256(json.dumps(
            config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        value["fingerprint"] = config["fingerprint"]
        path = self.save(value)
        frozen = path.parent / "run-input.json"
        frozen.write_text(json.dumps(config))
        return frozen, config

    def test_unequal_success_rates_preserve_both_costs_and_preference(self):
        self.save(example_report())
        data = dashboard.build_data(self.root)
        self.assertEqual(data["default_cost_metric"], "cost_usd_per_success")
        groups = data["reports"][0]["groups"]
        self.assertLess(groups[0]["cost_metrics"]["cost_usd_per_session"]["value"],
                        groups[1]["cost_metrics"]["cost_usd_per_session"]["value"])
        self.assertGreater(groups[0]["cost_metrics"]["cost_usd_per_success"]["value"],
                           groups[1]["cost_metrics"]["cost_usd_per_success"]["value"])
        self.assertEqual(data["reports"][0]["cost_metric_source"], "selection_policy")

    def test_zero_successes_and_missing_usage_are_different(self):
        value = example_report()
        source = value["holdout"]["source_original"]["score"]
        source.update(accuracy=0, cost_usd_per_success=None)
        target = value["holdout"]["target_original"]["score"]
        target.update(unpriced_requests=1, cost_usd_per_session=.022, cost_usd_per_success=.022)
        self.save(value)
        groups = dashboard.build_data(self.root)["reports"][0]["groups"]
        self.assertEqual(groups[0]["cost_metrics"]["cost_usd_per_success"], {"value": None, "status": "no_successes"})
        for metric in dashboard.COST_METRICS:
            self.assertEqual(groups[1]["cost_metrics"][metric], {"value": None, "status": "unknown"})
        self.assertEqual(groups[0]["cost_metrics"]["cost_usd_per_session"]["value"], .02)

    def test_mixed_backend_policies_require_an_explicit_chart_choice(self):
        self.save(example_report())
        self.save(example_report("agentcore", "cost_usd_per_session"))
        data = dashboard.build_data(self.root)
        self.assertTrue(data["cost_metric_conflict"])
        self.assertIsNone(data["default_cost_metric"])
        self.assertEqual({r["cost_metric"] for r in data["reports"]}, set(dashboard.COST_METRICS))

    def test_verified_manifest_supplies_expected_answers_and_fallback_policy(self):
        value = example_report()
        value.pop("selected_prompt")
        frozen, config = self.freeze(value)
        data = dashboard.build_data(self.root)
        run = data["reports"][0]
        self.assertEqual(run["cost_metric"], "cost_usd_per_success")
        self.assertEqual(run["cost_metric_source"], "frozen_input")
        trial = run["groups"][0]["trials"][0]
        self.assertTrue(trial["expected_available"])
        self.assertEqual(trial["expected_answer"], {"total": 10})
        self.assertEqual(trial["reference_metadata"]["label_status"], "unreviewed")
        config["datasets"]["holdout"][0]["expected"]["answer"]["total"] = 999
        frozen.write_text(json.dumps(config))
        run = dashboard.build_data(self.root)["reports"][0]
        self.assertEqual(run["cost_metric_source"], "legacy_default")
        self.assertFalse(run["groups"][0]["trials"][0]["expected_available"])
        self.assertIsNone(run["groups"][0]["trials"][0]["expected_answer"])

    def test_saved_outputs_never_become_expected_answers(self):
        self.save(example_report())
        trial = dashboard.build_data(self.root)["reports"][0]["groups"][0]["trials"][0]
        self.assertFalse(trial["expected_available"])
        self.assertIsNone(trial["expected_answer"])

    def test_explicit_report_file_uses_its_own_frozen_input_and_ignores_siblings(self):
        value = example_report()
        self.freeze(value)
        other = example_report("agentcore")
        other['fingerprint'] = 'different-study'
        self.save(other)
        selected = self.root / "local/report.json"
        data = dashboard.build_data(selected)
        self.assertEqual([r['backend'] for r in data['reports']], ['local'])
        self.assertEqual(data['reports'][0]['fingerprint'], value['fingerprint'])
        self.assertEqual(data['reports'][0]['groups'][0]['trials'][0]['expected_answer'], {'total': 10})
        # Explicitly choosing one run does not weaken combined-report validation.
        with self.assertRaisesRegex(ValueError, 'fingerprints'):
            dashboard.build_data(self.root)

    def test_other_file_names_and_unrelated_nested_reports_are_rejected(self):
        wrong = self.root / "connection.json"
        wrong.write_text(json.dumps(example_report()))
        with self.assertRaisesRegex(ValueError, 'report.json file'):
            dashboard.build_data(wrong)
        nested = self.root / "unrelated/deep/report.json"
        nested.parent.mkdir(parents=True)
        nested.write_text(json.dumps(example_report()))
        with self.assertRaisesRegex(ValueError, 'No saved reports'):
            dashboard.build_data(self.root)

    def test_cli_renders_a_single_report_file_without_dependencies(self):
        saved = self.save(example_report())
        output = self.root / "single-report.html"
        result = subprocess.run([sys.executable, '-S', str(PLUGIN / 'scripts/workflow_eval.py'),
            'dashboard', '--project', str(saved), '--output', str(output)],
            cwd=self.root, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual(reply['backends'], ['local'])
        self.assertEqual(reply['fresh_model_calls'], 0)
        self.assertTrue(output.is_file())

    def browser_probe(self, data, actions=""):
        if not shutil.which("node"):
            self.skipTest("Node is required for the offline dashboard JavaScript checks")
        code = re.findall(r"<script>\s*(.*?)</script>", dashboard.TEMPLATE.read_text(), flags=re.S)[-1]
        harness = r'''
const fs = require("fs"), vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const elements = new Map();
const viewIds = ["overview", "explorer", "integrity"];
let scrolledTo = null, focused = null;
function element(id) {
  if (!elements.has(id)) elements.set(id, {id, _innerHTML: "", generation: 0,
    get innerHTML() {return this._innerHTML;},
    set innerHTML(value) {this._innerHTML = value; this.generation++;},
    textContent: "", value: "",
    hidden: viewIds.includes(id) && id !== "overview", dataset: {}, attributes: {},
    classList: {toggle() {}, contains() {return false;}}, querySelectorAll(selector) {
      if (id === "backends" && selector === "button")
        return [...element(id).innerHTML.matchAll(/data-backend="([^"]+)"/g)].map((match) => {
          const button = element("backend-" + element(id).generation + "-" + match[1]); button.dataset.backend = match[1]; return button;
        });
      if (selector === "[data-prompt-link]")
        return [...element(id).innerHTML.matchAll(/data-prompt-link="([^"]+)"/g)].map((match) => {
          const link = element(id + "-prompt-link-" + element(id).generation + "-" + match[1]);
          link.dataset.promptLink = match[1]; link.href = "#" + match[1]; return link;
        });
      if (selector === "[data-return-overview]")
        return [...element(id).innerHTML.matchAll(/data-return-overview(?:="([^"]*)")?/g)].map((match, i) => {
          const link = element(id + "-return-overview-" + element(id).generation + "-" + i);
          link.dataset.returnOverview = match[1] || ""; return link;
        });
      return [];
    },
    setAttribute(name, value) {this.attributes[name] = value;},
    removeAttribute(name) {delete this.attributes[name];},
    scrollIntoView() {scrolledTo = id;}, focus() {focused = id;},
    click() {if (this.onclick) this.onclick({preventDefault() {}, currentTarget: this, target: this});}});
  return elements.get(id);
}
function queryAll(selector) {
  if (selector === ".view") return viewIds.map(element);
  if (selector === "[data-view]") return viewIds.map((id) => {
    const button = element("nav-" + id); button.dataset.view = id; return button;
  });
  if (selector === "[data-open]") {
    const button = element("open-explorer"); button.dataset.open = "explorer"; return [button];
  }
  if (selector === "[data-prompt-link]" || selector === "[data-return-overview]")
    return ["insight", "comparison", "prompt-cards"].flatMap((id) => element(id).querySelectorAll(selector));
  return [];
}
element("dashboard-data").textContent = JSON.stringify(input.data);
let blob;
const context = {document: {getElementById: element, querySelectorAll: queryAll,
    createElement() {return element("download");}, body: element("body")},
  Blob: class {constructor(parts) {blob = parts.join("");}},
  URL: {createObjectURL() {return "offline";}, revokeObjectURL() {}}, setTimeout() {},
  requestAnimationFrame(callback) {callback();}};
vm.runInNewContext(input.code + "\n" + input.actions, context);
process.stdout.write(JSON.stringify({table: element("comparison").innerHTML,
  insight: element("insight").innerHTML, prompt_cards: element("prompt-cards").innerHTML,
  dataset_notice: element("dataset-notice").innerHTML, provenance: element("provenance").textContent,
  visible_views: viewIds.filter((id) => !element(id).hidden), scrolled_to: scrolledTo, focused,
  current_nav: viewIds.filter((id) => element("nav-" + id).attributes["aria-current"] === "page"),
  chart: element("tradeoff").innerHTML, legend: element("legend").innerHTML, detail: element("detail").innerHTML,
  trial_list: element("trial-list").innerHTML,
  note: element("cost-metric-note").textContent, units_note: element("cost-units-note").textContent, chosen: element("cost-metric").value,
  exported: blob ? JSON.parse(blob) : null}));
'''
        result = subprocess.run([shutil.which("node"), "-e", harness], input=json.dumps(
            {"data": data, "code": code, "actions": actions}), text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def comparison_blocks(self, view, kind):
        return re.findall(r'<div class="result-comparison"[^>]*data-comparison="' + kind
                          + r'"[^>]*>.*?</div>', view['insight'], flags=re.S)

    def test_long_shared_inputs_resolve_for_detail_search_and_filtered_export(self):
        value = example_report()
        text = 'Start of complete packet\n' + 'important context ' * 6000 + '\nUNIQUE-END-MARKER <script>untrusted</script>'
        row = value['holdout']['source_original']['trials'][0]
        row['result']['turn_records'] = [{'user': text, 'output': 'recorded answer'}]
        self.save(value)
        data = dashboard.build_data(self.root)
        view = self.browser_probe(data,
            'showView("explorer"); $("search").oninput({target:{value:"unique-end-marker"}}); $("export").onclick();')
        self.assertIn('Start of complete packet', view['detail'])
        self.assertIn('UNIQUE-END-MARKER &lt;script&gt;untrusted&lt;/script&gt;', view['detail'])
        self.assertNotIn('<script>untrusted</script>', view['detail'])
        exported = view['exported']
        self.assertEqual(len(exported['trials']), 1)
        self.assertEqual(len(exported['input_bundles']), 1)
        trial = exported['trials'][0]
        self.assertEqual(exported['input_bundles'][trial['input_ref']], [text])
        self.assertNotIn('inputs', trial)

    def test_legacy_inline_inputs_still_render_search_and_export(self):
        self.save(example_report())
        data = dashboard.build_data(self.root)
        for report in data['reports']:
            shared = report.pop('input_bundles')
            report.pop('input_storage')
            for group in report['groups']:
                for trial in group['trials']:
                    trial['inputs'] = shared[trial.pop('input_ref')]
        view = self.browser_probe(data,
            'showView("explorer"); $("search").oninput({target:{value:"extract the invoice"}}); $("export").onclick();')
        self.assertIn('Extract the invoice', view['detail'])
        self.assertEqual(len(view['exported']['trials']), 20)
        self.assertEqual(view['exported']['input_bundles'], {})
        self.assertEqual(view['exported']['trials'][0]['inputs'], ['Extract the invoice'])

    def prompt_block(self, value):
        self.save(value)
        view = self.browser_probe(dashboard.build_data(self.root))
        blocks = self.comparison_blocks(view, 'prompt')
        self.assertEqual(len(blocks), 1, view['insight'])
        return blocks[0]

    def test_summary_names_each_comparison_and_explains_saved_results(self):
        value = optimization_report('agentcore')
        for name, group in value['holdout'].items():
            model = 'gpt-4.1-2025-04-14' if name == 'source_original' else 'us.openai.gpt-6.2-sol'
            for row in group['trials']:
                row['result']['model_requested'] = model
        self.save(value)
        data = dashboard.build_data(self.root)
        view = self.browser_probe(data)
        prompt = self.comparison_blocks(view, 'prompt')[0]
        model = self.comparison_blocks(view, 'model')[0]
        self.assertIn('Prompt optimization: GPT-6.2 Sol', prompt)
        self.assertIn('The starting prompt is the version we began with.', prompt)
        self.assertIn('The revised prompt is the version chosen by the optimizer.', prompt)
        self.assertIn('Starting prompt', re.sub('<[^>]+>', '', prompt))
        self.assertIn('Revised prompt', re.sub('<[^>]+>', '', prompt))
        self.assertIn('Model comparison: same starting prompt', model)
        self.assertIn('Both models used the starting prompt.', model)
        self.assertIn('<th scope="col">GPT-4.1-2025-04-14</th><th scope="col">GPT-6.2 Sol</th>', model)
        self.assertIn('Run on AgentCore', view['insight'])
        self.assertIn('Test data details are in Run details.', view['dataset_notice'])
        self.assertEqual(view['provenance'], data['provenance'])
        self.assertNotIn(data['reports'][0]['provenance'], view['insight'])
        linked = self.browser_probe(data, '$("dataset-details-link").click();')
        self.assertEqual(linked['visible_views'], ['integrity'])
        self.assertEqual(linked['focused'], 'dataset-details')
        self.assertEqual(linked['scrolled_to'], 'dataset-details')
        # Older payloads must link to the full details without inventing a data source.
        legacy = copy.deepcopy(data)
        legacy['reports'][0].pop('dataset_summary')
        legacy_view = self.browser_probe(legacy, '$("dataset-details-link").click();')
        self.assertIn('Test data details are in Run details.', legacy_view['dataset_notice'])
        self.assertEqual(legacy_view['provenance'], data['provenance'])
        self.assertEqual(legacy_view['visible_views'], ['integrity'])
        self.assertNotIn('original → selected', view['insight'])
        self.assertNotIn('The selected prompt improved the measured results', view['insight'])
        self.assertNotIn('The results show a tradeoff', view['insight'])
        sidebar = re.search(r'<div class="sidebar-bottom">(.*?)</div>',
                            dashboard.TEMPLATE.read_text(), flags=re.S).group(1)
        self.assertIn('Saved run results', sidebar)
        self.assertIn('Loaded from report files.', sidebar)
        self.assertIn('does not update live or run models', sidebar)
        self.assertNotIn('completed', sidebar.lower())
        self.assertNotIn('Saved evidence', sidebar)
        # The sidebar remains true for a partial run, which must still be visibly incomplete.
        value['evaluation_status'] = 'paused'
        self.save(value)
        partial = self.browser_probe(dashboard.build_data(self.root))
        self.assertIn('data-result="incomplete"', partial['insight'])
        self.assertIn('The run is not complete.', partial['insight'])

    def prompt_card(self, view, target):
        match = re.search(r'<section\b(?=[^>]*\bid="' + re.escape(target)
                          + r'")[^>]*>(.*?)</section>', view['prompt_cards'], re.S)
        self.assertIsNotNone(match, view['prompt_cards'])
        return match.group(1)

    def test_revised_prompt_link_opens_full_prompt_and_returns_to_overview(self):
        self.save(optimization_report('agentcore'))
        data = dashboard.build_data(self.root)
        target = 'prompt-' + data['reports'][0]['id'] + '-target_selected'
        initial = self.browser_probe(data)
        self.assertIn('href="#' + target + '"', initial['insight'])
        card = self.prompt_card(initial, target)
        self.assertIn('Revised prompt', card)
        self.assertIn('target-model', card)
        self.assertIn('selected instructions', card)
        self.assertNotIn('Frozen prompt', dashboard.TEMPLATE.read_text())
        click = ('$("insight").querySelectorAll("[data-prompt-link]")'
                 '.find(link => link.dataset.promptLink === ' + json.dumps(target) + ').click();')
        opened = self.browser_probe(data, click)
        self.assertEqual(opened['visible_views'], ['integrity'])
        self.assertEqual(opened['current_nav'], ['integrity'])
        self.assertEqual(opened['scrolled_to'], target)
        self.assertEqual(opened['focused'], target)
        returned = self.browser_probe(data, click +
            '$("prompt-cards").querySelectorAll("[data-return-overview]")[0].click();')
        self.assertEqual(returned['visible_views'], ['overview'])
        self.assertEqual(returned['current_nav'], ['overview'])
        self.assertEqual(returned['scrolled_to'], 'insight')
        self.assertEqual(returned['focused'], 'overview-tab')

    def test_trial_list_and_detail_identify_original_revised_and_unselected_prompts(self):
        value = optimization_report('agentcore')
        candidate = copy.deepcopy(value['holdout']['target_selected'])
        candidate['score']['prompt'] = 'unselected candidate instructions'
        value['holdout']['target_challenger'] = candidate
        self.save(value)
        data = dashboard.build_data(self.root)
        view = self.browser_probe(data)
        buttons = ET.fromstring('<root>' + view['trial_list'] + '</root>').findall('button')
        self.assertEqual(len(buttons), 40)
        # The two versions of the same target model need a visible distinction
        # even when a customer has not chosen a configuration filter.
        texts = [''.join(button.itertext()) for button in buttons]
        for label in ('Starting prompt', 'Revised prompt', 'Candidate prompt (not chosen)'):
            self.assertTrue(any('target-model' in text and label in text for text in texts))
        self.assertFalse(any(button.findall('.//a') or button.findall('.//button') for button in buttons))
        for group, label, text in (
            ('target_original', 'Starting prompt', 'original instructions'),
            ('target_selected', 'Revised prompt', 'selected instructions'),
            ('target_challenger', 'Candidate prompt (not chosen)', 'unselected candidate instructions'),
        ):
            with self.subTest(group=group):
                actions = 'state.configuration=' + json.dumps(group) + '; renderExplorer();'
                selected = self.browser_probe(data, actions)
                self.assertIn(label + '</a>', selected['detail'])
                target = re.search(r'data-prompt-link="([^"]+)"', selected['detail']).group(1)
                opened = self.browser_probe(data, actions +
                    '$("detail").querySelectorAll("[data-prompt-link]")[0].click();')
                self.assertEqual(opened['visible_views'], ['integrity'])
                self.assertEqual(opened['focused'], target)
                self.assertIn(text, self.prompt_card(opened, target))

    def test_trial_prompt_navigation_preserves_retained_original_and_missing_text(self):
        value = optimization_report('agentcore')
        value['holdout']['target_selected'] = copy.deepcopy(value['holdout']['target_original'])
        value['selected_prompt']['prompt'] = 'original instructions'
        self.save(value)
        data = dashboard.build_data(self.root)
        view = self.browser_probe(data, 'state.configuration="target_selected"; renderExplorer();')
        self.assertIn('Starting prompt</a>', view['detail'])
        self.assertNotIn('Revised prompt', view['detail'])
        self.assertNotIn('Revised prompt', view['trial_list'])
        # A legacy result without prompt text must still render without a dead link.
        for group in value['holdout'].values():
            group['score'].pop('prompt', None)
        value['selected_prompt'].pop('prompt', None)
        self.save(value)
        missing = self.browser_probe(dashboard.build_data(self.root),
            'state.configuration="target_original"; renderExplorer();')
        self.assertIn('Starting prompt', missing['detail'])
        self.assertNotIn('data-prompt-link', missing['detail'])

    def test_set_and_schema_failures_show_saved_diagnostics_with_escaped_values(self):
        value = example_report('agentcore')
        checks = value['holdout']['source_original']['trials'][0]['checks']
        checks.update({
            'required_actions': {'pass': False, 'reason': 'JSON set differs from the reference',
                'path': 'actions.<script>field</script>', 'missing_values': ['request_review', False, 0],
                'unexpected_values': ['</pre><script>unsafe()</script>'],
                'missing_count': 25, 'unexpected_count': 1},
            'output_schema': {'pass': False, 'reason': 'JSON structure or types violate the schema',
                'violations': [{'path': ['credit_cents'], 'rule': 'type',
                    'message': "'<img src=x onerror=unsafe()>' is not of type 'integer'"}],
                'violation_count': 2},
            'credit': {'pass': False, 'reason': 'Wrong credit',
                'differences': [{'path': 'credit', 'expected': 10, 'actual': 20}]},
            'empty': {'pass': True, 'reason': 'No mismatch', 'missing_values': [],
                'unexpected_values': [], 'violations': []},
        })
        self.save(value)
        view = self.browser_probe(dashboard.build_data(self.root))
        detail = view['detail']
        self.assertIn('Missing values · field actions.&lt;script&gt;field&lt;/script&gt; (showing 3 of 25)', detail)
        self.assertIn('Unexpected values', detail)
        self.assertIn('request_review', detail)
        self.assertIn('false,\n  0', detail)
        self.assertIn('&lt;/pre&gt;&lt;script&gt;unsafe()&lt;/script&gt;', detail)
        self.assertIn('Schema violations (showing 1 of 2)', detail)
        self.assertIn('credit_cents', detail)
        self.assertIn('&lt;img src=x onerror=unsafe()&gt;', detail)
        self.assertIn('Field differences', detail)
        self.assertNotIn('<script>', detail)
        self.assertNotIn('<img', detail)

    def test_astra_and_sol_model_names_are_consistent_across_result_views(self):
        value = optimization_report('agentcore')
        for key, group in value['holdout'].items():
            model = 'us.openai.gpt-6.2-astra' if key == 'source_original' else 'us.openai.gpt-6.2-sol'
            for row in group['trials']:
                row['model_id'] = model
                row['result']['model_requested'] = model
        self.save(value)
        view = self.browser_probe(dashboard.build_data(self.root))
        for key in ('insight', 'table', 'chart', 'trial_list', 'prompt_cards'):
            with self.subTest(view=key):
                self.assertIn('GPT-6.2 Astra', view[key])
                self.assertIn('GPT-6.2 Sol', view[key])
                self.assertNotIn('GPT-6.2-astra', view[key])
        self.assertIn('GPT-6.2 Astra', view['detail'])

    def test_starting_prompt_is_not_labeled_revised_when_original_is_retained(self):
        for reason, explicit_reuse in [('Baseline only', True), ('No eligible revision', True),
                                        ('No eligible revision', False)]:
            with self.subTest(reason=reason, explicit_reuse=explicit_reuse):
                value = optimization_report('agentcore')
                original = value['holdout']['target_original']
                value['holdout']['target_selected'] = copy.deepcopy(original)
                if explicit_reuse:
                    value['holdout']['target_selected']['reused_from'] = 'target_original'
                value['selected_prompt'].update(prompt='original instructions', reason=reason)
                self.save(value)
                data = dashboard.build_data(self.root)
                view = self.browser_probe(data)
                headings = re.findall(r'<h2[^>]*>(.*?)</h2>', view['prompt_cards'], re.S)
                self.assertTrue(any('Starting prompt' in heading for heading in headings))
                self.assertFalse(any('Revised prompt' in heading for heading in headings))
                self.assertNotIn('data-prompt-link="prompt-' + data['reports'][0]['id']
                                 + '-target_selected"', view['insight'])
                self.assertIn('original instructions', view['prompt_cards'])
                # One shared starting prompt serves both model columns without
                # creating a misleading duplicate or a revised-prompt destination.
                self.assertEqual(sum('Starting prompt' in heading for heading in headings), 1)
                target = 'prompt-' + data['reports'][0]['id'] + '-source_original'
                self.assertIn('data-prompt-link="' + target + '"', view['insight'])
                clicked = self.browser_probe(data,
                    '$("insight").querySelectorAll("[data-prompt-link]")'
                    '.find(link => link.dataset.promptLink === ' + json.dumps(target) + ').click();')
                self.assertEqual(clicked['visible_views'], ['integrity'])
                self.assertEqual(clicked['focused'], target)

    def test_prompt_cards_escape_saved_text_and_do_not_invent_a_revision(self):
        value = optimization_report('agentcore')
        text = 'Preserve </pre><script>alert("unsafe")</script> as input text.'
        value['selected_prompt']['prompt'] = text
        value['holdout']['target_selected']['score']['prompt'] = text
        self.save(value)
        view = self.browser_probe(dashboard.build_data(self.root))
        self.assertIn('&lt;/pre&gt;&lt;script&gt;', view['prompt_cards'])
        self.assertNotIn('<script>', view['prompt_cards'])
        value['holdout'] = {}
        self.save(value)
        view = self.browser_probe(dashboard.build_data(self.root))
        headings = re.findall(r'<h2[^>]*>(.*?)</h2>', view['prompt_cards'], re.S)
        self.assertIn('Saved prompt', headings)
        self.assertFalse(any('Revised prompt' in heading for heading in headings))
        self.assertIn('does not identify whether', view['prompt_cards'])

    def test_prompt_link_uses_the_matching_backend_and_survives_refresh(self):
        local = optimization_report('local')
        cloud = optimization_report('agentcore')
        for value, prompt in [(local, 'LOCAL revised instructions'),
                              (cloud, 'CLOUD revised instructions')]:
            value['selected_prompt']['prompt'] = prompt
            value['holdout']['target_selected']['score']['prompt'] = prompt
            self.save(value)
        data = dashboard.build_data(self.root)
        target = 'prompt-' + next(r['id'] for r in data['reports'] if r['backend'] == 'agentcore') + '-target_selected'
        click = ('$("insight").querySelectorAll("[data-prompt-link]")'
                 '.find(link => link.dataset.promptLink === ' + json.dumps(target) + ').click();')
        for refresh in ['', '$("cost-metric").onchange({target:{value:"cost_usd_per_session"}});',
                        '$("backends").querySelectorAll("button").find(b => b.dataset.backend === "agentcore").onclick();']:
            with self.subTest(refresh=refresh):
                view = self.browser_probe(data, refresh + click)
                self.assertEqual(view['visible_views'], ['integrity'])
                self.assertEqual(view['focused'], target)
                card = self.prompt_card(view, target)
                self.assertIn('CLOUD revised instructions', card)
                self.assertNotIn('LOCAL revised instructions', card)

    def test_summary_compares_selected_prompt_to_its_own_target(self):
        value = optimization_report()
        block = self.prompt_block(value)
        self.assertIn('data-result="improvement"', block)
        self.assertIn('<td>9/10 (90.0%)</td><td>10/10 (100%)</td>', block)
        self.assertIn('1 more test run passed.', block)
        self.assertIn('Model cost per successful result', block)
        self.assertIn('P95 conversation time', block)
        self.assertNotIn('A tradeoff to investigate', block)
        # The selected prompt still beats the source, but regresses against the
        # original target. A source-only comparison must not hide that loss.
        regressed = optimization_report(original_correct=10, selected_correct=9)
        block = self.prompt_block(regressed)
        self.assertIn('1 fewer test run passed.', block)
        self.assertIn('<td>10/10 (100%)</td><td>9/10 (90.0%)</td>', block)
        self.assertNotIn('more test run', block)

    def test_summary_handles_perfect_scores_ties_and_zero_baselines(self):
        for label, value, status in [
            ('perfect efficiency improvement', optimization_report(original_correct=10), 'improvement'),
            ('all metrics tie', optimization_report(original_correct=10, original_cost=.015,
                                                    original_p95=3), 'unchanged'),
            ('zero cost and time tie', optimization_report(original_correct=10,
                original_cost=0, selected_cost=0, original_p95=0, selected_p95=0), 'unchanged'),
            ('higher cost and time from zero', optimization_report(original_correct=10,
                original_cost=0, original_p95=0), 'regression'),
        ]:
            with self.subTest(label=label):
                block = self.prompt_block(value)
                self.assertIn(f'data-result="{status}"', block)
                self.assertNotIn('Infinity', block)
                self.assertNotIn('NaN', block)
                self.assertNotIn('A perfect score', block)
        block = self.prompt_block(optimization_report(original_correct=10,
                                 original_cost=0, original_p95=0))
        self.assertIn('<td>$0.000000</td><td>$0.015000</td>', block)
        self.assertIn('Cost per successful result rose.', block)
        self.assertIn('<td>0.000 s</td><td>3.000 s</td>', block)
        self.assertIn('P95 time rose.', block)

    def test_summary_names_tradeoffs_and_regressions(self):
        block = self.prompt_block(optimization_report(original_correct=10, selected_cost=.03))
        self.assertIn('data-result="tradeoff"', block)
        self.assertIn('Cost per successful result rose by 50.0%.', block)
        self.assertIn('P95 time fell by 25.0%.', block)
        block = self.prompt_block(optimization_report(original_correct=10, selected_cost=.03,
                                                      selected_p95=5))
        self.assertIn('data-result="regression"', block)
        self.assertIn('Cost per successful result rose by 50.0%.', block)
        self.assertIn('P95 time rose by 25.0%.', block)

    def test_summary_does_not_promote_cheaper_failures(self):
        value = optimization_report(original_correct=0, selected_correct=0)
        value['selected_prompt']['policy']['cost_metric'] = 'cost_usd_per_session'
        block = self.prompt_block(value)
        self.assertIn('data-result="no_successes"', block)
        self.assertIn('Neither configuration produced a successful result', block)
        self.assertIn('<td>$0.020000</td><td>$0.015000</td>', block)
        self.assertNotIn('more test run', block)

    def test_summary_requires_prompt_evidence_for_both_comparisons(self):
        for missing in (None, ''):
            with self.subTest(missing=missing):
                value = optimization_report()
                for group in value['holdout'].values():
                    group['score']['prompt'] = missing
                self.save(value)
                view = self.browser_probe(dashboard.build_data(self.root))
                for kind in ('prompt', 'model'):
                    block = self.comparison_blocks(view, kind)[0]
                    self.assertIn('data-result="incomplete"', block)
                    self.assertIn('recorded prompts are unavailable', block)
                    self.assertIn('Some results are missing or cannot be compared.', block)

    def test_summary_requires_complete_matched_and_comparable_evidence(self):
        changes = [
            ('zero sessions', lambda v: v['holdout']['target_selected']['score'].update(n_sessions=0), 'pass counts are unavailable'),
            ('missing trial', lambda v: v['holdout']['target_selected']['trials'].pop(), 'matching cases and repeats'),
            ('different case', lambda v: v['holdout']['target_selected']['trials'][0].update(case_id='another-case'), 'matching cases and repeats'),
            ('duplicate repeat', lambda v: v['holdout']['target_selected']['trials'][0].update(repeat=1), 'matching cases and repeats'),
            ('missing accuracy', lambda v: v['holdout']['target_selected']['score'].update(accuracy=None), 'pass counts are unavailable'),
            ('nonintegral pass count', lambda v: v['holdout']['target_selected']['score'].update(accuracy=.955), 'pass counts are unavailable'),
            ('unknown cost', lambda v: v['holdout']['target_selected']['score'].update(unpriced_requests=1), 'Comparable model costs are unavailable'),
            ('no successes', lambda v: v['holdout']['target_selected']['score'].update(accuracy=0, cost_usd_per_success=None), 'No successful results'),
            ('paused', lambda v: v.update(evaluation_status='paused'), 'run is not complete'),
            ('unknown recovery', lambda v: v.pop('unresolved_attempts'), 'recovery coverage is unknown'),
            ('unresolved attempt', lambda v: v.update(unresolved_attempts=[{'id': 'lost'}]), 'attempts remain unresolved'),
            ('grading error', lambda v: v.update(grading_error_records=1), 'contains grading errors'),
            ('simulation', lambda v: v.update(simulated=True), 'measurements are simulated'),
            ('missing identity', lambda v: v.update(fingerprint=None), 'fingerprint is missing'),
            ('missing p95', lambda v: v['holdout']['target_selected']['score'].update(latency_p95_s=None), 'Comparable p95'),
            ('different latency scope', lambda v: v['holdout']['target_selected']['score'].update(latency_scope='worker'), 'measurement scope are unavailable'),
            ('changed target model', lambda v: [r['result'].update(model_requested='different-model') for r in v['holdout']['target_selected']['trials']], 'same recorded model'),
        ]
        for label, change, expected in changes:
            with self.subTest(label=label):
                value = optimization_report()
                change(value)
                block = self.prompt_block(value)
                self.assertIn('data-result="incomplete"', block)
                self.assertIn(expected, block)
                self.assertIn('Some results are missing or cannot be compared.', block)

    def test_summary_preserves_fixed_and_retained_prompt_meanings(self):
        for reason, expected in [('Baseline only', 'no prompt optimization was performed'),
                                 ('Tradeoffs need customer review; incumbent retained', 'optimizer kept the starting prompt')]:
            with self.subTest(reason=reason):
                value = optimization_report()
                value['holdout']['target_selected'] = dict(
                    value['holdout']['target_original'], reused_from='target_original')
                value['selected_prompt'].update(prompt='original instructions', reason=reason)
                # An unselected challenger must not become the selected comparison.
                value['holdout']['target_challenger'] = optimization_report()['holdout']['target_selected']
                self.save(value)
                data = dashboard.build_data(self.root)
                self.assertEqual(data['reports'][0]['reused_groups']['target_selected'], 'target_original')
                view = self.browser_probe(data)
                self.assertEqual(self.comparison_blocks(view, 'prompt'), [])
                self.assertEqual(len(self.comparison_blocks(view, 'model')), 1)
                self.assertIn(expected, view['insight'])
                self.assertNotIn('Prompt optimization:', view['insight'])

    def test_unselected_candidate_summary_shows_quality_cost_and_latency_tradeoff(self):
        value = challenger_report('agentcore', selected_p95=5)
        for name, group in value['holdout'].items():
            model = 'us.openai.gpt-6.2-astra' if name == 'source_original' else 'us.openai.gpt-6.2-sol'
            for row in group['trials']:
                row['result']['model_requested'] = model
        self.freeze(value)
        data = dashboard.build_data(self.root)
        initial = self.browser_probe(data)
        block = self.comparison_blocks(initial, 'candidate')[0]
        self.assertIn('Candidate prompt comparison: GPT-6.2 Sol', block)
        self.assertIn('data-result="tradeoff"', block)
        self.assertIn('<td>9/10 (90.0%)</td><td>10/10 (100%)</td>', block)
        self.assertIn('<td>$0.022222</td><td>$0.015000</td>', block)
        self.assertIn('<td>4.000 s</td><td>5.000 s</td>', block)
        self.assertIn('1 more test run passed. Cost per successful result fell by 32.5%. P95 time rose by 25.0%.', block)
        self.assertIn('Candidate prompt (not selected)', block)
        self.assertIn('do not change the saved selection', block)
        self.assertNotIn('version chosen by the optimizer', block)
        self.assertEqual(self.comparison_blocks(initial, 'prompt'), [])
        self.assertEqual(len(self.comparison_blocks(initial, 'model')), 1)
        target = 'prompt-' + data['reports'][0]['id'] + '-target_challenger'
        self.assertIn('data-prompt-link="' + target + '"', block)
        opened = self.browser_probe(data,
            '$("insight").querySelectorAll("[data-prompt-link]")'
            '.find(link => link.dataset.promptLink === ' + json.dumps(target) + ').click();'
            'new Blob([JSON.stringify(D.reports[0].selected_prompt)]);')
        self.assertEqual(opened['visible_views'], ['integrity'])
        self.assertEqual(opened['focused'], target)
        self.assertIn('candidate instructions', self.prompt_card(opened, target))
        self.assertEqual(opened['exported']['prompt'], 'original instructions')
        self.assertFalse(opened['exported']['challenger']['selected'])

    def test_candidate_summary_reports_improvement_regression_and_ties_without_promotion(self):
        for label, scores, status, text in [
            ('improvement', {}, 'improvement', '1 more test run passed.'),
            ('regression', {'selected_correct': 8, 'selected_cost': .03, 'selected_p95': 5}, 'regression', '1 fewer test run passed.'),
            ('tie', {'selected_correct': 9, 'selected_cost': .02, 'selected_p95': 4}, 'unchanged', 'The same number of test runs passed.'),
        ]:
            with self.subTest(label=label):
                self.save(challenger_report(**scores))
                view = self.browser_probe(dashboard.build_data(self.root),
                    'new Blob([JSON.stringify(D.reports[0].selected_prompt)]);')
                block = self.comparison_blocks(view, 'candidate')[0]
                self.assertIn('data-result="' + status + '"', block)
                self.assertIn(text, block)
                self.assertIn('was evaluated but was not selected', block)
                self.assertEqual(view['exported']['prompt'], 'original instructions')
                self.assertFalse(view['exported']['challenger']['selected'])

    def test_candidate_summary_requires_the_same_complete_comparable_evidence(self):
        changes = [
            ('no original', lambda v: v['holdout'].pop('target_original'), 'Both configurations are needed'),
            ('different cases', lambda v: v['holdout']['target_challenger']['trials'][0].update(case_id='other'), 'matching cases and repeats'),
            ('different model', lambda v: [r['result'].update(model_requested='other-model') for r in v['holdout']['target_challenger']['trials']], 'same recorded model'),
            ('no prompt', lambda v: v['holdout']['target_challenger']['score'].pop('prompt'), 'recorded prompts are unavailable'),
            ('unknown cost', lambda v: v['holdout']['target_challenger']['score'].update(unpriced_requests=1), 'Comparable model costs are unavailable'),
            ('incompatible time', lambda v: v['holdout']['target_challenger']['score'].update(latency_scope='worker'), 'measurement scope are unavailable'),
            ('partial latency', lambda v: v['holdout']['target_challenger']['score'].update(timing_metrics={'conversation_ms': {'observations': 9}}), 'does not cover every attempt'),
            ('partial run', lambda v: v.update(evaluation_status='paused'), 'run is not complete'),
            ('unresolved', lambda v: v.update(unresolved_attempts=[{'id': 'lost'}]), 'attempts remain unresolved'),
            ('grading error', lambda v: v.update(grading_error_records=1), 'contains grading errors'),
            ('execution error', lambda v: v['holdout']['target_challenger']['trials'][0]['result'].update(status='infrastructure_error'), 'execution or grading issues'),
            ('simulation', lambda v: v.update(simulated=True), 'measurements are simulated'),
        ]
        for label, change, expected in changes:
            with self.subTest(label=label):
                value = challenger_report()
                change(value)
                self.save(value)
                view = self.browser_probe(dashboard.build_data(self.root))
                block = self.comparison_blocks(view, 'candidate')[0]
                self.assertIn('data-result="incomplete"', block)
                self.assertIn(expected, block)
                self.assertIn('Some results are missing or cannot be compared.', block)
                self.assertIn('not selected', block)

    def test_candidate_summary_preserves_cost_preference_and_frozen_input_guard(self):
        value = challenger_report(original_correct=8, selected_correct=10,
                                  original_cost=.02, selected_cost=.022, selected_p95=4)
        frozen, config = self.freeze(value)
        data = dashboard.build_data(self.root)
        initial = self.browser_probe(data)
        self.assertIn('data-result="improvement"', self.comparison_blocks(initial, 'candidate')[0])
        switched = self.browser_probe(data,
            '$("cost-metric").onchange({target:{value:"cost_usd_per_session"}});'
            'new Blob([JSON.stringify(D.reports[0].selected_prompt)]);')
        block = self.comparison_blocks(switched, 'candidate')[0]
        self.assertIn('data-result="tradeoff"', block)
        self.assertIn('Model cost per attempt', block)
        self.assertIn('Cost per attempt rose by 10.0%.', block)
        self.assertEqual(switched['exported']['prompt'], 'original instructions')
        self.assertFalse(switched['exported']['challenger']['selected'])
        config['document']['runner']['selection']['cost_metric'] = 'cost_usd_per_session'
        frozen.write_text(json.dumps(config))  # Invalidates the saved manifest hash.
        invalid = self.browser_probe(dashboard.build_data(self.root))
        block = self.comparison_blocks(invalid, 'candidate')[0]
        self.assertIn('data-result="incomplete"', block)
        self.assertIn('frozen input manifest could not be verified', block)

    def test_candidate_summary_needs_heldout_candidate_evidence(self):
        value = challenger_report()
        value['holdout'].pop('target_challenger')
        # A saved candidate prompt by itself is not a measured comparison.
        self.save(value)
        view = self.browser_probe(dashboard.build_data(self.root))
        self.assertEqual(self.comparison_blocks(view, 'candidate'), [])
        self.assertEqual(self.comparison_blocks(view, 'prompt'), [])
        self.assertIn('optimizer kept the starting prompt', view['insight'])

    def test_summary_rejects_trial_errors_even_when_run_claims_completion(self):
        for label, change in [
            ('unfinished execution', lambda row: row['result'].update(status='in_progress')),
            ('failed execution', lambda row: row['result'].update(status='infrastructure_error')),
            ('grading error', lambda row: row['checks']['total'].update(error=True)),
            ('unknown check result', lambda row: row['checks']['total'].update({'pass': None})),
            ('missing checks', lambda row: row.update(checks={})),
        ]:
            with self.subTest(label=label):
                value = optimization_report()
                row = value['holdout']['target_selected']['trials'][0]
                change(row)
                self.assertEqual(value['evaluation_status'], 'complete')
                self.assertEqual(value['grading_error_records'], 0)
                self.assertEqual(value['unresolved_attempts'], [])
                block = self.prompt_block(value)
                self.assertIn('data-result="incomplete"', block)
                self.assertIn('saved attempts have execution or grading issues', block)
                self.assertIn('Some results are missing or cannot be compared.', block)

    def test_summary_rejects_partial_or_unknown_latency_coverage(self):
        for coverage in [{'observations': 9, 'planned': 10}, {'planned': 10}]:
            with self.subTest(coverage=coverage):
                value = optimization_report()
                value['holdout']['target_selected']['score']['timing_metrics'] = {
                    'conversation_ms': coverage,
                    # Complete worker coverage cannot replace missing conversation coverage.
                    'worker_ms': {'observations': 10, 'planned': 10},
                }
                block = self.prompt_block(value)
                self.assertIn('data-result="incomplete"', block)
                self.assertIn('selected latency measure does not cover every attempt', block)
                self.assertIn('Some results are missing or cannot be compared.', block)

    def test_recorded_optimization_without_selected_evidence_stays_visible(self):
        value = optimization_report()
        value['optimization'] = {'generations': [], 'stopped_reason': 'interrupted'}
        del value['holdout']['target_selected']
        self.save(value)
        data = dashboard.build_data(self.root)
        self.assertTrue(data['reports'][0]['optimization_recorded'])
        view = self.browser_probe(data)
        blocks = self.comparison_blocks(view, 'prompt')
        self.assertEqual(len(blocks), 1, view['insight'])
        self.assertIn('data-result="incomplete"', blocks[0])
        self.assertIn('Both configurations are needed', blocks[0])
        self.assertNotIn('optimizer kept the starting prompt', view['insight'])
        self.assertIn('Some results are missing or cannot be compared.', blocks[0])

    def test_summary_cost_control_changes_description_not_saved_selection(self):
        value = optimization_report(original_correct=8, selected_correct=10,
                                     original_cost=.02, selected_cost=.022, selected_p95=4)
        self.save(value)
        data = dashboard.build_data(self.root)
        before = self.browser_probe(data)
        self.assertIn('data-result="improvement"', self.comparison_blocks(before, 'prompt')[0])
        after = self.browser_probe(data,
            '$("cost-metric").onchange({target:{value:"cost_usd_per_session"}}); '
            'new Blob([JSON.stringify(D.reports.map(r => r.selected_prompt))]);')
        block = self.comparison_blocks(after, 'prompt')[0]
        self.assertIn('data-result="tradeoff"', block)
        self.assertIn('Model cost per attempt', block)
        self.assertIn('These test cases were not used to choose the revised prompt.', after['insight'])
        self.assertEqual(after['exported'][0]['prompt'], 'selected instructions')
        self.assertEqual(data['reports'][0]['selected_prompt']['prompt'], 'selected instructions')

    def test_summary_keeps_backend_conclusions_separate_and_follows_control(self):
        self.save(optimization_report())
        self.save(optimization_report('agentcore', original_correct=10, selected_correct=8))
        data = dashboard.build_data(self.root)
        all_view = self.browser_probe(data)
        blocks = self.comparison_blocks(all_view, 'prompt')
        self.assertEqual(len(blocks), 2)
        self.assertTrue(any('data-result="improvement"' in b and '1 more test run passed.' in b for b in blocks))
        self.assertTrue(any('data-result="tradeoff"' in b and '2 fewer test runs passed.' in b for b in blocks))
        cloud = self.browser_probe(data,
            '$("backends").querySelectorAll("button").find(b => b.dataset.backend === "agentcore").onclick();')
        self.assertEqual(len(self.comparison_blocks(cloud, 'prompt')), 1)
        self.assertIn('2 fewer test runs passed.', cloud['insight'])
        self.assertNotIn('1 more test run passed.', cloud['insight'])

    def chart_point_groups(self, view):
        """Inspect the generated SVG, not the implementation's intermediate data."""
        return [{g.attrib['data-point-key']: g for g in ET.fromstring(svg).iter('g')
                 if 'data-point-key' in g.attrib}
                for svg in re.findall(r'<svg\b.*?</svg>', view['chart'], re.S)]

    def test_chart_labels_every_point_with_model_and_prompt(self):
        self.save(optimization_report('agentcore'))
        view = self.browser_probe(dashboard.build_data(self.root))
        panels = self.chart_point_groups(view)
        self.assertEqual(len(panels), 1)
        self.assertEqual(len(panels[0]), 3)
        self.assertIn('Execution location: AgentCore', view['chart'])
        self.assertNotIn('AgentCore', view['legend'])
        for key, group in panels[0].items():
            label = next(g for g in group.iter('g') if g.attrib.get('class') == 'point-label')
            text = ' '.join(label.itertext()).lower()
            expected_model = 'source-model' if key.endswith('source_original') else 'target-model'
            self.assertIn(expected_model, text)
            self.assertIn('revised prompt' if key.endswith('target_selected') else 'starting prompt', text)
            self.assertNotIn('agentcore', text)
            # A connector makes the point/label association explicit when values cluster.
            self.assertTrue(list(group.iter('path')) or list(group.iter('line')))
        self.assertIn('Model inference USD / 1,000 successful results', view['chart'])
        self.assertIn('not the total run cost', view['units_note'])
        self.assertIn('1,000 successful results', view['units_note'])
        self.assertNotIn('AgentCore', ''.join(next(iter(panels[0].values())).find('title').itertext()))
        template = " ".join(dashboard.TEMPLATE.read_text().split())
        for charge in ['AgentCore Runtime', 'Gateway', 'storage', 'logging', 'other infrastructure charges', 'Human review']:
            self.assertIn(charge, template)

    def test_chart_keeps_coincident_points_and_long_labels_readable(self):
        self.save(optimization_report('agentcore'))
        data = dashboard.build_data(self.root)
        model = 'provider-model-' + 'long-deployment-identifier-' * 5
        for group in data['reports'][0]['groups']:
            group['model'] = model
            group['score']['latency_p50_s'] = 3
            group['cost_metrics']['cost_usd_per_success'] = {'value': .015, 'status': 'known'}
        view = self.browser_probe(data)
        svg = ET.fromstring(re.search(r'<svg\b.*?</svg>', view['chart'], re.S).group())
        width, height = map(float, svg.attrib['viewBox'].split()[2:])
        points = self.chart_point_groups(view)[0]
        centers = [(g.find('circle').attrib['cx'], g.find('circle').attrib['cy']) for g in points.values()]
        self.assertEqual(len(set(centers)), 1, 'The cost/time positions must not be jittered to place labels')
        boxes = []
        for group in points.values():
            label = next(g for g in group.iter('g') if g.attrib.get('class') == 'point-label')
            top, bottom = (float(label.attrib[key]) for key in ('data-label-top', 'data-label-bottom'))
            boxes.append((top, bottom))
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(bottom, height)
            text = label.find('text')
            y = float(text.attrib['y'])
            reconstructed = ''.join(span.text or '' for span in text.findall('tspan'))
            self.assertIn(model, reconstructed)
            self.assertNotIn('AgentCore', reconstructed)
            self.assertNotIn('AgentCore', ''.join(group.find('title').itertext()))
            for span in text.findall('tspan'):
                y += float(span.attrib.get('dy', 0))
                self.assertGreaterEqual(y, top)
                self.assertLessEqual(y, bottom)
                self.assertGreaterEqual(float(span.attrib['x']), 0)
                self.assertLess(float(span.attrib['x']), width)
        boxes.sort()
        for previous, current in zip(boxes, boxes[1:]):
            self.assertLessEqual(previous[1], current[0], 'Labels must not overlap even when points coincide')

    def test_chart_tooltips_scale_cost_and_follow_metric_selection(self):
        self.save(optimization_report('agentcore'))
        data = dashboard.build_data(self.root)
        view = self.browser_probe(data)
        source = next(g for key, g in self.chart_point_groups(view)[0].items()
                      if key.endswith('source_original'))
        before = ''.join(source.find('title').itertext())
        self.assertIn('$25.000', before)
        self.assertIn('per 1,000 successful results', before)
        self.assertIn('$0.025000', before)
        view = self.browser_probe(data,
            '$("cost-metric").onchange({target:{value:"cost_usd_per_session"}});')
        source = next(g for key, g in self.chart_point_groups(view)[0].items()
                      if key.endswith('source_original'))
        after = ''.join(source.find('title').itertext())
        self.assertIn('$20.000', after)
        self.assertIn('per 1,000 attempts', after)
        self.assertIn('$0.020000', after)
        self.assertIn('Model inference USD / 1,000 attempts', view['chart'])
        self.assertIn('1,000 attempts', view['units_note'])
        self.assertNotIn('Model inference USD / 1,000 successful results', view['chart'])

    def test_chart_separates_locations_and_keeps_comparable_axes(self):
        self.save(optimization_report('local'))
        cloud = optimization_report('agentcore', selected_cost=.1)
        self.save(cloud)
        data = dashboard.build_data(self.root)
        view = self.browser_probe(data)
        panels = self.chart_point_groups(view)
        self.assertEqual(len(panels), 2)
        self.assertTrue(all(len(panel) == 3 for panel in panels))
        self.assertIn('Execution location: Local', view['chart'])
        self.assertIn('Execution location: AgentCore', view['chart'])
        # Source values are identical, despite the cloud panel's more expensive
        # revised prompt. Shared scales must put that source at the same position.
        sources = [next(g for key, g in panel.items() if key.endswith('source_original'))
                   for panel in panels]
        centers = [(g.find('circle').attrib['cx'], g.find('circle').attrib['cy']) for g in sources]
        self.assertEqual(centers[0], centers[1])
        selected = self.browser_probe(data,
            '$("backends").querySelectorAll("button").find(b => b.dataset.backend === "agentcore").onclick();')
        self.assertEqual(len(self.chart_point_groups(selected)), 1)
        self.assertIn('Execution location: AgentCore', selected['chart'])
        self.assertNotIn('Execution location: Local', selected['chart'])

    def test_chart_does_not_plot_unknown_cost_or_zero_success_as_zero(self):
        value = optimization_report('agentcore')
        value['holdout']['source_original']['score'].update(accuracy=0, cost_usd_per_success=None)
        value['holdout']['target_selected']['score'].update(unpriced_requests=1)
        self.save(value)
        view = self.browser_probe(dashboard.build_data(self.root))
        points = self.chart_point_groups(view)[0]
        self.assertEqual(len(points), 1)
        self.assertTrue(next(iter(points)).endswith('target_original'))
        self.assertIn('No successful results', view['table'])
        self.assertIn('Unknown', view['table'])

    def test_actual_chart_and_export_follow_metric_switch(self):
        value = example_report()
        self.freeze(value)
        data = dashboard.build_data(self.root)
        before = self.browser_probe(data, '$("export").onclick();')
        self.assertIn("$0.0200", before["table"])
        self.assertIn("$0.0250", before["table"])
        self.assertIn("1,000 successful results", before["chart"])
        self.assertIn("$0.025000 / successful result", before["chart"])
        self.assertIn("Expected answer", before["detail"])
        self.assertEqual(before["exported"]["chart_cost_metric"], "cost_usd_per_success")
        self.assertEqual(before["exported"]["comparison_metrics"][0]["cost_usd_per_success"]["value"], .025)
        self.assertEqual(before["exported"]["trials"][0]["expected_answer"], {"total": 10})
        after = self.browser_probe(data,
            '$("cost-metric").onchange({target:{value:"cost_usd_per_session"}}); $("export").onclick();')
        self.assertIn("1,000 attempts", after["chart"])
        self.assertIn("$0.020000 / attempt", after["chart"])
        self.assertEqual(after["exported"]["chart_cost_metric"], "cost_usd_per_session")

    def test_actual_ui_distinguishes_unknown_no_successes_and_policy_conflict(self):
        value = example_report()
        value["holdout"]["source_original"]["score"].update(accuracy=0, cost_usd_per_success=None)
        value["holdout"]["target_original"]["score"].update(unpriced_requests=1)
        self.save(value)
        view = self.browser_probe(dashboard.build_data(self.root))
        self.assertIn("No successful results", view["table"])
        self.assertIn("Unknown", view["table"])
        self.assertNotIn("<svg", view["chart"])
        self.assertIn("Expected answer unavailable", view["detail"])
        self.save(example_report("agentcore", "cost_usd_per_session"))
        data = dashboard.build_data(self.root)
        initial = self.browser_probe(data)
        self.assertEqual(initial["chosen"], "")
        self.assertIn("Choose a common cost measure", initial["chart"])
        chosen = self.browser_probe(data, '$("cost-metric").onchange({target:{value:"cost_usd_per_session"}});')
        self.assertIn("1,000 attempts", chosen["chart"])
        self.assertIn("different decision cost measures", chosen["note"])


if __name__ == "__main__":
    unittest.main()
