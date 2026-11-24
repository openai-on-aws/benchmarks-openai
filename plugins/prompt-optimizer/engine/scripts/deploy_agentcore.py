"""Create this evaluation Runtime directly; no List APIs are needed.

All created identities, exact requests and AWS errors are saved in a local receipt.
Use a distinct receipt directory per deployment; retries reuse the client token.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import sys
import zipfile
import hashlib
import json
import time
import uuid
from pathlib import Path

import boto3
from botocore.config import Config


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, default=str) + '\n')
    temporary.replace(path)


def policies(account, region, name, bucket):
    runtime = f'arn:aws:bedrock-agentcore:{region}:{account}:runtime/{name}-*'
    trust = {'Version':'2012-10-17','Statement':[{
        'Effect':'Allow','Principal':{'Service':'bedrock-agentcore.amazonaws.com'},'Action':'sts:AssumeRole',
        'Condition':{'StringEquals':{'aws:SourceAccount':account},'ArnLike':{'aws:SourceArn':runtime}}}]}
    models = ['openai.gpt-5.6-sol', 'openai.gpt-6.1-sol']
    model_arns = [f'arn:aws:bedrock:{r}::foundation-model/{m}' for r in ['us-east-1','us-east-2','us-west-2'] for m in models]
    profile_arns = [f'arn:aws:bedrock:{region}:{account}:inference-profile/us.{m}' for m in models]
    model_arns += profile_arns
    policy = {'Version':'2012-10-17','Statement':[
        {'Sid':'SelectedModels','Effect':'Allow','Action':['bedrock:InvokeModel','bedrock:InvokeModelWithResponseStream'],'Resource':model_arns},
        {'Sid':'SelectedProfiles','Effect':'Allow','Action':['bedrock:GetInferenceProfile'],'Resource':profile_arns},
        # Responses authorizes the default project as well as the inference target.
        # See https://docs.aws.amazon.com/bedrock/latest/userguide/inference-responses-api.html
        {'Sid':'ResponsesDefaultProject','Effect':'Allow','Action':['bedrock:InvokeModel'],
         'Resource':f'arn:aws:bedrock:{region}:{account}:project/default',
         'Condition':{'ArnEquals':{'bedrock:ModelArn':profile_arns}}},
        {'Sid':'OwnWorkers','Effect':'Allow','Action':['bedrock-agentcore:InvokeAgentRuntime','bedrock-agentcore:StopRuntimeSession'],'Resource':[runtime]},
        {'Sid':'DeploymentCode','Effect':'Allow','Action':['s3:GetObject','s3:GetObjectVersion'],'Resource':f'arn:aws:s3:::{bucket}/deployment/*'},
        {'Sid':'EvaluationArtifacts','Effect':'Allow','Action':['s3:GetObject','s3:PutObject'],'Resource':f'arn:aws:s3:::{bucket}/customer-evaluation/*'},
        {'Sid':'ReceiptLookup','Effect':'Allow','Action':['s3:ListBucket'],'Resource':f'arn:aws:s3:::{bucket}',
         'Condition':{'StringLike':{'s3:prefix':'customer-evaluation/trial-receipts/*'}}},
        {'Sid':'BucketLocation','Effect':'Allow','Action':['s3:GetBucketLocation'],'Resource':f'arn:aws:s3:::{bucket}'},
        {'Sid':'RuntimeLogGroups','Effect':'Allow','Action':['logs:CreateLogGroup','logs:DescribeLogStreams'],
         'Resource':f'arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/{name}-*'},
        {'Sid':'RuntimeLogStreams','Effect':'Allow','Action':['logs:CreateLogStream','logs:PutLogEvents'],
         'Resource':f'arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/{name}-*:log-stream:*'},
    ]}
    return trust, policy



# Read back current settings rather than replaying an old create request. In
# particular, an existing Gateway allowlist/credential setup must survive an
# engine-only deployment.
PRESERVED_RUNTIME_FIELDS = (
    'roleArn', 'networkConfiguration', 'description', 'authorizerConfiguration',
    'requestHeaderConfiguration', 'protocolConfiguration', 'lifecycleConfiguration',
    'metadataConfiguration', 'environmentVariables', 'filesystemConfigurations',
    'capacityProviderConfiguration', 'platformVersion',
)


def archive_identity(artifact):
    """Verify that the ZIP contains exactly the engine implementation being used."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from customer_eval.contract import digest, implementation_fingerprint
    def included(name):
        p = Path(name)
        return (name in ('hillclimb.py', 'optimizer.py', 'requirements-integration.txt',
                         'intake/skills/eval-intake/scripts/input_framework.py') or
                (name.endswith('.py') and name.startswith(('customer_eval/', 'customer_workloads/'))) or
                (p.parent.as_posix() in ('runtime', 'agent') and p.suffix == '.py') or
                (p.parent.as_posix() == 'intake/skills/eval-intake/assets' and p.suffix == '.json'))
    with zipfile.ZipFile(artifact) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Runtime archive contains duplicate file names')
        files = {name: hashlib.sha256(z.read(name)).hexdigest() for name in names if included(name)}
    fingerprint = digest(files)
    if fingerprint != implementation_fingerprint():
        raise ValueError('Runtime ZIP source differs from the current engine; rebuild before updating')
    return {'sha256': hashlib.sha256(Path(artifact).read_bytes()).hexdigest(),
            'implementation_fingerprint': fingerprint, 'verified_source_files': len(files)}


