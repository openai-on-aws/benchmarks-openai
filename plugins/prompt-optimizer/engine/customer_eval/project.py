"""Saved progressive intake around the existing importer and evaluation contract."""
from copy import deepcopy
import json
from pathlib import Path
import re

import yaml
from .contract import ROOT, digest, load, intake
from .input_preview import instructions, write_preview
from .log_import import import_logs, write_import, strict_json
from .replay import validate_replay
from .scoring import at


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)


def read(path):
    return strict_json(Path(path).read_text())


def start(directory, goal):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'project.json'
    if path.exists():
        return read(path)
    if any(directory.iterdir()):
        raise ValueError('Choose an empty project directory; existing files will not be replaced')
    state = {'version': 1, 'goal': goal, 'stage': 'intake', 'decisions': {}, 'import_revision': 0}
    save(path, state)
    return state


def inspect(directory, sources, *, prompt_location=None):
    directory = Path(directory).resolve()
    state = read(directory / 'project.json')
    if state.get('prepared'):
        raise ValueError('This project is frozen. Start a new project to change its inputs.')
    reference_mode = 'source_judge' if state['decisions'].get('quality', {}).get('kind') == 'source_judge' else 'standard'
    cases, reports = [], []
    # Mapping may differ per file. The final merge still checks global duplicates.
    for spec in sources:
        path = str(Path(spec['path']).expanduser().resolve())
        members, report = import_logs([path], mapping=spec.get('mapping'), prompt_location=prompt_location, reference_mode=reference_mode)
        cases += members
        reports.append(report)
    merged = {'found': sum(r['found'] for r in reports), 'accepted': 0, 'dropped': 0,
              'excluded_records': [x for r in reports for x in r['excluded_records']],
              'sources': [x for r in reports for x in r['sources']]}
    unique, ids, requests = [], set(), {}
    for case in cases:
        code = None
        sha = case['replay']['request_sha256']
        if case['id'] in ids:
            code = 'duplicate_case_id'
        elif sha in requests:
            code = 'duplicate_request' if requests[sha] == case.get('expected') else 'conflicting_reference'
        if code:
            merged['excluded_records'].append({k: case['metadata'][k] for k in ('source_file', 'source_row')} | {'reason': code})
        else:
            ids.add(case['id']); requests[sha] = case.get('expected'); unique.append(case)
    from collections import Counter
    merged.update(accepted=len(unique), dropped=len(merged['excluded_records']),
                  formats=dict(Counter(c['metadata']['format'] for c in unique)),
                  drop_reasons=dict(Counter(x['reason'] for x in merged['excluded_records'])))
    merged['reconciles'] = merged['found'] == merged['accepted'] + merged['dropped']
    merged['reference_mode'] = reference_mode
    merged['scope'] = 'One text response with recorded history; no tool execution or raw PDF extraction.'
    state['import_revision'] += 1
    relative = 'imports/' + str(state['import_revision']).zfill(4)
    report = write_import(unique, merged, directory / relative)
    save(directory / relative / 'all-cases.json', unique)
    state.update(sources=[dict(s, path=str(Path(s['path']).resolve())) for s in sources],
                 import_path=relative, stage='inputs_inspected')
    save(directory / 'project.json', state)
    preview = write_preview(directory, unique, report)
    return {'coverage': preview['coverage'], 'prompt_groups': preview['prompt_groups'],
            'instruction_slots': instructions(unique[0]) if unique else [],
            'preview': str(directory / 'input-preview.html'), 'excluded_records': preview['excluded_records']}


ALLOWED = {'title', 'owner', 'source_model', 'target_model', 'optimizer_model', 'judge_model', 'prompt_group',
           'prompt_location', 'connection', 'provenance', 'quality', 'repetitions', 'iterations',
           'candidates', 'concurrency', 'timeout_seconds', 'budget_usd', 'budget_policy', 'split_seed',
           'minimum_quality', 'minimum_quality_owner', 'intent', 'exclude_records', 'group_by',
           'cost_metric', 'primary_latency', 'optimization_enabled', 'label_review_file',
           'limits', 'optimizer_limits', 'judge_limits', 'split_counts'}
