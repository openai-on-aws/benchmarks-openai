"""Reference correctness stays separate from optional source judgments."""
import copy
import hashlib
import json
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('mixed_assessment_support', Path(__file__).with_name('test_dashboard_source_judge.py'))
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
dashboard = support.dashboard


def fixtures(max_cases_per_dataset=None, route_parameters=None):
    report, config = support.support.fixtures()
    config.setdefault('routes', {})['judge'] = {'model_id': 'us.openai.gpt-6.2-astra'}
    config['policy'] = 'Fixed source business policy.'
    for check in config['document']['checks']: check['method'] = 'deterministic'
    config['document'].setdefault('runner', {})['grading_reference_mode'] = 'references_with_source_judges'
    config['document']['checks'].append({'id': 'explanation', 'question': 'Is the explanation supported?',
        'required': False, 'method': 'llm_judge', 'judge_model_id': 'judge'})
    config['grading']['explanation'] = {'type': 'llm_source_rubric', 'rubric': 'Check support, not writing style.'}
    builder, criteria, digest = dashboard._source_helpers()
    cases = {c['id']: c for c in config['datasets']['heldout']}
    for case in cases.values():
        case['turns'] = ['Source record for ' + case['id']]
    for name, group in report['holdout'].items():
        for row in group['trials']:
            row['result']['turn_records'][0]['user'] = cases[row['case_id']]['turns'][0]
            sources = builder(cases[row['case_id']], policy=config['policy'])
            user = next(s for s in sources if s['role'] == 'user')
            status = 'pass'
            if name == 'target_selected' and row['case_id'] == 'case-a' and row['repeat'] == 0:
                status = 'fail'
            if name == 'target_original' and row['case_id'] == 'case-b' and row['repeat'] == 0:
                status = 'needs_review'
            row['checks']['explanation'] = {'pass': status == 'pass', 'status': status,
                'reason': 'Specific source evidence.' if status != 'needs_review' else 'Ambiguous source.',
                'evidence': [] if status == 'needs_review' else [{'source_id': user['source_id'], 'quote': user['text']}],
                'source_sha256': digest(sources), 'rubric_sha256': digest(criteria(config))}
    report['optimizer_requests'] = [{'model_requested': 'us.openai.gpt-6.2-astra', 'effective_parameters': {'reasoning': {'effort': 'high'}}}]
    report['judge_requests'] = [{'model_requested': 'us.openai.gpt-6.2-astra', 'effective_parameters': {'reasoning': {'effort': 'high'}}}] * 2
    # A mixed comparison declares its intended sample and binds saved receipts to it.
    config['document']['execution']={'repetitions':2,'max_cases_per_dataset':max_cases_per_dataset}
    config['document']['success_criteria']=[]
    config['document']['runner'].update(mode='prompt',workload='none',limits={'max_requests':1,'max_output_tokens':4096,'max_request_bytes':100000})
    config['document']['runner']['measurement']={'primary_latency':'conversation'}
    config['source']={'id':'source-original','model_id':'source'}
    config['target']={'id':'target-original','model_id':'target'}
    for alias,model in [('source','model-astra'),('target','model-sol')]:
        config['routes'][alias]={'model_id':model,'input_usd_per_million':10.,'output_usd_per_million':100.,'parameters':{'reasoning':{'effort':'high'},'service_tier':'default'}}
        config['routes'][alias]['parameters'].update(route_parameters or {})
    from customer_eval.replay import fingerprint as request_fingerprint,request_body
    from customer_eval.transport import constrain
    fixed_policy=config['policy'];config['policy']=''
    for case in config['datasets']['heldout']:
        request={'input':[{'role':'system','content':fixed_policy},{'role':'developer','content':config['prompt']},{'role':'user','content':case['turns'][0]}]}
        case['replay']={'version':1,'request':request,'request_sha256':request_fingerprint(request),'prompt_location':1}
    config['fingerprint']=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    from customer_eval.scoring import summarize
    for name,group in report['holdout'].items():
        variant=config['source'] if name=='source_original' else config['target']
        prompt=group['score']['prompt']
        allowed={c['id'] for c in config['datasets']['heldout'][:max_cases_per_dataset]}
        group['trials']=[row for row in group['trials'] if row['case_id'] in allowed]
        for row in group['trials']:
            ident=hashlib.sha256(json.dumps([config['fingerprint'],variant,prompt,row['case_id'],row['repeat'],'holdout','standard'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            row.update(id=ident,split='holdout',phase='standard')
            case=cases[row['case_id']];effective=constrain(request_body(case['replay'],prompt),dict(config['routes'][variant['model_id']],allowed_tool_names=[]),config['document']['runner']['limits'])
            row['result']['route_calls']=row['result']['route_calls'][:1]
            row['result'].update(trial_id=ident,simulated=False,replay={'captured_request_sha256':case['replay']['request_sha256'],'effective_request_sha256':request_fingerprint(effective)})
            sources=builder(case,policy=config['policy']);row['checks']['explanation']['source_sha256']=digest(sources)
            for index,call in enumerate(row['result']['route_calls']):
                call.update(request_id=ident+'-'+str(index),effective_parameters={key:effective.get(key) for key in ('reasoning','text','max_output_tokens')},service_tier_requested=effective.get('service_tier','default'))
        group['score']=vars(summarize(config,prompt,group['trials']))
    return report, config


class MixedAssessmentDashboardTests(unittest.TestCase):
    setUp = support.SourceJudgeDashboardTests.setUp
    build = support.SourceJudgeDashboardTests.build
    evidence = support.SourceJudgeDashboardTests.evidence
    browser = support.SourceJudgeDashboardTests.browser

    def test_reference_and_source_evidence_are_both_preserved(self):
        data = self.build(*fixtures());r = data['reports'][0];e = self.evidence(data)
        self.assertTrue(e['available'], e['issues'])
        self.assertEqual(r['assessment']['mode'], 'reference_and_source')
        self.assertEqual(r['assessment']['quality_label'], 'Reference check pass rate')
        self.assertEqual(r['assessment']['source_check_ids'], ['explanation'])
        self.assertEqual(len(r['source_bundles']), 3)
        for group in r['groups']:
            for t in group['trials']:
                self.assertTrue(t['expected_available'])
                self.assertIsNotNone(t['expected_answer'])
                self.assertTrue(t['source_available'])
                self.assertIn(t['source_ref'], r['source_bundles'])
        explanation = next(c for c in e['criteria'] if c['id'] == 'explanation')
        self.assertFalse(explanation['required'])
        self.assertEqual(explanation['before']['needs_review'], 1)
        self.assertEqual(explanation['after']['failed'], 1)
        self.assertEqual(e['outcomes']['before_passes'], 3)
        self.assertEqual(e['outcomes']['after_passes'], 5)

    def test_missing_references_block_claims_even_with_valid_judgments(self):
        r, c = fixtures()
        for case in c['datasets']['heldout']: case.pop('expected')
        e = self.evidence(self.build(r, c))
        self.assertFalse(e['available'])
        self.assertIn('Matching frozen reference answers are unavailable.', e['issues'])

    def test_source_digest_and_quotes_are_verified_for_optional_checks(self):
        for field, value in [('source_sha256', 'wrong'), ('rubric_sha256', 'wrong'), ('evidence', [{'source_id': 'source_999', 'quote': 'bad'}])]:
            with self.subTest(field=field):
                r, c = fixtures();r['holdout']['target_selected']['trials'][0]['checks']['explanation'][field] = value
                self.assertFalse(self.evidence(self.build(r, c))['available'])

    def test_mixed_mode_requires_explicit_marker_and_optional_source_checks(self):
        for mutation in ('marker', 'required'):
            with self.subTest(mutation=mutation):
                r, c = fixtures()
                if mutation == 'marker': c['document']['runner'].pop('grading_reference_mode')
                else: c['document']['checks'][-1]['required'] = True
                data = self.build(r,c)
                self.assertEqual(data['reports'][0]['assessment']['mode'], 'unverified_source_judge')
                self.assertFalse(self.evidence(data)['available'])

    def test_json_unordered_reference_rule_is_supported(self):
        r,c=fixtures();c['grading']['actions']['type']='json_unordered_equals'
        self.assertEqual(self.build(r,c)['reports'][0]['assessment']['mode'],'reference_and_source')

    def test_ui_separates_core_and_optional_quality_and_names_roles(self):
        data = self.build(*fixtures())
        actions = '''const d=$("prompt-example-report-1-0");d.open=true;d.ontoggle();
$("prompt-example-select-report-1-0").onchange({target:{value:"0"}});
new Blob([JSON.stringify({overview:$("prompt-evidence").innerHTML,kpis:$("kpis").innerHTML,example:$("prompt-example-body-report-1-0").innerHTML,integrity:$("integrity-cards").innerHTML})]);'''
        view=self.browser(data,actions);v=view['exported']
        self.assertIn('Reference check pass rate',v['kpis'])
        self.assertNotIn('LLM-assessed pass rate',v['kpis'])
        self.assertIn('Reference checks passed',v['overview'])
        self.assertIn('Optional source judgments are reported separately',v['overview'])
        self.assertIn('Expected answer',v['example'])
        self.assertIn('Full frozen sources',v['example'])
        self.assertNotIn('No expected answer was supplied',v['example'])
        self.assertIn('LLM-assessed criterion',v['example'])
        self.assertIn('Prompt optimizer:',view['insight'])
        self.assertIn('Explanation judge:',view['insight'])
        self.assertIn('high reasoning',view['insight'])
        self.assertIn('2 recorded requests',view['insight'])
        self.assertIn('Required reference checks determine',v['integrity'])
        self.assertIn('Optional LLM assessments',view['chart'])

    def test_export_contains_references_and_sources(self):
        data=self.build(*fixtures());v=self.browser(data,'$("export").onclick();')['exported']
        self.assertEqual(v['assessments'][0]['mode'],'reference_and_source')
        for t in v['trials']:
            self.assertTrue(t['expected_available'])
            self.assertIsNotNone(t['expected_answer'])
            self.assertIn(t['source_ref'],v['source_bundles'])

    def test_model_roles_come_from_recorded_requests(self):
        data=self.build(*fixtures());roles=data['reports'][0]['model_roles']
        self.assertEqual(roles,[{'role':'optimizer','model':'us.openai.gpt-6.2-astra','reasoning':'high','requests':1},
                               {'role':'judge','model':'us.openai.gpt-6.2-astra','reasoning':'high','requests':2}])
        self.assertEqual(dashboard._model_roles({}),[])


if __name__=='__main__': unittest.main()
