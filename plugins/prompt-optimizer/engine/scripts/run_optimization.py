"""Run the shared evaluation workflow. Simulation never invokes a model or AWS."""
import argparse
import json
import sys
import sqlite3
import subprocess
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from customer_eval.contract import load
from customer_eval.runner import Runner, InterruptedAttempt


def run_with_recovery(make_runner, recover, *, optimize, max_recoveries=2):
    """Retry orchestration only after durable recovery, never a model completion."""
    for attempt in range(max_recoveries + 1):
        try:
            return make_runner().run(optimize=optimize)
        except InterruptedAttempt as exc:
            if attempt == max_recoveries:
                raise
            recover(exc)


def recover_cloud_checkpoint(config, args, error):
    from scripts.recover_agentcore_trial import payload_for_attempt
    directory = Path(args.output)
    db = sqlite3.connect((directory / 'run.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        pending = [r[0] for r in db.execute("SELECT id FROM attempts WHERE state!='complete'")]
        records = [json.loads(r[0]) for r in db.execute("SELECT value FROM attempts WHERE state='complete'")]
    finally:
        db.close()
    if not pending:
        raise error
    # Reject uncertain optimizer/judge calls before dispatching anything. Their
    # provider does not share the worker's durable, atomic execution claim.
    for ident in pending:
        payload_for_attempt(config, records, ident)
    tag = str(uuid.uuid4())
    cleanup = directory / 'session-cleanup.json'
    if cleanup.exists():
        (directory / f'session-cleanup-before-recovery-{tag}.json').write_bytes(cleanup.read_bytes())
    (directory / f'transport-recovery-{tag}.json').write_text(json.dumps({
        'error': str(error.__cause__ or error), 'pending_trials': pending,
        'policy': 'Read durable receipts; dispatch only unclaimed trials with the same run and trial identity.'}, indent=2) + '\n')
    print('Recovering interrupted AgentCore trial receipts; completed work will not be replayed.', file=sys.stderr)
    for ident in pending:
        command = [sys.executable, str(Path(__file__).with_name('recover_agentcore_trial.py')),
                   '--output', str(directory), '--trial-id', ident, '--runtime-arn', args.runtime_arn,
                   '--region', args.region, '--dispatch-if-unclaimed']
        if args.profile:
            command += ['--profile', args.profile]
        subprocess.run(command, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'prepare-cloud', 'all', 'baseline', 'climb'])
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', default='runs/customer-evaluation')
    parser.add_argument('--backend', choices=['simulation', 'local', 'agentcore'], default='simulation')
    parser.add_argument('--live', action='store_true', help='Allow model calls using the configured routes and budget')
    parser.add_argument('--runtime-arn')
    parser.add_argument('--profile')
    parser.add_argument('--region')
    args = parser.parse_args(argv)
    if args.backend != 'simulation' and not args.live:
        parser.error('Use --live only after reviewing model IDs, endpoint, Region and budget')
    if args.backend == 'simulation' and args.live:
        parser.error('--live cannot be combined with simulation')
    if args.backend == 'agentcore' and args.action not in ('plan', 'prepare-cloud') and not (args.runtime_arn and args.region):
        parser.error('Live AgentCore execution requires --runtime-arn and --region')
    config = load(args.config, live=args.live)
    if args.action == 'prepare-cloud':
        if not args.live: parser.error('prepare-cloud requires --live to validate concrete routes; it does not invoke them')
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        (output / 'cloud-input.json').write_text(json.dumps(config, indent=2))
        print(str((output / 'cloud-input.json').resolve()))
        return
    if args.action == 'plan':
        print(json.dumps({'fingerprint': config['fingerprint'], 'mode': config['document']['runner']['mode'],
                          'harness': config['harness'], 'splits': config['splits'], 'backend': args.backend,
                          'budget_usd': config['document']['execution']['budget_usd']}, indent=2))
        return
    make_runner = lambda: Runner(config, args.output, backend=args.backend, runtime_arn=args.runtime_arn,
                                profile=args.profile, region=args.region)
    run_with_recovery(make_runner, lambda exc: recover_cloud_checkpoint(config, args, exc),
                      optimize=args.action != 'baseline', max_recoveries=2 if args.backend == 'agentcore' else 0)
    print(str((Path(args.output) / 'report.md').resolve()))


if __name__ == '__main__':
    main()