QUALITY = {'kind', 'fields', 'reference_source', 'reference_file', 'description', 'rules', 'checks', 'schema', 'source_checks'}
QUALITY_KINDS = ('json_equals', 'contains', 'json_checks', 'source_judge')
DEFAULT_LIMITS = {'max_requests': 1, 'max_output_tokens': 2048, 'max_request_bytes': 80000,
                  'request_timeout_seconds': 60}
DEFAULT_OPTIMIZER_LIMITS = {'max_requests': 1, 'max_output_tokens': 8192, 'max_request_bytes': 80000,
                            'request_timeout_seconds': 60}


def replay_limits(value, *, optimizer=False, judge=False):
    """Typed partial overrides; captured-request replay still makes one new call."""
    if optimizer and judge:
        raise ValueError('Choose one limits purpose')
    label = 'judge_limits' if judge else ('optimizer_limits' if optimizer else 'limits')
    if not isinstance(value, dict) or set(value) - set(DEFAULT_LIMITS):
        raise ValueError(label + ' accepts max_requests, max_output_tokens, max_request_bytes and request_timeout_seconds only')
    if any(type(v) is not int or v < 1 for v in value.values()):
        raise ValueError(label + ' values must be positive integers, not booleans')
    limits = dict(DEFAULT_OPTIMIZER_LIMITS if optimizer or judge else DEFAULT_LIMITS) | value
    if limits['max_requests'] != 1:
        raise ValueError(label + '.max_requests must be 1 for captured-request replay')
    return limits


def has_source_checks(quality):
    return quality.get('kind') == 'source_judge' or bool(quality.get('source_checks'))


