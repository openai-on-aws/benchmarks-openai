"""Recover one interrupted AgentCore trial without replaying claimed model work.

Stop the original controller first. The default only reads a durable receipt.
--dispatch-if-unclaimed permits the first dispatch only when no worker claim
exists. The deployed worker's atomic claim remains the authority in a race.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from customer_eval.contract import cases, digest, implementation_fingerprint, trial_payload
from customer_eval.runner import Runner, InterruptedAttempt
from optimizer import _parse_candidates


def payload_for_attempt(config, records, ident):
    prompts = [config['prompt']]
    requests = [r for r in records if r.get('purpose') == 'optimizer']
    for request in requests:
        prompts.extend(_parse_candidates(request['text'], config['document']['runner']['candidates_per_generation']))
    prompts = list(dict.fromkeys(prompts))
    for split in ('development', 'validation', 'holdout'):
        phases = ['standard'] + ([f'joint-comparison-{i+1}' for i in range(len(requests))] if split == 'validation' else [])
        for variant in (config['source'], config['target']):
            if variant == config['source'] and split == 'development':
                continue
            for prompt in ([config['prompt']] if variant == config['source'] else prompts):
                for case in cases(config, split):
                    for repeat in range(config['document']['execution']['repetitions']):
                        for phase in phases:
                            if digest([config['fingerprint'], variant, prompt, case['id'], repeat, split, phase]) == ident:
                                return trial_payload(config, variant, prompt, case, ident)
    raise ValueError('Pending ID is not a reconstructible agent trial. Judge and optimizer requests are never replayed.')


def recover_trial(pool, payload, *, dispatch_if_unclaimed=False):
    result = pool._call('trial_result', payload)
    method = 'durable_receipt'
    if result.get('trial_id') != payload['trial_id']:
        raise ValueError('Receipt identity mismatch')
    if result.get('status') == 'not_found' and dispatch_if_unclaimed:
        # Same run and trial identity: execute_once claims atomically before any
        # model call, including if the original request arrives concurrently.
        result = pool._call('trial', payload)
        method = 'dispatch_after_absent_claim'
    if result.get('trial_id') != payload['trial_id']:
        raise ValueError('Recovered trial identity mismatch')
    if result.get('status') in ('not_found', 'in_progress'):
        return None
    if result.get('status') not in ('completed', 'infrastructure_error', 'harness_error', 'timeout', 'agent_error', 'worker_error', 'error'):
        raise ValueError('Unexpected receipt status: ' + str(result.get('status')))
    recovery = {'method': method, 'no_paid_replay': True, 'original_invocation_timing': 'unavailable'}
    if result.get('dispatch_timing', {}).get('action') == 'trial':
        recovery['recovery_dispatch_timing'] = result['dispatch_timing']
        # This measures only the recovery dispatch, not the earlier failed call
        # and interruption. Keep it for diagnostics without understating latency.
        result = dict(result, dispatch_timing=dict(result['dispatch_timing'], client_ms=None))
    return dict(result, transport_recovery=recovery)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trial-id', required=True)
    parser.add_argument('--runtime-arn', required=True)
    parser.add_argument('--profile', help='Omit to use the normal AWS credential chain')
    parser.add_argument('--region', required=True)
    parser.add_argument('--dispatch-if-unclaimed', action='store_true')
    args = parser.parse_args()
    if not (args.output / 'run.sqlite').is_file():
        raise ValueError('An existing interrupted checkpoint is required')
    config = json.loads((args.output / 'run-input.json').read_text())
    if config['fingerprint'] != digest({k:v for k,v in config.items() if k != 'fingerprint'}):
        raise ValueError('The prepared configuration was changed after freezing')
    if config['implementation_fingerprint'] != implementation_fingerprint():
        raise ValueError('Local implementation differs from the frozen run; restore the matching source first')
    runner = Runner(config, args.output, backend='agentcore', runtime_arn=args.runtime_arn,
                    profile=args.profile, region=args.region)
    try:
        row = runner.store.db.execute('SELECT state FROM attempts WHERE id=?', (args.trial_id,)).fetchone()
        if not row or row[0] != 'started':
            raise ValueError('Expected exactly an existing pending attempt')
        records = [json.loads(r[0]) for r in runner.store.db.execute("SELECT value FROM attempts WHERE state='complete'")]
        payload = payload_for_attempt(config, records, args.trial_id)
        def reject_new():
            raise InterruptedAttempt('The pending reservation disappeared; no new attempt was created')
        result = runner.store.perform(args.trial_id, 0, reject_new,
            recover=lambda: recover_trial(runner.cloud_pool(), payload, dispatch_if_unclaimed=args.dispatch_if_unclaimed))
        (args.output / ('recovery-' + args.trial_id + '.json')).write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'trial_id': result['trial_id'], 'status': result['status'], 'recovery': result['transport_recovery']}))
    finally:
        if runner._cloud_pool:
            runner._cloud_pool.close()
            (args.output / ('recovery-cleanup-' + args.trial_id + '.json')).write_text(json.dumps(runner._cloud_pool.cleanup, indent=2) + '\n')


if __name__ == '__main__':
    main()
