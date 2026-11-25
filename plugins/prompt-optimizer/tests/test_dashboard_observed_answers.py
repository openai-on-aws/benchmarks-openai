"""Descriptive comparisons retain failures and use one common measured cohort."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('observed_answer_support', Path(__file__).with_name('test_dashboard_mixed_assessment.py'))
support = importlib.util.module_from_spec(spec); spec.loader.exec_module(support)
dashboard = support.dashboard


def reseal(report, config):
    """Test-only: construct frozen IDs after a deliberate configuration change."""
    config.pop('fingerprint', None)
    config['fingerprint'] = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    for name, group in report['holdout'].items():
        variant = config['source'] if name == 'source_original' else config['target']
        for row in group['trials']:
            ident = hashlib.sha256(json.dumps([config['fingerprint'], variant, group['score']['prompt'], row['case_id'], row['repeat'], 'holdout', 'standard'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            row['id'] = row['result']['trial_id'] = ident
            for i, call in enumerate(row['result']['route_calls']): call['request_id'] = ident + '-' + str(i)


def fixture():
    report, config = support.fixtures()
    for group in report['holdout'].values():
        for row in group['trials']:
            result = row['result']; turn = result['turn_records'][0]
            turn.update(started_ns=10_000_000, answered_ns=10_000_000 + int(result['timing']['conversation_ms'] * 1e6))
    return report, config


def fail(row):
    row['result'].update(status='error', outputs=[])
    for call in row['result']['route_calls']:
        call.update(status='HTTPError', http_status=500, usage=None); call.pop('model_returned')
    row['result']['timing']['conversation_ms'] = None
    for key in row['checks']: row['checks'][key] = {'pass': False, 'reason': 'execution did not complete'}


class ObservedAnswerTests(unittest.TestCase):
    setUp = support.MixedAssessmentDashboardTests.setUp
    build = support.MixedAssessmentDashboardTests.build

    def view(self, report, config):
        built = self.build(report, config)['reports'][0]
        return built, {g['id']: g for g in built['groups']}

    def test_complete_common_cohort_keeps_incorrect_answers_in_cost_denominator(self):
        report, config = fixture(); before = copy.deepcopy(report)
        result, groups = self.view(report, config)
        self.assertTrue(result['common_observed_answers']['valid'])
        self.assertEqual(result['common_observed_answers']['count'], 6)
        for name, expected_correct in [('source_original', 3), ('target_original', 3), ('target_selected', 5)]:
            group = groups[name]; obs = group['observed_answers']; common = group['common_observed_answers']
            self.assertTrue(obs['valid'], obs['issues'])
            self.assertEqual((obs['returned_answers'], obs['correct_answers'], obs['incorrect_answers']), (6, expected_correct, 6-expected_correct))
            self.assertEqual(common['count'], 6); self.assertEqual(common['correct_answers'], expected_correct)
            self.assertAlmostEqual(common['mean_model_cost_usd_per_returned_answer'], common['known_model_cost_usd']/6)
            self.assertEqual(common['p95_conversation_ms'], 1005)
            self.assertEqual(group['score'], before['holdout'][name]['score'])
        self.assertEqual(result['selected_prompt'], before['selected_prompt'])
        for name in before['holdout']: self.assertEqual(report['holdout'][name], before['holdout'][name])

    def test_different_failed_pairs_produce_one_common_set_and_keep_full_coverage(self):
        report, config = fixture()
        fail(report['holdout']['target_original']['trials'][0]); fail(report['holdout']['target_selected']['trials'][1])
        result, groups = self.view(report, config)
        self.assertTrue(result['common_observed_answers']['valid'])
        self.assertEqual(result['common_observed_answers']['count'], 4)
        self.assertEqual(result['common_observed_answers']['excluded_pairs'], 2)
        for name in groups:
            obs = groups[name]['observed_answers']; common = groups[name]['common_observed_answers']
            self.assertEqual(obs['planned_attempts'], 6); self.assertEqual(obs['observed_attempts'], 6)
            self.assertEqual(obs['returned_answers'], 6 if name == 'source_original' else 5)
            self.assertEqual(obs['application_failures'], 0 if name == 'source_original' else 1)
            self.assertEqual(common['count'], 4)
            self.assertEqual(common['correct_answers'], 3, 'The shared incorrect return remains in every denominator')
            self.assertEqual(common['excluded_returned_answers'], 2 if name == 'source_original' else 1)

    def test_partial_usage_and_timing_use_same_joint_intersection_without_zeroes(self):
        report, config = fixture(); rows = report['holdout']['target_selected']['trials']
        rows[0]['result']['route_calls'][0]['usage'] = None
        rows[1]['result']['timing']['conversation_ms'] = None
        result, groups = self.view(report, config); obs = groups['target_selected']['observed_answers']
        self.assertTrue(obs['valid'], obs['issues']); self.assertFalse(obs['cost_complete']); self.assertFalse(obs['timing_complete'])
        self.assertEqual((obs['returned_answers'], obs['fully_priced_answers'], obs['timed_answers'], obs['token_covered_answers']), (6,5,5,5))
        self.assertIsNone(obs['mean_model_cost_usd_per_returned_answer']); self.assertIsNone(obs['p95_conversation_ms'])
        self.assertEqual(result['common_observed_answers']['count'], 4)
        self.assertTrue(all(g['common_observed_answers']['count'] == 4 for g in groups.values()))
        self.assertEqual(result['common_observed_answers']['case_repeats'], [{'case_id': c, 'repeat': r} for c in ('case-b','case-c') for r in (0,1)])

    def test_all_unknown_usage_does_not_invent_zero_cost(self):
        report, config = fixture()
        for row in report['holdout']['target_selected']['trials']: row['result']['route_calls'][0]['usage'] = None
        result, groups = self.view(report, config); obs = groups['target_selected']['observed_answers']
        self.assertTrue(obs['valid']); self.assertEqual(obs['fully_priced_answers'], 0)
        self.assertIsNone(obs['known_model_cost_usd']); self.assertIsNone(obs['mean_model_cost_usd_per_returned_answer'])
        self.assertFalse(result['common_observed_answers']['valid'])

    def test_bad_identity_receipt_or_required_grade_cannot_be_silently_dropped(self):
        for mutation in ('model','requested','request_id','captured_hash','effective_hash','identity','duplicate','missing_grade','grade_error','output','detached_grade','empty_output','inflight','settings','malformed_receipt'):
            with self.subTest(mutation=mutation):
                report, config = fixture(); group = report['holdout']['target_selected']; row = group['trials'][0]
                call = row['result']['route_calls'][0]
                if mutation == 'model': call['model_returned'] = 'wrong'
                elif mutation == 'requested': call['model_requested'] = 'wrong'
                elif mutation == 'request_id': call.pop('request_id')
                elif mutation == 'captured_hash': row['result']['replay']['captured_request_sha256'] = 'wrong'
                elif mutation == 'effective_hash': row['result']['replay']['effective_request_sha256'] = 'wrong'
                elif mutation == 'identity': row['id'] = 'wrong'
                elif mutation == 'duplicate': group['trials'][1] = copy.deepcopy(row)
                elif mutation == 'missing_grade': row['checks'].pop('amount')
                elif mutation == 'grade_error': row['checks']['amount']['error'] = True
                elif mutation == 'output': row['result']['turn_records'][0]['output'] = 'different'
                elif mutation == 'detached_grade':
                    answer = json.loads(row['result']['outputs'][0]); answer['amount'] = 999
                    row['result']['outputs'][0] = row['result']['turn_records'][0]['output'] = json.dumps(answer)
                elif mutation == 'empty_output': row['result']['outputs'] = []
                elif mutation == 'inflight': row['result']['status'] = 'started'
                elif mutation == 'settings': call['effective_parameters']['reasoning'] = {'effort':'low'}
                elif mutation == 'malformed_receipt': row['result']['route_calls'] = [None]
                # Call the contract directly for malformed legacy containers;
                # surrounding historical renderers are outside this contract.
                assessment = dashboard._assessment(config)
                value = dashboard._observed_answers(report, config, 'target_selected', group, assessment, {})
                self.assertFalse(value['valid'], mutation); self.assertTrue(value['issues'])
                if mutation != 'malformed_receipt':
                    result, _ = self.view(report, config)
                    self.assertFalse(result['common_observed_answers']['valid'], mutation)

    def test_missing_or_inconsistent_timing_proof_reduces_timed_coverage(self):
        for mutation in ('missing_timestamp','negative_timestamp','invented_elapsed','infinite_elapsed'):
            report, config = fixture(); row = report['holdout']['target_selected']['trials'][0]
            if mutation == 'missing_timestamp': row['result']['turn_records'][0].pop('answered_ns')
            elif mutation == 'negative_timestamp': row['result']['turn_records'][0]['started_ns'] = -1
            elif mutation == 'invented_elapsed': row['result']['timing']['conversation_ms'] = 0
            else: row['result']['timing']['conversation_ms'] = float('inf')
            result, groups = self.view(report, config); obs = groups['target_selected']['observed_answers']
            self.assertTrue(obs['valid']); self.assertEqual(obs['timed_answers'], 5)
            self.assertIsNone(obs['p95_conversation_ms']); self.assertEqual(result['common_observed_answers']['count'], 5)

    def test_missing_price_is_unknown_and_does_not_reprice_from_other_arm(self):
        report, config = fixture(); config['routes']['target'].pop('output_usd_per_million'); reseal(report, config)
        result, groups = self.view(report, config)
        self.assertTrue(groups['source_original']['observed_answers']['cost_complete'])
        for name in ('target_original','target_selected'):
            obs = groups[name]['observed_answers']; self.assertTrue(obs['valid'], obs['issues'])
            self.assertEqual(obs['fully_priced_answers'], 0); self.assertIsNone(obs['mean_model_cost_usd_per_returned_answer'])
        self.assertFalse(result['common_observed_answers']['valid'])

    def test_cache_totals_use_common_answers_and_reasoning_is_not_double_charged(self):
        report, config = fixture()
        for route in (config['routes']['source'], config['routes']['target']):
            route.update(cached_input_usd_per_million=1., cache_write_usd_per_million=12.)
        for group in report['holdout'].values():
            for row in group['trials']:
                row['result']['route_calls'][0]['usage']['input_tokens_details'] = {'cached_tokens':20,'cache_write_tokens':10}
        reseal(report, config); fail(report['holdout']['target_original']['trials'][0])
        result, groups = self.view(report, config)
        self.assertEqual(result['common_observed_answers']['count'], 5)
        for group in groups.values():
            t = group['common_observed_answers']['token_totals']
            self.assertEqual(t['cache_read'],100); self.assertEqual(t['cache_write'],50)
            self.assertEqual(t['input'], t['uncached_input']+t['cache_read']+t['cache_write'])
            expected = (t['uncached_input']*10+t['cache_read']+t['cache_write']*12+t['output']*100)/1e6
            self.assertAlmostEqual(group['common_observed_answers']['known_model_cost_usd'],expected)

    def test_optional_judge_error_does_not_remove_returned_application(self):
        report, config = fixture(); report['holdout']['target_selected']['trials'][0]['checks']['explanation'] = {'pass':False,'error':True,'failure_category':'grading'}
        result, groups = self.view(report, config)
        self.assertTrue(result['common_observed_answers']['valid'])
        self.assertEqual(groups['target_selected']['common_observed_answers']['count'],6)

    def test_non_replay_cases_require_input_history_and_configured_settings(self):
        report, config = fixture()
        for case in config['datasets']['heldout']: case.pop('replay')
        reseal(report, config)
        result, _ = self.view(report, config)
        self.assertTrue(result['common_observed_answers']['valid'],result['common_observed_answers']['issues'])
        report['holdout']['target_selected']['trials'][0]['result']['turn_records'][0]['user'] = 'different input'
        result, _ = self.view(report, config); self.assertFalse(result['common_observed_answers']['valid'])

    def test_missing_frozen_input_and_unresolved_app_block_observed_claims(self):
        report, config = fixture()
        result = self.build(report, config, with_config=False)['reports'][0]
        self.assertFalse(result['common_observed_answers']['valid'])
        report['unresolved_attempts'] = [{'id':report['holdout']['target_selected']['trials'][0]['id'],'state':'started'}]
        result, _ = self.view(report, config); self.assertFalse(result['common_observed_answers']['valid'])

    def test_cross_arm_receipt_reuse_and_corrupt_failed_request_are_rejected(self):
        for mutation in ('cross_arm', 'failed_cross_arm', 'failed_hash', 'failed_settings'):
            with self.subTest(mutation=mutation):
                report, config = fixture(); row = report['holdout']['target_original']['trials'][0]
                source_id = report['holdout']['source_original']['trials'][0]['result']['route_calls'][0]['request_id']
                if mutation != 'cross_arm': fail(row)
                if mutation in ('cross_arm', 'failed_cross_arm'): row['result']['route_calls'][0]['request_id'] = source_id
                elif mutation == 'failed_hash': row['result']['replay']['effective_request_sha256'] = 'wrong'
                else: row['result']['route_calls'][0]['effective_parameters']['reasoning'] = {'effort':'low'}
                result, _ = self.view(report, config)
                self.assertFalse(result['common_observed_answers']['valid'])

    def test_source_only_judgments_bind_saved_request_to_exact_output_without_calls(self):
        report, config = fixture(); config['document']['runner'].pop('grading_reference_mode')
        for case in config['datasets']['heldout']: case.pop('expected')
        for check in config['document']['checks']:
            if check['required']:
                check.update(method='llm_judge', judge_model_id='judge')
                config['grading'][check['id']] = {'type':'llm_source_rubric','rubric':'Use only supplied source evidence.'}
        config['grading']['tone'] = {'type':'json_schema','schema':{'type':'object'}}
        reseal(report, config); report['judge_requests'] = []
        from customer_eval.source_grading import grade_source, build_source_context
        cases = {c['id']:c for c in config['datasets']['heldout']}
        for group in report['holdout'].values():
            for row in group['trials']:
                case = cases[row['case_id']]; source = build_source_context(case)[-1]
                def saved(model_id, request_text):
                    checks = {c['id']:{'status':'pass' if row['checks'][c['id']]['pass'] else 'fail',
                        'reason':'Saved source assessment.', 'evidence':[{'source_id':source['source_id'],'quote':source['text']}]} for c in config['document']['checks'] if config['grading'][c['id']]['type']=='llm_source_rubric'}
                    text = json.dumps({'checks':checks})
                    report['judge_requests'].append({'status':'completed','purpose':'judge','model_id':model_id,
                        'model_requested':config['routes'][model_id]['model_id'], 'model_returned':config['routes'][model_id]['model_id'],
                        'request_text':request_text,'text':text})
                    return text
                row['checks'].update(grade_source(config, case, row['result'], saved))
        result, _ = self.view(report, config)
        self.assertEqual(result['assessment']['mode'],'source_judge')
        self.assertTrue(result['common_observed_answers']['valid'],result['common_observed_answers']['issues'])
        row = report['holdout']['target_selected']['trials'][0]
        row['result']['outputs'][0] = row['result']['turn_records'][0]['output'] = '{"changed":"after judging"}'
        result, _ = self.view(report, config)
        self.assertFalse(result['common_observed_answers']['valid'])

    def test_unsupported_python_explains_render_environment_without_claiming_bad_evidence(self):
        report, config = fixture(); assessment = dashboard._assessment(config)
        with mock.patch.object(dashboard.sys, 'version_info', (3, 9, 6)):
            value = dashboard._observed_answers(report, config, 'target_original', report['holdout']['target_original'], assessment, {})
        self.assertFalse(value['valid'])
        message = ' '.join(value['issues'])
        self.assertIn('Python 3.9.6', message); self.assertIn('workspace Python 3.12 virtual environment', message)
        self.assertIn('requirements.txt', message); self.assertIn('already-rendered HTML', message)
        self.assertNotIn('could not be verified', message)

    def test_missing_jsonschema_has_explicit_setup_diagnostic(self):
        report, config = fixture()
        config['document']['checks'].append({'id':'schema','required':True,'method':'deterministic','question':'Valid schema?'})
        config['grading']['schema'] = {'type':'json_schema','schema':{'type':'object'}}
        for group in report['holdout'].values():
            for row in group['trials']: row['checks']['schema'] = {'pass':True}
        reseal(report, config); assessment = dashboard._assessment(config)
        import builtins
        original_import = builtins.__import__
        def without_schema(name, *args, **kwargs):
            if name == 'jsonschema': raise ModuleNotFoundError('No module named jsonschema', name='jsonschema')
            return original_import(name, *args, **kwargs)
        with mock.patch('builtins.__import__', side_effect=without_schema):
            value = dashboard._observed_answers(report, config, 'target_original', report['holdout']['target_original'], assessment, {})
        self.assertFalse(value['valid'])
        message = ' '.join(value['issues'])
        self.assertIn('dependency is unavailable: jsonschema', message)
        self.assertIn('Python 3.12', message); self.assertIn('requirements.txt', message)
        self.assertIsNone(value['mean_model_cost_usd_per_returned_answer'])


if __name__ == '__main__': unittest.main()