def quality_checks(quality):
    """Turn the small guided declaration into independent deterministic rules."""
    if 'source_checks' in quality and quality.get('kind') != 'json_checks':
        raise ValueError('quality.source_checks requires kind json_checks; source_judge uses checks instead')
    if quality.get('kind') == 'source_judge':
        if any(key in quality for key in ('fields', 'reference_source', 'reference_file')):
            raise ValueError('source_judge uses source inputs and a rubric, not expected-answer or recorded-output references')
        checks = quality.get('checks')
        if not isinstance(checks, list) or not checks:
            raise ValueError('source_judge requires named rubric checks')
        normalized, seen = [], {'output_schema'}
        for check in checks:
            if not isinstance(check, dict) or set(check) - {'id', 'rubric', 'description', 'required'}:
                raise ValueError('Each source_judge check accepts id, rubric, description and required only')
            ident = check.get('id')
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', ident) or ident in seen:
                raise ValueError('Quality check IDs must be unique identifiers; output_schema is reserved')
            seen.add(ident)
            if not isinstance(check.get('rubric'), str) or not check['rubric'].strip():
                raise ValueError('Each source_judge check needs a nonempty fixed rubric')
            if type(check.get('required', True)) is not bool:
                raise ValueError('Quality check required must be a boolean')
            description = check.get('description', 'Does the candidate satisfy ' + ident + ' using the supplied source?')
            if not isinstance(description, str) or not description.strip():
                raise ValueError('Quality check description must be nonempty text')
            normalized.append((ident, {'type': 'llm_source_rubric', 'rubric': check['rubric']}, description, check.get('required', True)))
        if not any(entry[3] for entry in normalized):
            raise ValueError('At least one source rubric check must be required')
        if 'schema' in quality:
            from .json_grading import schema_validator
            schema_validator(quality['schema'])
            normalized.insert(0, ('output_schema', {'type': 'json_schema', 'schema': quality['schema']},
                                  'Does the answer conform to the required JSON structure and types?', True))
        return normalized
    if quality.get('kind') == 'json_checks':
        checks = quality.get('checks')
        if not isinstance(checks, list) or not checks:
            raise ValueError('quality.checks must contain at least one structured JSON check')
        normalized, seen = [], {'output_schema'}
        for check in checks:
            if not isinstance(check, dict) or set(check) - {'id', 'type', 'path', 'description', 'required'}:
                raise ValueError('Each quality check accepts id, type, path, description and required only')
            ident = check.get('id')
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', ident) or ident in seen:
                raise ValueError('Quality check IDs must be unique identifiers; output_schema is reserved')
            seen.add(ident)
            if check.get('type') not in ('json_equals', 'json_set_equals', 'json_unordered_equals') or not isinstance(check.get('path'), str):
                raise ValueError('Quality checks need type json_equals/json_set_equals/json_unordered_equals and a JSON field path')
            if type(check.get('required', True)) is not bool:
                raise ValueError('Quality check required must be a boolean')
            if 'description' in check and (not isinstance(check['description'], str) or not check['description'].strip()):
                raise ValueError('Quality check description must be nonempty text')
            path = check['path']
            normalized.append((ident, {'type': check['type'], 'path': path,
                'expected_key': 'answer' + ('.' + path if path else '')},
                check.get('description') or ('Does ' + (path or 'the complete JSON answer') + ' match the supplied reference?'),
                check.get('required', True)))
        if not any(entry[3] for entry in normalized):
            raise ValueError('At least one JSON value check must be required')
        if 'source_checks' in quality:
            source_checks = quality['source_checks']
            if not isinstance(source_checks, list) or not source_checks:
                raise ValueError('quality.source_checks must be a nonempty array when supplied')
            for check in source_checks:
                if not isinstance(check, dict) or set(check) - {'id', 'rubric', 'description', 'required'}:
                    raise ValueError('Each source check accepts id, rubric, description and required only')
                ident = check.get('id')
                if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', ident) or ident in seen:
                    raise ValueError('Quality check IDs must be unique across deterministic and source checks')
                seen.add(ident)
                if check.get('required', False) is not False:
                    raise ValueError('Source checks accompanying references must be optional; required reference checks determine correctness')
                rubric = check.get('rubric')
                description = check.get('description', 'Is the explanation supported by the supplied sources?')
                if not isinstance(rubric, str) or not rubric.strip() or not isinstance(description, str) or not description.strip():
                    raise ValueError('Source checks need nonempty rubric and description text')
                normalized.append((ident, {'type': 'llm_source_rubric', 'rubric': rubric}, description, False))
        if 'schema' in quality:
            from .json_grading import schema_validator
            schema_validator(quality['schema'])
            normalized.insert(0, ('output_schema', {'type': 'json_schema', 'schema': quality['schema']},
                                  'Does the answer conform to the required JSON structure and types?', True))
        return normalized
    if quality.get('kind') not in QUALITY_KINDS:
        raise ValueError('Define checks using json_equals, contains, json_checks or source_judge')
    if 'checks' in quality or 'schema' in quality:
        raise ValueError('quality.checks and quality.schema require kind json_checks or source_judge')
    fields = quality.get('fields', [''])
    if not isinstance(fields, list) or not fields or any(not isinstance(p, str) for p in fields) or len(fields) != len(set(fields)):
        raise ValueError('Quality fields must be unique JSON field paths; use [""] for the whole object')
    return [('field_' + str(i + 1), {'type': quality['kind'], 'path': path,
        'expected_key': ('answer' + ('.' + path if path else '')) if quality['kind'] == 'json_equals' else 'reference'},
        quality.get('description') or ('Does ' + (path or 'the complete JSON answer') + ' match the supplied reference?'), True)
        for i, path in enumerate(fields)]


def configure(directory, patch):
    directory = Path(directory).resolve()
    state = read(directory / 'project.json')
    if state.get('prepared'):
        raise ValueError('This project is frozen. Start a new project to change its decisions.')
    if not isinstance(patch, dict):
        raise ValueError('Intake decisions must be an object')
    if set(patch) - ALLOWED:
        raise ValueError('Unknown intake decision: ' + str(set(patch) - ALLOWED))
    if 'quality' in patch and (not isinstance(patch['quality'], dict) or set(patch['quality']) - QUALITY):
        raise ValueError('Unknown quality decision; see the guided evaluation reference')
    for key, choices in {'cost_metric': ('cost_usd_per_session', 'cost_usd_per_success'),
                         'primary_latency': ('conversation', 'worker')}.items():
        if key in patch and patch[key] not in choices:
            raise ValueError(key + ' must be one of: ' + ', '.join(choices))
    if 'optimization_enabled' in patch and type(patch['optimization_enabled']) is not bool:
        raise ValueError('optimization_enabled must be a boolean')
    for key in ('limits', 'optimizer_limits', 'judge_limits'):
        if key in patch:
            replay_limits(patch[key], optimizer=key == 'optimizer_limits', judge=key == 'judge_limits')
    rules = patch.get('quality', {}).get('rules')
    if 'rules' in patch.get('quality', {}) and (not isinstance(rules, str) or not rules.strip()):
        raise ValueError('quality.rules must contain the supplied general business rules as nonempty text')
    previous_location = state['decisions'].get('prompt_location')
    previous_kind = state['decisions'].get('quality', {}).get('kind')
    next_kind = patch.get('quality', {}).get('kind', previous_kind)
    reference_mode_changed = (previous_kind == 'source_judge') != (next_kind == 'source_judge')
    for key, value in patch.items():
        if key == 'quality' and reference_mode_changed:
            state['decisions'][key] = deepcopy(value)
        elif key in ('quality', 'limits', 'optimizer_limits', 'judge_limits'):
            state['decisions'].setdefault(key, {}).update(value)
        else:
            state['decisions'][key] = value
    save(directory / 'project.json', state)
    location = state['decisions'].get('prompt_location')
    if state.get('sources') and (reference_mode_changed or (location is not None and location != previous_location)):
        inspect(directory, state['sources'], prompt_location=location)
    return status(directory)


