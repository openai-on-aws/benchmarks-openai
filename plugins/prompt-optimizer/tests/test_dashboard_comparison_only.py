"""A precommitted comparison never becomes a selected-prompt result."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('comparison_support',Path(__file__).with_name('test_dashboard_mixed_assessment.py'))
support=importlib.util.module_from_spec(spec);spec.loader.exec_module(support)
dashboard=support.dashboard
PHASE='comparison-only-precommitted-1'

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def bind(report,config,phase):
    config['fingerprint']=digest({k:v for k,v in config.items() if k!='fingerprint'})
    report['fingerprint']=config['fingerprint']
    from customer_eval.replay import fingerprint,request_body
    from customer_eval.transport import constrain
    for name,group in report['holdout'].items():
        variant=config['source'] if name=='source_original' else config['target'];route=config['routes'][variant['model_id']]
        for row in group['trials']:
            ident=digest([config['fingerprint'],variant,group['score']['prompt'],row['case_id'],row['repeat'],'holdout',phase])
            row.update(id=ident,phase=phase);row['result'].update(trial_id=ident,model_requested=route['model_id'])
            for call in row['result']['route_calls']:
                call.update(request_id=ident+'-provider',model_requested=route['model_id'],model_returned=route['model_id'],started_at='2026-11-24T13:00:00+00:00')
            case=next(c for c in config['datasets']['heldout'] if c['id']==row['case_id'])
            effective=constrain(request_body(case['replay'],group['score']['prompt']),dict(route,allowed_tool_names=[]),config['document']['runner']['limits'])
            row['result']['replay']['effective_request_sha256']=fingerprint(effective)

def fixture():
    report,config=support.fixtures();revised=report['holdout']['target_selected']['score']['prompt']
    config['implementation_fingerprint']='1'*64
    config['routes']['source']['model_id']='us.openai.gpt-6-astra'
    config['routes']['target']['model_id']='us.openai.gpt-6.2-astra'
    parent=copy.deepcopy(report);parent['backend']='agentcore';parent['execution_id']='parent-execution'
    parent['selected_prompt']={'prompt':config['prompt'],'sha256':digest(config['prompt'])}
    parent['holdout']['target_selected']=copy.deepcopy(parent['holdout']['target_original'])
    parent['holdout']['target_selected']['reused_from']='target_original'
    parent['optimization']={'generations':[{'index':1,'candidates':[{'prompt':revised}]}]}
    parent['model_costs']={'known_model_cost_usd':3.,'estimated_model_cost_usd':None}
    bind(parent,config,'standard')
    manifest={'recorded_at_utc':'2026-11-24T11:50:00+00:00','purpose':'Separate comparison',
      'candidate':'generation-1-candidate-1','prompt':revised,'raw_prompt_sha256':hashlib.sha256(revised.encode()).hexdigest(),
      'main_fingerprint':config['fingerprint'],'selection_basis':'Validation only','evidence':['validation.json'],
      'holdout_outputs_seen':False,'override_saved_main_selection':False,'automatic_adoption':False,'condition':'Parent omitted revised arm'}
    raw=json.dumps(manifest).encode();raw_sha=hashlib.sha256(raw).hexdigest()
    spec={'version':1,'parent_job_id':'parent-job-123','parent_report_sha256':digest(parent),
      'parent_implementation_fingerprint':config['implementation_fingerprint'],'candidate_manifest':manifest,
      'candidate_manifest_sha256':digest(manifest),'precommit_object_sha256':raw_sha,'phase':PHASE}
    config['comparison_only']=spec;config['implementation_fingerprint']='2'*64
    report.pop('selected_prompt',None);report.pop('optimization',None);report.pop('optimizer_requests',None)
    report.update(mode='comparison_only',backend='agentcore',execution_id='new-execution',phase=PHASE,
      comparison_manifest=spec,main_selection_unchanged=True,automatic_adoption=False,parent_selected_prompt=parent['selected_prompt'],
      parent_model_costs=parent['model_costs'],model_costs={'known_model_cost_usd':2.,'estimated_model_cost_usd':None},
      combined_model_costs={'known_model_cost_usd':5.,'estimated_model_cost_usd':None})
    report['holdout']['target_comparison']=report['holdout'].pop('target_selected')
    bind(report,config,PHASE)
    cohort=digest({'parent_job_id':spec['parent_job_id'],'parent_fingerprint':manifest['main_fingerprint'],'candidate_raw_prompt_sha256':manifest['raw_prompt_sha256'],'phase':PHASE})
    report['cohort_fingerprint']=cohort
    companions={'parent-report.json':parent,'comparison-precommit.json':raw,
      'comparison-precommit-receipt.json':{'bucket':'fixture','key':'customer-evaluation/comparison-precommits/'+raw_sha+'.json','sha256':raw_sha,'last_modified':'2026-11-24T12:00:00+00:00'},
      'comparison-claim.json':{'job_id':'comparison-job-123','parent_job_id':spec['parent_job_id'],'fingerprint':config['fingerprint'],'cohort_fingerprint':cohort}}
    return report,config,companions

class ComparisonOnlyDashboardTests(unittest.TestCase):
    setUp=support.MixedAssessmentDashboardTests.setUp
    browser=support.MixedAssessmentDashboardTests.browser

    def build(self,report,config,companions):
        for name,value in companions.items():
            (self.root/name).write_bytes(value if isinstance(value,bytes) else json.dumps(value).encode())
        path=self.root/'comparison-report.json';path.write_text(json.dumps(report))
        (self.root/'run-input.json').write_text(json.dumps(config))
        before=path.read_bytes();data=dashboard.build_data(path)
        self.assertEqual(path.read_bytes(),before)
        return data

    def test_three_models_prompts_and_review_status_are_explicit(self):
        data=self.build(*fixture());report=data['reports'][0]
        self.assertTrue(report['comparison_context']['complete'],report['comparison_context']['issues'])
        self.assertEqual({g['id'] for g in report['groups']},{'source_original','target_original','target_comparison'})
        self.assertTrue(all(g['core_scope']['complete'] for g in report['groups']))
        self.assertTrue(report['prompt_evidence'][0]['available'],report['prompt_evidence'][0]['issues'])
        self.assertEqual(report['selected_prompt'],{})
        view=self.browser(data,'new Blob([JSON.stringify({evidence:$("prompt-evidence").innerHTML,integrity:$("integrity-cards").innerHTML,prompts:$("prompt-cards").innerHTML})]);')
        text=view['insight']+json.dumps(view['exported'])
        self.assertIn('Astra',text);self.assertIn('Revised prompt for review',text)
        self.assertIn('parent study retained the original prompt',text)
        self.assertNotIn('The optimizer chose this revised prompt',text)
        self.assertIn('Parent + this cohort model cost',text)
        self.assertIn('Unknown',text);self.assertIn('Combined known priced portion',text)

    def test_missing_or_mismatched_provenance_blocks_factual_claims(self):
        for mutation in ('claim_missing','claim_wrong','late_precommit','parent_cost','selection','extra_arm','wrong_phase','reused_parent_trial','missing_trial'):
            with self.subTest(mutation=mutation):
                report,config,companions=fixture()
                if mutation=='claim_missing':companions.pop('comparison-claim.json');(self.root/'comparison-claim.json').unlink(missing_ok=True)
                elif mutation=='claim_wrong':companions['comparison-claim.json']['cohort_fingerprint']='wrong'
                elif mutation=='late_precommit':companions['comparison-precommit-receipt.json']['last_modified']='2026-11-24T14:00:00+00:00'
                elif mutation=='parent_cost':report['parent_model_costs']={'known_model_cost_usd':0.}
                elif mutation=='selection':report['selected_prompt']={'prompt':'pretend selected'}
                elif mutation=='extra_arm':report['holdout']['unexpected']=report['holdout']['target_comparison']
                elif mutation=='wrong_phase':report['holdout']['target_comparison']['trials'][0]['phase']='standard'
                elif mutation=='missing_trial':report['holdout']['target_comparison']['trials'].pop()
                elif mutation=='reused_parent_trial':report['holdout']['target_comparison']['trials'][0]['id']=companions['parent-report.json']['holdout']['target_original']['trials'][0]['id']
                data=self.build(report,config,companions)
                self.assertFalse(all(g['core_scope']['complete'] for g in data['reports'][0]['groups']))
                self.assertFalse(data['reports'][0]['prompt_evidence'][0]['available'])
                self.assertIn('Some results are missing or cannot be compared.',self.browser(data)['insight'])

    def test_discovery_keeps_parent_and_new_cohorts_separate(self):
        report,config,companions=fixture();self.build(report,config,companions)
        self.assertEqual(dashboard.discover_reports(self.root),[(self.root/'comparison-report.json').resolve()])
        (self.root/'report.json').write_text(json.dumps(companions['parent-report.json']))
        with self.assertRaisesRegex(ValueError,'cohorts must remain separate'):dashboard.discover_reports(self.root)
        self.assertEqual(len(dashboard.discover_reports(self.root/'comparison-report.json')),1)

    def test_export_preserves_cohort_identity_without_selection(self):
        data=self.build(*fixture());export=self.browser(data,'$("export").onclick();')['exported']
        self.assertEqual(export['cohorts'][0]['mode'],'comparison_only')
        self.assertTrue(export['cohorts'][0]['comparison_context']['complete'])
        self.assertEqual({row['configuration'] for row in export['trials']},{'source_original','target_original','target_comparison'})

if __name__=='__main__':unittest.main()
