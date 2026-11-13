"""Deterministic checks for turn responses and tool evidence. Labels never reach the agent."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator

TYPES = {'turn_trace', 'turn_response', 'turn_tools', 'turn_semantic'}
SCHEMA = Path(__file__).resolve().parents[1] / 'intake/skills/eval-intake/assets/turn-expectations.schema.json'


def same(actual, expected):
    return json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    return same(actual, expected)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('Non-finite JSON number')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def validate_expectations(case, grading):
    rules = [r for r in grading.values() if r['type'] in TYPES]
    if not rules:
        return
    expected = case['expected'].get('turns')
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(expected)
    if len(expected) != len(case['turns']):
        raise ValueError(f"{case['id']}: expected.turns must cover every user turn")
    prior_allowed = []
    for turn in expected:
        allowed = turn['tools']['allowed']
        for spec in turn['tools']['required']:
            pool = prior_allowed + allowed if spec.get('scope', 'turn') == 'conversation' else allowed
            if not any(s['name'] == spec['name'] and same(s['arguments'], spec['arguments']) for s in pool):
                raise ValueError('Required tool evidence must have a corresponding allowed invocation')
        prior_allowed += allowed
    for rule in rules:
        if rule['type'] == 'turn_response':
            schema = rule.get('response_schema')
            if not schema:
                raise ValueError('turn_response requires a response_schema')
            # Keep grading local and reproducible; no network schema resolution.
            def check_refs(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in ('$ref', '$dynamicRef') and not str(child).startswith('#'):
                            raise ValueError('External response schema references are unsupported')
                        check_refs(child)
                elif isinstance(value, list):
                    for child in value:
                        check_refs(child)
            check_refs(schema)
            Draft202012Validator.check_schema(schema)
            for turn in expected:
                Draft202012Validator(schema).validate(turn['response'])


def trace_errors(case, result):
    errors = []
    records = result.get('turn_records', [])
    if result.get('trace_version') != 'turns-v1' or not isinstance(records, list) or len(records) != len(case['turns']):
        return ['missing or incomplete per-turn receipts']
    if not isinstance(result.get('outputs'), list) or len(result['outputs']) != len(records):
        return ['answer count does not match turn receipts']
    seen, all_calls, previous_end = set(), [], 0
    for index, (user, record, output) in enumerate(zip(case['turns'], records, result['outputs']), 1):
        prefix = f'turn {index}: '
        if not isinstance(record, dict):
            errors.append(prefix + 'turn receipt is not an object')
            continue
        if type(record.get('turn_index')) is not int or record['turn_index'] != index or record.get('user') != user:
            errors.append(prefix + 'receipt does not match user turn')
        if record.get('status') != 'completed' or not isinstance(output, str) or not output.strip() or record.get('output') != output:
            errors.append(prefix + 'answer missing or inconsistent')
        start, answer, end = record.get('started_ns'), record.get('answered_ns'), record.get('ended_ns')
        if (type(start) is not int or type(answer) is not int or type(end) is not int
                or start < previous_end or not start <= answer <= end):
            errors.append(prefix + 'invalid turn timing')
            continue
        previous_end = end
        calls = record.get('tool_calls')
        if not isinstance(calls, list):
            errors.append(prefix + 'tool receipts missing')
            continue
        for call in calls:
            if not isinstance(call, dict):
                errors.append(prefix + 'tool receipt is not an object')
                continue
            ident = call.get('call_id')
            if not isinstance(ident, str) or not ident or ident in seen:
                errors.append(prefix + 'missing or duplicate tool call ID')
            seen.add(str(ident))
            cs, ce = call.get('started_ns'), call.get('ended_ns')
            if (type(call.get('turn_index')) is not int or call['turn_index'] != index
                    or type(cs) is not int or type(ce) is not int or not start <= cs <= ce <= answer):
                errors.append(prefix + 'tool evidence occurred outside this turn or after its answer')
            if (call.get('status') != 'completed' or 'result' not in call or
                    not isinstance(call.get('name'), str) or not isinstance(call.get('arguments'), dict)):
                errors.append(prefix + 'tool execution lacks a completed result')
        all_calls.extend(calls)
    if not same(all_calls, result.get('tool_calls')):
        errors.append('conversation and turn tool receipts disagree')
    return errors


def matches(call, spec, with_result=False):
    return (call['name'] == spec['name'] and same(call['arguments'], spec['arguments'])
            and (not with_result or contains(call.get('result'), spec['result_contains'])))


def grade_turns(case, result, rule):
    errors = trace_errors(case, result)
    if errors:
        return {'pass': False, 'reason': '; '.join(errors), 'error': True, 'failure_category': 'trace'}
    if rule['type'] == 'turn_trace':
        return {'pass': True, 'reason': 'Every answer and tool result has a consistent turn receipt'}
    details, prior = [], []
    for index, (expected, record) in enumerate(zip(case['expected']['turns'], result['turn_records']), 1):
        reasons = []
        calls = record['tool_calls']
        if rule['type'] == 'turn_response':
            try:
                actual = strict_json(record['output'])
                Draft202012Validator(rule['response_schema']).validate(actual)
                if not contains(actual, expected['response']):
                    reasons.append('response disagrees with expected behavior: expected ' + json.dumps(expected['response']) + '; actual ' + record['output'][:1000])
            except (ValueError, TypeError) as exc:
                reasons.append('response is not valid strict JSON: ' + type(exc).__name__)
            except Exception as exc:
                from jsonschema.exceptions import ValidationError
                if not isinstance(exc, ValidationError):
                    raise
                reasons.append('response violates the agreed JSON schema: ' + exc.message[:500])
        elif rule['type'] == 'turn_tools':
            allowed = expected['tools']['allowed']
            for call in calls:
                if not any(matches(call, spec) for spec in allowed):
                    reasons.append('unexpected tool or arguments: ' + json.dumps({'name': call['name'], 'arguments': call['arguments']}))
            for spec in allowed:
                if 'max_calls' in spec and sum(matches(call, spec) for call in calls) > spec['max_calls']:
                    reasons.append('too many calls for ' + spec['name'] + ' ' + json.dumps(spec['arguments'], sort_keys=True))
            for spec in expected['tools']['required']:
                evidence = prior + calls if spec.get('scope', 'turn') == 'conversation' else calls
                if sum(matches(call, spec, True) for call in evidence) < spec.get('min_count', 1):
                    reasons.append('missing supporting tool evidence: ' + json.dumps(spec))
        details.append({'turn_index': index, 'pass': not reasons, 'reasons': reasons})
        prior += calls
    failures = [f"turn {t['turn_index']}: {reason}" for t in details for reason in t['reasons']]
    return {'pass': not failures, 'reason': '; '.join(failures) if failures else 'All turn checks passed',
            'turn_details': details, 'failure_category': 'behavior' if failures else None}
