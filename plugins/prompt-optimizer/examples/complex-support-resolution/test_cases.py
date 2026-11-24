"""Hand-calculated boundary tests for the synthetic reference generator."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_cases import ROOT, dt, fixture, manual, oracle, schema, stamp


def minimal():
    c = fixture(0)
    start, end = dt(c['period_start']), dt(c['period_end'])
    contract = next(x for x in c['contracts'] if x['contract_id'].endswith('-BASE'))
    contract.update(monthly_cap_percent=10, claim_deadline_days=7, credits_included=True,
                    tiers=[{'minimum_outage_minutes': 30, 'credit_percent': 3},
                           {'minimum_outage_minutes': 60, 'credit_percent': 8}])
    c['contracts'] = [contract]
    c['invoice'] = [dict(account=c['account'],service=c['service'],billing_month=c['billing_month'],
                         kind='recurring_service', amount_cents=10001),
                    dict(account=c['account'],service=c['service'],billing_month=c['billing_month'],
                         kind='recurring_discount', amount_cents=-101),
                    dict(account=c['account'],service=c['service'],billing_month=c['billing_month'],
                         kind='tax', amount_cents=99999)]
    anchor = deepcopy(c['incidents'][-1])
    anchor.update(account=c['account'],service=c['service'],region=c['region'],impact='unavailable',
                  cause='provider_fault',superseded=False,verification='verified',notice_id=None,
                  start=stamp(start+timedelta(days=1,hours=10)),end=stamp(start+timedelta(days=1,hours=10,minutes=45)))
    second=anchor|{'event_id':'second','start':stamp(start+timedelta(days=1,hours=10,minutes=30),5),
                   'end':stamp(start+timedelta(days=1,hours=11),-7)}
    c['incidents']=[anchor,second]
    c['credits']=[];c['notices']=[]
    c['claims']=[dict(recorded_at=stamp(end+timedelta(days=8)),received_at=stamp(end+timedelta(days=7)),unresolved_claim_conflict=False)]
    c['security']=[dict(assessed_at=stamp(end),status='none')]
    c['operations']=[dict(assessed_at=stamp(end),ongoing=False,scope='limited',workaround=False)]
    return c


class ReferenceBoundaryTests(unittest.TestCase):
    def test_overlap_timezone_invoice_filter_and_half_open_deadline(self):
        result=oracle(minimal())
        self.assertEqual((result['eligible_fee_cents'], result['eligible_outage_minutes'],
                          result['credit_rate_percent'], result['credit_cents']), (9900,60,8,792))
        self.assertEqual(result['disposition'],'eligible')

    def test_one_minute_late_is_late(self):
        c=minimal();c['claims'][0]['received_at']=stamp(dt(c['claims'][0]['received_at'])+timedelta(minutes=1))
        self.assertEqual(oracle(c)['disposition'],'late_claim')
        self.assertEqual(oracle(c)['credit_cents'],0)

    def test_round_half_up_and_remaining_monthly_cap(self):
        c=minimal();c['invoice']=c['invoice'][:1];c['invoice'][0]['amount_cents']=1050
        c['incidents']=c['incidents'][:1]
        self.assertEqual(oracle(c)['credit_cents'],32)  # 1050 * 3% = 31.5 cents.
        c['credits']=[dict(account=c['account'],service=c['service'],billing_month=c['billing_month'],
                           status='issued',amount_cents=100,claim_id='another-claim')]
        self.assertEqual(oracle(c)['credit_cents'],5)  # 10% cap =105; 100 already issued.

    def test_uncertain_relevant_evidence_review_but_not_unrelated_evidence(self):
        c=minimal(); pending=deepcopy(c['incidents'][0]);pending['verification']='pending'
        c['incidents'].append(pending)
        result=oracle(c)
        self.assertEqual((result['disposition'],result['credit_cents'],result['eligible_outage_minutes']),('require_review',None,60))
        c['incidents'][-1]['account']='unrelated-account'
        self.assertEqual(oracle(c)['disposition'],'eligible')

    def test_notice_threshold_and_evidence_requirement(self):
        c=minimal();c['incidents']=c['incidents'][:1];event=c['incidents'][0]
        event.update(cause='planned_maintenance',notice_id='notice')
        c['notices']=[dict(notice_id='notice',sent_at=stamp(dt(event['start'])-timedelta(hours=24)))]
        result=oracle(c)
        self.assertEqual(result['eligible_outage_minutes'],0)
        self.assertIn(c['id']+'-MAINTENANCE_NOTICES',result['evidence_source_ids'])
        c['notices'][0]['sent_at']=stamp(dt(event['start'])-timedelta(hours=23,minutes=59))
        result=oracle(c)
        self.assertEqual(result['eligible_outage_minutes'],45)
        self.assertNotIn(c['id']+'-MAINTENANCE_NOTICES',result['evidence_source_ids'])

    def test_latest_security_and_current_operations_override_old_alert(self):
        c=minimal();end=dt(c['period_end'])
        c['security']=[dict(assessed_at=stamp(end),status='active_exposure'),dict(assessed_at=stamp(end+timedelta(hours=1)),status='contained')]
        self.assertEqual(oracle(c)['priority'],'normal')
        c['operations'][0]['ongoing']=True
        self.assertEqual(oracle(c)['priority'],'high')
        c['operations'][0]['scope']='organization'
        self.assertEqual(oracle(c)['priority'],'urgent')

    def test_duplicate_claim_precedence_over_review(self):
        c=minimal();c['claims'][0]['unresolved_claim_conflict']=True
        c['credits']=[dict(account=c['account'],service=c['service'],billing_month=c['billing_month'],
                           status='issued',amount_cents=1,claim_id=c['claim_id'])]
        result=oracle(c)
        self.assertEqual((result['disposition'],result['credit_cents']),('already_credited',0))
        self.assertFalse(result['requires_human_review'])

    def test_contract_signed_and_effective_dates(self):
        c=minimal();base=c['contracts'][0];start=dt(c['period_start'])
        c['contracts'] += [base|{'contract_id':'unsigned','signed':False,'effective_from':stamp(start-timedelta(days=1))},
                           base|{'contract_id':'future','effective_from':stamp(start+timedelta(days=1))}]
        self.assertEqual(oracle(c)['contract_id'],base['contract_id'])
        c['contracts'].append(base|{'contract_id':'operative','effective_from':stamp(start),'monthly_cap_percent':2})
        self.assertEqual((oracle(c)['contract_id'],oracle(c)['credit_cents']),('operative',198))

    def test_policy_actions_for_all_seven_financial_dispositions(self):
        # Hand-written policy truth table: reconciliation supplements the
        # explanation for review; it does not replace that explanation.
        cases = {name:minimal() for name in ('eligible','require_review','ineligible_plan',
                    'late_claim','no_eligible_credit','cap_exhausted','already_credited')}
        cases['require_review']['claims'][0]['unresolved_claim_conflict']=True
        cases['ineligible_plan']['contracts'][0]['credits_included']=False
        late=cases['late_claim']['claims'][0]
        late['received_at']=stamp(dt(late['received_at'])+timedelta(minutes=1))
        cases['no_eligible_credit']['invoice'][0]['amount_cents']=101  # exactly cancels the discount
        for disposition,amount,claim in [('cap_exhausted',990,'different-claim'),
                                          ('already_credited',1,cases['already_credited']['claim_id'])]:
            c=cases[disposition]
            c['credits']=[dict(account=c['account'],service=c['service'],billing_month=c['billing_month'],
                              status='issued',amount_cents=amount,claim_id=claim)]
        expected = {
            'eligible': {'recommend_service_credit'},
            'require_review': {'explain_credit_decision','reconcile_claim_evidence'},
            'ineligible_plan': {'explain_credit_decision'},
            'late_claim': {'explain_credit_decision'},
            'no_eligible_credit': {'explain_credit_decision'},
            'cap_exhausted': {'explain_credit_decision'},
            'already_credited': {'explain_credit_decision'},
        }
        for disposition,c in cases.items():
            with self.subTest(disposition=disposition):
                result=oracle(c)
                self.assertEqual(result['disposition'],disposition)
                self.assertEqual(set(result['required_actions']),expected[disposition])
                self.assertEqual(len(result['required_actions']),len(expected[disposition]))

    def test_review_explanation_is_additive_with_security_and_operations(self):
        c=minimal();c['claims'][0]['unresolved_claim_conflict']=True
        c['security'][0]['status']='active_exposure'
        c['operations'][0].update(ongoing=True,scope='organization')
        result=oracle(c)
        self.assertEqual(result['disposition'],'require_review')
        self.assertEqual(set(result['required_actions']),{
            'explain_credit_decision','reconcile_claim_evidence',
            'open_security_escalation','investigate_service_incident'})
        self.assertEqual((result['priority'],result['queue'],result['requires_human_review']),('urgent','security',True))


class GeneratedAssetTests(unittest.TestCase):
    def test_schema_and_independent_pilot(self):
        import jsonschema
        all_ids=set(); all_inputs=set(); expected_keys=set(schema()['properties'])
        for name,expected_n in [('cases',180),('pilot',20)]:
            rows=[json.loads(l) for l in (ROOT/(name+'.jsonl')).read_text().splitlines()]
            self.assertEqual(len(rows),expected_n)
            for row in rows:
                self.assertNotIn(row['id'],all_ids);all_ids.add(row['id'])
                self.assertNotIn(row['input'],all_inputs);all_inputs.add(row['input'])
                jsonschema.validate(row['expected'],schema())
                self.assertEqual(set(row['expected']),expected_keys)
                self.assertEqual(row['fixture_metadata']['label_status'],'unreviewed')
                self.assertLess(len(json.dumps({'messages':[{'role':'system','content':row['prompt']},{'role':'user','content':row['input']}],**row['settings']}).encode()),160000)

    def test_all_required_outcomes_and_sizes_are_present(self):
        rows=[json.loads(l) for l in (ROOT/'cases.jsonl').read_text().splitlines()]
        self.assertEqual({r['expected']['disposition'] for r in rows},set(schema()['properties']['disposition']['enum']))
        self.assertEqual({r['expected']['priority'] for r in rows},{'normal','high','urgent'})
        for band in ('small','medium','large'):
            self.assertEqual(sum(r['fixture_metadata']['size_band']==band for r in rows),60)

    def test_generated_records_reproduce_facts_and_policy(self):
        prompt=(ROOT/'prompt.txt').read_text()
        for name in ('cases','pilot'):
            facts=json.loads((ROOT/(name+'-facts.json')).read_text())
            rows=[json.loads(l) for l in (ROOT/(name+'.jsonl')).read_text().splitlines()]
            for fact,row in zip(facts,rows):
                self.assertEqual(manual(fact,prompt,schema()),row)


if __name__=='__main__': unittest.main()
