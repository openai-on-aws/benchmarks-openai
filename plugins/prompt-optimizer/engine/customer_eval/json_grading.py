"""JSON output failures are model behavior; invalid references are grader errors."""
import json
import math
from .turn_grading import strict_json


def schema_validator(schema):
    """Validate a customer JSON Schema without fetching external references."""
    from jsonschema import Draft202012Validator
    if not isinstance(schema, dict):
        raise ValueError('quality.schema must be a JSON Schema object')
    strict_json(json.dumps(schema, allow_nan=False))
    def inspect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('$ref', '$dynamicRef') and (not isinstance(child, str) or not child.startswith('#')):
                    raise ValueError('JSON Schema references must be local fragments; external references are not fetched')
                if key == '$schema' and child not in ('https://json-schema.org/draft/2020-12/schema',
                                                     'https://json-schema.org/draft/2020-12/schema#'):
                    raise ValueError('JSON Schema must use draft 2020-12')
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise ValueError('Invalid JSON Schema: ' + str(exc).splitlines()[0][:200]) from exc
    return Draft202012Validator(schema)


def scalar_set(value):
    """Only unique JSON scalars; bools and numbers retain their distinct types."""
    if not isinstance(value, list):
        raise ValueError('A JSON set check requires an array')
    if any(type(v) not in (str, int, float, bool, type(None)) or
           (isinstance(v, float) and not math.isfinite(v)) for v in value):
        raise ValueError('JSON set values must be finite JSON scalars')
    items = {(type(v).__name__, json.dumps(v, ensure_ascii=False, allow_nan=False)) for v in value}
    if len(items) != len(value):
        raise ValueError('JSON set values must not contain duplicates')
    return items


def output_value(outputs, path, lookup):
    if not isinstance(outputs, list) or not outputs or not isinstance(outputs[-1], str) or not outputs[-1].strip():
        return None, {'pass': False, 'reason': 'No JSON answer was returned',
                      'failure_category': 'behavior', 'issue': 'missing_output'}
    try:
        value = strict_json(outputs[-1])
    except (ValueError, TypeError) as exc:
        return None, {'pass': False, 'reason': 'Answer is not valid JSON: ' + str(exc)[:160],
                      'failure_category': 'behavior', 'issue': 'invalid_json', 'actual_excerpt': outputs[-1][:500]}
    try:
        return lookup(value, path), None
    except (KeyError, IndexError, TypeError, ValueError):
        return None, {'pass': False, 'reason': 'Required output field is missing: ' + path,
                      'failure_category': 'behavior', 'issue': 'missing_field',
                      'differences': [{'path': path, 'issue': 'missing_field'}]}


def grade_json_set(outputs, expected, path, lookup):
    wanted = scalar_set(expected)  # A malformed reference remains a grading error.
    actual, error = output_value(outputs, path, lookup)
    if error:
        return error
    try:
        observed = scalar_set(actual)
    except ValueError as exc:
        return {'pass': False, 'reason': str(exc), 'failure_category': 'behavior', 'issue': 'invalid_set',
                'differences': differences(actual, expected, path or '$')}
    missing = [json.loads(v) for _, v in sorted(wanted - observed)]
    unexpected = [json.loads(v) for _, v in sorted(observed - wanted)]
    passed = not missing and not unexpected
    return {'pass': passed, 'reason': 'JSON set matches the reference' if passed else 'JSON set differs from the reference',
            'failure_category': None if passed else 'behavior', 'path': path,
            'missing_values': missing[:20], 'unexpected_values': unexpected[:20],
            'missing_count': len(missing), 'unexpected_count': len(unexpected)}


def grade_json_schema(outputs, schema, lookup):
    validator = schema_validator(schema)
    actual, error = output_value(outputs, '', lookup)
    if error:
        return error
    errors = sorted(validator.iter_errors(actual), key=lambda e: (str(list(e.absolute_path)), e.message))
    return {'pass': not errors, 'reason': 'JSON structure and types match the schema' if not errors else
            'JSON structure or types violate the schema', 'failure_category': 'behavior' if errors else None,
            'violations': [{'path': list(e.absolute_path), 'rule': e.validator, 'message': e.message[:500]}
                           for e in errors[:20]], 'violation_count': len(errors)}


