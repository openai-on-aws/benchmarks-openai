"""Import the intake contract, resolve local assets and freeze a run specification."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from importlib.metadata import version
from pathlib import Path
from .workloads import names
from .turn_grading import TYPES, validate_expectations

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "intake_helper", ROOT / "intake/skills/eval-intake/scripts/input_framework.py")
intake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(intake)


def digest(value):
    return hashlib.sha256(intake.canonical(value)).hexdigest()


def implementation_fingerprint():
    sources = [ROOT / 'hillclimb.py', ROOT / 'optimizer.py']
    sources += sorted((ROOT / 'customer_eval').rglob('*.py'))
    sources += sorted((ROOT / 'runtime').glob('*.py'))
    sources += [ROOT / 'requirements-integration.txt']
    sources += sorted((ROOT / 'agent').glob('*.py'))
    sources += sorted((ROOT / 'customer_workloads').rglob('*.py'))
    sources += [ROOT / 'intake/skills/eval-intake/scripts/input_framework.py']
    sources += sorted((ROOT / 'intake/skills/eval-intake/assets').glob('*.json'))
    return digest({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})


def load(path: str | Path, *, live=False):
    path = Path(path).resolve()
    doc = intake.load_document(path)
    base = intake.resolve_asset_base(path, doc)
    report = intake.validate(doc, base, check_assets=True)
    if not report['valid']:
        raise ValueError('\n'.join(report['errors']))
    if 'runner' not in doc:
        raise ValueError('Intake is valid; add the runner section before execution. See examples/customer-support/input.yaml.')
    cfg = doc['runner']
    names(cfg['workload'])
    replay_mode = cfg.get('input_mode', 'conversation') == 'replay'
    if replay_mode and (cfg['mode'] != 'prompt' or cfg['workload'] != 'none'):
        raise ValueError('Captured request replay uses prompt mode with no application tools')
    if live and any(q['blocks'] == 'execution' for q in doc['open_questions']):
        raise ValueError('Resolve execution-blocking intake questions before a live run')
    indexes = {key: {v['id']: v for v in doc[key]} for key in
               ('models', 'prompts', 'agents', 'variants', 'datasets', 'checks')}
    for key in ('source_variant_id', 'target_variant_id'):
        if cfg[key] not in indexes['variants']:
            raise ValueError(f'runner.{key}: unknown variant')
    source = indexes['variants'][cfg['source_variant_id']]
    target = indexes['variants'][cfg['target_variant_id']]
    if any(v['route'] != 'direct_model_api' for v in (source, target)):
        raise ValueError('gateway_model_tool is not a Responses API route. Configure the Gateway inference URL as endpoint_env with direct_model_api.')
    if (source['prompt_id'], source['agent_id']) != (target['prompt_id'], target['agent_id']):
        raise ValueError('Source and target must use the same starting prompt and agent; compare models with the harness fixed.')
    if doc['baseline_variant_id'] != source['id']:
        raise ValueError('baseline_variant_id must match runner.source_variant_id')
    if doc['optimization']['enabled'] and doc['optimization']['target_prompt_id'] != target['prompt_id']:
        raise ValueError('optimization.target_prompt_id must match the selected target variant')
    if cfg['mode'] == 'agent':
        agent = indexes['agents'][target['agent_id']]
        if agent['harness'] not in ('codex', 'strands'):
            raise ValueError('Agent execution supports Codex or Strands. Set harness: codex by default.')
        if agent['side_effects'] != 'mocked':
            raise ValueError('This first runner supports isolated mock tools only; live side effects need a separate reviewed adapter.')
        expected_tools = names(cfg['workload'])
        if set(agent['tool_allowlist']) != set(expected_tools):
            raise ValueError('agent.tool_allowlist must match the selected workload tools')
        if agent['codex_profile'] or agent['workspace']:
            raise ValueError('This isolated runner does not import a local Codex profile or customer workspace. Use the protected instructions and mock workload adapter.')
    prompt = intake.local_path(base, indexes['prompts'][target['prompt_id']]['path']).read_text()
    if indexes['prompts'][target['prompt_id']]['variables']:
        raise ValueError('This conversation runner takes customer inputs as user turns; use a system prompt without template variables')
    policy = intake.local_path(base, cfg['protected_instructions_path']).read_text()
    if replay_mode and policy.strip():
        raise ValueError('Replay keeps protected instructions in captured messages; policy file must be empty')
    grading = intake.load_document(intake.local_path(base, cfg['grading_path']))
    if set(grading) != set(indexes['checks']):
        raise ValueError('grading file must define exactly the intake checks')
    for check_id, rule in grading.items():
        if rule.get('type') not in {'json_equals', 'json_set_equals', 'json_unordered_equals', 'json_schema', 'contains', 'tool_called', 'state_equals', 'llm_rubric', 'llm_source_rubric'} | TYPES:
            raise ValueError(f'Unknown grading type for {check_id}')
        method = indexes['checks'][check_id]['method']
        if method != ('llm_judge' if rule['type'] in ('llm_rubric', 'turn_semantic', 'llm_source_rubric') else 'deterministic'):
            raise ValueError(f'Grading method does not match intake check {check_id}')
        if rule['type'] == 'llm_source_rubric':
            if set(rule) != {'type', 'rubric'} or not isinstance(rule['rubric'], str) or not rule['rubric'].strip():
                raise ValueError('llm_source_rubric requires only type and a nonempty fixed rubric')
        if rule['type'] == 'turn_semantic' and (not isinstance(rule.get('rubric'), str) or not rule['rubric'].strip()):
            raise ValueError('turn_semantic requires a nonempty rubric')
        if rule['type'] == 'json_schema':
            from .json_grading import schema_validator
            schema_validator(rule.get('schema'))
        if rule['type'] in ('json_set_equals', 'json_unordered_equals') and (not isinstance(rule.get('path'), str) or not isinstance(rule.get('expected_key'), str)):
            raise ValueError('JSON set/unordered checks require path and expected_key strings')
    semantic_models = {indexes['checks'][key]['judge_model_id'] for key, rule in grading.items()
                       if rule['type'] == 'turn_semantic'}
    if len(semantic_models) > 1:
        raise ValueError('Turn semantic criteria must use one judge model')
    source_checks = [key for key, rule in grading.items() if rule['type'] == 'llm_source_rubric']
    reference_and_source = cfg.get('grading_reference_mode') == 'references_with_source_judges'
    if reference_and_source and not source_checks:
        raise ValueError('references_with_source_judges requires optional source checks alongside required reference checks')
    if source_checks:
        if cfg['mode'] != 'prompt' or cfg['workload'] != 'none':
            raise ValueError('Source-grounded judging currently supports text prompt mode with no tools')
        if reference_and_source:
            if any(indexes['checks'][key]['required'] for key in source_checks):
                raise ValueError('Source checks accompanying references must be optional')
            if any(rule['type'] not in ('llm_source_rubric', 'json_schema', 'json_equals', 'json_set_equals', 'json_unordered_equals') for rule in grading.values()):
                raise ValueError('Mixed source judging supports JSON reference checks only')
            if not any(indexes['checks'][key]['required'] and rule['type'] in ('json_equals', 'json_set_equals', 'json_unordered_equals') for key, rule in grading.items()):
                raise ValueError('Mixed source judging needs a required reference value check')
        elif any(rule['type'] not in ('llm_source_rubric', 'json_schema') for rule in grading.values()):
            raise ValueError('Source-grounded rubrics may be combined with JSON schema checks, not reference-based checks without explicit reference mode')
        if len({indexes['checks'][key]['judge_model_id'] for key in source_checks}) != 1:
            raise ValueError('Source-grounded criteria must use one judge model')
    datasets, all_ids, seen = {}, set(), {}
    for dataset in doc['datasets']:
        rows = [json.loads(line) for line in intake.local_path(base, dataset['path']).read_text().splitlines() if line.strip()]
        for row in rows:
            if row['id'] in all_ids:
                raise ValueError('Case IDs must be globally unique across all datasets')
            all_ids.add(row['id'])
            # Holdout and validation duplicates must not slip through whitespace or casing.
            if replay_mode:
                from .replay import validate_replay
                validate_replay(row.get('replay'), prompt)
                if row['replay']['request'].get('max_output_tokens', 1) > cfg['limits']['max_output_tokens']:
                    raise ValueError('Captured output limit exceeds run limit; explicitly reconcile the settings')
                task_input = row['replay']['request']
            else:
                if 'replay' in row:
                    raise ValueError('Captured request requires runner.input_mode: replay')
                task_input = {'turns': row.get('turns', [row['input'].get('question')]),
                              'initial_state': row.get('initial_state', {})}
            normalized = ' '.join(json.dumps(task_input, sort_keys=True).casefold().split())
            if normalized in seen and seen[normalized] != dataset['split']:
                raise ValueError('Equivalent input appears in different dataset splits')
            seen[normalized] = dataset['split']
            turns = row.get('turns', [row['input'].get('question')])
            if not turns or any(not isinstance(t, str) or not t.strip() for t in turns):
                raise ValueError('Each case needs input.question or nonempty scripted turns')
            if len(turns) > cfg['max_turns']:
                raise ValueError('A case exceeds runner.max_turns; no silent truncation')
            row['turns'] = turns
            if replay_mode:
                from .replay import message_text
                if turns != [message_text(row['replay']['request']['input'][-1])]:
                    raise ValueError('Replay grading turn must equal the captured final user message')
            if source_checks:
                if not reference_and_source and 'expected' in row:
                    raise ValueError('Source-grounded judging uses no expected answers; remove references or choose reference-based grading')
                if len(turns) != 1:
                    raise ValueError('Source-grounded judging currently supports one text extraction response per case')
                from .source_grading import build_source_context
                build_source_context(row, policy=policy)
            if (not source_checks or reference_and_source) and 'expected' not in row:
                raise ValueError('Runnable cases need expected values; use intake for incomplete drafts')
            from .scoring import at
            from .json_grading import scalar_set
            for rule in grading.values():
                if rule['type'] == 'json_set_equals':
                    try:
                        scalar_set(at(row['expected'], rule['expected_key']))
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        raise ValueError('Invalid JSON set reference for case ' + row['id']) from exc
                elif rule['type'] == 'json_unordered_equals':
                    try:
                        from .json_grading import unordered_value
                        unordered_value(at(row['expected'], rule['expected_key']))
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        raise ValueError('Invalid JSON unordered reference for case ' + row['id']) from exc
            validate_expectations(row, grading)
            if any(rule['type'] in TYPES for rule in grading.values()):
                for turn in row['expected']['turns']:
                    if any(spec['name'] not in names(cfg['workload']) for spec in turn['tools']['allowed']):
                        raise ValueError('Turn expectation names a tool outside the workload allowlist')
        datasets[dataset['id']] = rows
    splits = {
        'development': doc['optimization']['development_dataset_ids'],
        'validation': cfg['validation_dataset_ids'],
        'holdout': doc['optimization']['holdout_dataset_ids'],
    }
    required_splits = ('development', 'validation', 'holdout') if doc['optimization']['enabled'] else ('validation', 'holdout')
    if any(not splits[key] for key in required_splits):
        raise ValueError(', '.join(required_splits) + ' datasets are required')
    for split, ids in splits.items():
        for ident in ids:
            if ident not in indexes['datasets'] or indexes['datasets'][ident]['split'] != split:
                raise ValueError(f'{split} dataset reference is invalid')
    for field in ('repetitions', 'concurrency', 'timeout_seconds'):
        if not doc['execution'][field] or doc['execution'][field] <= 0:
            raise ValueError(f'execution.{field} must be positive before running')
    if ('request_timeout_seconds' in cfg['limits'] and
            doc['execution']['timeout_seconds'] < cfg['limits']['request_timeout_seconds']):
        raise ValueError('execution.timeout_seconds must be at least runner.limits.request_timeout_seconds; allow worker setup time')
    if cfg.get('selection'):
        from .selection import validate_policy
        validate_policy(cfg['selection'])
        for key in cfg['selection'].get('non_regression_checks', []):
            if key not in indexes['checks'] or not indexes['checks'][key]['required'] or grading[key]['type'] not in TYPES:
                raise ValueError('Non-regression checks must name required per-turn checks')
        count = sum(len(datasets[i][:doc['execution']['max_cases_per_dataset']])
                    for i in splits['validation']) * doc['execution']['repetitions']
        if doc['optimization']['enabled'] and count < cfg['selection']['min_validation_sessions']:
            raise ValueError('Joint selection requires more validation sessions; increase cases or repetitions')
    unlimited = cfg.get('budget_policy', 'capped') == 'unlimited'
    if not unlimited and not doc['execution']['budget_usd']:
        raise ValueError('Set a positive budget or explicitly select runner.budget_policy: unlimited')
    if doc['execution']['fallback_policy'] != 'none':
        raise ValueError('This runner requires fallback_policy: none')
    for criterion in doc['success_criteria']:
        if criterion['status'] != 'confirmed' or criterion['threshold'] is None:
            raise ValueError('Confirm success criteria before running; proposed criteria remain an intake draft')
        if criterion['metric'] not in ('all_required_checks_pass_rate', 'check_pass_rate', 'latency_p95_ms'):
            raise ValueError('This runner supports quality/check/latency gates. Monetary limits are conservative model spend reservations, not all-in cost gates.')
    objective = next((c for c in doc['success_criteria'] if c['id'] == doc['optimization']['objective_criterion_id']), None)
    relative_objective = (doc['optimization']['objective_criterion_id'] is None and
                          cfg.get('selection', {}).get('mode') == 'joint_cost_latency')
    if doc['optimization']['enabled'] and not relative_objective and (not objective or objective['metric'] != 'all_required_checks_pass_rate'):
        raise ValueError('Hill climbing currently optimizes all_required_checks_pass_rate; other objectives require a scoring adapter')
    used = {source['model_id'], target['model_id']}
    if doc['optimization']['enabled']:
        used.add(cfg['optimizer_model_id'])
    used.update(c['judge_model_id'] for c in doc['checks'] if c['method'] == 'llm_judge')
    routes = {}
    for ident in used:
        if ident not in indexes['models'] or ident not in cfg['routes']:
            raise ValueError('Every candidate, optimizer and judge model needs an explicit model and route')
        model = indexes['models'][ident]
        if model['api'] != 'responses':
            raise ValueError('This shared runner uses the Responses API. Other APIs require an explicit adapter, never a silent translation.')
        route = dict(cfg['routes'][ident])
        route.update(model_id=model['model_id'] or os.environ.get(model['model_id_env'] or ''),
                     credential_ref=model['credential_ref'], region=model['region'],
                     base_url=os.environ.get(model['endpoint_env'] or '', ''), parameters=model['parameters'])
        if live:
            if (not unlimited or cfg.get('selection')) and ('ILLUSTRATIVE' in route['pricing_source'].upper() or
                                  route['input_usd_per_million'] is None or route['output_usd_per_million'] is None):
                raise ValueError(f'{ident}: replace illustrative prices with verified route pricing before live execution')
            if cfg.get('selection'):
                if any(route.get(k) is None for k in ('cached_input_usd_per_million',
                       'cache_write_usd_per_million', 'pricing_verified_at', 'pricing_max_input_tokens')):
                    raise ValueError(f'{ident}: joint selection requires cache prices, pricing date and context bound')
                envelopes = []
                if ident in {source['model_id'], target['model_id']}:
                    envelopes.append(('application', cfg['limits']))
                if any(c['judge_model_id'] == ident for c in doc['checks'] if c['method'] == 'llm_judge'):
                    envelopes.append(('judge', cfg.get('judge_limits', cfg['limits'])))
                if doc['optimization']['enabled'] and ident == cfg['optimizer_model_id']:
                    envelopes.append(('optimizer', cfg.get('optimizer_limits', cfg['limits'])))
                # UTF-8 request bytes are a conservative input-token bound for this
                # text-only runner, not a tokenizer estimate. Check each purpose's
                # actual envelope, including an optimizer sharing a model route.
                for purpose, envelope in envelopes:
                    if envelope['max_request_bytes'] > route['pricing_max_input_tokens']:
                        raise ValueError(f'{ident}: {purpose} request envelope exceeds the priced context band')
            if not route['model_id'] or not route['base_url'].startswith('https://'):
                raise ValueError(f'{ident}: set the model ID and explicit HTTPS endpoint environment variable')
            if route['auth'] == 'sigv4' and not route['region']:
                raise ValueError(f'{ident}: Region required for SigV4')
            if route['auth'] == 'bearer' and not (route['credential_ref'] or '').startswith('env:'):
                raise ValueError(f'{ident}: bearer route needs an env: credential reference')
            if route['auth'] == 'sigv4' and not (route['credential_ref'] or '').startswith('aws_profile:'):
                raise ValueError(f'{ident}: SigV4 route needs an aws_profile: reference')
        routes[ident] = route
    config = dict(document=doc, prompt=prompt, policy=policy, grading=grading,
                  datasets=datasets, splits=splits, routes=routes,
                  source=source, target=target,
                  harness=indexes['agents'][target['agent_id']]['harness'] if cfg['mode'] == 'agent' else 'direct',
                  implementation_fingerprint=implementation_fingerprint(),
                  package_versions={p: version(p) for p in ('openai-codex', 'strands-agents', 'mcp', 'boto3', 'bedrock-agentcore')})
    if cfg.get('intake_context_path'):
        config['intake_context'] = intake.load_document(intake.local_path(base, cfg['intake_context_path']))
    # Includes actual assets, resolved model IDs/endpoints and all grader/runner settings.
    config['fingerprint'] = digest(config)
    return config


def cases(config, split):
    limit = config['document']['execution']['max_cases_per_dataset']
    return [row for ident in config['splits'][split] for row in config['datasets'][ident][:limit]]


def trial_payload(config, variant, prompt, case, trial_id):
    """Project only task-visible fields. Expected labels and grading rules never reach the agent."""
    return dict(trial_id=trial_id, harness=config['harness'], prompt=prompt,
                implementation_fingerprint=config['implementation_fingerprint'],
                policy=config['policy'], turns=case['turns'], initial_state=case.get('initial_state', {}),
                workload=config['document']['runner']['workload'], route=config['routes'][variant['model_id']],
                limits=config['document']['runner']['limits'],
                **({'replay': case['replay']} if 'replay' in case else {}))
