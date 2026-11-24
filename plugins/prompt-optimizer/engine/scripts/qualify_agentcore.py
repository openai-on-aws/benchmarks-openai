"""Qualify source and target on one non-holdout case before an AgentCore study."""
import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from customer_eval.contract import digest, load
from customer_eval.costs import trial_cost
from customer_eval.runner import Runner


def qualification_config(full):
    cfg = deepcopy(full)
    cfg['qualification_of'] = cfg.pop('fingerprint')
    source_split = 'development' if cfg['document']['optimization']['enabled'] else 'validation'
    first = next((key for key in cfg['splits'].get(source_split, []) if cfg['datasets'].get(key)), None)
    if first is None:
        raise ValueError('Qualification needs one ' + source_split + ' case; holdout cases are never used')
    cfg['datasets'] = {first: cfg['datasets'][first][:1]}
    # The qualification runner uses its own development alias. Preserve where
    # the case came from; this neither moves a study case nor opens holdout.
    cfg['qualification_source_split'] = source_split
    cfg['qualification_case_id'] = cfg['datasets'][first][0]['id']
    cfg['splits'] = {'development': [first]}
    cfg['document']['execution']['repetitions'] = 1
    cfg['fingerprint'] = digest(cfg)
    return cfg


def assess(cfg, row):
    result = row['result']
    model = cfg['routes'][row['model_id']]['model_id']
    calls = result.get('route_calls', [])
    route_passed = bool(calls) and result['status'] == 'completed' and all(
        c.get('status') == 'completed' and c.get('model_requested') == model
        and c.get('model_returned') == model and c.get('request_id') for c in calls)
    required = [c['id'] for c in cfg['document']['checks'] if c['required']]
    return {'model': model, 'route_verified': route_passed,
            'required_checks_passed': all(row['checks'][ident]['pass'] for ident in required),
            'model_cost': trial_cost(cfg, row), 'row': row}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('config', 'output', 'runtime-arn', 'region', 'profile'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--live', action='store_true', required=True)
    args = parser.parse_args()
    full = load(args.config, live=True)
    cfg = qualification_config(full)
    runner = Runner(cfg, args.output, backend='agentcore', runtime_arn=args.runtime_arn,
                    profile=args.profile, region=args.region)
    output = Path(args.output)
    try:
        groups = runner.evaluate_group([(cfg['source'], cfg['prompt']), (cfg['target'], cfg['prompt'])],
                                       'development', phase='qualification')
        records = [assess(cfg, group[0]) for group in groups]
        for key, record in zip(('source', 'target'), records):
            (output / (key + '.json')).write_text(json.dumps(record, indent=2) + '\n')
        summary = {'qualified': all(r['route_verified'] for r in records),
                   'required_checks_passed': all(r['required_checks_passed'] for r in records),
                   'run_id': runner.store.execution_id,
                   'parent_fingerprint': cfg['qualification_of'],
                   'qualification_fingerprint': cfg['fingerprint'],
                   'source_split': cfg['qualification_source_split'],
                   'case_id': cfg['qualification_case_id'],
                   'models': [{k: v for k, v in r.items() if k != 'row'} for r in records]}
        print(json.dumps(summary, indent=2))
        (output / 'qualification-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        return 0 if summary['qualified'] else 2
    finally:
        runner.close()


if __name__ == '__main__':
    sys.exit(main())
