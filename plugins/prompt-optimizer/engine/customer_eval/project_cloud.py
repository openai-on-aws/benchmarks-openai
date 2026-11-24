"""Qualification, submission and recovery links using the existing cloud scripts."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from .contract import ROOT, digest
from .project import read, save, load_with_endpoints


def _receipts(directory):
    return sorted((Path(directory) / 'submission').glob('*.json'))


def validate_runtime_capacity(config, cloud, runtime):
    """Reject deployment mismatches before any billable qualification calls."""
    env = runtime.get('environmentVariables', {})
    if (env.get('EVAL_ARTIFACT_BUCKET') != cloud['bucket'] or
            env.get('EVAL_ARTIFACT_PREFIX', 'customer-evaluation') != cloud.get('prefix', 'customer-evaluation')):
        raise ValueError('Project artifact bucket/prefix must match the deployed runtime')
    execution = config['document']['execution']
    limits = config['document']['runner']['limits']
    from .cloud_limits import study_input_limit
    input_maximum = study_input_limit(env)
    for name, requested, default in (
        ('EVAL_MAX_INPUT_BYTES', len(json.dumps(config).encode()), input_maximum),
        ('EVAL_MAX_CONCURRENCY', execution['concurrency'], 4),
        ('EVAL_MAX_TRIAL_SECONDS', execution['timeout_seconds'], 300),
        ('EVAL_MAX_REQUESTS_PER_TRIAL', limits['max_requests'], 20),
    ):
        maximum = int(env.get(name, str(default)))
        if maximum < 1 or requested > maximum:
            raise ValueError(f'Study requires {requested} but deployed {name} is {maximum}; update the deployment before qualification')


def run(directory, *, live=False):
    if not live:
        raise ValueError('Live execution needs explicit user authorization and --live; preparing an intake does not authorize spend.')
    directory = Path(directory).resolve()
    with (directory / '.launch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'launch_in_progress', 'message': 'Another invocation is qualifying or submitting this project. Inspect status; do not start another job.'}
        receipts = _receipts(directory)
        if receipts:
            return {'status': 'already_submitted', 'receipt': str(receipts[0]),
                    'message': 'Inspect status. Existing or uncertain jobs are never resubmitted automatically.'}
        state = read(directory / 'project.json')
        if not state.get('prepared'):
            raise ValueError('Complete intake and prepare the input preview before running')
        path = directory / state['prepared']['config']
        config = load_with_endpoints(path, live=True)
        if config['fingerprint'] != state['prepared']['fingerprint']:
            raise ValueError('Prepared inputs or implementation changed; create a new reviewed project revision')
        connection = read(path.parent / 'connection.json')
        cloud = connection['cloud']
        import boto3
        from botocore.config import Config
        session = boto3.Session(profile_name=cloud['profile'], region_name=cloud['region'])
        sdk = Config(connect_timeout=10, read_timeout=30, retries={'max_attempts': 0})
        identity = session.client('sts', config=sdk).get_caller_identity()
        account = cloud['runtime_arn'].split(':')[4]
        if identity['Account'] != account or identity['Account'] != cloud['account']:
            raise ValueError('AWS identity does not match the configured AgentCore account')
        runtime = session.client('bedrock-agentcore-control', config=sdk).get_agent_runtime(
            agentRuntimeId=cloud['runtime_arn'].split('/')[-1])
        if runtime['status'] != 'READY':
            raise ValueError('The configured runtime is not ready: ' + runtime['status'])
        validate_runtime_capacity(config, cloud, runtime)
        save(directory / 'runtime-verification.json', {'account': account, 'region': cloud['region'],
            'runtime_arn': cloud['runtime_arn'], 'version': runtime['agentRuntimeVersion'], 'status': runtime['status']})
        env = dict(os.environ, **read(path.parent / 'endpoints.json'))
        qualifier = directory / 'qualification'
        summary_path = qualifier / 'qualification-summary.json'
        if not summary_path.exists():
            # The qualification Runner has its own durable checkpoint. Interrupted
            # attempts cannot silently create another billable request on re-entry.
            subprocess.run([sys.executable, str(ROOT / 'scripts/qualify_agentcore.py'),
                '--config', str(path), '--output', str(qualifier), '--runtime-arn', cloud['runtime_arn'],
                '--region', cloud['region'], '--profile', cloud['profile'], '--live'], check=True, env=env)
        summary = read(summary_path)
        if summary['parent_fingerprint'] != config['fingerprint'] or not summary['qualified']:
            raise ValueError('Route qualification did not pass for these exact inputs. Inspect qualification receipts.')
        # Poor application answers are an evaluation input, not a route failure.
        state['stage'] = 'routes_qualified'
        save(directory / 'project.json', state)
        subprocess.run([sys.executable, str(ROOT / 'scripts/submit_agentcore.py'),
            '--config', str(path), '--runtime-arn', cloud['runtime_arn'], '--region', cloud['region'],
            '--profile', cloud['profile'], '--bucket', cloud['bucket'], '--prefix', cloud.get('prefix', 'customer-evaluation'),
            '--receipt-dir', str(directory / 'submission')], check=True, env=env)
        state['stage'] = 'submitted'
        save(directory / 'project.json', state)
        return {'status': 'submitted', 'receipt': str(_receipts(directory)[0]),
                'qualification': str(summary_path), 'next': 'Use status to check this job; do not submit it again.'}


def refresh(directory):
    directory = Path(directory).resolve()
    receipts = _receipts(directory)
    if not receipts:
        return {'status': 'not_submitted', 'stage': read(directory / 'project.json')['stage']}
    if len(receipts) != 1:
        raise ValueError('Multiple submissions exist; select and investigate receipts before continuing')
    receipt = read(receipts[0])
    import boto3
    from botocore.config import Config
    session = boto3.Session(profile_name=receipt['profile'], region_name=receipt['region'])
    s3 = session.client('s3', config=Config(connect_timeout=10, read_timeout=30, retries={'max_attempts': 0}))
    def fetch(key):
        response = s3.get_object(Bucket=receipt['bucket'], Key=key)
        try:
            return response['Body'].read()
        finally:
            response['Body'].close()
    try:
        status = json.loads(fetch(receipt['status_key']))
    except s3.exceptions.NoSuchKey:
        return {'status': receipt['status'], 'message': 'Cloud status is not available yet. The saved receipt remains authoritative; do not resubmit automatically.'}
    if status.get('fingerprint') != receipt['request']['fingerprint']:
        raise ValueError('Cloud status does not match the submitted input fingerprint')
    save(directory / 'cloud-status.json', status)
    if status['status'] != 'completed':
        return dict(status, job_id=receipt['job_id'],
                    recovery='Completed attempts are preserved. For paused/failed jobs, inspect the checkpoint and use the existing recovery commands; do not create a new job.')
    output = directory / 'results'
    output.mkdir(exist_ok=True)
    key = receipt['status_key'].rsplit('/', 1)[0]
    for filename in ('report.json', 'report.md', 'selected-prompt.json', 'checkpoint.sqlite'):
        path = output / filename
        if not path.exists():
            data = fetch(key + '/' + filename)
            tmp = path.with_suffix(path.suffix + '.tmp')
            tmp.write_bytes(data); tmp.replace(path)
    report = read(output / 'report.json')
    state = read(directory / 'project.json')
    # Reading an older result must not require today's source, local assets or
    # provider endpoint environment to match the original execution. Use the
    # self-contained submitted input and verify it against the durable receipt.
    config = json.loads(fetch(receipt['request']['input_key']))
    submitted = receipt['request']['fingerprint']
    if (config.get('fingerprint') != submitted or
            digest({k: v for k, v in config.items() if k != 'fingerprint'}) != submitted):
        raise ValueError('Submitted input does not match its durable receipt')
    if report['fingerprint'] != config['fingerprint']:
        raise ValueError('Downloaded result does not match the frozen submitted input')
    from .decision_report import write
    decision = write(config, report, output, read(directory / 'qualification/qualification-summary.json'))
    save(output / 'run-input.json', config)
    state['stage'] = 'completed'
    save(directory / 'project.json', state)
    return {'status': 'completed', 'job_id': receipt['job_id'], 'recommendation': decision['recommendation'],
            'report': str(output / 'decision.html'), 'quality_label': decision['quality_label'],
            'holdout': [{k: v for k, v in r.items() if k != 'timing'} for r in decision['holdout']],
            'total_estimated_model_cost_usd': decision['campaign_plus_qualification_model_cost_usd'],
            'prompt_changed': decision['prompt_comparison']['changed']}
