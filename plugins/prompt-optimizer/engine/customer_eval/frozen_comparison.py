"""Bounded operator continuation: compare three frozen arms without selection.

This mode uses the existing cloud job and trial protocols. It never reruns the
search, retries a parent attempt, or changes the parent's saved selection.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import re

from .contract import cases, digest, implementation_fingerprint
from .costs import run_costs
from .scoring import summarize, source_judge_metrics, turn_metrics


PHASE = 'comparison-only-precommitted-1'
MAX_TRIALS = 300
FILES = ('comparison-report.json', 'comparison-report.md', 'comparison-manifest.json')
SPEC_FIELDS = {'version', 'parent_job_id', 'parent_report_sha256',
               'parent_implementation_fingerprint', 'candidate_manifest',
               'candidate_manifest_sha256', 'precommit_object_sha256', 'phase'}
MANIFEST_FIELDS = {'recorded_at_utc', 'purpose', 'candidate', 'prompt',
                   'raw_prompt_sha256', 'main_fingerprint', 'selection_basis',
                   'evidence', 'holdout_outputs_seen', 'override_saved_main_selection',
                   'automatic_adoption', 'condition'}


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)


def validate(config):
    """Validate the bounded mode before a coordinator claims or starts a job."""
    spec = config.get('comparison_only')
    if not isinstance(spec, dict) or set(spec) != SPEC_FIELDS:
        raise ValueError('Comparison-only configuration has unsupported or missing fields')
    if type(spec['version']) is not int or spec['version'] != 1 or spec['phase'] != PHASE:
        raise ValueError('Unsupported comparison-only version or phase')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', spec['parent_job_id']):
        raise ValueError('Comparison parent must be a job ID in the same artifact prefix')
    if any(not _sha(spec[key]) for key in ('parent_report_sha256', 'parent_implementation_fingerprint',
                                          'candidate_manifest_sha256', 'precommit_object_sha256')):
        raise ValueError('Comparison-only receipt hashes must be SHA256 values')
    if digest({k: v for k, v in config.items() if k != 'fingerprint'}) != config['fingerprint']:
        raise ValueError('Comparison-only input fingerprint mismatch')
    if config['implementation_fingerprint'] != implementation_fingerprint():
        raise ValueError('Comparison-only job requires the exact reviewed implementation')
    manifest = spec['candidate_manifest']
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise ValueError('Unsupported candidate precommitment manifest')
    if digest(manifest) != spec['candidate_manifest_sha256']:
        raise ValueError('Candidate manifest fingerprint mismatch')
    if any(manifest[key] is not False for key in
           ('holdout_outputs_seen', 'override_saved_main_selection', 'automatic_adoption')):
        raise ValueError('Comparison must be precommitted without holdout selection or adoption')
    for key in ('purpose', 'prompt', 'selection_basis', 'condition'):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise ValueError('Candidate manifest requires ' + key)
    if len(manifest['prompt'].encode()) > config['document']['runner']['limits']['max_request_bytes']:
        raise ValueError('Candidate prompt exceeds the frozen request envelope')
    if hashlib.sha256(manifest['prompt'].encode()).hexdigest() != manifest['raw_prompt_sha256']:
        raise ValueError('Candidate prompt SHA256 mismatch')
    if not re.fullmatch(r'generation-[1-9][0-9]*-candidate-[1-9][0-9]*', manifest['candidate']):
        raise ValueError('Candidate must identify one saved search candidate')
    try:
        recorded = datetime.fromisoformat(manifest['recorded_at_utc'])
    except (TypeError, ValueError) as exc:
        raise ValueError('Candidate precommitment needs a timestamp') from exc
    if recorded.utcoffset() is None:
        raise ValueError('Candidate precommitment timestamp needs a timezone')
    if (not isinstance(manifest['evidence'], list) or not manifest['evidence'] or
            any(not isinstance(value, str) or not value for value in manifest['evidence'])):
        raise ValueError('Candidate precommitment requires validation evidence references')
    # Reconstruct the exact original frozen config. No routes, settings, data,
    # graders, limits, roles, or original prompts may change in the continuation.
    parent = {k: deepcopy(v) for k, v in config.items() if k not in ('fingerprint', 'comparison_only')}
    parent['implementation_fingerprint'] = spec['parent_implementation_fingerprint']
    if not _sha(manifest['main_fingerprint']) or digest(parent) != manifest['main_fingerprint']:
        raise ValueError('Comparison-only inputs differ from the frozen parent study')
    if manifest['prompt'] == config['prompt']:
        raise ValueError('Comparison candidate must differ from the original prompt')
    runner = config['document']['runner']
    if runner.get('mode') != 'prompt' or runner.get('input_mode') != 'replay' or runner.get('workload') != 'none':
        raise ValueError('Comparison-only continuation supports captured text prompt workflows')
    if runner.get('grading_reference_mode') != 'references_with_source_judges':
        raise ValueError('Comparison-only continuation requires the frozen mixed reference grading mode')
    for role in ('source', 'target'):
        variant = config[role]
        if variant['model_id'] not in config['routes'] or variant['route'] != 'direct_model_api':
            raise ValueError('Comparison-only role has no frozen direct model route')
    if config['source']['model_id'] == config['target']['model_id']:
        raise ValueError('Comparison-only migration requires distinct source and target roles')
    repetitions = config['document']['execution']['repetitions']
    cap = config['document']['execution']['max_cases_per_dataset']
    if type(repetitions) is not int or repetitions < 1 or (cap is not None and (type(cap) is not int or cap < 1)):
        raise ValueError('Comparison-only repetitions and case cap must be positive integers')
    holdout = cases(config, 'holdout')
    if not holdout or len({row['id'] for row in holdout}) != len(holdout):
        raise ValueError('Comparison-only holdout must contain distinct case IDs')
    if 3 * len(holdout) * repetitions > MAX_TRIALS:
        raise ValueError('Comparison-only continuation exceeds the 300 application trial bound')
    return spec


def validate_parent(config, report):
    spec = validate(config)
    if digest(report) != spec['parent_report_sha256'] or report.get('fingerprint') != spec['candidate_manifest']['main_fingerprint']:
        raise ValueError('Saved parent report does not match the frozen comparison receipt')
    original = config['prompt']
    selected = report.get('selected_prompt', {})
    if selected.get('prompt') != original or selected.get('sha256') != digest(original):
        raise ValueError('Parent study did not retain the original prompt')
    arms = report.get('holdout')
    if not isinstance(arms, dict) or not {'source_original', 'target_original'} <= set(arms):
        raise ValueError('Parent report is missing its original holdout arms')
    if any(arm.get('score', {}).get('prompt') != original for arm in arms.values()):
        raise ValueError('Parent report already contains a distinct revised holdout arm')
    manifest = spec['candidate_manifest']
    generation, candidate = (int(value) for value in re.findall(r'\d+', manifest['candidate']))
    saved = [g for g in (report.get('optimization') or {}).get('generations', []) if g.get('index') == generation]
    if (len(saved) != 1 or candidate > len(saved[0].get('candidates', [])) or
            saved[0]['candidates'][candidate - 1].get('prompt') != manifest['prompt']):
        raise ValueError('Precommitted prompt does not match its saved parent candidate')
    return spec


def read_parent(config, s3, bucket, prefix, read_body):
    """Read only the completed parent in the coordinator's own bucket/prefix."""
    spec = validate(config)
    key = f"{prefix.strip('/')}/jobs/{spec['parent_job_id']}"
    status = json.loads(read_body(s3.get_object(Bucket=bucket, Key=key + '/status.json')['Body']))
    if status.get('status') != 'completed' or status.get('fingerprint') != spec['candidate_manifest']['main_fingerprint']:
        raise ValueError('Comparison requires the completed parent job; do not overlap controllers')
    report = json.loads(read_body(s3.get_object(Bucket=bucket, Key=key + '/report.json')['Body']))
    validate_parent(config, report)
    obj = s3.get_object(Bucket=bucket, Key=f"{prefix.strip('/')}/comparison-precommits/{spec['precommit_object_sha256']}.json")
    raw = read_body(obj['Body'])
    if (len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != spec['precommit_object_sha256'] or
            json.loads(raw) != spec['candidate_manifest']):
        raise ValueError('Saved candidate precommitment does not match its immutable object hash')
    uploaded = obj.get('LastModified')
    if not isinstance(uploaded, datetime) or uploaded.utcoffset() is None:
        raise ValueError('Candidate precommitment has no server timestamp')
    starts = []
    for arm in report['holdout'].values():
        for row in arm.get('trials', []):
            calls = row.get('result', {}).get('route_calls')
            if not calls:
                raise ValueError('Parent holdout start evidence is incomplete')
            for call in calls:
                try:
                    started = datetime.fromisoformat(call['started_at'])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError('Parent holdout start evidence is incomplete') from exc
                if started.utcoffset() is None:
                    raise ValueError('Parent holdout start evidence has no timezone')
                starts.append(started)
    if not starts or uploaded >= min(starts):
        raise ValueError('Candidate must have been saved before any parent holdout application request')
    return report


