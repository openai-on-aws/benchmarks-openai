#!/usr/bin/env python3
"""Build synthetic support case packets; no model or network access.

Generated answers are constructed references, not human-reviewed customer truth.
The oracle uses interval union and Decimal arithmetic over structured fixture facts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parent
SPLIT_SEED = 'complex-support-resolution-v1'
BANDS = {'small': (12, 12), 'medium': (50, 60), 'large': (100, 120)}
SCENARIOS = ['threshold_boundary', 'overlapping_monitors', 'late_claim', 'deadline_correction',
             'active_security', 'contained_security', 'pending_evidence',
             'claim_conflict', 'cap_exhausted', 'already_credited',
             'ineligible_plan', 'ongoing_outage']


def stamp(value, offset=0):
    return value.astimezone(timezone(timedelta(hours=offset))).isoformat(timespec='minutes')


def dt(value):
    return datetime.fromisoformat(value)


def cents(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def source(case, suffix):
    return case['id'] + '-' + suffix


def schema():
    props = {
        'case_id': {'type': 'string'}, 'contract_id': {'type': 'string'},
        'eligible_fee_cents': {'type': 'integer', 'minimum': 0},
        'eligible_outage_minutes': {'type': 'integer', 'minimum': 0},
        'credit_rate_percent': {'type': ['integer', 'null']},
        'credit_cents': {'type': ['integer', 'null']},
        'disposition': {'type': 'string', 'enum': ['eligible', 'require_review', 'ineligible_plan',
            'late_claim', 'no_eligible_credit', 'cap_exhausted', 'already_credited']},
        'priority': {'type': 'string', 'enum': ['urgent', 'high', 'normal']},
        'queue': {'type': 'string', 'enum': ['security', 'billing_review', 'technical', 'billing', 'support']},
        'requires_human_review': {'type': 'boolean'},
        'required_actions': {'type': 'array', 'items': {'type': 'string'}},
        'evidence_source_ids': {'type': 'array', 'items': {'type': 'string'}},
    }
    return {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}


def oracle(case):
    start, end = dt(case['period_start']), dt(case['period_end'])
    contract = max((c for c in case['contracts'] if c['signed'] and dt(c['effective_from']) <= start
                    and (c['effective_to'] is None or dt(c['effective_to']) >= end)),
                   key=lambda c: dt(c['effective_from']))
    fee = max(0, sum(line['amount_cents'] for line in case['invoice']
                    if line['account'] == case['account'] and line['service'] == case['service']
                    and line['billing_month'] == case['billing_month']
                    and line['kind'] in ('recurring_service', 'recurring_discount')))
    intervals, pending, noticed = [], False, False
    notices = {n['notice_id']: n for n in case['notices']}
    for event in case['incidents']:
        a, b = max(start, dt(event['start'])), min(end, dt(event['end']))
        if (event['account'] != case['account'] or event['service'] != case['service']
                or event['region'] != case['region'] or event['impact'] != 'unavailable'
                or event['superseded'] or event['cause'] == 'client_caused' or b <= a):
            continue
        n = notices.get(event.get('notice_id'))
        if event['cause'] == 'planned_maintenance' and n and dt(event['start']) - dt(n['sent_at']) >= timedelta(hours=24):
            noticed |= event['verification'] == 'verified'
            continue
        if event['verification'] == 'pending':
            pending = True
        else:
            intervals.append((a, b))
    union = []
    for a, b in sorted(intervals):
        if union and a <= union[-1][1]:
            union[-1] = (union[-1][0], max(b, union[-1][1]))
        else:
            union.append((a, b))
    minutes = sum(int((b-a).total_seconds() // 60) for a,b in union)
    rate = max((t['credit_percent'] for t in contract['tiers'] if minutes >= t['minimum_outage_minutes']), default=0)
    issued = [c for c in case['credits'] if c['account'] == case['account'] and c['service'] == case['service']
              and c['billing_month'] == case['billing_month'] and c['status'] == 'issued']
    cap = max(0, cents(Decimal(fee)*contract['monthly_cap_percent']/100) - sum(c['amount_cents'] for c in issued))
    claim = max(case['claims'], key=lambda c: dt(c['recorded_at']))
    conflict = pending or claim['unresolved_claim_conflict']
    duplicate = any(c['claim_id'] == case['claim_id'] for c in issued)
    if duplicate: disposition = 'already_credited'
    elif conflict: disposition = 'require_review'
    elif not contract['credits_included']: disposition = 'ineligible_plan'
    elif dt(claim['received_at']) > end + timedelta(days=contract['claim_deadline_days']): disposition = 'late_claim'
    elif not rate or not fee: disposition = 'no_eligible_credit'
    elif not cap: disposition = 'cap_exhausted'
    else: disposition = 'eligible'
    amount = min(cents(Decimal(fee)*rate/100), cap) if disposition == 'eligible' else 0
    if disposition == 'require_review': rate, amount = None, None
    sec = max(case['security'], key=lambda c: dt(c['assessed_at']))['status']
    ops = max(case['operations'], key=lambda c: dt(c['assessed_at']))
    active, ongoing = sec == 'active_exposure', ops['ongoing']
    priority = 'urgent' if active or (ongoing and ops['scope'] == 'organization') else ('high' if ongoing and not ops['workaround'] else 'normal')
    queue = ('security' if active else 'billing_review' if disposition == 'require_review' else
             'technical' if ongoing else 'billing' if disposition == 'eligible' and amount else 'support')
    actions = ['recommend_service_credit' if disposition == 'eligible' and amount else 'explain_credit_decision']
    if disposition == 'require_review': actions.append('reconcile_claim_evidence')
    if active: actions.append('open_security_escalation')
    if ongoing: actions.append('investigate_service_incident')
    evidence = [source(case, k) for k in ('ACCOUNT', 'INVOICE', 'INCIDENTS', 'CLAIM', 'CREDIT_LEDGER', 'SECURITY', 'OPERATIONS')]
    evidence.append(contract['source_id'])
    if noticed: evidence.append(source(case, 'MAINTENANCE_NOTICES'))
    return dict(case_id=case['id'], contract_id=contract['contract_id'], eligible_fee_cents=fee,
                eligible_outage_minutes=minutes, credit_rate_percent=rate, credit_cents=amount,
                disposition=disposition, priority=priority, queue=queue,
                requires_human_review=conflict if disposition == 'require_review' else active,
                required_actions=sorted(actions), evidence_source_ids=sorted(evidence))


def fixture(index, *, pilot=False):
    ident = f'{"PILOT" if pilot else "CASE"}-{index+1:03d}'
    rng = random.Random(f'complex-support-v1:{ident}')
    scenario = SCENARIOS[index % len(SCENARIOS)]
    band = list(BANDS)[(index // len(SCENARIOS) + index % len(SCENARIOS)) % 3]
    invoice_n, incident_n = BANDS[band]
    month = 8 + (index % 3)
    start = datetime(2026,month,1,tzinfo=timezone.utc)
    end = datetime(2026,month+1,1,tzinfo=timezone.utc)
    account = f'acct-{rng.randrange(10000,99999)}-{ident}'
    case = dict(id=ident, scenario=scenario, band=band, account=account,
                service=rng.choice(['SearchCloud', 'DataFlow', 'WorkspaceAPI']),
                region=rng.choice(['us-east', 'eu-west', 'ap-south']),
                period_start=stamp(start), period_end=stamp(end), billing_month=start.strftime('%Y-%m'),
                claim_id=f'CLAIM-{ident}')
    tiers = [{'minimum_outage_minutes': m, 'credit_percent': p} for m,p in [(30,5),(90,10),(180,20)]]
    contracts = []
    for version, effective, signed, cap, included in [
        ('BASE', start-timedelta(days=500), True, 10, scenario != 'ineligible_plan'),
        ('AMENDMENT', end+timedelta(days=1) if (index//12)%3==0 else start-timedelta(days=12), True, rng.choice([15,20,25]), scenario != 'ineligible_plan'),
        ('PROPOSAL', start-timedelta(days=3), False, 100, True),
        ('FUTURE', end+timedelta(days=1), True, 50, True)]:
        contracts.append(dict(source_id=source(case, 'CONTRACT_'+version), contract_id=f'{ident}-{version}',
            signed=signed, effective_from=stamp(effective), effective_to=None,
            credits_included=included, monthly_cap_percent=cap,
            claim_deadline_days=7 if (index//12)%2 else 15,
            tiers=tiers if version == 'AMENDMENT' else
                [{'minimum_outage_minutes': m, 'credit_percent': p} for m,p in [(30,3),(60,8),(120,15)]] if version=='BASE' else
                [{'minimum_outage_minutes': 1, 'credit_percent': 50}]))
    rng.shuffle(contracts)
    case['contracts'] = contracts
    invoice = []
    kinds = ['recurring_service','recurring_service','recurring_discount','usage','tax','implementation','support']
    for j in range(invoice_n):
        kind = kinds[j % len(kinds)]
        amount = rng.randrange(1001,12001)
        if kind == 'recurring_discount': amount = -rng.randrange(101,901)
        invoice.append(dict(line_id=f'{ident}-L{j+1:03d}', account=account if j%13 else f'{account}-subsidiary',
            service=case['service'] if j%9 else 'ArchiveStore', billing_month=case['billing_month'] if j%17 else '2026-06',
            kind=kind, amount_cents=amount, currency='USD',
            description=f'{kind.replace("_"," ")} for project {rng.choice(["commerce","analytics","identity","search","warehouse"])}-{j+1}, allocation {rng.randrange(10,99)} seats; invoice revision {rng.randrange(1,4)}'))
    case['invoice'] = invoice
    notices = []
    incidents = []
    base = start+timedelta(days=3,hours=3)
    for j in range(incident_n):
        # Independent time windows include many qualifying rows; adding packet size
        # therefore increases actual reconciliation work, not just padding.
        a = base + timedelta(minutes=j*23)
        length = rng.choice([3,5,7,11,13,17])
        if j%11 == 0: a = start-timedelta(minutes=6) if j == 0 else end-timedelta(minutes=4)
        if j%10 == 1: a = base+timedelta(minutes=(j-1)*23+2)
        cause = ['provider_fault','provider_dependency','planned_maintenance','client_caused'][j%4]
        notice_id = f'{ident}-NOTICE-{j:03d}' if cause == 'planned_maintenance' else None
        if notice_id:
            notices.append(dict(notice_id=notice_id, sent_at=stamp(a-timedelta(hours=26 if j%3 else 4), -4),
                change_description=f'Rolling infrastructure maintenance for shard {j%19}, batch {j//19+1}; customer notification delivery confirmed.'))
        event = dict(event_id=f'{ident}-INC-{j+1:03d}', account=account if j%19 else account+'-subsidiary',
            service=case['service'] if j%17 else 'ArchiveStore', region=case['region'] if j%23 else 'other-region',
            start=stamp(a,rng.choice([-7,0,5])), end=stamp(a+timedelta(minutes=length),rng.choice([-7,0,5])),
            impact='degraded' if j%7 == 3 else 'unavailable', cause=cause, superseded=j%13==7,
            verification='verified', notice_id=notice_id,
            incident_note=f'Monitor {rng.randrange(100,999)} observed request failures in cluster {j%23}; correlation {ident}-{j:03d}. An operations specialist checked routing and account attribution. Raw detection may overlap another monitor; use the recorded interval and status fields.')
        incidents.append(event)
    # Always include one relevant bounded incident and its corrected monitor record.
    anchor = dict(event_id=f'{ident}-ANCHOR', account=account,service=case['service'],region=case['region'],
        start=stamp(start+timedelta(days=12,hours=1), -7),end=stamp(start+timedelta(days=12,hours=3),5),
        impact='unavailable',cause='provider_fault',superseded=False,verification='verified',notice_id=None,
        incident_note='Final postmortem window; supersedes the early customer estimate. Independent monitors of this same window must not be added twice.')
    if scenario == 'threshold_boundary':
        for event in incidents: event['cause']='client_caused'
        anchor['end']=stamp(dt(anchor['start'])+timedelta(minutes=[29,30,89,90,179,180,0][(index//12)%7]))
        anchor['incident_note']='Final verified interval. Other recorded incidents were attributed to customer configuration and excluded by the postmortem.'
    incidents.append(anchor)
    if scenario == 'overlapping_monitors':
        incidents.append(anchor|{'event_id':f'{ident}-SECOND-MONITOR','start':stamp(start+timedelta(days=12,hours=1,minutes=30)), 'incident_note':'Second monitor, same incident. Retained to preserve evidence.'})
    if scenario == 'pending_evidence':
        incidents.append(anchor|{'event_id':f'{ident}-PENDING','start':stamp(start+timedelta(days=18,hours=1)),
            'end':stamp(start+timedelta(days=18,hours=2)),'verification':'pending','incident_note':'Customer-specific routing attribution remains unresolved; this is not a verified outage.'})
    rng.shuffle(incidents)
    case['incidents'], case['notices'] = incidents, notices
    active = max((c for c in contracts if c['signed'] and dt(c['effective_from'])<=start), key=lambda c:dt(c['effective_from']))
    deadline = end+timedelta(days=active['claim_deadline_days'])
    received = deadline+timedelta(minutes=1) if scenario=='late_claim' else deadline-timedelta(days=2)
    if scenario=='deadline_correction': received=deadline
    case['claims'] = [
        dict(recorded_at=stamp(end+timedelta(days=1)),received_at=stamp(deadline+timedelta(days=1)),
             unresolved_claim_conflict=False,note='Initial CRM import used the processing time. Customer requested a cash refund and quoted an unsigned proposal.'),
        dict(recorded_at=stamp(deadline+timedelta(days=2)),received_at=stamp(received,-7),
             unresolved_claim_conflict=scenario=='claim_conflict',
             note='Authoritative intake correction: received_at is the actual submission timestamp, not processing time. '+
                  ('Account ownership remains disputed between two claim submitters.' if scenario=='claim_conflict' else 'Earlier spend and duration claims are superseded by this packet.'))]
    case['credits'] = [dict(credit_id=f'{ident}-OLD',claim_id='prior-claim',account=account,service=case['service'],
        billing_month=case['billing_month'],status='issued',amount_cents=rng.randrange(0,501)),
        dict(credit_id=f'{ident}-VOID',claim_id='prior-void',account=account,service=case['service'],
        billing_month=case['billing_month'],status='voided',amount_cents=999999),
        dict(credit_id=f'{ident}-OTHER-MONTH',claim_id='prior-month',account=account,service=case['service'],
        billing_month='2026-06',status='issued',amount_cents=888888)]
    if scenario=='cap_exhausted': case['credits'][0]['amount_cents']=999999
    if scenario=='already_credited': case['credits'][0]['claim_id']=case['claim_id']
    assessed=end+timedelta(days=2)
    case['security']=[dict(assessed_at=stamp(assessed),status='active_exposure' if scenario=='contained_security' else 'none',
            note='Early incident classification from security analyst.'),
        dict(assessed_at=stamp(assessed+timedelta(hours=4)), status='active_exposure' if scenario=='active_security' else 'contained' if scenario=='contained_security' else 'none',
            note='Latest signed security assessment. '+('Active credential exposure requires immediate security handling.' if scenario=='active_security' else 'No active credential or data exposure remains established.'))]
    case['operations']=[dict(assessed_at=stamp(assessed),ongoing=True,scope='organization',workaround=False,
             note='Early outage bulletin; evaluate later updates before assigning current severity.'),
        dict(assessed_at=stamp(assessed+timedelta(hours=5)),ongoing=scenario=='ongoing_outage',
             scope='organization' if (index//12)%2 else 'limited',workaround=False,
             note='Current service state after mitigation and verification. Historical billing-period incidents remain in the credit assessment.')]
    return case


def packet(case):
    header = (f'Fictional enterprise support case {case["id"]}\n'
        'Determine the service-credit decision and current operational routing from this case packet. '
        'The customer asks for an immediate cash refund; determine the supported service-credit recommendation instead.\n')
    docs = []
    def add(suffix, title, data):
        docs.append(f'\n[SOURCE {source(case,suffix)}]\n{title}\n'+json.dumps(data,ensure_ascii=False,indent=2))
    add('ACCOUNT','Account scope and billing period', {k:case[k] for k in ('account','service','region','billing_month','period_start','period_end','claim_id')})
    for contract in case['contracts']:
        docs.append('\n[SOURCE '+contract['source_id']+']\nContract archive entry\n'+json.dumps(contract,indent=2))
    add('INVOICE','Invoice line-item export; credits, taxes and other products are not interchangeable',case['invoice'])
    add('INCIDENTS','Incident ledger: record order is not chronology; inspect source timestamps, scope and corrections',case['incidents'])
    add('MAINTENANCE_NOTICES','Maintenance communication audit',case['notices'])
    add('CLAIM','Claim intake history; latest recorded_at correction is authoritative',case['claims'])
    add('CREDIT_LEDGER','Issued and voided service-credit ledger',case['credits'])
    add('SECURITY','Security assessment history',case['security'])
    add('OPERATIONS','Operational status history',case['operations'])
    # Packets differ in order; all sources remain present with stable IDs.
    random.Random(case['id']+':packet-order').shuffle(docs)
    return header+'\n'.join(docs)+'\n'


def manual(case, prompt, output_schema):
    return {'id':case['id'],'prompt':prompt,'input':packet(case),'expected':oracle(case),
        'settings':{'response_format':{'type':'json_schema','json_schema':{
            'name':'enterprise_support_resolution','strict':True,'schema':output_schema}},'max_completion_tokens':8192},
        'fixture_metadata':{'provenance':'synthetic','label_status':'unreviewed','scenario':case['scenario'],'size_band':case['band']}}


def decisions(output_schema):
    checks=[]
    for path in output_schema['properties']:
        checks.append({'id':path,'type':'json_set_equals' if path in ('required_actions','evidence_source_ids') else 'json_equals',
                       'path':path,'required':True,'description':'Verify '+path.replace('_',' ')+' against the constructed reference.'})
    return {'title':'Complex support-case resolution: Astra versus Sol and prompt optimization','owner':None,
        'source_model':'astra','target_model':'sol','optimizer_model':'optimizer','connection':None,'prompt_location':0,
        'optimization_enabled':True,'provenance':'synthetic','quality':{'kind':'json_checks','reference_source':'supplied',
            'checks':checks,'schema':output_schema,'rules':(ROOT/'prompt.txt').read_text()},
        'repetitions':3,'concurrency':4,'timeout_seconds':360,'split_seed':SPLIT_SEED,'budget_policy':None,'budget_usd':None,
        'cost_metric':'cost_usd_per_success','primary_latency':'conversation','iterations':1,'candidates':2,
        'limits':{'max_request_bytes':240000,'max_output_tokens':8192,'request_timeout_seconds':300},
        'optimizer_limits':{'max_request_bytes':272000,'max_output_tokens':16384,'request_timeout_seconds':600}}


def build(output):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    prompt=(ROOT/'prompt.txt').read_text(); output_schema=schema()
    summaries={}
    for label,n,pilot in [('cases',180,False),('pilot',20,True)]:
        fixtures=[fixture(i,pilot=pilot) for i in range(n)]
        rows=[manual(c,prompt,output_schema) for c in fixtures]
        path=output/(label+'.jsonl')
        path.write_text(''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows))
        # Raw fixture facts are retained for independent oracle audits; they are
        # never appended as hidden instructions or labels to model requests.
        (output/(label+'-facts.json')).write_text(json.dumps(fixtures,ensure_ascii=False,indent=2)+'\n')
        inventory=[dict(id=c['id'],scenario=c['scenario'],size_band=c['band'],
            input_characters=len(r['input']),request_text_characters=len(prompt)+len(r['input']),
            estimated_tokens_at_four_chars=round((len(prompt)+len(r['input']))/4),
            invoice_lines=len(c['invoice']),incident_rows=len(c['incidents']),expected=r['expected']) for c,r in zip(fixtures,rows)]
        (output/(label+'-inventory.json')).write_text(json.dumps(inventory,indent=2)+'\n')
        summaries[label]={'distinct_cases':n,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes':path.stat().st_size,'size_bands':dict(Counter(c['band'] for c in fixtures)),
            'scenarios':dict(Counter(c['scenario'] for c in fixtures)),
            'dispositions':dict(Counter(r['expected']['disposition'] for r in rows)),
            'input_characters':{'minimum':min(len(r['input']) for r in rows),'maximum':max(len(r['input']) for r in rows)},
            'token_estimate_method':'characters / 4; not a model tokenizer or measured usage'}
    (output/'output.schema.json').write_text(json.dumps(output_schema,indent=2)+'\n')
    (output/'decisions.template.json').write_text(json.dumps(decisions(output_schema),indent=2)+'\n')
    (output/'fixture-manifest.json').write_text(json.dumps({'version':1,'seed':'complex-support-v1','provenance':'synthetic',
        'label_status':'unreviewed','split_seed':SPLIT_SEED,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
        'datasets':summaries},indent=2)+'\n')
    print(json.dumps(summaries,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT)
    build(parser.parse_args().output)