def status(directory):
    directory = Path(directory).resolve()
    state = read(directory / 'project.json')
    d = state['decisions']
    missing = []
    if not state.get('import_path'):
        missing.append('Provide captured requests or manual prompt/input examples; choose supplied references, recorded agreement, or source-based judging.')
    else:
        report = read(directory / state['import_path'] / 'import-report.json')
        if not report['accepted']:
            missing.append('Repair the excluded records or provide supported text examples.')
        if len(report['prompt_groups']) > 1 and d.get('prompt_group') not in report['prompt_groups']:
            missing.append('Choose the prompt group to evaluate; different starting prompts are kept separate.')
        if report['dropped'] and not d.get('exclude_records'):
            missing.append('Review the excluded rows and decide whether to repair them or run only the accepted subset.')
    for key, question in [
        ('owner', 'Who owns the evaluation?'),
        ('source_model', 'Which existing model is the baseline?'),
        ('target_model', 'Which model should it be compared with?'),
        ('optimizer_model', 'Which approved model should generate prompt revisions?'),
        ('judge_model', 'Which separately selected approved model should judge the extraction against its source?'),
        ('connection', 'Provide the platform connection file, or continue with a local draft.'),
        ('prompt_location', 'Which instruction slot may the optimizer change? Other instructions remain protected.'),
        ('provenance', 'Are these synthetic, customer, or public examples? Record any redaction in the intake notes.'),
        ('budget_policy', 'Use the existing paid-run authorization, or obtain a budget decision before execution.')]:
        if key == 'judge_model' and not has_source_checks(d.get('quality', {})):
            continue
        if key == 'optimizer_model' and not d.get('optimization_enabled', True):
            continue
        if key == 'prompt_location' and not d.get('optimization_enabled', True):
            question = 'Which instruction slot contains the fixed prompt to preserve?'
        if d.get(key) is None:
            missing.append(question)
    quality = d.get('quality', {})
    try:
        quality_checks(quality)
        limits = replay_limits(d.get('limits', {}))
        replay_limits(d.get('optimizer_limits', {}), optimizer=True)
        if has_source_checks(quality) or 'judge_limits' in d:
            replay_limits(d.get('judge_limits', {}), judge=True)
        trial_timeout = d.get('timeout_seconds', 180)
        if type(trial_timeout) is not int or trial_timeout < 1:
            raise ValueError('timeout_seconds must be a positive integer')
        if trial_timeout < limits['request_timeout_seconds']:
            raise ValueError('timeout_seconds must be at least limits.request_timeout_seconds; allow extra time for worker setup')
    except ValueError as exc:
        missing.append(str(exc))
    source_judge = quality.get('kind') == 'source_judge'
    if not source_judge and quality.get('reference_source') not in ('recorded', 'supplied'):
        missing.append('Should we compare against supplied expected answers, measure agreement with recorded outputs, or judge outputs against the source inputs using a fixed rubric when no answers are available?')
    plan = None
    if state.get('import_path') and quality.get('kind') in QUALITY_KINDS and (source_judge or quality.get('reference_source') in ('recorded', 'supplied')):
        try:
            selected, _, _ = prepare_cases(directory, state)
            splits = _split(selected, d.get('split_seed', 'customer-eval-v1'), d.get('group_by'),
                            optimize=d.get('optimization_enabled', True), split_counts=d.get('split_counts'))
            repeats = d.get('repetitions', 3)
            if type(repeats) is not int or repeats < 1:
                raise ValueError('repetitions must be a positive integer')
            plan = {'mode': 'optimize' if d.get('optimization_enabled', True) else 'compare',
                    'provided_cases': len(read(directory / state['import_path'] / 'all-cases.json')),
                    'selected_cases': len(selected), 'split_counts': {k: len(v) for k, v in splits.items()},
                    'repetitions': repeats, 'validation_observations': len(splits['validation']) * repeats,
                    'limits': replay_limits(d.get('limits', {})),
                    'optimizer_limits': replay_limits(d.get('optimizer_limits', {}), optimizer=True) if d.get('optimization_enabled', True) else None,
                    'judge_limits': replay_limits(d.get('judge_limits', {}), judge=True) if has_source_checks(quality) else None,
                    'evidence_scope': 'Exploratory workload comparison; repeated attempts are not independent cases.'}
            if d.get('optimization_enabled', True) and plan['validation_observations'] < 20:
                missing.append('Prompt optimization needs at least 20 validation observations; the current split provides '
                               + str(plan['validation_observations']) + '. Add independent cases or explicitly adjust repetitions; repeats do not add independent cases.')
        except (ValueError, KeyError, TypeError, OSError) as exc:
            missing.append(str(exc))
    return {'stage': state['stage'], 'missing_decisions': missing, 'ready_to_prepare': not missing,
            'project': str(directory), 'decisions': d, 'prepared': state.get('prepared'), 'evaluation_plan': plan}