def prepare(parent_config, parent_report, manifest, parent_job_id, precommit_object_sha256):
    """Build a new frozen input after the original job completes, without calls."""
    if 'comparison_only' in parent_config:
        raise ValueError('A comparison-only study cannot be used as a new search parent')
    if digest({k: v for k, v in parent_config.items() if k != 'fingerprint'}) != parent_config['fingerprint']:
        raise ValueError('Parent input fingerprint mismatch')
    config = deepcopy(parent_config)
    config['comparison_only'] = {
        'version': 1, 'parent_job_id': parent_job_id,
        'parent_report_sha256': digest(parent_report),
        'parent_implementation_fingerprint': parent_config['implementation_fingerprint'],
        'candidate_manifest': deepcopy(manifest), 'candidate_manifest_sha256': digest(manifest),
        'precommit_object_sha256': precommit_object_sha256, 'phase': PHASE}
    config['implementation_fingerprint'] = implementation_fingerprint()
    config['fingerprint'] = digest({k: v for k, v in config.items() if k != 'fingerprint'})
    validate_parent(config, parent_report)
    return config


def claim_fingerprint(config):
    """A runtime rebuild must not authorize a second copy of this same cohort."""
    spec = config['comparison_only']
    manifest = spec['candidate_manifest']
    return digest({'parent_job_id': spec['parent_job_id'], 'parent_fingerprint': manifest['main_fingerprint'],
                   'candidate_raw_prompt_sha256': manifest['raw_prompt_sha256'], 'phase': spec['phase']})


