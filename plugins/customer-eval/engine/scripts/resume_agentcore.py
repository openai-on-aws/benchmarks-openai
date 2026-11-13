"""Continue a stopped cloud job locally, preserving completed paid attempts."""
import argparse
import hashlib
import json
import sys
import os
import sqlite3
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from customer_eval.contract import digest,implementation_fingerprint
from customer_eval.runner import Runner


def restore(s3,receipt,output,*,confirmed_stopped=False):
    bucket=receipt['bucket']
    def read(key):
        body=s3.get_object(Bucket=bucket,Key=key)['Body']
        try:return body.read()
        finally:body.close()
    status=json.loads(read(receipt['status_key']))
    if status['status']=='completed':raise ValueError('Job is already complete; download its report instead')
    if status['status'] not in ('paused','failed','interrupted') and not confirmed_stopped:
        raise ValueError('Confirm the original coordinator is stopped before resuming')
    config=json.loads(read(receipt['request']['input_key']))
    fingerprint=config['fingerprint']
    if digest({k:v for k,v in config.items() if k!='fingerprint'})!=fingerprint:
        raise ValueError('Cloud input fingerprint mismatch')
    if fingerprint!=receipt['request']['fingerprint'] or fingerprint!=status['fingerprint']:
        raise ValueError('Receipt/status fingerprint mismatch')
    if config['implementation_fingerprint']!=implementation_fingerprint():
        raise ValueError('Use the exact source version that created this job')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    checkpoint=output/'run.sqlite'
    if not checkpoint.exists():
        data=read(receipt['status_key'].rsplit('/',1)[0]+'/checkpoint.sqlite')
        with tempfile.NamedTemporaryFile(dir=output,prefix='.checkpoint-',delete=False) as f:
            temporary=Path(f.name)
            f.write(data);f.flush();os.fsync(f.fileno())
        try:
            db=sqlite3.connect(f'file:{temporary}?mode=ro',uri=True)
            try:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('Cloud checkpoint failed SQLite integrity check')
                if not db.execute("SELECT value FROM meta WHERE key='execution_id'").fetchone():
                    raise ValueError('Cloud checkpoint has no durable execution identity')
            finally:db.close()
            os.link(temporary,checkpoint)  # Atomic publication; never overwrite a local run.
        finally:temporary.unlink(missing_ok=True)
        (output/'restoration.json').write_text(json.dumps({'parent_job_id':receipt['job_id'],
            'source_checkpoint_sha256':hashlib.sha256(data).hexdigest(),'cloud_status':status['status']},indent=2))
    return config


def main():
    import boto3
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receipt',required=True);p.add_argument('--output',required=True)
    p.add_argument('--live',action='store_true');p.add_argument('--confirm-cloud-stopped',action='store_true')
    args=p.parse_args()
    if not args.live:p.error('--live is required to continue model execution')
    receipt=json.loads(Path(args.receipt).read_text())
    s3=boto3.Session(profile_name=receipt['profile'],region_name=receipt['region']).client('s3')
    config=restore(s3,receipt,args.output,confirmed_stopped=args.confirm_cloud_stopped)
    runner=Runner(config,args.output,backend='agentcore',runtime_arn=receipt['runtime_arn'],
                  profile=receipt['profile'],region=receipt['region'])
    runner.run()
    print(str((Path(args.output)/'report.md').resolve()))


if __name__=='__main__':main()
