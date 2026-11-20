#!/usr/bin/env python3
"""Validate and package evaluation inputs. Never invokes a model or deploys resources."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

if __name__ == '__main__' and sys.version_info < (3, 10):
    print('Evaluation intake needs Python 3.10 or newer. Select a newer interpreter and create a local virtual environment; do not change the system Python.', file=sys.stderr)
    raise SystemExit(2)

try:
    import yaml
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:
    if __name__ != '__main__':
        raise
    requirements = Path(__file__).resolve().parents[1] / 'requirements.txt'
    print(f'Intake helper dependencies are missing. In a local virtual environment, run: python -m pip install -r "{requirements}"', file=sys.stderr)
    raise SystemExit(2)

SCHEMA = Path(__file__).resolve().parents[1] / 'assets/evaluation-input.schema.json'
COLLECTIONS = ('models', 'prompts', 'agents', 'gateways', 'variants', 'datasets',
               'checks', 'success_criteria', 'open_questions')


class InputError(ValueError):
    """An actionable input error whose message contains no supplied field values."""


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError('Duplicate field in the input. Keep one value for each field; remove the duplicate rather than relying on an override.')
        result[key] = value
    return result


class StrictLoader(yaml.SafeLoader):
    pass


def unique_yaml(loader, node, deep=False):
    return unique_pairs((loader.construct_object(k, deep=deep),
                         loader.construct_object(v, deep=deep)) for k, v in node.value)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_yaml)


def reject_nonfinite(value):
    raise ValueError(f'Non-finite number is not valid JSON: {value}')


def load_document(path: Path):
    text = path.read_text(encoding='utf-8')
    try:
        if path.suffix.lower() in ('.yaml', '.yml'):
            # safe_load does not accept a custom loader. Validate duplicate
            # fields separately without changing PyYAML's global constructors.
            loader = StrictLoader(text)
            try:
                loader.get_single_data()
            finally:
                loader.dispose()
            doc = yaml.safe_load(text)
        else:
            doc = json.loads(text, object_pairs_hook=unique_pairs, parse_constant=reject_nonfinite)
    except json.JSONDecodeError as exc:
        raise InputError(f'JSON syntax error at line {exc.lineno}, column {exc.colno}. Check commas, quotes and brackets; the input was not changed.') from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        location = f' at line {mark.line + 1}, column {mark.column + 1}' if mark else ''
        raise InputError(f'YAML syntax error{location}. Check indentation, quotes and brackets; the input was not changed.') from None
    try:
        # Rejects YAML-only objects such as dates/sets, and non-finite numbers.
        json.dumps(doc, allow_nan=False)
    except (ValueError, TypeError):
        raise InputError('Use JSON-compatible values only. Quote dates as strings; NaN, infinity, YAML sets and circular aliases are not supported.') from None
    return doc


def canonical(doc: Any) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def local_path(base: Path, value: str) -> Path:
    path = (base / value).resolve()
    if Path(value).is_absolute() or not path.is_relative_to(base.resolve()):
        raise ValueError('Asset paths must stay inside the configuration directory')
    return path


def schema_error(issue) -> str:
    """Explain how to repair an error without echoing the rejected value."""
    pointer = '/' + '/'.join(map(str, issue.path))
    rule, expected = issue.validator, issue.validator_value
    if rule == 'anyOf':
        specific = next((e for e in issue.context if e.validator not in ('type', 'anyOf')), None)
        if specific:
            rule, expected = specific.validator, specific.validator_value
    if rule == 'required':
        missing = sorted(set(expected) - set(issue.instance))
        detail = 'add required fields: ' + ', '.join(missing)
    elif rule == 'additionalProperties':
        detail = 'unrecognized field; use a field from the input schema'
    elif rule in ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'):
        operator = {'minimum': '>=', 'maximum': '<=', 'exclusiveMinimum': '>', 'exclusiveMaximum': '<'}[rule]
        detail = f'must be {operator} {expected}'
    elif rule == 'enum':
        detail = 'choose one of: ' + ', '.join('null' if x is None else str(x) for x in expected)
    elif rule == 'type':
        detail = 'expected type: ' + (', '.join(expected) if isinstance(expected, list) else expected)
    elif rule in ('minLength', 'pattern'):
        detail = 'invalid text format; check the field definition and do not use an empty placeholder'
    else:
        detail = f'violates {rule}; check the field definition in the input schema'
    return f'{pointer}: {detail}'


def contains_credentials(value) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and re.search(r'(secret|password|api.?key|credential|authorization|access.?token)', key, re.I):
                return True
            if contains_credentials(child):
                return True
    elif isinstance(value, list):
        return any(contains_credentials(child) for child in value)
    return False


def resolve_asset_base(config: Path, doc: dict, override: Path | None = None) -> Path:
    manifest_path = config.parent/'manifest.json'
    if config.name != 'evaluation-input.json' or not manifest_path.is_file():
        return override.resolve() if override is not None else config.parent
    manifest = load_document(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get('asset_base'), str):
        if override is not None:
            return override.resolve()
        raise InputError('The handoff manifest has no usable asset_base. Supply --asset-base with the original or restored input directory.')
    if manifest.get('input_sha256') != hashlib.sha256(canonical(doc)).hexdigest():
        if override is not None:
            return override.resolve()
        raise InputError('The configuration changed after this manifest was written. Supply --asset-base with the known input directory and create a new handoff revision; do not reuse a stale manifest.')
    asset_base = override.resolve() if override is not None else manifest_path.parent / manifest['asset_base']
    for entry in manifest.get('validation', {}).get('assets', []):
        path = local_path(asset_base, entry['path'])
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise InputError(f'Handoff asset changed after bundling: {entry["path"]}. Restore the frozen file or bundle a new revision from the source configuration.')
    return asset_base.resolve()


def validate(doc: Any, base: Path, check_assets: bool = False) -> dict:
    schema = json.loads(SCHEMA.read_text())
    errors = []
    open_items = []
    runtime_checks = []
    assets = {}
    validator = Draft202012Validator(schema)
    for issue in sorted(validator.iter_errors(doc), key=lambda e: str(list(e.path))):
        errors.append(schema_error(issue))
    if errors:
        return {'valid': False, 'ready_for_builder': False, 'errors': errors,
                'open_items': [], 'runtime_checks': [], 'assets': [],
                'execution_authorized': False}

    indexes = {}
    for group in COLLECTIONS:
        indexes[group] = {}
        for item in doc[group]:
            if item['id'] in indexes[group]:
                errors.append(f'{group}: duplicate id {item["id"]}')
            indexes[group][item['id']] = item

    def need_ref(group, ident, context):
        if ident not in indexes[group]:
            errors.append(f'{context}: unknown {group} reference {ident!r}')
            return None
        return indexes[group][ident]

    def asset(value, context, is_dir=False):
        try:
            p = local_path(base, value)
            if check_assets:
                if (not p.is_dir()) if is_dir else (not p.is_file()):
                    errors.append(f'{context}: asset missing or wrong type: {value}. Restore it under the asset directory, use --asset-base for its new location, or record an unavailable path as null without inventing its contents.')
                    return None
                if not is_dir:
                    assets[value] = hashlib.sha256(p.read_bytes()).hexdigest()
            return p
        except (ValueError, OSError) as exc:
            errors.append(f'{context}: {exc}')
            return None

    for field in ('objective', 'decision', 'owner'):
        if doc['experiment'][field] is None:
            open_items.append(f'experiment.{field}')
    for group in ('models', 'prompts', 'agents', 'variants', 'datasets', 'checks'):
        if not doc[group]:
            open_items.append(f'Add at least one {group} entry')

    for m in doc['models']:
        if m['model_id'] and m['model_id_env']:
            errors.append(f'models.{m["id"]}: choose model_id or model_id_env, not both')
        if not m['model_id'] and not m['model_id_env']:
            open_items.append(f'models.{m["id"]}: model identifier or environment reference')
        for field in ('provider', 'api'):
            if m[field] is None:
                open_items.append(f'models.{m["id"]}.{field}')
        if m['provider'] == 'amazon_bedrock' and not m['region']:
            open_items.append(f'models.{m["id"]}.region')
        if m['compatibility'] == 'verified' and not m['verification_ref']:
            errors.append(f'models.{m["id"]}: verified compatibility needs evidence')
        runtime_checks.append(f'{m["id"]}: verify exact model, API, endpoint, permissions, parameters and pricing')
        if contains_credentials(m['parameters']):
            errors.append(f'models.{m["id"]}: credentials belong in credential_ref, not parameters or nested headers; remove embedded credentials before saving or sharing')

    prompt_paths = {}
    for p in doc['prompts']:
        prompt_paths[p['id']] = asset(p['path'], f'prompts.{p["id"]}') if p['path'] else None
        for field in ('path', 'version'):
            if p[field] is None:
                open_items.append(f'prompts.{p["id"]}.{field}')
        if len(p['variables']) != len(set(p['variables'])):
            errors.append(f'prompts.{p["id"]}: duplicate variable names')

    for g in doc['gateways']:
        for field in ('role', 'endpoint_env', 'auth', 'credential_ref'):
            if g[field] is None:
                open_items.append(f'gateways.{g["id"]}.{field}')
        if g['compatibility'] == 'verified' and not g['verification_ref']:
            errors.append(f'gateways.{g["id"]}: verified compatibility needs evidence')
        runtime_checks.append(f'{g["id"]}: verify Gateway tool discovery, authorization and invocation')
        if g['auth'] == 'iam_sigv4' and not g['adapter_ref']:
            open_items.append(f'gateways.{g["id"]}: identify per-request SigV4 signing adapter')

    for a in doc['agents']:
        for field in ('harness', 'runtime', 'sandbox', 'side_effects'):
            if a[field] is None:
                open_items.append(f'agents.{a["id"]}.{field}')
        if a['workspace']:
            asset(a['workspace'], f'agents.{a["id"]}.workspace', is_dir=True)
        if a['instructions_path']:
            asset(a['instructions_path'], f'agents.{a["id"]}.instructions')
        if a['gateway_id']:
            need_ref('gateways', a['gateway_id'], f'agents.{a["id"]}')
        if a['runtime'] in ('agentcore_runtime', 'existing_endpoint') and not a['runtime_ref']:
            open_items.append(f'agents.{a["id"]}.runtime_ref')
        if a['harness'] == 'codex':
            runtime_checks.append(f'{a["id"]}: verify Codex version, profile, model compatibility and effective tool permissions')
        if a['runtime'] == 'agentcore_runtime':
            runtime_checks.append(f'{a["id"]}: validate container, architecture, invocation contract and session isolation')

    for v in doc['variants']:
        for field, group in [('model_id', 'models'), ('prompt_id', 'prompts'), ('agent_id', 'agents')]:
            need_ref(group, v[field], f'variants.{v["id"]}.{field}')
        if v['route'] is None:
            open_items.append(f'variants.{v["id"]}.route')
        if v['gateway_id']:
            gateway = need_ref('gateways', v['gateway_id'], f'variants.{v["id"]}')
            if v['route'] == 'gateway_model_tool' and gateway and gateway['role'] not in ('model_invocation_tools', 'both'):
                errors.append(f'variants.{v["id"]}: gateway does not declare model invocation tools')
        if v['route'] == 'gateway_model_tool' and not v['gateway_id']:
            errors.append(f'variants.{v["id"]}: gateway_model_tool requires gateway_id')
        if v['route'] == 'direct_model_api' and v['gateway_id']:
            errors.append(f'variants.{v["id"]}: use agent.gateway_id for business tools on a direct model route')
    if doc['baseline_variant_id']:
        need_ref('variants', doc['baseline_variant_id'], 'baseline_variant_id')
    elif len(doc['variants']) > 1:
        open_items.append('Choose baseline_variant_id for the comparison')

    case_ids = {}
    case_inputs = {}
    for d in doc['datasets']:
        for field in ('path', 'version', 'split', 'provenance', 'label_status'):
            if d[field] is None:
                open_items.append(f'datasets.{d["id"]}.{field}')
        p = asset(d['path'], f'datasets.{d["id"]}') if d['path'] else None
        if not check_assets or not p or not p.is_file():
            continue
        records = []
        for line_no, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            context = f'datasets.{d["id"]}:{line_no}'
            try:
                row = json.loads(line, object_pairs_hook=unique_pairs, parse_constant=reject_nonfinite)
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip() or not isinstance(row.get('input'), dict):
                    errors.append(f'{context}: each case needs a nonempty id and an input object')
                    continue
                if row['id'] in case_ids:
                    errors.append(f'{context}: duplicate case id {row["id"]}')
                case_ids[row['id']] = d['id']
                task_input = (row['replay']['request'] if isinstance(row.get('replay'), dict) and 'request' in row['replay']
                              else {'input': row['input'], 'turns': row.get('turns'), 'initial_state': row.get('initial_state', {})})
                key = hashlib.sha256(canonical(task_input)).hexdigest()
                if key in case_inputs and case_inputs[key] != d['split']:
                    errors.append(f'{context}: identical task input appears in different dataset splits')
                case_inputs[key] = d['split']
                if d['label_status'] == 'human_reviewed' and ('expected' not in row or row['expected'] is None):
                    errors.append(f'{context}: human_reviewed case has no expected label. Supply a reviewed value or mark labels as unreviewed; represent an intentional null answer inside a labelled object.')
                expected = row.get('expected')
                if isinstance(expected, dict) and 'turns' in expected:
                    if expected['turns'] is None:
                        open_items.append(f'{context}.expected.turns')
                    else:
                        turn_schema = json.loads(SCHEMA.with_name('turn-expectations.schema.json').read_text())
                        for issue in Draft202012Validator(turn_schema).iter_errors(expected['turns']):
                            errors.append(f'{context}.expected.turns: {schema_error(issue)}')
                        if isinstance(expected['turns'], list) and len(expected['turns']) != len(row.get('turns', [row['input'].get('question')])):
                            errors.append(f'{context}: expected.turns must cover every user turn')
                records.append(row)
            except (ValueError, TypeError):
                errors.append(f'{context}: invalid JSON record')
        if not records:
            errors.append(f'datasets.{d["id"]}: dataset has no usable cases')
        for prompt in doc['prompts']:
            if not any(v['prompt_id'] == prompt['id'] for v in doc['variants']):
                continue
            for row in records:
                missing = set(prompt['variables']) - set(row['input'])
                if missing:
                    errors.append(f'datasets.{d["id"]}.{row["id"]}: missing prompt variables {sorted(missing)}')

    if check_assets:
        for prompt in doc['prompts']:
            p = prompt_paths[prompt['id']]
            if p and p.is_file():
                used = set(re.findall(r'{{\s*([a-z][a-z0-9_]*)\s*}}', p.read_text()))
                if used != set(prompt['variables']):
                    errors.append(f'prompts.{prompt["id"]}: template variables do not match declared variables')
    else:
        open_items.append('Run validation with --check-assets to verify local inputs')

    if 'runner' in doc:
        for field in ('protected_instructions_path', 'grading_path', 'intake_context_path'):
            if field == 'intake_context_path' and not doc['runner'].get(field):
                continue
            asset(doc['runner'][field], f'runner.{field}')

    for check in doc['checks']:
        if check['method'] is None:
            open_items.append(f'checks.{check["id"]}.method')
        if check['judge_model_id']:
            need_ref('models', check['judge_model_id'], f'checks.{check["id"]}')
        if check['method'] == 'llm_judge':
            if not check['judge_model_id']:
                open_items.append(f'checks.{check["id"]}.judge_model_id')
            if not check['calibration_ref']:
                runtime_checks.append(f'{check["id"]}: judge needs calibration against independent labels')

    for criterion in doc['success_criteria']:
        if criterion['check_id']:
            need_ref('checks', criterion['check_id'], f'success_criteria.{criterion["id"]}')
        if criterion['metric'] == 'check_pass_rate' and not criterion['check_id']:
            errors.append(f'success_criteria.{criterion["id"]}: check_pass_rate requires check_id')
        if criterion['metric'] == 'all_required_checks_pass_rate' and doc['checks'] and not any(check['required'] for check in doc['checks']):
            errors.append(f'success_criteria.{criterion["id"]}: all_required_checks_pass_rate needs at least one required check; select the relevant check or choose a different metric')
        if criterion['metric'].endswith('pass_rate') and criterion['threshold'] is not None and criterion['threshold'] > 1:
            errors.append(f'success_criteria.{criterion["id"]}: pass rates use 0..1, not percentages')
        if criterion['status'] == 'confirmed' and (criterion['threshold'] is None or criterion['operator'] is None or not criterion['confirmed_by']):
            errors.append(f'success_criteria.{criterion["id"]}: confirmed criterion needs threshold, operator and owner')
        if criterion['status'] != 'confirmed':
            open_items.append(f'success_criteria.{criterion["id"]}: obtain agreement on threshold')
    relative_objective = doc.get('runner', {}).get('selection', {}).get('mode') == 'joint_cost_latency'
    if not doc['success_criteria'] and not relative_objective:
        open_items.append('Agree at least one success criterion')

    for key, value in doc['execution'].items():
        if key == 'max_cases_per_dataset' and 'runner' in doc:
            continue  # The executable runner defines null as all cases, not an unresolved choice.
        if key == 'budget_usd' and doc.get('runner', {}).get('budget_policy') == 'unlimited':
            continue
        if value is None:
            open_items.append(f'execution.{key}')
    opt = doc['optimization']
    for field, split in [('development_dataset_ids', 'development'), ('holdout_dataset_ids', 'holdout')]:
        for ident in opt[field]:
            dataset = need_ref('datasets', ident, f'optimization.{field}')
            if dataset and dataset['split'] != split:
                errors.append(f'optimization.{field}: dataset {ident} has wrong split')
    if opt['target_prompt_id']:
        need_ref('prompts', opt['target_prompt_id'], 'optimization.target_prompt_id')
    if opt['objective_criterion_id']:
        need_ref('success_criteria', opt['objective_criterion_id'], 'optimization.objective_criterion_id')
    if opt['enabled']:
        for field in ('target_prompt_id', 'development_dataset_ids', 'holdout_dataset_ids', 'max_iterations', 'objective_criterion_id'):
            if field == 'objective_criterion_id' and relative_objective:
                continue  # Compare required-check pass rates; no invented absolute threshold.
            if not opt[field]:
                open_items.append(f'optimization.{field}')
        runtime_checks.append('Optimizer must never receive holdout labels or cases while revising prompts')
    for item in doc['open_questions']:
        open_items.append(f'{item["id"]} ({item["blocks"]}): {item["question"]}')
    return {'valid': not errors, 'ready_for_builder': not errors and not open_items,
            'errors': errors, 'open_items': open_items, 'runtime_checks': runtime_checks,
            'assets': [{'path': path, 'sha256': assets[path]} for path in sorted(assets)],
            'execution_authorized': False}


def render_brief(doc: dict, report: dict) -> str:
    exp = doc['experiment']
    lines = [f'# {exp["title"]}', '', f'Experiment: {exp["id"]}',
             f'Objective: {exp["objective"] or "Unknown"}',
             f'Decision: {exp["decision"] or "Unknown"}', f'Owner: {exp["owner"] or "Unknown"}', '',
             'Status: ' + ('Ready for builder planning.' if report['ready_for_builder'] else 'Draft; resolve the open items below.'),
             'This package does not authorize execution or establish runtime compatibility.', '',
             '## Explicit comparison variants', '',
             '| Variant | Agent | Model reference | Prompt | Route |', '|---|---|---|---|---|']
    for v in doc['variants']:
        lines.append(f'| {v["id"]} | {v["agent_id"]} | {v["model_id"]} | {v["prompt_id"]} | {v["route"] or "Not selected"} |')
    lines += ['', f'Baseline: {doc["baseline_variant_id"] or "Not selected"}', '', '## Criteria', '']
    for c in doc['success_criteria']:
        threshold = f'{c["operator"]} {c["threshold"]}' if c['operator'] is not None and c['threshold'] is not None else 'threshold not agreed'
        lines.append(f'- {c["id"]}: {c["definition"]}; {threshold}; {c["status"]}.')
    lines += ['', '## Models and agent setup', '']
    for m in doc['models']:
        identifier = m['model_id'] or ('environment variable '+m['model_id_env'] if m['model_id_env'] else 'model ID not selected')
        lines.append(f'- {m["id"]}: {identifier}; provider {m["provider"] or "unknown"}; API {m["api"] or "unknown"}; compatibility {m["compatibility"]}.')
    for a in doc['agents']:
        lines.append(f'- {a["id"]}: harness {a["harness"] or "unknown"}; runtime {a["runtime"] or "unknown"}; Gateway {a["gateway_id"] or "none specified"}; {a["description"]}')
        if a.get('planned_runtime'):
            lines.append(f'  Planned deployment: {a["planned_runtime"]}; this does not establish the current evaluation runtime.')
    for g in doc['gateways']:
        lines.append(f'- Gateway {g["id"]}: AgentCore Gateway; role {g["role"] or "not selected"}; authentication {g["auth"] or "not selected"}; compatibility {g["compatibility"]}.')
    lines += ['', '## Prompts and data', '']
    for p in doc['prompts']:
        lines.append(f'- Prompt {p["id"]}: {p["path"] or "file not supplied"}; version {p["version"] or "unknown"}.')
    for d in doc['datasets']:
        lines.append(f'- Dataset {d["id"]}: {d["path"] or "file not supplied"}; split {d["split"] or "not selected"}; source {d["provenance"] or "unknown"}; labels {d["label_status"] or "unknown"}.')
    execution = doc['execution']
    budget = ('No dollar cap explicitly selected' if doc.get('runner', {}).get('budget_policy') == 'unlimited'
              else ('Not selected' if execution['budget_usd'] is None else f'USD {execution["budget_usd"]}'))
    lines += ['', '## Run limits', '', f'- Model budget: {budget}.',
              f'- Repetitions: {execution["repetitions"]}; case limit per dataset: {execution["max_cases_per_dataset"]}.',
              f'- Concurrency: {execution["concurrency"]}; timeout per trial: {execution["timeout_seconds"]} seconds.',
              f'- Optimization iterations: {doc["optimization"]["max_iterations"]}.']
    lines += ['', '## Evidence and assumptions', '']
    for item in doc['evidence']:
        lines.append(f'- {item["field"]} [{item["status"]}]: {item["source"]}')
    lines += ['', '## Open items', ''] + [f'- {x}' for x in report['open_items']]
    if not report['open_items']:
        lines.append('- None recorded in the input contract.')
    lines += ['', '## Runtime verification still required', ''] + [f'- {x}' for x in report['runtime_checks']]
    lines += ['', '## Builder handoff', '',
              'Read evaluation-input.json as supplied requirements, then inspect the actual workload.',
              'Use repository evidence to confirm or challenge these inputs; retain their provenance.',
              'Design evaluators from observed failures. Do not silently change model, prompt, baseline or thresholds.',
              ('The Model Upgrade customer_eval runner imports this configuration directly. Validate it with scripts/run_optimization.py plan before submission.'
               if 'runner' in doc else 'A separate eval-builder workflow needs an explicit mapping into its eval_config.yaml; this intake alone does not run evaluations.'),
              'Preserve attempted, invalid, failed, skipped and scored counts; report quality, time and total cost separately.',
              'The input contract describes desired behavior; enforcement belongs to the runner and gateway integrations.', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'bundle'])
    parser.add_argument('config', type=Path)
    parser.add_argument('--check-assets', action='store_true')
    parser.add_argument('--require-complete', action='store_true')
    parser.add_argument('--output', type=Path, help='New output directory for bundle')
    parser.add_argument('--asset-base', type=Path, help='Original or restored directory for relative asset paths; overrides handoff manifest')
    args = parser.parse_args(argv)
    try:
        doc = load_document(args.config)
        asset_base = resolve_asset_base(args.config.resolve(), doc, args.asset_base)
        report = validate(doc, asset_base, args.check_assets)
        if args.check_assets and not asset_base.is_dir():
            report['errors'].insert(0, 'The original asset directory is unavailable. Restore the referenced files and provide --asset-base; the handoff contains references, not copies of those files.')
            report['valid'] = report['ready_for_builder'] = False
        if not report['valid'] or (args.require_complete and not report['ready_for_builder']):
            print(json.dumps(report, indent=2))
            return 1
        if args.command == 'bundle':
            if not args.output:
                parser.error('bundle requires --output')
            if not args.check_assets:
                parser.error('bundle requires --check-assets')
            if args.output.exists():
                raise InputError('Output directory already exists. Choose a new revision directory, for example handoff-v2. Existing files were preserved.')
            args.output.mkdir(parents=True)
            (args.output/'evaluation-input.json').write_text(json.dumps(doc, indent=2, ensure_ascii=False)+'\n')
            (args.output/'brief.md').write_text(render_brief(doc, report))
            manifest = {'schema_version': doc['schema_version'], 'input_sha256': hashlib.sha256(canonical(doc)).hexdigest(),
                        'source_config': str(args.config.resolve()), 'asset_base': str(asset_base),
                        'validation': report}
            (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
            print(f'Wrote handoff to {args.output}. Asset paths remain relative to the original configuration directory.')
        else:
            print(json.dumps(report, indent=2))
        return 0
    except InputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError:
        print('Input file or directory was not found. Check the configuration path and the original asset directory; no missing input was created.', file=sys.stderr)
        return 2
    except PermissionError:
        print('The input or output location is not accessible. Choose a readable input and writable output directory; existing files were preserved.', file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        print('Input could not be read or packaged. Check that files are UTF-8 JSON/YAML and paths are accessible. Supplied values are not echoed.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
