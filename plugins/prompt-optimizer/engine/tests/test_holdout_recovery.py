import hashlib,json,sqlite3
from pathlib import Path

import pytest

from customer_eval.contract import ROOT,load,digest,trial_payload
from customer_eval.runner import Runner,Store
from customer_eval.scoring import grade
from customer_eval.simulation import execute
from customer_eval.costs import trial_cost
from scripts.complete_holdout import prepare,recovery_runner_class,HoldoutReady


@pytest.fixture
def cfg():return load(ROOT/'examples/customer-support/input.yaml')


def interrupted(cfg,tmp_path,split='holdout'):
    store=Store(tmp_path/'original','identity',100)
    case=cfg['datasets'][cfg['splits'][split][0]][0]
    ident=digest([cfg['fingerprint'],cfg['source'],cfg['prompt'],case['id'],0,split,'standard'])
    store.db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(ident,'started',3,None));store.db.commit();store.db.close()
    return tmp_path/'original/run.sqlite',ident


def test_recovery_retains_unknown_usage_and_original_checkpoint(cfg,tmp_path):
    source,ident=interrupted(cfg,tmp_path);before=source.read_bytes()
    audit=prepare(cfg,source,tmp_path/'new',cfg['prompt'],digest,grade,[ident])
    assert source.read_bytes()==before
    assert audit['source_checkpoint_sha256']==hashlib.sha256(before).hexdigest()
    row=audit['retained_interrupted_holdout'][0]
    assert row['result']['status']=='infrastructure_error' and row['result']['usage_unknown']
    assert not all(v['pass'] for v in row['checks'].values())
    assert trial_cost(cfg,row)['estimated_cost_usd'] is None
    db=sqlite3.connect(tmp_path/'new/run.sqlite')
    assert db.execute('SELECT reserved FROM attempts WHERE id=?',(ident,)).fetchone()[0]==3
    db.close()


def test_recovery_requires_exact_uncertain_acknowledgement(cfg,tmp_path):
    source,_=interrupted(cfg,tmp_path)
    with pytest.raises(ValueError,match='exactly'):prepare(cfg,source,tmp_path/'new',cfg['prompt'],digest,grade,[])
    assert not (tmp_path/'new').exists()


def test_recovery_refuses_interrupted_search(cfg,tmp_path):
    source,ident=interrupted(cfg,tmp_path,'development')
    with pytest.raises(ValueError,match='only uncertain holdout'):prepare(cfg,source,tmp_path/'new',cfg['prompt'],digest,grade,[ident])
    assert not (tmp_path/'new').exists()


def test_recovery_never_overwrites_an_existing_output(cfg,tmp_path):
    source,ident=interrupted(cfg,tmp_path);out=tmp_path/'new';out.mkdir()
    with pytest.raises(ValueError,match='new directory'):prepare(cfg,source,out,cfg['prompt'],digest,grade,[ident])


def runner(cfg,tmp_path):return recovery_runner_class(Runner,digest)(cfg,tmp_path/'run',backend='agentcore',runtime_arn='arn:example',region='us-east-1')


def test_recovery_uses_cached_optimizer_without_new_calls(cfg,tmp_path):
    r=runner(cfg,tmp_path);key=digest(['completion','optimizer','m2','same input'])
    r.store.db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(key,'complete',1,json.dumps({'text':'saved output'})));r.store.db.commit()
    assert r.call('m2','same input','optimizer')=='saved output'
    with pytest.raises(ValueError,match='cannot create new'):r.call('m2','different input','optimizer')
    assert r.store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==1


def test_recovery_fails_before_reserving_missing_search_trials(cfg,tmp_path):
    r=runner(cfg,tmp_path);r.execute=lambda _:pytest.fail('must not execute')
    with pytest.raises(ValueError,match='complete search'):r.evaluate_group([(cfg['target'],cfg['prompt'])],'validation')
    assert r.store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==0


def test_recovery_rejects_a_changed_selected_prompt(cfg,tmp_path):
    r=runner(cfg,tmp_path);r.expected_prompt_sha256=digest('reviewed prompt')
    with pytest.raises(ValueError,match='differs'):r.evaluate_group([(cfg['target'],cfg['prompt'])],'holdout')


def test_recovery_preflight_stops_before_holdout_calls(cfg,tmp_path):
    r=runner(cfg,tmp_path);r.expected_prompt_sha256=digest(cfg['prompt']);r.execute=lambda _:pytest.fail('must not execute')
    with pytest.raises(HoldoutReady):r.evaluate_group([(cfg['target'],cfg['prompt'])],'holdout')
    assert r.store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==0


def test_fresh_holdout_has_distinct_ids_and_resumes_without_replay(cfg,tmp_path):
    r=runner(cfg,tmp_path);r.expected_prompt_sha256=digest(cfg['prompt']);r.allow_holdout=True;seen=[]
    r.execute=lambda payload:(seen.append(payload['trial_id']) or execute(payload))
    groups=r.evaluate_group([(cfg['source'],cfg['prompt']),(cfg['target'],cfg['prompt'])],'holdout')
    assert seen and all(row['phase']=='holdout-confirmation-1' for group in groups for row in group)
    for group,variant in zip(groups,[cfg['source'],cfg['target']]):
        for row in group:
            old=digest([cfg['fingerprint'],variant,cfg['prompt'],row['case_id'],row['repeat'],'holdout','standard'])
            assert old!=row['id']
    count=len(seen)
    assert r.evaluate_group([(cfg['source'],cfg['prompt']),(cfg['target'],cfg['prompt'])],'holdout')==groups
    assert len(seen)==count
