"""Submit a validated customer job to an already deployed AgentCore coordinator."""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from customer_eval.contract import load


def read_config(path, comparison_only=False):
    if not comparison_only:
        return load(path, live=True)
    from customer_eval.frozen_comparison import validate
    config = json.loads(Path(path).read_text())
    validate(config)
    return config


def main():
    import boto3
    from botocore.config import Config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--comparison-only', action='store_true',
                        help='Use a reviewed frozen comparison JSON input; no search or selection')
    parser.add_argument('--runtime-arn', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--prefix', default='customer-evaluation')
    parser.add_argument('--receipt-dir', default='runs/cloud-submissions')
    args = parser.parse_args()
    config = read_config(args.config, args.comparison_only)
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    job_id = str(uuid.uuid4())
    key = args.prefix.strip('/') + '/inputs/' + job_id + '.json'
    request = {'action': 'start_job', 'job_id': job_id, 'input_key': key, 'fingerprint': config['fingerprint']}
    runtime_session = str(uuid.uuid4())
    receipt_path = Path(args.receipt_dir) / (job_id + '.json')
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {'status': 'prepared', 'job_id': job_id, 'runtime_session_id': runtime_session,
               'runtime_arn': args.runtime_arn, 'region': args.region, 'profile': args.profile,
               'bucket': args.bucket, 'request': request,
               'status_key': args.prefix.strip('/') + '/jobs/' + job_id + '/status.json'}
    def save():
        temporary = receipt_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(receipt, indent=2) + '\n')
        temporary.replace(receipt_path)
    save()
    print(json.dumps({'receipt': str(receipt_path), 'job_id': job_id}), flush=True)
    session.client('s3').put_object(Bucket=args.bucket, Key=key, IfNoneMatch='*',
                                   Body=json.dumps(config).encode(), ContentType='application/json')
    receipt['status'] = 'dispatching'
    save()
    client = session.client('bedrock-agentcore',config=Config(read_timeout=60,retries={'max_attempts':0}))
    try:
        response = client.invoke_agent_runtime(agentRuntimeArn=args.runtime_arn, runtimeSessionId=runtime_session,
            payload=json.dumps(request).encode(), contentType='application/json', accept='application/json')
        if response.get('runtimeSessionId') != runtime_session: raise ValueError('Coordinator session mismatch')
        body = json.loads(response['response'].read())
        if body.get('job_id') != job_id or body.get('status') != 'accepted':
            raise ValueError('Coordinator did not acknowledge this job')
        receipt.update(status='accepted', response=body,
                       request_id=response.get('ResponseMetadata', {}).get('RequestId'))
        save()
        print(json.dumps(body))
    except Exception as exc:
        receipt.update(status='uncertain', error_type=type(exc).__name__,
                       recovery='Inspect the recorded S3 status and checkpoint before submitting another job.')
        save()
        raise


if __name__ == '__main__': main()
