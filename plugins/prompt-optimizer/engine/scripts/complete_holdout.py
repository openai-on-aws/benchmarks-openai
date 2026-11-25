"""Reviewed continuation after a coordinator dies during holdout.

Preserve the interrupted checkpoint. Reuse completed search evidence without
new mutation calls, retain lost results as infrastructure failures with unknown
usage, and execute a distinct full holdout cohort on the same deployed workers.
This is an operator-driven recovery path, not automatic cloud failover.
"""
from pathlib import Path
import argparse,hashlib,json,re,sqlite3,sys


def require_deterministic_grading(config):
    if any(rule['type'] in ('llm_rubric', 'llm_source_rubric')
           for rule in config['grading'].values()):
        raise ValueError('This recovery path requires deterministic graders')


def prepare(config, checkpoint, directory, selected_prompt, digest, grade, accepted_uncertain):
    require_deterministic_grading(config)
    directory=Path(directory)
    if directory.exists():raise ValueError('Recovery output must be a new directory; preserve earlier attempts')
    source=sqlite3.connect('file:'+str(Path(checkpoint).resolve())+'?mode=ro',uri=True)
    records=source.execute('SELECT id,state,value FROM attempts').fetchall()
    pending={ident for ident,state,_ in records if state!='complete'}
    if pending!=set(accepted_uncertain):
        source.close();raise ValueError('Acknowledge exactly the uncertain trial IDs; no implicit retry')
    mapped={}
    variants=[(config['source'],config['prompt']), (config['target'],config['prompt'])]
    if selected_prompt!=config['prompt']:variants.append((config['target'],selected_prompt))
    for dataset in config['splits']['holdout']:
        for case in config['datasets'][dataset][:config['document']['execution']['max_cases_per_dataset']]:
            for repeat in range(config['document']['execution']['repetitions']):
                for variant,prompt in variants:
                    ident=digest([config['fingerprint'],variant,prompt,case['id'],repeat,'holdout','standard'])
                    mapped[ident]=(variant,case,repeat)
    if not pending.issubset(mapped):
        source.close();raise ValueError('Recovery supports only uncertain holdout trials, not an interrupted search or judge')
    directory.mkdir(parents=True)
    target=sqlite3.connect(directory/'run.sqlite');source.backup(target);source.close()
    for ident in pending:
        variant,case,_=mapped[ident]
        value={'trial_id':ident,'status':'infrastructure_error','outputs':[],
               'model_requested':config['routes'][variant['model_id']]['model_id'],
               'error_type':'CoordinatorResultLost','usage_unknown':True,
               'recovery_note':'Original dispatched attempt was not replayed. Its response was not checkpointed before the coordinator ended.'}
        target.execute('UPDATE attempts SET state="complete",value=? WHERE id=?',(json.dumps(value),ident))
        target.execute('INSERT INTO attempts VALUES (?,?,?,?)',(digest(['grade',ident]),'complete',0,json.dumps(grade(config,case,value,None))))
    target.commit()
    values={ident:json.loads(value) for ident,state,value in target.execute('SELECT id,state,value FROM attempts') if state=='complete'}
    old=[]
    for ident,(variant,case,repeat) in mapped.items():
        if ident in values:
            old.append({'id':ident,'case_id':case['id'],'repeat':repeat,'split':'holdout','phase':'standard',
                        'model_id':variant['model_id'],'result':values[ident],'checks':values[digest(['grade',ident])]})
    target.close()
    audit={'source_checkpoint_sha256':hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest(),
           'uncertain_trial_ids':sorted(pending),'retained_interrupted_holdout':old,
           'selected_prompt_sha256':digest(selected_prompt),
           'scope':'Search and prompt selection are reused. Interrupted holdout remains separate. All final comparisons use a fresh complete cohort; uncertain charges remain unknown.'}
    (directory/'recovery.json').write_text(json.dumps(audit,indent=2)+'\n')
    return audit


