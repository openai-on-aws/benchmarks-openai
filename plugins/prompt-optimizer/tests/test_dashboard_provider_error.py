"""Public dashboard accepts failed judge receipts without inventing output or cost."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('provider_error_dashboard_support', Path(__file__).with_name('test_dashboard_mixed_assessment.py'))
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


def fixtures():
    report, config = support.fixtures()
    report['judge_requests'] = [{'status': 'provider_error', 'purpose': 'judge', 'model_id': 'judge',
        'model_requested': 'us.openai.gpt-6.2-astra', 'http_status': 500, 'usage': None,
        'request_text': 'Opaque saved source-judge request',
        'provider_error': {'http_status': 500, 'body_omitted': True}}]
    report.update(evaluation_status='needs_review', grading_error_records=1, unresolved_attempts=[])
    report['model_costs'] = {'estimated_model_cost_usd': None, 'known_model_cost_usd': 15.,
        'by_purpose_usd': {'agent': 10., 'optimizer': 2., 'judge': 3.},
        'unpriced_requests_or_incomplete_traces': 1}
    check = report['holdout']['target_original']['trials'][0]['checks']['explanation']
    check.pop('status'); check.pop('evidence')
    check.update(error=True, failure_category='grading', reason='Provider returned HTTP 500; saved failure receipt.', **{'pass': False})
    return report, config


class ProviderErrorDashboardTests(unittest.TestCase):
    setUp = support.MixedAssessmentDashboardTests.setUp
    build = support.MixedAssessmentDashboardTests.build
    browser = support.MixedAssessmentDashboardTests.browser

    def test_failed_receipt_has_role_without_response_text_or_returned_model(self):
        report, config = fixtures()
        frozen_scores = {key: copy.deepcopy(group['score']) for key,group in report['holdout'].items()}
        data = self.build(report, config); result = data['reports'][0]
        self.assertEqual(result['assessment']['mode'], 'reference_and_source')
        self.assertEqual(result['assessment']['quality_label'], 'Reference check pass rate')
        judge = next(role for role in result['model_roles'] if role['role'] == 'judge')
        self.assertEqual(judge, {'role': 'judge', 'model': 'us.openai.gpt-6.2-astra', 'reasoning': None, 'requests': 1})
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['grading_error_records'], 1)
        self.assertEqual(result['unresolved_attempts'], [])
        self.assertIsNone(result['model_costs']['estimated_model_cost_usd'])
        self.assertEqual(result['model_costs']['known_model_cost_usd'], 15.)
        self.assertEqual(result['model_costs']['unpriced_requests_or_incomplete_traces'], 1)
        for group in result['groups']:
            self.assertEqual(group['score'], frozen_scores[group['id']])
            self.assertEqual(group['cost_metrics']['cost_usd_per_session']['status'], 'known')
            self.assertEqual(group['cost_metrics']['cost_usd_per_success']['status'], 'known')
        saved = json.loads((self.root/'report.json').read_text())
        self.assertEqual(saved['judge_requests'], report['judge_requests'])
        self.assertEqual(len(saved['judge_requests']), 1)
        self.assertNotIn('text', saved['judge_requests'][0])
        self.assertNotIn('model_returned', saved['judge_requests'][0])

    def test_ui_retains_unknown_full_cost_and_counts_failed_judge_request(self):
        data = self.build(*fixtures())
        actions = 'new Blob([JSON.stringify({integrity:$("integrity-cards").innerHTML,kpis:$("kpis").innerHTML,table:$("comparison").innerHTML})]);'
        view = self.browser(data, actions); integrity = view['exported']['integrity']
        self.assertIn('Estimated run model cost</span><strong>Unknown</strong>', integrity)
        self.assertIn('Known priced portion</span><strong>$15.0000</strong>', integrity)
        self.assertIn('Judge model costs · known priced portion', integrity)
        self.assertIn('$3.000000', integrity)
        self.assertIn('Unpriced / incomplete traces</span><strong>1</strong>', integrity)
        self.assertIn('Explanation judge:', integrity)
        self.assertIn('1 recorded requests', integrity)
        self.assertIn('Reference check pass rate', view['exported']['kpis'])
        self.assertIn('1 unknown', view['exported']['table'])


if __name__ == '__main__': unittest.main()
