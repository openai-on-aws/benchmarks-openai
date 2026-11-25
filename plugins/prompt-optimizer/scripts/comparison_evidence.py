"""Read-only provenance for a frozen comparison cohort; no model or AWS calls."""
from datetime import datetime
import hashlib
import json
import math
import re

PHASE = 'comparison-only-precommitted-1'
ARMS = {'source_original', 'target_original', 'target_comparison'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def inspect_comparison(directory, report, config):
    """Verify saved companion evidence without treating local files as signed attestations."""
    if report.get('mode') != 'comparison_only':
        return None
    issues=[]
    def need(ok, message):
        if not ok and message not in issues:issues.append(message)
    context={'complete':False,'issues':issues,'phase':None,'candidate':None,
             'parent_selection':None,'parent_model_costs':{},'combined_model_costs':{},
             'scope':'New application attempts on the parent held-out cases. Candidate fixed before parent holdout; no selection or automatic adoption in this cohort.'}
    try:
        need(isinstance(config,dict),'Verified frozen comparison inputs are unavailable.')
        spec=config['comparison_only'];manifest=spec['candidate_manifest']
        need(type(spec.get('version')) is int and spec['version']==1,'Unsupported frozen comparison version.')
        need(config['document']['runner'].get('grading_reference_mode')=='references_with_source_judges','Comparison-only reporting requires the frozen reference-and-source mode.')
        need(report.get('comparison_manifest')==spec,'Comparison manifest differs from the frozen input.')
        need(spec.get('phase')==report.get('phase')==PHASE,'Comparison phase is invalid.')
        need(set(report.get('holdout',{}))==ARMS,'Exactly three frozen comparison arms are required.')
        need(report.get('main_selection_unchanged') is True and report.get('automatic_adoption') is False and 'selected_prompt' not in report,'Comparison-only results cannot claim a selected prompt.')
        need(not report.get('optimizer_requests') and not report.get('optimization'),'Comparison-only results contain optimization work.')
        need(report.get('backend')=='agentcore' and report.get('simulated') is False,'Live AgentCore comparison evidence is required.')
        need(manifest.get('holdout_outputs_seen') is False and manifest.get('override_saved_main_selection') is False and manifest.get('automatic_adoption') is False,'Precommitment does not preserve the selection boundary.')
        need(digest(manifest)==spec['candidate_manifest_sha256'],'Candidate manifest fingerprint differs.')
        need(hashlib.sha256(manifest['prompt'].encode()).hexdigest()==manifest['raw_prompt_sha256'],'Precommitted prompt bytes differ.')
        parent_config={key:value for key,value in config.items() if key not in ('fingerprint','comparison_only')}
        parent_config['implementation_fingerprint']=spec['parent_implementation_fingerprint']
        need(digest(parent_config)==manifest['main_fingerprint'],'Comparison inputs differ from the frozen parent study.')
        parent=json.loads((directory/'parent-report.json').read_text())
        need(digest(parent)==spec['parent_report_sha256'] and parent.get('fingerprint')==manifest['main_fingerprint'],'Parent report does not match the frozen receipt.')
        original=config['prompt'];selected=parent.get('selected_prompt') or {}
        need(selected.get('prompt')==original and selected.get('sha256')==digest(original) and report.get('parent_selected_prompt')==selected,'The original parent selection was not preserved.')
        need(parent.get('backend')=='agentcore' and parent.get('simulated') is False and parent.get('evaluation_status') in ('complete','needs_review'),'Parent report does not contain a finished live AgentCore evaluation.')
        need({'source_original','target_original'}<=set(parent.get('holdout',{})) and all(arm.get('score',{}).get('prompt')==original for arm in parent['holdout'].values()),'Parent already contains a revised holdout arm or its arms are unavailable.')
        candidate=re.fullmatch(r'generation-([1-9][0-9]*)-candidate-([1-9][0-9]*)',manifest['candidate'])
        need(candidate is not None,'Candidate identity is unavailable.')
        generation,index=map(int,candidate.groups())
        saved=[g for g in (parent.get('optimization') or {}).get('generations',[]) if g.get('index')==generation]
        need(len(saved)==1 and index<=len(saved[0].get('candidates',[])) and saved[0]['candidates'][index-1].get('prompt')==manifest['prompt'],'Revised prompt does not match the saved parent candidate.')
        raw=(directory/'comparison-precommit.json').read_bytes()
        receipt=json.loads((directory/'comparison-precommit-receipt.json').read_text())
        need(hashlib.sha256(raw).hexdigest()==spec['precommit_object_sha256'] and json.loads(raw)==manifest,'Precommit object bytes do not match the recorded hash.')
        need(receipt.get('sha256')==spec['precommit_object_sha256'] and isinstance(receipt.get('bucket'),str) and bool(receipt['bucket']) and receipt.get('key','').endswith('/comparison-precommits/'+spec['precommit_object_sha256']+'.json'),'Server precommit receipt is incomplete or mismatched.')
        uploaded=datetime.fromisoformat(receipt['last_modified'])
        need(all(row.get('result',{}).get('route_calls') for arm in parent['holdout'].values() for row in arm['trials']),'Parent holdout start receipts are incomplete.')
        starts=[datetime.fromisoformat(call['started_at']) for arm in parent['holdout'].values() for row in arm['trials'] for call in row['result']['route_calls']]
        need(bool(starts) and uploaded.utcoffset() is not None and all(t.utcoffset() is not None for t in starts) and uploaded<min(starts),'Server precommit timestamp does not precede every parent holdout request.')
        claim=json.loads((directory/'comparison-claim.json').read_text())
        cohort=digest({'parent_job_id':spec['parent_job_id'],'parent_fingerprint':manifest['main_fingerprint'],'candidate_raw_prompt_sha256':manifest['raw_prompt_sha256'],'phase':PHASE})
        need(report.get('cohort_fingerprint')==cohort and claim.get('cohort_fingerprint')==cohort and claim.get('fingerprint')==config['fingerprint'] and claim.get('parent_job_id')==spec['parent_job_id'] and isinstance(claim.get('job_id'),str) and claim['job_id']!=spec['parent_job_id'],'Cohort claim is missing or inconsistent.')
        parent_costs=parent.get('model_costs') or {};cohort_costs=report.get('model_costs') or {}
        need(report.get('parent_model_costs')==parent_costs,'Parent costs differ from the preserved report.')
        combined={}
        for key in ('known_model_cost_usd','estimated_model_cost_usd'):
            values=[parent_costs.get(key),cohort_costs.get(key)]
            expected=sum(values) if all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in values) else None
            observed=(report.get('combined_model_costs') or {}).get(key)
            need(observed is None if expected is None else type(observed) in (int,float) and math.isclose(observed,expected,rel_tol=1e-9,abs_tol=1e-12),'Combined parent/cohort model costs do not reconcile.')
            combined[key]=expected
        parent_ids={row['id'] for arm in parent['holdout'].values() for row in arm['trials']}
        cohort_ids=[row['id'] for arm in report['holdout'].values() for row in arm['trials']]
        need(not parent_ids.intersection(cohort_ids) and len(set(cohort_ids))==len(cohort_ids),'Comparison attempts reuse parent receipts or duplicate another arm.')
        need(isinstance(report.get('execution_id'),str) and report['execution_id'] and report['execution_id']!=parent.get('execution_id'),'Comparison execution identity must be distinct from its parent.')
        context.update(phase=PHASE,candidate=manifest['candidate'],parent_selection=selected,
                       parent_model_costs=parent_costs,combined_model_costs=combined,
                       cohort_fingerprint=cohort,server_precommit_at=receipt['last_modified'])
    except (OSError,ValueError,KeyError,TypeError,AttributeError,IndexError) as exc:
        need(False,'Complete parent, precommit and cohort-claim companion evidence is required: '+type(exc).__name__+'.')
    context['complete']=not issues
    return context