def update_request(current, runtime_id, location, token):
    if current.get('status') != 'READY':
        raise ValueError('Existing runtime must be READY before an update')
    request = {k: deepcopy(current[k]) for k in PRESERVED_RUNTIME_FIELDS if k in current}
    request.update(agentRuntimeId=runtime_id, clientToken=token,
                   agentRuntimeArtifact={'codeConfiguration': {'code': {'s3': location},
                       'runtime': 'PYTHON_3_12', 'entryPoint': ['main.py']}})
    return request


def _public_runtime(current):
    # An execution environment can contain secrets. Receipts retain provenance,
    # identities and variable names, never its values or a full update request.
    keys = ('agentRuntimeArn', 'agentRuntimeId', 'agentRuntimeVersion', 'status',
            'roleArn', 'networkConfiguration', 'lifecycleConfiguration', 'agentRuntimeArtifact')
    return {k: current[k] for k in keys if k in current} | {
        'environment_variable_names': sorted(current.get('environmentVariables', {}))}


def runtime_limit_overrides(args):
    """Only explicit, non-secret runtime capacity settings may change on update."""
    settings = {}
    for attribute, key, maximum in (
        ('max_input_bytes', 'EVAL_MAX_INPUT_BYTES', 268435456),
        ('max_concurrency', 'EVAL_MAX_CONCURRENCY', 64),
        ('max_trial_seconds', 'EVAL_MAX_TRIAL_SECONDS', 28800),
    ):
        value = getattr(args, attribute, None)
        if value is None:
            continue
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'{attribute} must be an integer between 1 and {maximum}')
        settings[key] = str(value)
    return settings


def freeze_create_request(path, request):
    """Keep one create token tied to one exact request after uncertain dispatch."""
    if path.exists() and json.loads(path.read_text()) != request:
        raise ValueError('Create request differs from the saved request; inspect the existing deployment before changing inputs')
    if not path.exists():
        save(path, request)