def _split(cases, seed, group_by, *, optimize=True, split_counts=None):
    groups = {}
    for case in cases:
        ident = at(case, group_by) if group_by else case['id']
        if not isinstance(ident, (str, int)) or isinstance(ident, bool):
            raise ValueError('Each grouping value must be a document/conversation identifier')
        groups.setdefault(str(ident), []).append(case)
    names = ('development', 'validation', 'holdout') if optimize else ('validation', 'holdout')
    if len(groups) < len(names):
        raise ValueError('Need at least ' + str(len(names)) + ' independent groups for ' + ', '.join(names) + '; add examples or save a draft.')
    ordered = sorted(groups, key=lambda x: digest([seed, x]))
    result = {key: [] for key in ('development', 'validation', 'holdout')}
    if split_counts is not None:
        if (not isinstance(split_counts, dict) or set(split_counts) != set(names) or
                any(type(v) is not int or v < 1 for v in split_counts.values()) or
                sum(split_counts.values()) != len(cases)):
            raise ValueError('split_counts must give positive case counts for each active split and sum to all selected cases')
        if any(len(group) != 1 for group in groups.values()):
            raise ValueError('Exact split_counts currently requires independent one-case groups; use grouped default splitting for related cases')
        cursor = 0
        for name in names:
            for ident in ordered[cursor:cursor + split_counts[name]]:
                result[name].extend(groups[ident])
            cursor += split_counts[name]
    else:
        for i, ident in enumerate(ordered):
            result[names[i % len(names)]].extend(groups[ident])
    return result