def run(runner, parent_report):
    """Evaluate the frozen arms once; keep original selection and costs separate."""
    config = runner.config
    spec = validate_parent(config, parent_report)
    with runner.store.lock:
        previous = runner.store.db.execute('SELECT value FROM meta WHERE key="workflow_mode"').fetchone()
        if previous and previous[0] != 'comparison_only':
            raise ValueError('Do not reuse an optimization run directory for comparison-only work')
        runner.store.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('workflow_mode', 'comparison_only'))
        runner.store.db.commit()
    manifest = spec['candidate_manifest']
    configurations = [('source_original', config['source'], config['prompt']),
                      ('target_original', config['target'], config['prompt']),
                      ('target_comparison', config['target'], manifest['prompt'])]
    (runner.store.directory / 'comparison-manifest.json').write_text(json.dumps(spec, indent=2) + '\n')
    try:
        groups = runner.evaluate_group([(variant, prompt) for _, variant, prompt in configurations], 'holdout', spec['phase'])
    finally:
        runner.close()
    arms = {}
    for (name, variant, prompt), rows in zip(configurations, groups):
        arms[name] = {'model_id': variant['model_id'], 'prompt': prompt, 'prompt_sha256': digest(prompt),
                      'score': asdict(summarize(config, prompt, rows)), 'trials': rows,
                      'turn_quality': turn_metrics(config, rows),
                      'source_judge_metrics': source_judge_metrics(config, rows)}
    records = [json.loads(row[0]) for row in runner.store.db.execute('SELECT value FROM attempts WHERE state="complete"')]
    unresolved = [{'id': ident, 'state': state} for ident, state in
                  runner.store.db.execute('SELECT id,state FROM attempts WHERE state!="complete" ORDER BY id')]
    grading_errors = sum(any(isinstance(check, dict) and 'pass' in check and check.get('error')
                            for check in record.values()) for record in records)
    model_costs = run_costs(config, records, runner.backend == 'simulation', unresolved_attempts=len(unresolved))
    parent_costs = deepcopy(parent_report.get('model_costs', {}))
    combined = {}
    for output, field in [('known_model_cost_usd', 'known_model_cost_usd'),
                          ('estimated_model_cost_usd', 'estimated_model_cost_usd')]:
        values = [parent_costs.get(field), model_costs.get(field)]
        combined[output] = sum(values) if all(type(value) in (int, float) for value in values) else None
    report = {'mode': 'comparison_only', 'backend': runner.backend, 'simulated': runner.backend == 'simulation',
              'fingerprint': config['fingerprint'], 'execution_id': runner.store.execution_id,
              'cohort_fingerprint': claim_fingerprint(config),
              'phase': spec['phase'], 'evaluation_status': 'needs_review' if unresolved or grading_errors else 'complete',
              'comparison_manifest': deepcopy(spec), 'main_selection_unchanged': True,
              'automatic_adoption': False, 'holdout': arms, 'unresolved_attempts': unresolved,
              'grading_error_records': grading_errors, 'judge_requests': [r for r in records if r.get('purpose') == 'judge'],
              'model_costs': model_costs, 'parent_model_costs': parent_costs,
              'combined_model_costs': combined, 'reserved_model_spend_usd': runner.store.reserved(),
              'parent_unresolved_attempts': deepcopy(parent_report.get('unresolved_attempts', [])),
              'parent_selected_prompt': deepcopy(parent_report['selected_prompt']),
              'design': 'Three frozen arms on a fresh interleaved cohort. Candidate chosen on validation before holdout; no selection or adoption is performed here.',
              'cost_note': 'This cohort and parent spend are separate. Missing usage remains unknown. Model estimates exclude infrastructure and human review.',
              'latency_note': 'Application conversation/worker time excludes optional judging and coordinator setup. Quantiles are descriptive; this is not a significance test.',
              'dataset_note': 'Parent dataset declarations and unreviewed generated references are unchanged. Any holdout cases run by the parent are reused, not newly unseen.'}
    (runner.store.directory / FILES[0]).write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Frozen prompt comparison', '', report['design'], '', report['dataset_note'], '',
             '| Arm | Required checks pass | Model cost per successful result | p95 seconds |',
             '|---|---:|---:|---:|']
    for name, arm in arms.items():
        score = arm['score']
        cost = 'unknown' if score['cost_usd_per_success'] is None else f"${score['cost_usd_per_success']:.6f}"
        latency = 'unknown' if score['latency_p95_s'] is None else f"{score['latency_p95_s']:.3f}"
        lines.append(f"| {name} | {score['accuracy']:.1%} | {cost} | {latency} |")
    lines += ['', f"Status: {report['evaluation_status']}. Optional grading errors: {grading_errors}. Unresolved cohort requests: {len(unresolved)}.",
              '', report['cost_note'], '', report['latency_note'], '', 'The original saved prompt selection is unchanged.']
    (runner.store.directory / FILES[1]).write_text('\n'.join(lines) + '\n')
    return report
