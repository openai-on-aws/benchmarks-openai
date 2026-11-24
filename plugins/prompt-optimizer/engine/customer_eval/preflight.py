"""Inspect a study and its deployment without inference, writes or provisioning."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from .contract import implementation_fingerprint
from .project import load_with_endpoints, read


def inspect(directory, *, backend='local', deployment_receipt=None, session=None):
    """Return explicit pass/fail/unknown checks; successful preflight is not inference qualification.

    ``session`` is an optional boto3-compatible session for offline tests or a
    caller-managed AWS session. This function performs only GET/HEAD/STS reads.
    It does not save a report or modify the project.
    """
    if backend not in ('local', 'agentcore'):
        raise ValueError('Preflight backend must be local or agentcore')
    directory = Path(directory).resolve()
    state = read(directory / 'project.json')
    checks = []
    def record(name, status, detail, *, blocking=True, **evidence):
        checks.append({'check': name, 'status': status, 'detail': detail,
                       'blocking': blocking, **evidence})
    local_fingerprint = implementation_fingerprint()
    prepared = state.get('prepared')
    connection_path = directory / prepared['config'] if prepared else None
    connection_path = (connection_path.parent / 'connection.json' if prepared else
                       Path(state.get('decisions', {}).get('connection', '')))
    if not connection_path.is_file():
        record('connection', 'fail', 'Provide a model connection file before preflight')
        return _result(backend, checks, False)
    connection = read(connection_path)
    config = None
    if prepared:
        try:
            config = load_with_endpoints(directory / prepared['config'], live=True)
            matched = config['fingerprint'] == prepared['fingerprint']
            record('frozen_inputs', 'pass' if matched else 'fail',
                   'Frozen study matches current inputs and implementation' if matched else
                   'Prepared inputs or implementation changed; create a new project revision')
        except (ValueError, KeyError, OSError) as exc:
            record('frozen_inputs', 'fail', str(exc))
    else:
        record('frozen_inputs', 'unknown', 'Intake is not prepared yet; this is a draft preflight', blocking=False)
    if config:
        routes = config['routes']
    else:
        decisions = state.get('decisions', {})
        selected = [decisions.get('source_model'), decisions.get('target_model')]
        if decisions.get('optimization_enabled', True):
            selected.append(decisions.get('optimizer_model'))
        routes = {}
        for ident in dict.fromkeys(selected):
            if not ident or ident not in connection.get('models', {}):
                record('model_selection', 'fail', 'Select all model roles and their connection entries')
                continue
            entry = connection['models'][ident]
            model = entry['model']
            routes[ident] = dict(entry['route'], model_id=model.get('model_id'),
                base_url=entry.get('base_url'), region=model.get('region'),
                credential_ref=model.get('credential_ref'), parameters=model.get('parameters', {}))
    for ident, route in routes.items():
        url = urlsplit(route.get('base_url') or '')
        valid = bool(route.get('model_id') and url.scheme == 'https' and url.hostname and
                     not url.username and not url.password and url.port in (None, 443))
        record('route:' + ident, 'pass' if valid else 'fail',
               'Explicit model and HTTPS endpoint' if valid else 'Missing model or invalid endpoint',
               model_id=route.get('model_id'), endpoint_host=url.hostname)
        prices = [route.get(k) for k in ('input_usd_per_million', 'output_usd_per_million',
                    'cached_input_usd_per_million', 'cache_write_usd_per_million')]
        priced = (all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in prices)
                  and bool(route.get('pricing_verified_at')) and bool(route.get('pricing_source'))
                  and 'ILLUSTRATIVE' not in str(route.get('pricing_source')).upper()
                  and type(route.get('pricing_max_input_tokens')) is int
                  and route['pricing_max_input_tokens'] > 0)
        record('pricing:' + ident, 'pass' if priced else 'fail',
               'Route price inputs supplied; provider invoice and entitlement are not verified here' if priced else
               'Supply current input/output/cache prices, source, verification date and context bound',
               pricing_verified_at=route.get('pricing_verified_at'))
    cloud = connection.get('cloud', {})
    sdk = None
    def aws(profile, region):
        nonlocal sdk
        from botocore.config import Config
        sdk = Config(connect_timeout=10, read_timeout=30, retries={'max_attempts': 0})
        if session is not None:
            return session
        import boto3
        return boto3.Session(profile_name=profile, region_name=region)
    if backend == 'local':
        verified_profiles = set()
        for ident, route in routes.items():
            ref = route.get('credential_ref') or ''
            if route.get('auth') == 'bearer':
                available = ref.startswith('env:') and bool(os.environ.get(ref[4:]))
                record('credential:' + ident, 'pass' if available else 'fail',
                       'Bearer credential is available in the named environment variable' if available else
                       'Configure the named bearer credential locally; never put it in the connection file')
            elif route.get('auth') == 'sigv4':
                key = (ref, route.get('region'))
                if key in verified_profiles:
                    continue
                verified_profiles.add(key)
                try:
                    if not ref.startswith('aws_profile:'):
                        raise ValueError('SigV4 requires an aws_profile: reference')
                    sess = aws(ref.split(':', 1)[1], route.get('region'))
                    identity = sess.client('sts', config=sdk).get_caller_identity()
                    record('credential:' + ident, 'pass', 'AWS credentials resolved through STS', account=identity['Account'])
                except Exception as exc:
                    record('credential:' + ident, 'fail', 'AWS credential check failed: ' + type(exc).__name__)
            else:
                record('credential:' + ident, 'fail', 'Unsupported route authentication')
        record('inference_access', 'unknown', 'Model entitlement, request compatibility and inference must be qualified with an authorized model call', blocking=False)
        return _result(backend, checks, bool(prepared))
    required_cloud = ('profile', 'region', 'account', 'runtime_arn', 'bucket')
    if any(not cloud.get(k) for k in required_cloud):
        record('cloud_connection', 'fail', 'Set cloud profile, Region, account, runtime ARN and artifact bucket')
        return _result(backend, checks, bool(prepared))
    try:
        sess = aws(cloud['profile'], cloud['region'])
        identity = sess.client('sts', config=sdk).get_caller_identity()
        account_ok = identity['Account'] == cloud['account'] == cloud['runtime_arn'].split(':')[4]
        record('aws_identity', 'pass' if account_ok else 'fail', 'AWS identity must match the configured runtime account', account=identity['Account'])
        if not account_ok:
            return _result(backend, checks, bool(prepared))
        control = sess.client('bedrock-agentcore-control', config=sdk)
        runtime_id = cloud['runtime_arn'].split('/')[-1]
        runtime = control.get_agent_runtime(agentRuntimeId=runtime_id)
        endpoint = control.get_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName='DEFAULT')
        version = str(runtime['agentRuntimeVersion'])
        ready = (runtime['status'] == 'READY' and endpoint['status'] == 'READY'
                 and str(endpoint.get('liveVersion')) == version)
        record('runtime', 'pass' if ready else 'fail', 'Runtime and DEFAULT endpoint must be READY on the same version',
               runtime_version=version, runtime_status=runtime['status'], endpoint_status=endpoint['status'])
        s3 = sess.client('s3', config=sdk)
        try:
            s3.head_bucket(Bucket=cloud['bucket'], ExpectedBucketOwner=cloud['account'])
            record('artifact_bucket', 'pass', 'Caller can inspect the configured artifact bucket')
        except Exception as exc:
            record('artifact_bucket', 'fail', 'Artifact bucket read check failed: ' + type(exc).__name__)
        env = runtime.get('environmentVariables', {})
        hosts = set(env.get('EVAL_ALLOWED_ENDPOINT_HOSTS', '').split(','))
        missing = sorted({urlsplit(r.get('base_url') or '').hostname for r in routes.values()} - hosts, key=str)
        record('endpoint_allowlist', 'fail' if missing else 'pass',
               'Runtime must permit every selected model endpoint', missing_hosts=missing)
        worker = env.get('EVAL_WORKER_RUNTIME_ARN')
        record('worker_runtime', 'pass' if worker == cloud['runtime_arn'] else 'fail',
               'Runtime worker ARN must point to this deployment')
        artifact_ok = (env.get('EVAL_ARTIFACT_BUCKET') == cloud['bucket'] and
                       env.get('EVAL_ARTIFACT_PREFIX') == cloud.get('prefix', 'customer-evaluation'))
        record('runtime_artifacts', 'pass' if artifact_ok else 'fail', 'Runtime and project artifact bucket/prefix must match')
        if config:
            execution = config['document']['execution']
            limits = config['document']['runner']['limits']
            from .cloud_limits import study_input_limit
            try:
                input_ceiling = study_input_limit(env)
            except (ValueError, TypeError):
                input_ceiling = None
            comparisons = [('concurrency', execution['concurrency'], env.get('EVAL_MAX_CONCURRENCY')),
                           ('trial_seconds', execution['timeout_seconds'], env.get('EVAL_MAX_TRIAL_SECONDS')),
                           ('requests', limits['max_requests'], env.get('EVAL_MAX_REQUESTS_PER_TRIAL')),
                           ('study_input_bytes', len(json.dumps(config).encode()),
                            input_ceiling)]
            for name, requested, ceiling in comparisons:
                fits = ceiling is not None and float(requested) <= float(ceiling)
                record('limit:' + name, 'pass' if fits else 'fail', 'Study must fit the deployed runtime limit', requested=requested, deployed=ceiling)
            budget = execution.get('budget_usd')
            uncapped = config['document']['runner'].get('budget_policy') == 'unlimited'
            if uncapped:
                record('budget_policy', 'pass' if env.get('EVAL_ALLOW_UNLIMITED_BUDGET') == '1' else 'fail',
                       'Runtime must permit the study spending policy')
            elif budget is not None:
                cap = env.get('EVAL_MAX_MODEL_BUDGET_USD')
                record('budget_policy', 'pass' if cap is not None and budget <= float(cap) else 'fail',
                       'Capped study budget must fit the runtime ceiling', requested=budget, deployed=cap)
        role_arn = runtime['roleArn']
        try:
            role = sess.client('iam', config=sdk).get_role(RoleName=role_arn.rsplit('/', 1)[1])['Role']
            record('execution_role', 'pass' if role['Arn'] == role_arn else 'fail',
                   'Execution role exists; effective permission evaluation requires route qualification', role_arn=role_arn)
        except Exception as exc:
            record('execution_role', 'unknown', 'Role readback unavailable: ' + type(exc).__name__ + '; this does not establish inference denial', blocking=False)
        deployed = runtime.get('agentRuntimeArtifact', {}).get('codeConfiguration', {}).get('code', {}).get('s3', {})
        if deployed.get('bucket') and deployed.get('prefix'):
            request = {'Bucket': deployed['bucket'], 'Key': deployed['prefix']}
            if deployed.get('versionId'):
                request['VersionId'] = deployed['versionId']
            metadata = s3.head_object(**request).get('Metadata', {})
            claimed = metadata.get('implementation-fingerprint')
            evidence = 'deployed artifact metadata recorded by the verified ZIP uploader'
            if deployment_receipt:
                receipt = read(deployment_receipt)
                artifact = receipt.get('artifact', {})
                matched = (receipt.get('status') == 'ready' and receipt.get('runtime_arn') == cloud['runtime_arn']
                    and str(receipt.get('target_version')) == version and artifact.get('bucket') == deployed['bucket']
                    and artifact.get('key') == deployed['prefix'] and artifact.get('version_id') == deployed.get('versionId')
                    and artifact.get('sha256') == metadata.get('sha256'))
                if not matched:
                    record('deployment_receipt', 'fail', 'Upgrade receipt does not identify the deployed artifact/version')
                else:
                    claimed = artifact.get('implementation_fingerprint')
                    evidence = 'matching verified ZIP upgrade receipt and deployed artifact metadata'
            record('implementation', 'pass' if claimed == local_fingerprint else 'fail' if claimed else 'unknown',
                   'Local engine identity compared with ' + evidence if claimed else
                   'Deployed engine identity is unknown; use a verified upgrade receipt or deploy a matching engine',
                   local_fingerprint=local_fingerprint, deployed_fingerprint=claimed)
        else:
            record('implementation', 'unknown', 'Container deployment needs platform-specific build identity verification')
    except Exception as exc:
        record('cloud_readback', 'fail', 'AWS read-only preflight failed: ' + type(exc).__name__)
    record('inference_access', 'unknown', 'No inference was performed. Qualification still checks model access, worker fingerprint, effective IAM, and request compatibility', blocking=False)
    return _result(backend, checks, bool(prepared))


def _result(backend, checks, prepared):
    blockers = [c['check'] for c in checks if c['blocking'] and c['status'] != 'pass']
    return {'backend': backend, 'read_only': True, 'model_calls': 0,
            'ready_for_qualification': bool(prepared and not blockers),
            'blockers': blockers, 'checks': checks}