def prepare_cases(directory, state=None, *, apply_reviews=True):
    """Resolve the exact references before reviewing, splitting, or freezing them."""
    directory = Path(directory).resolve()
    state = state or read(directory / 'project.json')
    d = state['decisions']
    cases = read(directory / state['import_path'] / 'all-cases.json')
    report = read(directory / state['import_path'] / 'import-report.json')
    if not report['prompt_groups']:
        raise ValueError('No supported prompt group remains; repair the excluded records or choose a valid instruction slot.')
    group = d.get('prompt_group') or next(iter(report['prompt_groups']))
    cases = [c for c in cases if c['metadata']['prompt_group'] == group]
    if not cases:
        raise ValueError('The selected prompt group has no cases')
    if 'prompt_location' in d and any(c['replay']['prompt_location'] != d['prompt_location'] for c in cases):
        raise ValueError('Import does not use the selected editable instruction slot')
    quality = d['quality']
    if quality.get('kind') == 'source_judge':
        quality_checks(quality)
        if report.get('reference_mode') != 'source_judge':
            raise ValueError('Reinspect the original inputs in source_judge mode before preparing')
        if d.get('label_review_file'):
            raise ValueError('Source judging has no expected-answer labels to review; remove label_review_file')
        for c in cases:
            if 'expected' in c or c['metadata'].get('reference_kind') != 'source_judge':
                raise ValueError('Source judging requires source-only cases without expected answers')
            c['metadata']['label_status'] = 'not_provided'
        return cases, report, group
    if quality['reference_source'] == 'recorded':
        if quality.get('reference_file'):
            raise ValueError('Choose supplied references when using an expected-answer file; recorded agreement uses historical responses.')
        for c in cases:
            if c['metadata']['format'] == 'manual':
                raise ValueError('Manual expected answers are supplied references, not historical model outputs.')
            if quality['kind'] in ('json_equals', 'json_checks'):
                try:
                    c['expected']['answer'] = strict_json(c['expected']['reference'])
                except ValueError:
                    raise ValueError('Historical response is not valid JSON for case ' + c['id'] + '; supply a correct reference or choose a different check.')
            c['metadata']['reference_kind'] = 'recorded_response'
    if quality.get('reference_file'):
        refs = read(Path(quality['reference_file']).expanduser().resolve())
        missing = {c['id'] for c in cases} - set(refs)
        if missing:
            raise ValueError('Missing expected answers for: ' + ', '.join(sorted(missing)[:10]))
        for c in cases:
            c['expected']['answer'] = refs[c['id']]
            c['expected']['reference'] = refs[c['id']] if isinstance(refs[c['id']], str) else json.dumps(refs[c['id']])
            c['metadata']['reference_kind'] = 'supplied_expected'
    if quality['reference_source'] == 'supplied' and any(c['metadata']['reference_kind'] != 'supplied_expected' for c in cases):
        raise ValueError('Supplied correctness requires expected answers; historical model responses are only agreement references')
    for c in cases:
        c['metadata']['label_status'] = 'unreviewed'
    if apply_reviews and d.get('label_review_file'):
        from .label_review import apply_review
        cases, summary = apply_review(cases, Path(d['label_review_file']).expanduser().resolve())
        if summary.get('issues') or summary.get('rejected_case_ids'):
            raise ValueError('Label review needs attention: ' + json.dumps(summary))
    return cases, report, group


