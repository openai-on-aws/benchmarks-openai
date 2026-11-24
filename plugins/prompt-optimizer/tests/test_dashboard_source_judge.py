"""Source-based LLM judgments stay distinct from reference-answer accuracy."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


support = load('source_judge_dashboard_evidence_support', 'test_dashboard_evidence.py')
dashboard = support.dashboard


def fixtures():
    report, config = support.fixtures()
    config['routes'] = {'judge': {'model_id': 'openai.gpt-6.2-astra'}}
    config['policy'] = 'Judge extraction against the supplied document.'
    for check in config['document']['checks']:
        if check['id'] == 'tone':
            check['method'] = 'deterministic'
            config['grading']['tone'] = {'type': 'json_schema', 'schema': {'type': 'object'}}
        else:
            check.update(method='llm_judge', judge_model_id='judge')
            config['grading'][check['id']] = {'type': 'llm_source_rubric', 'rubric': 'Extract only facts supported by the source; abstain when evidence is insufficient.'}
    context, criteria, digest = dashboard._source_helpers()
    cases = {case['id']: case for case in config['datasets']['heldout']}
    for case in cases.values():
        case.pop('expected')
        case['turns'] = ['Evaluate complete input for ' + case['id'] + ' <script>untrusted()</script>']
    for name, group in report['holdout'].items():
        for row in group['trials']:
            case = cases[row['case_id']]
            row['result']['turn_records'][0]['user'] = case['turns'][0]
            sources = context(case, policy=config['policy'])
            quote = next(s for s in sources if s['role'] == 'user')
            for ident in ('actions', 'amount'):
                check = row['checks'][ident]
                check.update(status='pass' if check['pass'] else 'fail',
                    evidence=[{'source_id': quote['source_id'], 'quote': quote['text']}],
                    source_sha256=digest(sources), rubric_sha256=digest(criteria(config)))
            if name != 'target_selected' and row['case_id'] == 'case-a' and row['repeat'] == 0:
                row['checks']['actions'].update(status='needs_review', reason='Insufficient evidence <img src=x>', evidence=[], **{'pass': False})
    report['model_costs'] = {'by_purpose_usd': {'agent': 10., 'optimizer': 2., 'judge': 3.},
        'known_model_cost_usd': 15., 'estimated_model_cost_usd': 15., 'unpriced_requests_or_incomplete_traces': 0}
    return report, config


class SourceJudgeDashboardTests(unittest.TestCase):
    setUp = support.PromptEvidenceTests.setUp
    build = support.PromptEvidenceTests.build
    evidence = support.PromptEvidenceTests.evidence

    def browser(self, data, actions=''):
        return support.existing_tests.DashboardMetricTests.browser_probe(self, data, actions)

    def test_verified_source_mode_works_without_reference_answers(self):
        report, config = fixtures();data = self.build(report, config)
        result = data['reports'][0];evidence = self.evidence(data)
        self.assertTrue(evidence['available'], evidence['issues'])
        self.assertEqual(result['assessment']['mode'], 'source_judge')
        self.assertEqual(result['assessment']['judge_model'], 'openai.gpt-6.2-astra')
        self.assertIn('no expected answers', result['provenance'])
        for group in result['groups']:
            for trial in group['trials']:
                self.assertFalse(trial['expected_available'])
                self.assertIsNone(trial['expected_answer'])
                self.assertTrue(trial['source_available'])
                self.assertIn(trial['source_ref'], result['source_bundles'])
        self.assertEqual(len(result['source_bundles']), 3)

    def test_abstentions_remain_in_all_attempt_denominators(self):
        data = self.build(*fixtures());evidence = self.evidence(data)
        check = next(c for c in evidence['criteria'] if c['id'] == 'actions')
        self.assertEqual(check['before'], {'passed': 4, 'failed': 1, 'needs_review': 1, 'unknown': 0, 'total': 6})
        self.assertEqual(evidence['outcomes']['attempts'], 6)
        self.assertEqual(evidence['outcomes']['before_passes'], 3)
        self.assertEqual(evidence['outcomes']['before_needs_review'], 1)
        self.assertEqual(evidence['outcomes']['after_needs_review'], 0)

    def test_source_or_rubric_digest_mismatch_blocks_paired_claims(self):
        for field in ('source_sha256', 'rubric_sha256'):
            with self.subTest(field=field):
                report, config = fixtures()
                report['holdout']['target_selected']['trials'][0]['checks']['actions'][field] = 'wrong'
                evidence = self.evidence(self.build(report, config))
                self.assertFalse(evidence['available'])
                self.assertTrue(any('digest' in issue for issue in evidence['issues']))

    def test_source_status_cannot_contradict_pass_boolean(self):
        for status, passed in (('needs_review', True), ('pass', False), ('fail', True)):
            with self.subTest(status=status):
                report, config = fixtures()
                report['holdout']['target_selected']['trials'][0]['checks']['actions'].update(status=status, **{'pass': passed})
                self.assertFalse(self.evidence(self.build(report, config))['available'])

    def test_citations_must_match_the_preserved_source_text(self):
        for citation in ({'source_id': 'invented', 'quote': 'case-a'},
                         {'source_id': 'source_002', 'quote': 'invented fact'}):
            with self.subTest(citation=citation):
                report, config = fixtures()
                report['holdout']['target_selected']['trials'][0]['checks']['actions']['evidence'] = [citation]
                evidence = self.evidence(self.build(report, config))
                self.assertFalse(evidence['available'])
                self.assertTrue(any('does not match' in issue for issue in evidence['issues']))

    def test_all_abstentions_are_zero_passes_not_zero_attempts(self):
        report, config = fixtures()
        group = report['holdout']['target_original']
        for row in group['trials']:
            row['checks']['actions'].update(status='needs_review', evidence=[], **{'pass': False})
        group['score'].update(accuracy=0, cost_usd_per_success=None)
        evidence = self.evidence(self.build(report, config))
        self.assertTrue(evidence['available'], evidence['issues'])
        self.assertEqual(evidence['outcomes']['attempts'], 6)
        self.assertEqual(evidence['outcomes']['before_passes'], 0)
        self.assertEqual(evidence['outcomes']['before_needs_review'], 6)
        check = next(c for c in evidence['criteria'] if c['id'] == 'actions')
        self.assertEqual(check['before'], {'passed': 0, 'failed': 0, 'needs_review': 6, 'unknown': 0, 'total': 6})

    def test_full_fixed_replay_context_survives_and_editable_prompt_is_excluded(self):
        report, config = fixtures()
        from customer_eval.replay import fingerprint
        context, criteria, digest = dashboard._source_helpers()
        cases = {case['id']: case for case in config['datasets']['heldout']}
        for case in cases.values():
            body = {'instructions': 'EDITABLE-APPLICATION-PROMPT', 'input': [
                {'role': 'developer', 'content': 'FIXED-SOURCE-CONTEXT'},
                {'role': 'user', 'content': case['turns'][0]}]}
            case['replay'] = {'version': 1, 'request': body, 'request_sha256': fingerprint(body), 'prompt_location': 'instructions'}
        for group in report['holdout'].values():
            for row in group['trials']:
                sources = context(cases[row['case_id']], policy=config['policy'])
                user = next(s for s in sources if s['role'] == 'user')
                for ident in ('actions', 'amount'):
                    check = row['checks'][ident]
                    check.update(source_sha256=digest(sources), evidence=[] if check['status'] == 'needs_review' else
                                 [{'source_id': user['source_id'], 'quote': user['text']}])
        data = self.build(report, config)
        self.assertTrue(self.evidence(data)['available'])
        source_text = json.dumps(data['reports'][0]['source_bundles'])
        self.assertIn('FIXED-SOURCE-CONTEXT', source_text)
        self.assertIn('Judge extraction against', source_text)
        self.assertNotIn('EDITABLE-APPLICATION-PROMPT', source_text)

    def test_reference_mode_without_expected_answers_still_fails(self):
        report, config = support.fixtures()
        for case in config['datasets']['heldout']:
            case.pop('expected')
        data = self.build(report, config)
        self.assertEqual(data['reports'][0]['assessment']['mode'], 'reference')
        evidence = self.evidence(data)
        self.assertFalse(evidence['available'])
        self.assertIn('Matching frozen reference answers are unavailable.', evidence['issues'])

    def test_missing_or_unverified_manifest_cannot_enable_source_mode(self):
        report, config = fixtures();data = self.build(report, config, with_config=False)
        self.assertEqual(data['reports'][0]['assessment']['mode'], 'reference')
        self.assertFalse(self.evidence(data)['available'])

    def test_mixed_or_invalid_source_config_does_not_bypass_reference_checks(self):
        for mutation in ('other_judge', 'empty_rubric', 'deterministic_reference'):
            with self.subTest(mutation=mutation):
                report, config = fixtures()
                if mutation == 'other_judge':config['document']['checks'][1]['judge_model_id'] = 'other'
                elif mutation == 'empty_rubric':config['grading']['actions']['rubric'] = ''
                else:config['grading']['amount'] = {'type': 'json_equals', 'expected_key': 'answer.amount', 'path': 'amount'}
                data = self.build(report, config)
                self.assertNotEqual(data['reports'][0]['assessment']['mode'], 'source_judge')
                self.assertFalse(self.evidence(data)['available'])

    def test_ui_labels_assessment_abstention_and_source_evidence(self):
        data = self.build(*fixtures())
        actions = '''const d=$("prompt-example-report-1-0");d.open=true;d.ontoggle();
$("prompt-example-select-report-1-0").onchange({target:{value:"0"}});
new Blob([JSON.stringify({overview:$("prompt-evidence").innerHTML,kpis:$("kpis").innerHTML,example:$("prompt-example-body-report-1-0").innerHTML,integrity:$("integrity-cards").innerHTML})]);'''
        view = self.browser(data, actions);capture = view['exported']
        self.assertIn('LLM-assessed pass rate', capture['kpis'])
        self.assertIn('LLM-assessed: all required checks passed', capture['overview'])
        self.assertIn('1 needs review', capture['overview'])
        self.assertIn('3/6 (50.0%)', capture['overview'])
        self.assertIn('Needs review', capture['example'])
        self.assertIn('No expected answer was supplied or used.', capture['example'])
        self.assertNotIn('Expected answer unavailable', capture['example'])
        self.assertIn('Source evidence', capture['example'])
        self.assertIn('source_002', capture['example'])
        self.assertIn('&lt;script&gt;untrusted()', capture['example'])
        self.assertNotIn('<script>untrusted()', capture['example'])
        self.assertIn('Judge model costs', capture['integrity'])
        self.assertIn('$3.000000', capture['integrity'])
        self.assertIn('known priced portion', capture['integrity'])
        self.assertIn('Judge spend is separate', view['chart'])

    def test_export_preserves_full_sources_and_rubric_without_invented_answers(self):
        data = self.build(*fixtures());view = self.browser(data, '$("export").onclick();')
        exported = view['exported']
        self.assertEqual(len(exported['trials']), 18)
        self.assertEqual(len(exported['source_bundles']), 3)
        for row in exported['trials']:
            self.assertFalse(row['expected_available'])
            self.assertIn(row['source_ref'], exported['source_bundles'])
        self.assertEqual(exported['assessments'][0]['mode'], 'source_judge')

    def test_source_mode_does_not_relabel_recorded_answers_as_references(self):
        report, config = fixtures()
        for case in config['datasets']['heldout']:
            case['expected'] = {'answer': 'old model output is not a golden answer'}
        data = self.build(report, config)
        self.assertTrue(self.evidence(data)['available'])
        self.assertTrue(all(t['expected_answer'] is None for g in data['reports'][0]['groups'] for t in g['trials']))


if __name__ == '__main__':
    unittest.main()