def update_existing(args, session):
    """Update one existing runtime; retain an idempotent receipt on interruption."""
    directory = Path(args.receipt_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    receipt_path = directory / 'upgrade.json'
    with (directory / '.upgrade.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('This runtime update is already running; inspect its receipt')
        sdk = Config(connect_timeout=10, read_timeout=60, retries={'max_attempts': 0})
        control = session.client('bedrock-agentcore-control', config=sdk)
        s3 = session.client('s3', config=sdk)
        account = session.client('sts', config=sdk).get_caller_identity()['Account']
        if not args.runtime_id or not args.artifact:
            raise ValueError('update requires --runtime-id and --artifact')
        artifact = Path(args.artifact).resolve()
        identity = archive_identity(artifact)
        overrides = runtime_limit_overrides(args)
        current = control.get_agent_runtime(agentRuntimeId=args.runtime_id)
        arn = current['agentRuntimeArn']
        if arn.split(':')[4] != account or arn.split(':')[3] != args.region:
            raise ValueError('Runtime identity differs from the selected AWS account/Region')
        if not args.runtime_id.startswith(args.name + '-'):
            raise ValueError('--name does not match the selected runtime identity')
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if (receipt['runtime_arn'] != arn or receipt['artifact']['sha256'] != identity['sha256']
                    or receipt['profile'] != args.profile
                    or receipt.get('runtime_limit_overrides', {}) != overrides):
                raise ValueError('Upgrade receipt belongs to different inputs; use a new receipt directory')
        else:
            old_code = current.get('agentRuntimeArtifact', {}).get('codeConfiguration', {})
            old_s3 = old_code.get('code', {}).get('s3', {})
            if not old_s3.get('bucket'):
                raise ValueError('This helper upgrades an existing Python ZIP runtime, not a container runtime')
            receipt = {'version': 1, 'account': account, 'region': args.region, 'profile': args.profile,
                       'runtime_arn': arn, 'runtime_id': args.runtime_id,
                       'client_token': str(uuid.uuid4()), 'status': 'prepared',
                       'previous_runtime_version': current['agentRuntimeVersion'],
                       'artifact': identity | {'bucket': old_s3['bucket'],
                           'key': 'deployment/' + identity['sha256'] + '.zip'},
                       'before': _public_runtime(current), 'runtime_limit_overrides': overrides}
            save(receipt_path, receipt)
        if receipt['status'] == 'prepared':
            if current['agentRuntimeVersion'] != receipt['previous_runtime_version'] or current['status'] != 'READY':
                raise ValueError('Runtime changed since this update was prepared; inspect and use a new receipt')
            item = receipt['artifact']
            with artifact.open('rb') as stream:
                uploaded = s3.put_object(Bucket=item['bucket'], Key=item['key'], Body=stream,
                    ExpectedBucketOwner=account, ContentType='application/zip', Metadata={
                        'sha256': item['sha256'], 'implementation-fingerprint': item['implementation_fingerprint']})
            item['version_id'] = uploaded.get('VersionId')
            location = {'bucket': item['bucket'], 'prefix': item['key']}
            if item['version_id']:
                location['versionId'] = item['version_id']
            latest = control.get_agent_runtime(agentRuntimeId=args.runtime_id)
            if latest['agentRuntimeVersion'] != receipt['previous_runtime_version'] or latest['status'] != 'READY':
                save(receipt_path, receipt)
                raise ValueError('Another runtime update occurred during upload; inspect before retrying')
            request = update_request(latest, args.runtime_id, location, receipt['client_token'])
            if overrides:
                request.setdefault('environmentVariables', {}).update(overrides)
            receipt['status'] = 'update_dispatching'
            receipt['preserved_fields'] = [k for k in PRESERVED_RUNTIME_FIELDS if k in request]
            save(receipt_path, receipt)
            try:
                response = control.update_agent_runtime(**request)
            except Exception as exc:
                receipt['status'] = 'update_uncertain'
                receipt['error_type'] = type(exc).__name__
                save(receipt_path, receipt)
                raise
            receipt['target_version'] = response['agentRuntimeVersion']
            receipt['request_id'] = response.get('ResponseMetadata', {}).get('RequestId')
            receipt['status'] = 'updating'
            save(receipt_path, receipt)
        # Reentry after dispatch is read-only: never blindly submit another update.
        deadline = time.monotonic() + args.wait_seconds
        while True:
            after = control.get_agent_runtime(agentRuntimeId=args.runtime_id)
            deployed = after.get('agentRuntimeArtifact', {}).get('codeConfiguration', {}).get('code', {}).get('s3', {})
            item = receipt['artifact']
            same_artifact = (deployed.get('bucket') == item['bucket'] and deployed.get('prefix') == item['key']
                             and (not item.get('version_id') or deployed.get('versionId') == item['version_id']))
            endpoint = control.get_agent_runtime_endpoint(agentRuntimeId=args.runtime_id, endpointName='DEFAULT')
            expected = str(receipt.get('target_version', after['agentRuntimeVersion']))
            limits_match = all(after.get('environmentVariables', {}).get(k) == v for k, v in overrides.items())
            if (same_artifact and limits_match and after['status'] == 'READY' and str(after['agentRuntimeVersion']) == expected
                    and endpoint['status'] == 'READY' and str(endpoint.get('liveVersion')) == expected):
                receipt.update(status='ready', target_version=expected, after=_public_runtime(after),
                               endpoint={'name': 'DEFAULT', 'status': 'READY', 'live_version': expected})
                save(receipt_path, receipt)
                return {'status': 'ready', 'runtime_arn': arn, 'runtime_version': expected,
                        'implementation_fingerprint': item['implementation_fingerprint'],
                        'receipt': str(receipt_path)}
            receipt['observed_status'] = after['status']
            save(receipt_path, receipt)
            if after['status'] in ('CREATE_FAILED', 'UPDATE_FAILED', 'FAILED'):
                raise ValueError('Runtime update failed; inspect the saved receipt and AWS failureReason')
            if time.monotonic() >= deadline:
                return {'status': 'pending', 'receipt': str(receipt_path),
                        'message': 'Re-run this same update command to check the existing update; it will not resubmit.'}
            time.sleep(min(2, max(0, deadline - time.monotonic())))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['bootstrap','sync-policy','create','status','configure-worker','update'])
    parser.add_argument('--profile', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--receipt-dir', required=True)
    parser.add_argument('--artifact')
    parser.add_argument('--runtime-id', help='Existing runtime ID for update; preserves the runtime identity')
    parser.add_argument('--wait-seconds', type=int, default=600)
    parser.add_argument('--max-session-seconds',type=int,default=28800)
    parser.add_argument('--idle-session-seconds',type=int,default=300)
    parser.add_argument('--max-input-bytes', type=int, help='Maximum serialized study size in S3; preserve existing value on update unless supplied')
    parser.add_argument('--max-concurrency', type=int, help='Maximum simultaneous study workers; preserve existing value on update unless supplied')
    parser.add_argument('--max-trial-seconds', type=int, help='Maximum duration of one application trial; preserve existing value on update unless supplied')
    args=parser.parse_args()
    overrides = runtime_limit_overrides(args)
    if not 60 <= args.idle_session_seconds <= args.max_session_seconds <= 28800:
        parser.error('MicroVM lifecycle requires 60 <= idle <= maximum <= 28800 seconds')
    if args.wait_seconds < 0:
        parser.error('--wait-seconds must be nonnegative')
    if args.action == 'update':
        session=boto3.Session(profile_name=args.profile,region_name=args.region)
        print(json.dumps(update_existing(args, session), indent=2))
        return
    directory=Path(args.receipt_dir).resolve();directory.mkdir(parents=True,exist_ok=True)
    path=directory/'deployment.json'
    session=boto3.Session(profile_name=args.profile,region_name=args.region)
    sdk=Config(connect_timeout=10,read_timeout=60,retries={'max_attempts':0})
    account=session.client('sts',config=sdk).get_caller_identity()['Account']
    receipt=json.loads(path.read_text()) if path.exists() else {
        'account':account,'region':args.region,'profile':args.profile,'name':args.name,
        'client_token':str(uuid.uuid4()),'created':{},'events':[]}
    if any(receipt[k]!=v for k,v in [('account',account),('region',args.region),('profile',args.profile),('name',args.name)]):
        raise ValueError('Deployment identity does not match the saved receipt')
    bucket=f'ade-eval-{account}-{args.region}-{receipt["client_token"][:8]}'
    role_name=f'AmazonBedrockAgentCore-{args.name}'
    trust,policy=policies(account,args.region,args.name,bucket)
    save(directory/'role-trust.json',trust);save(directory/'role-policy.json',policy)
    save(path,receipt)

    def call(label,fn):
        try:
            response=fn()
            receipt['events'].append({'action':label,'status':'success','request_id':response.get('ResponseMetadata',{}).get('RequestId'),'time':time.time()})
            save(path,receipt)
            return response
        except Exception as exc:
            receipt['events'].append({'action':label,'status':'failed','error_type':type(exc).__name__,
                                      'detail':str(exc),'request_id':getattr(exc,'response',{}).get('ResponseMetadata',{}).get('RequestId'),
                                      'time':time.time()})
            save(path,receipt)
            raise

    iam=session.client('iam',config=sdk);s3=session.client('s3',config=sdk)
    acc=session.client('bedrock-agentcore-control',config=sdk)
    if args.action=='sync-policy':
        if receipt['created'].get('role_name') != role_name:
            raise ValueError('The scoped execution role must already be recorded')
        call('iam:PutRolePolicy',lambda:iam.put_role_policy(RoleName=role_name,PolicyName='customer-evaluation',PolicyDocument=json.dumps(policy)))
        print(json.dumps({'role_arn':receipt['created']['role_arn'],'status':'policy updated'}))
        return
    if args.action=='bootstrap':
        if 'role_arn' not in receipt['created']:
            response=call('iam:CreateRole',lambda:iam.create_role(RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust),Description='Customer evaluation Runtime execution role',
                Tags=[{'Key':'Project','Value':'customer-eval-integration'}]))
            receipt['created']['role_arn']=response['Role']['Arn'];receipt['created']['role_name']=role_name;save(path,receipt)
        call('iam:PutRolePolicy',lambda:iam.put_role_policy(RoleName=role_name,PolicyName='customer-evaluation',PolicyDocument=json.dumps(policy)))
        if 'bucket' not in receipt['created']:
            request={'Bucket':bucket}
            if args.region!='us-east-1':request['CreateBucketConfiguration']={'LocationConstraint':args.region}
            call('s3:CreateBucket',lambda:s3.create_bucket(**request))
            receipt['created']['bucket']=bucket;save(path,receipt)
        call('s3:PutPublicAccessBlock',lambda:s3.put_public_access_block(Bucket=bucket,ExpectedBucketOwner=account,
            PublicAccessBlockConfiguration={'BlockPublicAcls':True,'IgnorePublicAcls':True,'BlockPublicPolicy':True,'RestrictPublicBuckets':True}))
        call('s3:PutBucketEncryption',lambda:s3.put_bucket_encryption(Bucket=bucket,ExpectedBucketOwner=account,
            ServerSideEncryptionConfiguration={'Rules':[{'ApplyServerSideEncryptionByDefault':{'SSEAlgorithm':'AES256'}}]}))
        call('s3:PutBucketVersioning',lambda:s3.put_bucket_versioning(Bucket=bucket,ExpectedBucketOwner=account,VersioningConfiguration={'Status':'Enabled'}))
        print(json.dumps(receipt['created'],indent=2))
        return
    if args.action=='status':
        result=call('bedrock-agentcore:GetAgentRuntime',lambda:acc.get_agent_runtime(agentRuntimeId=receipt['created']['runtime_id']))
        save(directory/'runtime-status.json',result)
        print(json.dumps({k:result.get(k) for k in ['agentRuntimeArn','status','failureReason']},default=str))
        return
    if args.action=='create':
        if 'runtime_arn' in receipt['created']:
            print(json.dumps({'runtime_arn':receipt['created']['runtime_arn'],'status':'already recorded; use status'}))
            return
        artifact=Path(args.artifact).resolve()
        archive_info=archive_identity(artifact)
        sha=archive_info['sha256']
        key=f'deployment/{sha}.zip'
        def upload():
            with artifact.open('rb') as body:
                return s3.put_object(Bucket=receipt['created']['bucket'],Key=key,Body=body,ExpectedBucketOwner=account,
                                    ContentType='application/zip',Metadata={'sha256':sha,
                                        'implementation-fingerprint':archive_info['implementation_fingerprint']})
        if receipt.get('artifact',{}).get('sha256') == sha:
            uploaded={'VersionId':receipt['artifact'].get('version_id')}
        else:
            uploaded=call('s3:PutObject',upload)
        receipt['artifact']={**archive_info,'key':key,'version_id':uploaded.get('VersionId')};save(path,receipt)
        location={'bucket':receipt['created']['bucket'],'prefix':key}
        if uploaded.get('VersionId'):location['versionId']=uploaded['VersionId']
        request={'agentRuntimeName':args.name,'clientToken':receipt['client_token'],
                 'agentRuntimeArtifact':{'codeConfiguration':{'code':{'s3':location},'runtime':'PYTHON_3_12','entryPoint':['main.py']}},
                 'roleArn':receipt['created']['role_arn'],'networkConfiguration':{'networkMode':'PUBLIC'},
                 'lifecycleConfiguration':{'idleRuntimeSessionTimeout':args.idle_session_seconds,'maxLifetime':args.max_session_seconds},
                 'environmentVariables':{'EVAL_USE_EXECUTION_ROLE':'1','EVAL_ALLOWED_ENDPOINT_HOSTS':f'bedrock-runtime.{args.region}.amazonaws.com',
                    'EVAL_ARTIFACT_BUCKET':receipt['created']['bucket'],'EVAL_ARTIFACT_PREFIX':'customer-evaluation',
                    'EVAL_ALLOW_UNLIMITED_BUDGET':'1','EVAL_MAX_MODEL_BUDGET_USD':'100','EVAL_MAX_CONCURRENCY':'2',
                    'EVAL_MAX_TRIAL_SECONDS':'300','EVAL_MAX_REQUESTS_PER_TRIAL':'12','AWS_REGION':args.region,
                    'EVAL_COORDINATOR_MAX_SECONDS':str(args.max_session_seconds)}}
        request['environmentVariables'].update(overrides)
        freeze_create_request(directory/'create-request.json',request)
        result=call('bedrock-agentcore:CreateAgentRuntime',lambda:acc.create_agent_runtime(**request))
        receipt['created'].update(runtime_arn=result['agentRuntimeArn'],runtime_id=result['agentRuntimeId'])
        save(path,receipt);save(directory/'create-response.json',result)
        print(json.dumps({'runtime_arn':result['agentRuntimeArn'],'status':result['status']}))
        return
    current=acc.get_agent_runtime(agentRuntimeId=receipt['created']['runtime_id'])
    if current['status'] != 'READY':
        raise ValueError('Runtime must be READY before configuring worker invocation')
    request={k:deepcopy(current[k]) for k in PRESERVED_RUNTIME_FIELDS if k in current}
    request.update(agentRuntimeId=receipt['created']['runtime_id'], clientToken=str(uuid.uuid4()),
                   agentRuntimeArtifact=deepcopy(current['agentRuntimeArtifact']))
    request.setdefault('environmentVariables', {})['EVAL_WORKER_RUNTIME_ARN']=receipt['created']['runtime_arn']
    result=call('bedrock-agentcore:UpdateAgentRuntime',lambda:acc.update_agent_runtime(**request))
    save(directory/'update-response.json',result)
    print(json.dumps({'status':result['status']}))


if __name__=='__main__':main()
