"""AgentCore entrypoint using the same isolated worker as local execution."""
import os
from urllib.parse import urlparse
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from customer_eval.worker import isolated

app = BedrockAgentCoreApp()


def validate_route(route):
    url = urlparse(route['base_url'])
    allowed = os.environ['EVAL_ALLOWED_ENDPOINT_HOSTS'].split(',')
    if url.scheme != 'https' or url.hostname not in allowed or url.username or url.password or url.port not in (None, 443):
        raise ValueError('Model endpoint is not in the deployment allowlist')


@app.entrypoint
def invoke(payload, context):
    if payload.get('action') == 'start_job':
        from runtime.coordinator import start_job
        return start_job(payload, app)
    if payload.get('action') not in ('trial', 'trial_result'):
        raise ValueError('Expected action: trial or trial_result')
    maximum = int(os.environ.get('EVAL_MAX_TRIAL_SECONDS', '300'))
    timeout = payload['timeout_seconds']
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= maximum:
        raise ValueError('Trial timeout exceeds the deployment limit')
    validate_route(payload['trial']['route'])
    limits = payload['trial']['limits']
    if limits['max_requests'] > int(os.environ.get('EVAL_MAX_REQUESTS_PER_TRIAL', '20')):
        raise ValueError('Request count exceeds deployment limit')
    if payload.get('run_id'):
        from runtime.receipts import execute_once
        return execute_once(payload, lambda: isolated(payload['trial'], timeout),
                            lookup_only=payload['action']=='trial_result')
    if payload['action']=='trial_result':raise ValueError('Receipt lookup requires a run ID')
    return isolated(payload['trial'], timeout)


if __name__ == '__main__':
    app.run()