def prepare(directory):
    directory = Path(directory).resolve()
    state = read(directory / 'project.json')
    if state.get('prepared'):
        config = load_with_endpoints(directory / state['prepared']['config'])
        if config['fingerprint'] != state['prepared']['fingerprint']:
            raise ValueError('Prepared files or runner source changed; use a new project revision')
        return state['prepared']
    summary = status(directory)
    if summary['missing_decisions']:
        return summary
    d = state['decisions']
    connection = read(Path(d['connection']).expanduser().resolve())
    if set(connection) - {'version', 'models', 'cloud'}:
        raise ValueError('Connection file contains unknown fields; use credential references, never credential values')
    cases, report, group = prepare_cases(directory, state)
    quality = d['quality']
    optimize = d.get('optimization_enabled', True)
    splits = _split(cases, d.get('split_seed', 'customer-eval-v1'), d.get('group_by'), optimize=optimize, split_counts=d.get('split_counts'))
    repetitions = d.get('repetitions', 3)
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError('repetitions must be a positive integer')
    if optimize and len(splits['validation']) * repetitions < 20:
        raise ValueError('Joint comparison requires at least 20 validation observations. Add independent examples or choose more repetitions; repeated cases are not independent cases.')
    doc = yaml.safe_load((ROOT / 'intake/skills/eval-intake/assets/input.template.yaml').read_text())
    doc['experiment'] = {'id': 'customer_evaluation', 'title': d.get('title', 'Customer model and prompt evaluation'),
        'objective': state['goal'], 'decision': d.get('intent', 'Compare required checks, model cost and conversation time; review tradeoffs.'), 'owner': d['owner']}
    routes = {}
    endpoints = {}
    model_roles = [('m1', 'source_model'), ('m2', 'target_model')]
    if optimize:
        model_roles.append(('optimizer', 'optimizer_model'))
    source_judge = quality.get('kind') == 'source_judge'
    if has_source_checks(quality):
        model_roles.append(('judge', 'judge_model'))
    for role, field in model_roles:
        entry = connection['models'].get(d[field])
        if not entry:
            raise ValueError('Model is not in the supplied connection file: ' + d[field])
        model = deepcopy(entry['model'])
        model.update(id=role, endpoint_env='EVAL_PROJECT_' + role.upper() + '_BASE_URL',
                     compatibility='unverified', verification_ref=None)
        doc['models'].append(model)
        routes[role] = deepcopy(entry['route'])
        endpoints[model['endpoint_env']] = entry['base_url']
    doc['prompts'] = [{'id': 'p1', 'path': 'prompt.txt', 'version': '1', 'variables': []}]
    doc['agents'] = [{'id': 'text_replay', 'description': 'One new model answer using recorded text history.',
        'harness': 'custom', 'harness_version': None, 'workspace': None, 'instructions_path': 'policy.txt',
        'runtime': 'agentcore_runtime', 'runtime_ref': connection['cloud']['runtime_arn'], 'gateway_id': None,
        'tool_allowlist': [], 'sandbox': 'read_only', 'side_effects': 'mocked', 'codex_profile': None}]
    doc['variants'] = [{'id': m + '_p1', 'model_id': m, 'prompt_id': 'p1', 'agent_id': 'text_replay',
                        'route': 'direct_model_api', 'gateway_id': None} for m in ('m1', 'm2')]
    doc['baseline_variant_id'] = 'm1_p1'
    grading = {}
    for ident, rule, question, required in quality_checks(quality):
        for c in ([] if source_judge or rule['type'] == 'llm_source_rubric' else cases):
            try:
                if rule['type'] == 'json_schema':
                    from .json_grading import schema_validator
                    if not schema_validator(rule['schema']).is_valid(c['expected']['answer']):
                        raise ValueError('Reference answer does not match quality.schema for ' + c['id'])
                else:
                    expected = at(c['expected'], rule['expected_key'])
                    if rule['type'] == 'json_set_equals':
                        from .json_grading import scalar_set
                        scalar_set(expected)
                    elif rule['type'] == 'json_unordered_equals':
                        from .json_grading import unordered_value
                        unordered_value(expected)
            except (KeyError, IndexError, TypeError, ValueError):
                raise ValueError('Expected answer for ' + c['id'] + ' is invalid for check ' + ident + '; repair the reference before running.')
        grading[ident] = rule
        doc['checks'].append({'id': ident, 'question': question,
            'method': 'llm_judge' if rule['type'] == 'llm_source_rubric' else 'deterministic',
            'implementation_ref': 'grading.json', 'judge_model_id': 'judge' if rule['type'] == 'llm_source_rubric' else None,
            'calibration_ref': None, 'required': required})
    if d.get('minimum_quality') is not None:
        if not d.get('minimum_quality_owner'):
            raise ValueError('An absolute quality threshold needs its customer-provided owner/source')
        doc['success_criteria'] = [{'id': 'quality', 'metric': 'all_required_checks_pass_rate', 'check_id': None,
            'definition': 'All configured required checks pass.', 'operator': '>=', 'threshold': d['minimum_quality'],
            'status': 'confirmed', 'confirmed_by': d['minimum_quality_owner']}]
    doc['execution'].update(repetitions=repetitions, max_cases_per_dataset=None,
        concurrency=d.get('concurrency', 2), timeout_seconds=d.get('timeout_seconds', 180),
        budget_usd=d.get('budget_usd'), fallback_policy='none')
    doc['optimization'].update(enabled=optimize, target_prompt_id='p1', development_dataset_ids=['development'] if optimize else [],
        holdout_dataset_ids=['holdout'], max_iterations=d.get('iterations', 1),
        objective_criterion_id='quality' if doc['success_criteria'] else None)
    doc['runner'] = {'mode': 'prompt', 'input_mode': 'replay', 'workload': 'none',
        'source_variant_id': 'm1_p1', 'target_variant_id': 'm2_p1', 'validation_dataset_ids': ['validation'],
        'protected_instructions_path': 'policy.txt', 'grading_path': 'grading.json', 'optimizer_model_id': 'optimizer' if optimize else None,
        'intake_context_path': 'customer-context.json',
        'candidates_per_generation': d.get('candidates', 2), 'min_gain': 0.0, 'max_turns': 1,
        'routes': routes, 'limits': replay_limits(d.get('limits', {})),
        'optimizer_limits': replay_limits(d.get('optimizer_limits', {}), optimizer=True),
        'budget_policy': d['budget_policy'], 'selection': {'mode': 'joint_cost_latency', 'min_validation_sessions': 20,
            'cost_metric': d.get('cost_metric', 'cost_usd_per_session')},
        'optimization_patience': 1, 'measurement': {'primary_latency': d.get('primary_latency', 'conversation')},
        'report_challenger': True}
    if quality.get('source_checks'):
        doc['runner']['grading_reference_mode'] = 'references_with_source_judges'
    if has_source_checks(quality):
        doc['runner']['judge_limits'] = replay_limits(d.get('judge_limits', {}), judge=True)
    doc['evidence'] = [
        {'field': 'datasets', 'source': ('No expected answers are supplied; model judgments use the source inputs and fixed rubric. ' if source_judge else 'References are ' + quality['reference_source'] + '; scoped label review is recorded per case. ') + d['provenance'] + ' examples.', 'status': 'observed'},
        {'field': 'success_criteria', 'source': 'Compare required-check pass rates; cost and latency are preferences. Only explicitly supplied absolute thresholds are gates.', 'status': 'proposed'}]
    package = directory / 'prepared'
    package.mkdir(exist_ok=True)
    (package / 'prompt.txt').write_text(validate_replay(cases[0]['replay']))
    (package / 'policy.txt').write_text('')
    save(package / 'grading.json', grading)
    for split, members in splits.items():
        if not members:
            continue
        (package / (split + '.jsonl')).write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in members))
        doc['datasets'].append({'id': split, 'path': split + '.jsonl', 'format': 'jsonl', 'version': '1',
            'split': split, 'provenance': d['provenance'], 'label_status':
                'not_provided' if source_judge else ('human_reviewed' if all(c['metadata'].get('label_status') == 'human_reviewed' for c in members) else 'unreviewed')})
    (package / 'input.yaml').write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    save(package / 'endpoints.json', endpoints)
    save(package / 'connection.json', connection)
    if d.get('label_review_file'):
        save(package / 'label-review.json', read(Path(d['label_review_file']).expanduser().resolve()))
    context = {'reference_source': 'source_inputs' if source_judge else quality['reference_source'], 'label_status':
        'not_provided' if source_judge else ('human_reviewed' if all(c['metadata'].get('label_status') == 'human_reviewed' for c in cases) else 'unreviewed'),
        'reviewed_cases': sum(c['metadata'].get('label_status') == 'human_reviewed' for c in cases),
        'evaluation_plan': summary['evaluation_plan'],
        'import_coverage': {k: report[k] for k in ('found', 'accepted', 'dropped')},
        'selected_cases': len(cases), 'selected_prompt_group': group, 'editable_location': d['prompt_location'],
        'quality': quality, 'group_by': d.get('group_by'), 'splits': {k: [c['id'] for c in v] for k, v in splits.items()}}
    if source_judge:
        context['expected_answers_provided'] = False
    elif quality.get('source_checks'):
        context['expected_answers_provided'] = True
        context['assessment_method'] = 'reference_and_source'
    save(package / 'customer-context.json', context)
    config = load_with_endpoints(package / 'input.yaml')
    write_preview(directory, cases, report, config=config)
    state['prepared'] = {'config': 'prepared/input.yaml', 'fingerprint': config['fingerprint'],
        'implementation_fingerprint': config['implementation_fingerprint'], 'split_counts': {k: len(v) for k, v in splits.items()},
        'repetitions': repetitions, 'execution_authorized': False}
    state['stage'] = 'prepared'
    save(directory / 'project.json', state)
    return state['prepared']


def load_with_endpoints(path, live=False):
    import os
    path = Path(path)
    endpoints = read(path.parent / 'endpoints.json')
    old = {key: os.environ.get(key) for key in endpoints}
    try:
        os.environ.update(endpoints)
        return load(path, live=live)
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
