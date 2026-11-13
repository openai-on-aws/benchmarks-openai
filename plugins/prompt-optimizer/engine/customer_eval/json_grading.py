"""JSON output failures are model behavior; invalid references are grader errors."""
import json
from .turn_grading import strict_json


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
