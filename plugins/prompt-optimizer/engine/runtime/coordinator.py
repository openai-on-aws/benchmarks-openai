"""Background AgentCore job; checkpoints and reports survive the caller disconnecting.

Uses the Runtime session lifetime, not an unbounded durable scheduler. Abrupt
termination can leave status at running because finally handlers cannot execute.
Inspect the saved checkpoint; charged attempts are never automatically replayed.
For long studies use the local coordinator with AgentCore workers. Reviewed
holdout-only continuation is available in scripts/complete_holdout.py.
"""
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from customer_eval.contract import digest
from customer_eval.runner import Runner


class CoordinatorPaused(RuntimeError): pass


def read_study_input(body):
    """Bound the full S3 study, independently of each model request envelope."""
    try:
        from customer_eval.cloud_limits import study_input_limit
        maximum = study_input_limit(os.environ)
        raw = body.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError(f'Cloud input exceeds deployed EVAL_MAX_INPUT_BYTES ({maximum})')
        return raw
    finally:
        body.close()


def deadline_guard(started, max_seconds, trial_seconds, *, clock=time.monotonic):
    """Stop at a checkpoint before the microVM's finite lifetime expires."""
    if clock()-started >= max_seconds-max(120,trial_seconds+30):
        raise CoordinatorPaused('Coordinator lifetime approaching; resume the durable checkpoint')


def start_job(payload, app):
    bucket = os.environ['EVAL_ARTIFACT_BUCKET']
    prefix = os.environ.get('EVAL_ARTIFACT_PREFIX', 'customer-evaluation').strip('/')
    job_id = payload['job_id']
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', job_id):
        raise ValueError('Use an 8-80 character job ID containing letters, numbers, hyphens or underscores')
    input_key = payload['input_key']
    if not input_key.startswith(prefix + '/inputs/'):
        raise ValueError('Input key must be under the configured evaluation input prefix')
    s3 = boto3.client('s3')
    raw = read_study_input(s3.get_object(Bucket=bucket, Key=input_key)['Body'])
    config = json.loads(raw)
    fingerprint = config.pop('fingerprint')
    if digest(config) != fingerprint or payload['fingerprint'] != fingerprint:
        raise ValueError('Cloud input fingerprint mismatch')
    config['fingerprint'] = fingerprint
    if config['document']['runner'].get('budget_policy') == 'unlimited' and os.environ.get('EVAL_ALLOW_UNLIMITED_BUDGET') != '1':
        raise ValueError('Deployment does not allow uncapped model budgets')
    budget = config['document']['execution']['budget_usd']
    if budget is not None and budget > float(os.environ['EVAL_MAX_MODEL_BUDGET_USD']):
        raise ValueError('Job model budget exceeds the deployment limit')
    if config['document']['execution']['concurrency'] > int(os.environ.get('EVAL_MAX_CONCURRENCY', '4')):
        raise ValueError('Job concurrency exceeds the deployment limit')
    from runtime.app import validate_route
    for route in config['routes'].values(): validate_route(route)
    key = f'{prefix}/jobs/{job_id}'
    # Unique job claim: duplicate submissions never start a second set of paid trials.
    s3.put_object(Bucket=bucket, Key=key + '/status.json', IfNoneMatch='*',
                  Body=json.dumps({'status': 'accepted', 'fingerprint': fingerprint}).encode(), ContentType='application/json')
    task = app.add_async_task('evaluation-' + job_id)
    started = time.monotonic()

    def run():
        try:
            with tempfile.TemporaryDirectory(prefix='evaluation-job-') as directory:
                runner = Runner(config, directory, backend='agentcore',
                                runtime_arn=os.environ['EVAL_WORKER_RUNTIME_ARN'],
                                region=os.environ['AWS_REGION'])
                runner.store.before_new_attempt = lambda: deadline_guard(started,
                    int(os.environ.get('EVAL_COORDINATOR_MAX_SECONDS','28800')),
                    config['document']['execution']['timeout_seconds'])
                last_checkpoint = 0
                def checkpoint():
                    nonlocal last_checkpoint
                    if runner.store.checkpoint_event=='progress' and time.monotonic()-last_checkpoint<5:
                        return
                    # Called under Store.lock after commit; SQLite backup creates a consistent snapshot.
                    destination = sqlite3.connect(Path(directory) / 'checkpoint.sqlite')
                    try: runner.store.db.backup(destination)
                    finally: destination.close()
                    s3.upload_file(str(Path(directory) / 'checkpoint.sqlite'), bucket, key + '/checkpoint.sqlite')
                    s3.put_object(Bucket=bucket, Key=key + '/status.json', ContentType='application/json',
                                  Body=json.dumps({'status':'running','fingerprint':fingerprint,
                                      'updated_at':datetime.now(timezone.utc).isoformat()}).encode())
                    last_checkpoint = time.monotonic()
                runner.store.checkpoint = checkpoint
                try: report = runner.run()
                finally:
                    runner.store.checkpoint_event = 'final'
                    checkpoint()
                for filename in ('report.json', 'report.md', 'selected-prompt.json',
                                 'decision.json', 'decision.md', 'decision.html', 'original-prompt.txt',
                                 'selected-prompt.txt', 'prompt.diff'):
                    s3.upload_file(str(Path(directory) / filename), bucket, key + '/' + filename)
                for candidate in (Path(directory) / 'candidate-prompts').glob('*.txt'):
                    s3.upload_file(str(candidate), bucket, key + '/candidate-prompts/' + candidate.name)
                state = {'status': 'completed', 'fingerprint': fingerprint, 'report_key': key + '/report.json',
                         'reserved_model_spend_usd': report['reserved_model_spend_usd']}
        except CoordinatorPaused:
            state = {'status':'paused','fingerprint':fingerprint,
                     'reason':'Coordinator lifetime approaching; checkpoint and worker receipts retained',
                     'recovery':'Use scripts/resume_agentcore.py to continue with a local coordinator; no overall job time limit.'}
        except Exception as exc:
            state = {'status': 'failed', 'error_type': type(exc).__name__, 'fingerprint': fingerprint,
                     'recovery': 'Inspect the checkpoint. Do not resubmit automatically; charged attempts may be incomplete.'}
        finally:
            try:
                s3.put_object(Bucket=bucket, Key=key + '/status.json', Body=json.dumps(state).encode(), ContentType='application/json')
            finally: app.complete_async_task(task)
    threading.Thread(target=run, daemon=True).start()
    return {'job_id': job_id, 'status': 'accepted', 'bucket': bucket, 'status_key': key + '/status.json'}