def differences(actual, expected, path='$', limit=20):
    """Bound evidence size, with types preserved (true must not equal 1)."""
    found = []
    def walk(a, e, p):
        if len(found) >= limit:
            return
        if type(a) is not type(e):
            found.append({'path': p, 'issue': 'wrong_type', 'expected': e, 'actual': a})
        elif isinstance(e, dict):
            for key in sorted(set(a) | set(e)):
                child = p + '[' + json.dumps(key, ensure_ascii=False) + ']'
                if key not in a:
                    found.append({'path': child, 'issue': 'missing_field', 'expected': e[key]})
                elif key not in e:
                    found.append({'path': child, 'issue': 'unexpected_field', 'actual': a[key]})
                else:
                    walk(a[key], e[key], child)
                if len(found) >= limit:
                    break
        elif isinstance(e, list):
            if len(a) != len(e):
                found.append({'path': p, 'issue': 'array_length', 'expected': len(e), 'actual': len(a)})
            for i, (av, ev) in enumerate(zip(a, e)):
                walk(av, ev, f'{p}[{i}]')
        elif a != e:
            found.append({'path': p, 'issue': 'wrong_value', 'expected': e, 'actual': a})
    walk(actual, expected, path)
    # Long customer text still has a useful bounded excerpt rather than dropping
    # the entire failing example from the optimizer context.
    def bounded(v):
        text = json.dumps(v, ensure_ascii=False, allow_nan=False)
        return v if len(text) <= 500 else {'excerpt': text[:500], 'truncated': True}
    return [{k: bounded(v) if k in ('expected', 'actual') else v for k, v in d.items()} for d in found[:limit]]


def grade_json(outputs, expected, path, lookup):
    # Reject broken reference data before looking at the model response.
    strict_json(json.dumps(expected, allow_nan=False))
    if not isinstance(path, str):
        raise ValueError('JSON grading path must be a string')
    if not isinstance(outputs, list) or not outputs or not isinstance(outputs[-1], str) or not outputs[-1].strip():
        return {'pass': False, 'reason': 'No JSON answer was returned',
                'failure_category': 'behavior', 'issue': 'missing_output'}
    try:
        value = strict_json(outputs[-1])
    except (ValueError, TypeError) as exc:
        return {'pass': False, 'reason': 'Answer is not valid JSON: ' + str(exc)[:160],
                'failure_category': 'behavior', 'issue': 'invalid_json',
                'actual_excerpt': outputs[-1][:500]}
    try:
        actual = lookup(value, path)
    except (KeyError, IndexError, TypeError, ValueError):
        return {'pass': False, 'reason': 'Required output field is missing: ' + path,
                'failure_category': 'behavior', 'issue': 'missing_field',
                'differences': [{'path': path, 'issue': 'missing_field'}]}
    diffs = differences(actual, expected, path or '$')
    return {'pass': not diffs, 'reason': 'JSON matches the reference' if not diffs else
            'JSON differs from the reference: ' + '; '.join(d['path'] + ' ' + d['issue'] for d in diffs[:5]),
            'failure_category': None if not diffs else 'behavior', 'differences': diffs,
            'difference_limit': 20}


def unordered_value(value):
    """Canonical JSON with unordered arrays at every depth and no duplicate items.

    This is an explicit check type for record collections, not the default JSON
    equality behavior. Object keys, scalar types and values remain exact. Use
    ordered json_equals when sequence matters. Duplicate array members, including
    duplicate IDs inside records, are invalid rather than silently deduplicated.
    """
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError('JSON object keys must be strings')
        return ('object', tuple((k, unordered_value(v)) for k, v in sorted(value.items())))
    if isinstance(value, list):
        items = [unordered_value(v) for v in value]
        if len(set(items)) != len(items):
            raise ValueError('Unordered JSON arrays must not contain duplicate items')
        return ('array', tuple(sorted(items, key=repr)))
    if type(value) not in (str, int, float, bool, type(None)) or isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Unordered JSON values must be finite JSON values')
    return (type(value).__name__, json.dumps(value, ensure_ascii=False, allow_nan=False))


def grade_json_unordered(outputs, expected, path, lookup):
    expected_key = unordered_value(expected)
    actual, error = output_value(outputs, path, lookup)
    if error:
        return error
    try:
        actual_key = unordered_value(actual)
    except (TypeError, ValueError) as exc:
        return {'pass': False, 'reason': str(exc), 'failure_category': 'behavior',
                'path': path, 'issue': 'invalid_unordered_value'}
    passed = actual_key == expected_key
    outcome = {'pass': passed, 'reason': 'Unordered JSON matches the reference' if passed else
               'Unordered JSON differs from the reference', 'failure_category': None if passed else 'behavior', 'path': path}
    if not passed and isinstance(expected, list) and isinstance(actual, list):
        e = {unordered_value(v): v for v in expected}
        a = {unordered_value(v): v for v in actual}
        missing = [e[k] for k in sorted(e.keys() - a.keys(), key=repr)]
        unexpected = [a[k] for k in sorted(a.keys() - e.keys(), key=repr)]
        outcome.update(missing_values=missing[:20], unexpected_values=unexpected[:20],
                       missing_count=len(missing), unexpected_count=len(unexpected))
    elif not passed:
        outcome['differences'] = differences(actual, expected, path or '$')
    return outcome
