"""Optional assessment gaps cannot hide complete, verifiable reference comparisons."""
import copy
import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('core_scope_support',Path(__file__).with_name('test_dashboard_mixed_assessment.py'))
support=importlib.util.module_from_spec(spec);spec.loader.exec_module(support)


def fixture(optional_error=False):
    report,config=support.fixtures()
    report.update(evaluation_status='needs_review',grading_error_records=1,unresolved_attempts=[])
    report['model_costs']={'estimated_model_cost_usd':None,'known_model_cost_usd':15.,'by_purpose_usd':{'agent':10.,'optimizer':2.,'judge':3.},'unpriced_requests_or_incomplete_traces':1}
    if optional_error:
        grade=report['holdout']['target_selected']['trials'][0]['checks']['explanation']
        grade.pop('status');grade.pop('evidence');grade.update(error=True,failure_category='grading',reason='Provider HTTP500',**{'pass':False})
    return report,config


class CoreScopeTests(unittest.TestCase):
    setUp=support.MixedAssessmentDashboardTests.setUp
    build=support.MixedAssessmentDashboardTests.build
    browser=support.MixedAssessmentDashboardTests.browser
    evidence=support.MixedAssessmentDashboardTests.evidence

    def test_global_validation_judge_error_preserves_complete_final_comparisons(self):
        report,config=fixture();data=self.build(report,config);r=data['reports'][0]
        self.assertEqual(r['status'],'needs_review');self.assertIsNone(r['model_costs']['estimated_model_cost_usd'])
        self.assertTrue(all(g['core_scope']['complete'] for g in r['groups']))
        evidence=self.evidence(data);self.assertTrue(evidence['available'],evidence['issues'])
        view=self.browser(data,'new Blob([JSON.stringify({integrity:$("integrity-cards").innerHTML,evidence:$("prompt-evidence").innerHTML})]);')
        self.assertNotIn('Some results are missing or cannot be compared.',view['insight'])
        self.assertIn('Campaign status: needs review',view['insight'])
        self.assertIn('Unknown',view['exported']['integrity'])
        self.assertIn('What changed with the revised prompt',view['exported']['evidence'])
        self.assertIn('Other campaign issues',view['insight'])

    def test_optional_error_keeps_worked_pair_and_required_trial_outcome(self):
        report,config=fixture(True);bad_id=report['holdout']['target_selected']['trials'][0]['id'];data=self.build(report,config)
        e=self.evidence(data);self.assertTrue(e['available'],e['issues']);criterion=next(c for c in e['criteria'] if c['id']=='explanation')
        self.assertEqual(criterion['after']['unknown'],1);self.assertEqual(e['outcomes']['after_passes'],5)
        view=self.browser(data,'$("export").onclick();');trial=next(t for t in view['exported']['trials'] if t['trial_id']==bad_id)
        self.assertEqual(trial['recorded_checks_outcome'],'error');self.assertEqual(trial['required_checks_outcome'],'passed')
        self.assertIn('Required checks passed',view['trial_list']);self.assertIn('1 unknown',view['table'])
        actions='const d=$("prompt-example-report-1-0");d.open=true;d.ontoggle();new Blob([JSON.stringify({example:$("prompt-example-body-report-1-0").innerHTML})]);'
        view=self.browser(data,actions);self.assertIn('Provider HTTP500',view['exported']['example']);self.assertIn('Expected answer',view['exported']['example'])

    def test_unknown_campaign_operations_are_not_assumed_to_be_optional(self):
        report,config=fixture();report['unresolved_attempts']=[{'id':'unclassified-operation','state':'started'}]
        data=self.build(report,config);self.assertTrue(self.evidence(data)['available'])
        view=self.browser(data,'new Blob([JSON.stringify({integrity:$("integrity-cards").innerHTML})]);')
        self.assertIn('Other campaign issues',view['insight']);self.assertIn('Unresolved attempts</span><strong>1</strong>',view['exported']['integrity'])
        self.assertNotIn('only optional',view['insight'].lower())

    def test_required_source_trace_cost_or_timing_defects_block_scoped_claims(self):
        for mutation in ('required_error','required_missing','usage','model','trace','source_hash','error_source_hash','quote','cost','timing','identity','pending_app','source_request','reasoning','effective_request'):
            with self.subTest(mutation=mutation):
                report,config=fixture(True);group=report['holdout']['target_selected'];row=group['trials'][0]
                if mutation=='required_error':row['checks']['actions'].update(error=True,failure_category='grading')
                elif mutation=='required_missing':row['checks'].pop('actions')
                elif mutation=='usage':row['result']['route_calls'][0]['usage']=None
                elif mutation=='model':row['result']['route_calls'][0]['model_returned']='wrong'
                elif mutation=='trace':row['result']['status']='failed'
                elif mutation in ('source_hash','error_source_hash'):
                    check=row['checks']['explanation'];check['source_sha256']='wrong'
                    if mutation=='source_hash':check.pop('error');check.update(status='pass',**{'pass':True})
                elif mutation=='quote':
                    check=row['checks']['explanation'];check.pop('error');check.update(status='pass',evidence=[{'source_id':'source_002','quote':'invented quote'}],**{'pass':True})
                elif mutation=='cost':group['score']['cost_usd_per_session']=0
                elif mutation=='timing':row['result']['timing'].pop('conversation_ms')
                elif mutation=='identity':row['id']='wrong'
                elif mutation=='effective_request':row['result']['replay']['effective_request_sha256']='wrong'
                elif mutation=='reasoning':row['result']['route_calls'][0]['effective_parameters']['reasoning']['effort']='low'
                elif mutation=='source_request':row['result']['replay']['captured_request_sha256']='wrong'
                elif mutation=='pending_app':report['unresolved_attempts']=[{'id':row['id'],'state':'started'}]
                data=self.build(report,config);g=next(g for g in data['reports'][0]['groups'] if g['id']=='target_selected')
                self.assertFalse(g['core_scope']['complete']);self.assertFalse(self.evidence(data)['available'])
                self.assertIn('Some results are missing or cannot be compared.',self.browser(data)['insight'])

    def test_matched_subsets_cannot_replace_the_intended_case_repeat_sample(self):
        report,config=fixture(True)
        for name in ('target_original','target_selected'):
            group=report['holdout'][name];group['trials'].pop();group['score']['n_sessions']=5
        data=self.build(report,config)
        self.assertFalse(self.evidence(data)['available'])
        for group in data['reports'][0]['groups']:
            if group['id'].startswith('target'):self.assertIn('The intended held-out cases and repeats are not complete.',group['core_scope']['issues'])

    def test_missing_frozen_repeat_plan_cannot_be_inferred_from_observed_rows(self):
        report,config=fixture();config['document'].pop('execution')
        self.assertFalse(self.evidence(self.build(report,config))['available'])

    def test_declared_per_dataset_case_cap_is_respected(self):
        report,config=support.fixtures(max_cases_per_dataset=2)
        report.update(evaluation_status='needs_review',grading_error_records=1)
        data=self.build(report,config)
        self.assertTrue(self.evidence(data)['available'],self.evidence(data)['issues'])
        self.assertTrue(all(g['core_scope']['complete'] for g in data['reports'][0]['groups']))
        report['holdout']['target_selected']['trials'].pop()
        self.assertFalse(self.evidence(self.build(report,config))['available'])

    def test_repeat_types_and_invalid_caps_cannot_be_coerced(self):
        for repeat in (False,0.0):
            report,config=fixture();group=report['holdout']['target_selected'];row=group['trials'][0];row['repeat']=repeat
            import hashlib,json
            ident=hashlib.sha256(json.dumps([config['fingerprint'],config['target'],group['score']['prompt'],row['case_id'],repeat,'holdout','standard'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            row['id']=row['result']['trial_id']=ident
            result=self.build(report,config)['reports'][0]
            self.assertIn('Recorded repeat indices must be nonnegative integers.',next(g for g in result['groups'] if g['id']=='target_selected')['core_scope']['issues'])
        for cap in (0,-1,True,1.0):
            report,config=fixture();config['document']['execution']['max_cases_per_dataset']=cap
            result=self.build(report,config)['reports'][0]
            self.assertFalse(any(g['core_scope']['complete'] for g in result['groups']))

    def test_replay_settings_use_effective_request_and_available_receipt_fields(self):
        for settings in ({'temperature':0.2,'top_p':0.8}, {'service_tier':None}, {'reasoning':None,'text':None}):
            with self.subTest(settings=settings):
                report,config=support.fixtures(route_parameters=settings)
                data=self.build(report,config)
                self.assertTrue(all(g['core_scope']['complete'] for g in data['reports'][0]['groups']))
                row=report['holdout']['target_selected']['trials'][0]
                row['result']['replay']['effective_request_sha256']='wrong'
                self.assertFalse(self.evidence(self.build(report,config))['available'])
        report,config=support.fixtures(route_parameters={'temperature':0.2})
        report['holdout']['target_selected']['trials'][0]['result']['route_calls'][0]['effective_parameters']['temperature']=0.9
        self.assertFalse(self.evidence(self.build(report,config))['available'])

    def test_source_only_judge_errors_still_block_quality_comparison(self):
        report,config=support.support.fixtures();report.update(evaluation_status='needs_review',grading_error_records=1)
        grade=report['holdout']['target_selected']['trials'][0]['checks']['actions'];grade.update(error=True,failure_category='grading',**{'pass':False})
        data=self.build(report,config);self.assertEqual(data['reports'][0]['assessment']['mode'],'source_judge')
        self.assertFalse(self.evidence(data)['available']);self.assertIn('Some results are missing or cannot be compared.',self.browser(data)['insight'])

if __name__=='__main__':unittest.main()
