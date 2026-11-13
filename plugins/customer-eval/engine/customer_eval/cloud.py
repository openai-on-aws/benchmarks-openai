"""AgentCore Runtime transport with exact trial/session correlation and no retries."""
import json
import uuid
import queue
import time


class AgentCorePool:
    """Reuse a bounded set of environments within one run, never agent state.

    Every invocation still starts an isolated worker subprocess and Codex home.
    Environments rotate between trials, so their lifetime is not a job deadline.
    """
    def __init__(self, runtime_arn, profile, region, timeout, concurrency, *, run_id,
                 max_session_seconds=1800, client=None):
        import boto3
        from botocore.config import Config
        if not runtime_arn or not region or concurrency < 1:
            raise ValueError('Explicit Runtime, Region and positive concurrency are required')
        self.runtime_arn, self.timeout, self.run_id = runtime_arn, timeout, run_id
        self.max_age = max_session_seconds
        self.client = client or boto3.Session(profile_name=profile, region_name=region).client(
            'bedrock-agentcore', config=Config(connect_timeout=10, read_timeout=timeout+30,
                max_pool_connections=concurrency, retries={'max_attempts':0}))
        self.available = queue.Queue()
        for _ in range(concurrency): self.available.put(None)
        self.closed = False
        self.cleanup = []

    def _stop(self, lease):
        if not lease: return
        try:
            self.client.stop_runtime_session(agentRuntimeArn=self.runtime_arn, runtimeSessionId=lease['id'])
            status = 'stop_accepted'
        except Exception as exc:
            status = type(exc).__name__
        self.cleanup.append({'runtime_session_id':lease['id'],'status':status})

    def _call(self, action, payload):
        if self.closed: raise ValueError('Runtime pool is closed')
        lease = self.available.get()
        reusable = True
        try:
            if lease and time.monotonic()-lease['created']+self.timeout+30 >= self.max_age:
                self._stop(lease); lease = None
            if lease is None: lease = {'id':str(uuid.uuid4()),'created':time.monotonic(),'calls':0}
            started = time.monotonic_ns()
            response = self.client.invoke_agent_runtime(agentRuntimeArn=self.runtime_arn,
                runtimeSessionId=lease['id'], payload=json.dumps({'action':action,
                    'trial':payload,'timeout_seconds':self.timeout,'run_id':self.run_id}).encode(),
                contentType='application/json',accept='application/json')
            body = response['response']
            try:
                if response.get('runtimeSessionId') != lease['id']:
                    raise ValueError('AgentCore response session does not match the invoked session')
                result = json.loads(body.read())
            finally: body.close()
            if result.get('trial_id') != payload['trial_id']:
                raise ValueError('AgentCore result does not match the requested trial')
            result.update(runtime_session_id=lease['id'],runtime_arn=self.runtime_arn)
            result['dispatch_timing'] = {'client_ms':(time.monotonic_ns()-started)/1e6,
                'action':action,
                'environment_reused':lease['calls']>0,'environment_call_index':lease['calls']+1}
            lease['calls'] += 1
            return result
        except Exception:
            # An unacknowledged request may still be running. Do not stop it or
            # automatically repeat paid work; its durable receipt can be read.
            reusable = False
            if lease:self.cleanup.append({'runtime_session_id':lease['id'],'status':'retired_unacknowledged; idle expiry applies'})
            raise
        finally:
            self.available.put(lease if reusable else None)

    def invoke(self, payload): return self._call('trial', payload)

    def recover(self, payload):
        result = self._call('trial_result', payload)
        return result if result.get('status') not in ('not_found','in_progress') else None

    def close(self):
        if self.closed: return
        self.closed = True
        while not self.available.empty(): self._stop(self.available.get_nowait())
        self.client.close()


def invoke_trial(payload, runtime_arn, profile, region, timeout):
    import boto3
    from botocore.config import Config
    if not runtime_arn or not region:
        raise ValueError('AgentCore execution requires an explicit Runtime ARN and Region')
    client = boto3.Session(profile_name=profile, region_name=region).client(
        'bedrock-agentcore', config=Config(connect_timeout=10, read_timeout=timeout + 30, retries={'max_attempts': 0}))
    session = str(uuid.uuid4())
    response = client.invoke_agent_runtime(agentRuntimeArn=runtime_arn, runtimeSessionId=session,
        payload=json.dumps({'action': 'trial', 'trial': payload, 'timeout_seconds': timeout}).encode(),
        contentType='application/json', accept='application/json')
    if response.get('runtimeSessionId') != session:
        raise ValueError('AgentCore response session does not match the invoked session')
    try: result = json.loads(response['response'].read())
    finally: response['response'].close()
    if result.get('trial_id') != payload['trial_id']:
        raise ValueError('AgentCore result does not match the requested trial')
    result['runtime_session_id'] = session
    result['runtime_arn'] = runtime_arn
    return result
