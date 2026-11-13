"""Create this evaluation Runtime directly; no List APIs are needed.

All created identities, exact requests and AWS errors are saved in a local receipt.
Use a distinct receipt directory per deployment; retries reuse the client token.
"""
from __future__ import annotations

import argparse
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['bootstrap','sync-policy','create','status','configure-worker'])
    parser.add_argument('--profile', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--receipt-dir', required=True)
    parser.add_argument('--artifact')
    parser.add_argument('--max-session-seconds',type=int,default=28800)
    parser.add_argument('--idle-session-seconds',type=int,default=300)
    args=parser.parse_args()
    if not 60 <= args.idle_session_seconds <= args.max_session_seconds <= 28800:
        parser.error('MicroVM lifecycle requires 60 <= idle <= maximum <= 28800 seconds')
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
        sha=hashlib.sha256(artifact.read_bytes()).hexdigest()
        key=f'deployment/{sha}.zip'
        def upload():
            with artifact.open('rb') as body:
                return s3.put_object(Bucket=receipt['created']['bucket'],Key=key,Body=body,ExpectedBucketOwner=account,
                                    ContentType='application/zip',Metadata={'sha256':sha})
        if receipt.get('artifact',{}).get('sha256') == sha:
            uploaded={'VersionId':receipt['artifact'].get('version_id')}
        else:
            uploaded=call('s3:PutObject',upload)
        receipt['artifact']={'sha256':sha,'key':key,'version_id':uploaded.get('VersionId')};save(path,receipt)
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
        save(directory/'create-request.json',request)
        result=call('bedrock-agentcore:CreateAgentRuntime',lambda:acc.create_agent_runtime(**request))
        receipt['created'].update(runtime_arn=result['agentRuntimeArn'],runtime_id=result['agentRuntimeId'])
        save(path,receipt);save(directory/'create-response.json',result)
        print(json.dumps({'runtime_arn':result['agentRuntimeArn'],'status':result['status']}))
        return
    request=json.loads((directory/'create-request.json').read_text())
    request.pop('agentRuntimeName');request['clientToken']=str(uuid.uuid4())
    request['agentRuntimeId']=receipt['created']['runtime_id']
    request['environmentVariables']['EVAL_WORKER_RUNTIME_ARN']=receipt['created']['runtime_arn']
    request['lifecycleConfiguration']={'idleRuntimeSessionTimeout':args.idle_session_seconds,'maxLifetime':args.max_session_seconds}
    request['environmentVariables']['EVAL_COORDINATOR_MAX_SECONDS']=str(args.max_session_seconds)
    result=call('bedrock-agentcore:UpdateAgentRuntime',lambda:acc.update_agent_runtime(**request))
    save(directory/'update-response.json',result)
    print(json.dumps({'status':result['status']}))


if __name__=='__main__':main()