def recovery_runner_class(Runner,digest):
    class RecoveryRunner(Runner):
        phase='holdout-confirmation-1'
        expected_prompt_sha256=None
        allow_holdout=False

        def call(self,model_id,text,purpose='judge'):
            ident=digest(['completion',purpose,model_id,text])
            row=self.store.db.execute('SELECT state,value FROM attempts WHERE id=?',(ident,)).fetchone()
            if not row or row[0]!='complete':raise ValueError('Recovery cannot create new optimizer or judge calls')
            return json.loads(row[1])['text']

        def evaluate_group(self,variants,split,phase='standard'):
            if split=='holdout':
                if digest(variants[-1][1])!=self.expected_prompt_sha256:
                    raise ValueError('Reconstructed selected prompt differs from reviewed frozen prompt')
                if not self.allow_holdout:raise HoldoutReady('Search was reconstructed entirely from completed records')
                return super().evaluate_group(variants,split,self.phase)
            for dataset in self.config['splits'][split]:
                for case in self.config['datasets'][dataset][:self.config['document']['execution']['max_cases_per_dataset']]:
                    for repeat in range(self.config['document']['execution']['repetitions']):
                        for variant,prompt in variants:
                            ident=digest([self.config['fingerprint'],variant,prompt,case['id'],repeat,split,phase])
                            for key in (ident,digest(['grade',ident])):
                                row=self.store.db.execute('SELECT state FROM attempts WHERE id=?',(key,)).fetchone()
                                if not row or row[0]!='complete':raise ValueError('Recovery requires complete search trials and grades')
            self._search_only=True
            try:return super().evaluate_group(variants,split,phase)
            finally:self._search_only=False

        def execute(self,payload):
            if getattr(self,'_search_only',False):raise ValueError('Recovery cannot execute missing search trials')
            return super().execute(payload)
    return RecoveryRunner


class HoldoutReady(Exception):pass


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['config','checkpoint','output','selected-prompt','source-snapshot','runtime-arn','profile','region']:
        p.add_argument('--'+name,required=True)
    p.add_argument('--accept-uncertain',action='append',default=[])
    p.add_argument('--phase',default='holdout-confirmation-1')
    p.add_argument('--live',action='store_true')
    args=p.parse_args()
    if not re.fullmatch(r'holdout-confirmation-[a-zA-Z0-9_-]+',args.phase):p.error('Use a distinct holdout-confirmation phase')
    sys.path.insert(0,str(Path(args.source_snapshot).resolve()))
    from customer_eval.contract import digest,implementation_fingerprint
    from customer_eval.runner import Runner
    from customer_eval.scoring import grade
    cfg=json.loads(Path(args.config).read_text());selected=Path(args.selected_prompt).read_text().strip()
    assert digest({k:v for k,v in cfg.items() if k!='fingerprint'})==cfg['fingerprint']
    assert implementation_fingerprint()==cfg['implementation_fingerprint'],'Use the exact measured source snapshot'
    require_deterministic_grading(cfg)
    audit=prepare(cfg,args.checkpoint,args.output,selected,digest,grade,args.accept_uncertain)
    cls=recovery_runner_class(Runner,digest)
    runner=cls(cfg,args.output,backend='agentcore',runtime_arn=args.runtime_arn,profile=args.profile,region=args.region)
    runner.phase=args.phase;runner.expected_prompt_sha256=digest(selected)
    try:runner.run()
    except HoldoutReady:pass
    else:raise RuntimeError('Expected a no-network preflight stop before holdout')
    audit.update(phase=args.phase,search_reconstructed_without_calls=True,live=args.live)
    (Path(args.output)/'recovery.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps({'preflight':'passed','frozen_prompt_sha256':digest(selected),'phase':args.phase,'uncertain_original_trials':len(args.accept_uncertain),'live':args.live}),flush=True)
    if not args.live:return
    runner.allow_holdout=True
    def checkpoint():
        db=sqlite3.connect(Path(args.output)/'checkpoint.sqlite')
        try:runner.store.db.backup(db)
        finally:db.close()
    runner.store.checkpoint=checkpoint
    report=runner.run();report['recovery']=audit
    (Path(args.output)/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    with (Path(args.output)/'report.md').open('a') as f:
        f.write(f'\nRecovery: the original coordinator ended during holdout. Its partial cohort is retained in report.json. The final tables use a new complete holdout cohort with the same frozen prompt. {len(args.accept_uncertain)} lost trial receipts have unknown usage and are not scored as successful.\n')
    (Path(args.output)/'status.json').write_text(json.dumps({'status':'completed','fingerprint':cfg['fingerprint'],'coordinator':'local','phase':args.phase},indent=2)+'\n')
    print(json.dumps({'status':'completed','phase':args.phase,'output':args.output}),flush=True)


if __name__=='__main__':main()
