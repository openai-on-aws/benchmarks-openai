"""An explicitly reused same-model baseline is not missing model evidence."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('same_model_dashboard_support', Path(__file__).with_name('test_dashboard_metrics.py'))
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


class SameModelDashboardTests(unittest.TestCase):
    setUp = support.DashboardMetricTests.setUp
    save = support.DashboardMetricTests.save
    freeze = support.DashboardMetricTests.freeze
    browser_probe = support.DashboardMetricTests.browser_probe
    comparison_blocks = support.DashboardMetricTests.comparison_blocks

    def test_explicit_same_model_alias_keeps_prompt_comparison_without_missing_model_warning(self):
        report = support.optimization_report(original_correct=10, selected_correct=9)
        report['optimization'] = {'generations': []}
        report['holdout']['source_original'] = dict(copy.deepcopy(report['holdout']['target_original']), reused_from='target_original')
        self.save(report)
        view = self.browser_probe(support.dashboard.build_data(self.root))
        self.assertEqual(self.comparison_blocks(view, 'model'), [])
        self.assertEqual(len(self.comparison_blocks(view, 'prompt')), 1)
        self.assertIn('data-result="tradeoff"', self.comparison_blocks(view, 'prompt')[0])
        self.assertIn('This run uses one model and compares prompts.', view['insight'])
        self.assertNotIn('Unresolved model', view['insight'])
        self.assertNotIn('Some results are missing', view['insight'])

    def test_fixed_same_model_run_does_not_invent_a_missing_comparison(self):
        report = support.optimization_report()
        report['optimization'] = None
        original = report['holdout']['target_original']
        report['holdout']['source_original'] = dict(copy.deepcopy(original), reused_from='target_original')
        report['holdout']['target_selected'] = dict(copy.deepcopy(original), reused_from='target_original')
        report['selected_prompt'].update(prompt=original['score']['prompt'], reason='Baseline only')
        self.save(report)
        view = self.browser_probe(support.dashboard.build_data(self.root))
        self.assertIn('This run uses one model and a fixed prompt.', view['insight'])
        self.assertNotIn('No supported comparison pair', view['insight'])
        self.assertEqual(self.comparison_blocks(view, 'model'), [])
        self.assertEqual(self.comparison_blocks(view, 'prompt'), [])

    def test_distinct_models_still_have_separate_model_comparison(self):
        self.save(support.optimization_report())
        view = self.browser_probe(support.dashboard.build_data(self.root))
        self.assertEqual(len(self.comparison_blocks(view, 'model')), 1)
        self.assertEqual(len(self.comparison_blocks(view, 'prompt')), 1)
        self.assertNotIn('This run uses one model', view['insight'])

    def test_missing_source_without_declared_reuse_still_warns(self):
        report = support.optimization_report()
        del report['holdout']['source_original']
        self.save(report)
        view = self.browser_probe(support.dashboard.build_data(self.root))
        self.assertIn('Both configurations are needed', view['insight'])
        self.assertIn('Unresolved model', view['insight'])
        self.assertNotIn('This run uses one model', view['insight'])


if __name__ == '__main__':
    unittest.main()
