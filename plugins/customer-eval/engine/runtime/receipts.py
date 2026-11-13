"""Independent worker receipts survive a coordinator disconnect or expiration."""
import json
import os
import re
from datetime import datetime, timezone
from customer_eval.contract import digest


def location(run_id, trial_id):
    if not re.fullmatch(r'[a-f0-9-]{36}',run_id) or not re.fullmatch(r'[a-f0-9]{64}',trial_id):
        raise ValueError('Invalid run/trial receipt identity')
    prefix=os.environ.get('EVAL_ARTIFACT_PREFIX','customer-evaluation').strip('/')
    return os.environ['EVAL_ARTIFACT_BUCKET'],f'{prefix}/trial-receipts/{run_id}/{trial_id}'


def read(s3,bucket,key):
    # An absent GetObject returns 403 without bucket-wide ListBucket access.
    # List only this exact receipt prefix, then read an exact matching object.
    listing=s3.list_objects_v2(Bucket=bucket,Prefix=key,MaxKeys=1)
    if not any(item['Key']==key for item in listing.get('Contents',[])):return None
    try:
        body=s3.get_object(Bucket=bucket,Key=key)['Body']
        try:return json.loads(body.read())
        finally:body.close()
    except s3.exceptions.NoSuchKey:return None


def execute_once(payload, operation, *, lookup_only=False, s3=None):
    import boto3
    s3=s3 or boto3.client('s3')
    trial=payload['trial'];bucket,key=location(payload['run_id'],trial['trial_id'])
    fingerprint=digest(trial)
    prior=read(s3,bucket,key+'.json')
    if prior:
        if prior['fingerprint']!=fingerprint:raise ValueError('Receipt payload fingerprint mismatch')
        return dict(prior['result'],receipt_recovered=True)
    claim=read(s3,bucket,key+'.claim.json')
    if claim and claim['fingerprint']!=fingerprint:raise ValueError('Trial claim fingerprint mismatch')
    if lookup_only or claim:
        return {'trial_id':trial['trial_id'],'status':'in_progress' if claim else 'not_found','outputs':[]}
    # A successful claim precedes paid work. Duplicate invocations cannot execute
    # the same trial again, including when the first response was lost.
    try:
        s3.put_object(Bucket=bucket,Key=key+'.claim.json',IfNoneMatch='*',ContentType='application/json',
            Body=json.dumps({'fingerprint':fingerprint,'started_at':datetime.now(timezone.utc).isoformat()}).encode())
    except Exception as exc:
        if getattr(exc,'response',{}).get('Error',{}).get('Code') not in ('PreconditionFailed','412'):raise
        return execute_once(payload,operation,lookup_only=True,s3=s3)
    result=operation()
    s3.put_object(Bucket=bucket,Key=key+'.json',IfNoneMatch='*',ContentType='application/json',
        Body=json.dumps({'fingerprint':fingerprint,'completed_at':datetime.now(timezone.utc).isoformat(),'result':result}).encode())
    return result
