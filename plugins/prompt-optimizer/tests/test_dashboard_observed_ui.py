"""The UI keeps common returned-answer measurements apart from full-run scores."""
import copy
import importlib.util
import json
from html.parser import HTMLParser
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('observed_ui_support', Path(__file__).with_name('test_dashboard_comparison_only.py'))
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


class ObservedResultsUITests(unittest.TestCase):
    setUp = support.ComparisonOnlyDashboardTests.setUp
    build = support.ComparisonOnlyDashboardTests.build
    browser = support.ComparisonOnlyDashboardTests.browser

    def financial_view(self):
        """Sanitized presentation fixture: 90 planned per arm; common 86 returns."""
        data = self.build(*support.fixture())
        report = data['reports'][0]
        report.update(status='needs_review', grading_error_records=4, unresolved_attempts=[])
        report['model_costs']['unpriced_requests_or_incomplete_traces'] = 8
        report['common_observed_answers'] = {'valid': True, 'issues': [], 'count': 86,
            'planned_pairs': 90, 'excluded_pairs': 4,
            'case_repeats': [[f'synthetic-{i // 3}', i % 3] for i in range(86)]}
        values = {
            'source_original': (90, .06972142790697675, 11998.95905, 14450.047486,
                                220500, 220328, 0, 104578),
            'target_original': (88, .03629181395348837, 8149.076951, 10975.50276,
                                220500, 220500, 0, 68729),
            'target_comparison': (88, .04168365581395349, 7651.6865165, 10215.31727,
                                  210352, 137110, 73242, 61791),
        }
        for group in report['groups']:
            returned, cost, p50, p95, inputs, reads, writes, outputs = values[group['id']]
            group['observed_answers'] = {'valid': True, 'issues': [], 'planned_attempts': 90,
                'observed_attempts': 90, 'returned_answers': returned, 'correct_answers': returned,
                'incorrect_answers': 0, 'application_failures': 90-returned, 'missing_attempts': 0}
            group['common_observed_answers'] = {'valid': True, 'issues': [], 'count': 86,
                'correct_answers': 86, 'known_model_cost_usd': cost*86,
                'mean_model_cost_usd_per_returned_answer': cost,
                'p50_conversation_ms': p50, 'p95_conversation_ms': p95,
                'token_totals': {'input': inputs, 'uncached_input': inputs-reads-writes,
                                 'cache_read': reads, 'cache_write': writes, 'output': outputs},
                'excluded_returned_answers': returned-86}
            group['score'].update(n_sessions=90, accuracy=returned/90)
            # Retain the unknown full-run measurements. They must not be replaced
            # with a returned-answer mean by the presentation layer.
            if returned < 90:
                group['core_scope']['complete'] = False
                group['score'].update(cost_usd_per_session=None, cost_usd_per_success=None,
                                      latency_p50_s=None, latency_p95_s=None)
            original = copy.deepcopy(group['trials'])
            group['trials'] = [copy.deepcopy(original[i % len(original)]) for i in range(90)]
            for i, row in enumerate(group['trials']):
                row.update(id=f'{group["id"]}-{i}', case_id=f'synthetic-{i//3}', repeat=i%3,
                           status='completed' if i < returned else 'error')
                for key in row['required_check_ids']:
                    row['checks'][key] = {'pass': i < returned}
        return data

    def probe(self, data, prefix=''):
        return self.browser(data, prefix + '''new Blob([JSON.stringify({
          observed:$("observed-results").innerHTML, hidden:$("observed-results").hidden,
          fullOpen:$("full-run-details").open, summaryHidden:$("full-run-summary").hidden
        })]);''')

    def test_three_financial_rows_and_points_use_the_same_86_answers(self):
        data = self.financial_view(); before = copy.deepcopy(data)
        view = self.probe(data); text = view['exported']['observed']
        self.assertFalse(view['exported']['hidden']); self.assertFalse(view['exported']['fullOpen'])
        self.assertFalse(view['exported']['summaryHidden'])
        self.assertEqual(text.count('data-observed-row='), 3)
        self.assertEqual(text.count('data-observed-point-key='), 3)
        for expected in ('Compare model and prompt results', '86 matched returned answers per configuration; reliability uses 90 attempts each',
                         'GPT-6 Astra · Original prompt', 'GPT-6.2 Astra · Original prompt',
                         'GPT-6.2 Astra · Revised prompt for review', '$0.069721', '$0.036292', '$0.041684',
                         '14.450 s', '10.976 s', '10.215 s', '99.9% / 0.0%', '100.0% / 0.0%', '65.2% / 34.8%',
                         'USD / 1,000 returned answers', 'Incorrect returned answers stay included',
                         'do not select a winner', 'Full-run costs remain unknown'):
            self.assertIn(expected, text)
        self.assertEqual(text.count('90 / 90'), 2)
        self.assertEqual(text.count('88 / 90'), 4)
        self.assertEqual(text.count('2<small>Application failures'), 2)
        self.assertEqual(data, before)

    def test_all_three_model_and_prompt_names_open_the_exact_prompt_cards(self):
        data = self.financial_view()
        actions = '''const links=$("observed-results").querySelectorAll("[data-prompt-link]");
          links[2].click();'''
        view = self.probe(data, actions)
        self.assertIn('integrity', view['visible_views'])
        self.assertEqual(view['scrolled_to'], 'prompt-report-1-target_comparison')
        self.assertEqual(view['focused'], 'prompt-report-1-target_comparison')
        self.assertIn('Revised prompt for review', view['prompt_cards'])
        returned = self.probe(data, actions + '$("prompt-cards").querySelectorAll("[data-return-overview]")[0].click();')
        self.assertIn('overview', returned['visible_views'])
        self.assertEqual(returned['scrolled_to'], 'observed-results')

    def test_missing_measured_values_keep_the_configuration_visible_without_zero(self):
        data = self.financial_view(); group = data['reports'][0]['groups'][-1]
        group['common_observed_answers']['mean_model_cost_usd_per_returned_answer'] = None
        group['common_observed_answers']['p95_conversation_ms'] = None
        view = self.probe(data); text = view['exported']['observed']
        self.assertEqual(text.count('data-observed-row='), 3)
        self.assertEqual(text.count('data-observed-point-key='), 2)
        self.assertIn('Measurements unavailable:', text)
        self.assertIn('These configurations remain in the table above.', text)
        revised = text.split('data-observed-row="report-1::target_comparison"')[1].split('</tr>')[0]
        self.assertIn('Unknown', revised); self.assertNotIn('0.000 s', revised)

    def test_invalid_or_absent_common_evidence_does_not_fall_back_to_independent_subsets(self):
        for mutation in ('invalid', 'absent', 'mismatched_count'):
            with self.subTest(mutation=mutation):
                data = self.financial_view(); report = data['reports'][0]
                if mutation == 'invalid': report['common_observed_answers'].update(valid=False, issues=['Unverified frozen request'])
                elif mutation == 'absent': report.pop('common_observed_answers')
                else:
                    for group in report['groups']: group['common_observed_answers']['count'] = 85
                text = self.probe(data)['exported']['observed']
                self.assertEqual(text.count('data-observed-row='), 3)
                self.assertNotIn('data-observed-point-key=', text)
                self.assertIn('Measurements unavailable:', text)

    def test_unverified_arm_does_not_show_its_recorded_values_as_measured(self):
        data = self.financial_view()
        data['reports'][0]['groups'][-1]['observed_answers'].update(valid=False, issues=['Frozen request mismatch'])
        text = self.probe(data)['exported']['observed']
        self.assertEqual(text.count('data-observed-row='), 3)
        self.assertEqual(text.count('data-observed-point-key='), 2)
        revised = text.split('data-observed-row="report-1::target_comparison"')[1].split('</tr>')[0]
        self.assertIn('Unknown', revised)
        self.assertIn('Frozen request mismatch', revised)
        self.assertNotIn('$0.041684', revised)

    def test_normal_complete_dashboard_keeps_existing_layout(self):
        data = self.build(*support.fixture())
        view = self.probe(data)
        self.assertTrue(view['exported']['hidden']); self.assertTrue(view['exported']['fullOpen'])
        self.assertTrue(view['exported']['summaryHidden'])
        self.assertEqual(view['exported']['observed'], '')
        self.assertIn('data-point-key=', view['chart'])

    def test_cost_selector_cannot_silently_change_returned_answer_basis(self):
        data = self.financial_view(); initial = self.probe(data)['exported']['observed']
        changed = self.probe(data, '$("cost-metric").onchange({target:{value:"cost_usd_per_session"}});')
        self.assertEqual(changed['exported']['observed'], initial)
        self.assertFalse(changed['exported']['fullOpen'])

    def test_export_preserves_full_scores_and_separately_names_matched_measurements(self):
        data = self.financial_view()
        export = self.browser(data, '$("export").onclick();')['exported']
        self.assertEqual(export['primary_chart_basis'], 'common matched returned answers')
        self.assertEqual(export['measured_returned_answers'][0]['common']['count'], 86)
        arms = export['measured_returned_answers'][0]['configurations']
        self.assertEqual([arm['all_attempts']['returned_answers'] for arm in arms], [90, 88, 88])
        self.assertEqual(sum(arm['all_attempts']['application_failures'] for arm in arms), 4)
        self.assertEqual([arm['matched']['count'] for arm in arms], [86, 86, 86])
        self.assertIsNone(data['reports'][0]['groups'][-1]['score']['cost_usd_per_success'])

    def test_primary_section_sits_outside_collapsed_full_accounting(self):
        class Structure(HTMLParser):
            def __init__(self): super().__init__(); self.stack=[]; self.parents={}
            def handle_starttag(self, tag, attrs):
                attributes=dict(attrs)
                if attributes.get('id'): self.parents[attributes['id']]=list(self.stack)
                if tag not in ('meta','link','br','hr','input','img'): self.stack.append((tag,attributes.get('id')))
            def handle_endtag(self, tag):
                for index in range(len(self.stack)-1,-1,-1):
                    if self.stack[index][0]==tag: self.stack=self.stack[:index]; break
        parsed=Structure(); parsed.feed(support.dashboard.TEMPLATE.read_text())
        self.assertNotIn(('details','full-run-details'), parsed.parents['observed-results'])
        for ident in ('tradeoff','comparison','insight','prompt-evidence'):
            self.assertIn(('details','full-run-details'), parsed.parents[ident])


if __name__ == '__main__': unittest.main()
