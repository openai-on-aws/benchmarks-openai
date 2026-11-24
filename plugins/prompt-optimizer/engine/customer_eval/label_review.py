"""Bind a person's label-review attestation to specific inputs and answers.

This records review provenance; it does not authenticate a reviewer or turn an
automated judgment into human review. Templates never assert completed review.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


def _digest(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def _identities(cases):
    identities, seen = [], set()
    for case in cases:
        ident = case.get('id')
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError('Label review needs nonempty, unique case IDs')
        if 'expected' not in case:
            raise ValueError('Label review needs finalized expected answers for ' + ident)
        seen.add(ident)
        # Include the actual replay body, not just its potentially stale digest.
        inputs = {key: case[key] for key in ('turns', 'replay') if key in case}
        inputs['source_sha256'] = case.get('metadata', {}).get('source_sha256')
        identities.append({'case_id': ident, 'input_sha256': _digest(inputs),
                           'expected_sha256': _digest(case['expected'])})
    return sorted(identities, key=lambda item: item['case_id'])


def review_template(cases, *, dataset_id='selected-cases'):
    """Export review targets. A human must supply the attestation and outcomes."""
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError('Review dataset_id must be a nonempty string')
    identities = _identities(cases)
    return {'version': 1, 'dataset': {'id': dataset_id, 'sha256': _digest(identities)},
            'reviewer': {'name': None, 'type': None}, 'reviewed_at': None,
            'attested': False,
            'reviews': [dict(item, outcome='pending', notes='') for item in identities]}


def write_review_template(cases, path, *, dataset_id='selected-cases'):
    """Write once; never overwrite a person's existing review file."""
    path = Path(path)
    value = review_template(cases, dataset_id=dataset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    return path


def dataset_label_status(cases):
    """A partially reviewed dataset remains unreviewed in the intake schema."""
    return ('human_reviewed' if cases and all(
        c.get('metadata', {}).get('label_status') == 'human_reviewed' for c in cases)
        else 'unreviewed')


def _load_review(review_file):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate label review field: ' + key)
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError('Nonfinite number in label review: ' + value)

    if isinstance(review_file, dict):
        return deepcopy(review_file)
    return json.loads(Path(review_file).read_text(), object_pairs_hook=pairs,
                      parse_constant=invalid_constant)


def _validate(value):
    if not isinstance(value, dict) or set(value) != {
            'version', 'dataset', 'reviewer', 'reviewed_at', 'attested', 'reviews'}:
        raise ValueError('Label review must contain version, dataset, reviewer, reviewed_at, attested and reviews')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Unsupported label review version')
    dataset, reviewer = value['dataset'], value['reviewer']
    if (not isinstance(dataset, dict) or set(dataset) != {'id', 'sha256'}
            or not isinstance(dataset['id'], str) or not dataset['id'].strip()
            or not isinstance(dataset['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', dataset['sha256'])):
        raise ValueError('Label review dataset needs its ID and SHA256')
    if (not isinstance(reviewer, dict) or set(reviewer) != {'name', 'type'}
            or reviewer['type'] not in (None, 'human', 'automated', 'simulated')
            or (reviewer['name'] is not None and not isinstance(reviewer['name'], str))):
        raise ValueError('Label review reviewer needs name and type (human, automated or simulated)')
    if type(value['attested']) is not bool:
        raise ValueError('Label review attested must be a boolean')
    timestamp = value['reviewed_at']
    if timestamp is not None:
        try:
            parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('timezone missing')
        except (AttributeError, TypeError, ValueError):
            raise ValueError('Label review reviewed_at needs an ISO8601 timestamp with timezone') from None
    if value['attested'] and (not reviewer['name'] or not reviewer['name'].strip() or not timestamp):
        raise ValueError('A review attestation needs a named reviewer and reviewed_at timestamp')
    if not isinstance(value['reviews'], list):
        raise ValueError('Label review reviews must be a list')
    seen = set()
    for item in value['reviews']:
        if (not isinstance(item, dict) or set(item) - {'case_id', 'input_sha256', 'expected_sha256', 'outcome', 'notes'}
                or not {'case_id', 'input_sha256', 'expected_sha256', 'outcome'} <= set(item)):
            raise ValueError('Each label review needs case_id, input_sha256, expected_sha256 and outcome')
        ident = item['case_id']
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError('Label review contains missing or duplicate case IDs')
        seen.add(ident)
        if any(not isinstance(item[key], str) or not re.fullmatch('[a-f0-9]{64}', item[key])
               for key in ('input_sha256', 'expected_sha256')):
            raise ValueError('Label review needs input and expected SHA256 hashes for ' + ident)
        if item['outcome'] not in ('pending', 'accepted', 'rejected'):
            raise ValueError('Label review outcome must be pending, accepted or rejected')
        if 'notes' in item and not isinstance(item['notes'], str):
            raise ValueError('Label review notes must be text')


def apply_review(cases, review_file=None, *, dataset_id='selected-cases'):
    """Return copied cases and scope summary; stale/nonhuman attestations fail closed.

    Malformed/unknown case records raise ValueError. Otherwise issues describe
    why a supplied review cannot be used; intake must resolve those issues
    before preparation, or explicitly remove the review to run as unreviewed.
    Rejected answers also remain unreviewed and are reported as blockers.
    """
    cases = deepcopy(cases)
    current = review_template(cases, dataset_id=dataset_id)
    index = {item['case_id']: item for item in current['reviews']}
    for case in cases:
        meta = case.setdefault('metadata', {})
        meta['label_status'] = 'unreviewed'
        meta.pop('label_review', None)
    summary = {'dataset': current['dataset'], 'label_status': 'unreviewed',
               'reviewed_case_ids': [], 'unreviewed_case_ids': sorted(index),
               'rejected_case_ids': [], 'issues': [], 'review_sha256': None}
    if review_file is None:
        return cases, summary
    review = _load_review(review_file)
    _validate(review)
    summary['review_sha256'] = _digest(review)
    summary['reviewer'] = review['reviewer']
    summary['reviewed_at'] = review['reviewed_at']
    unknown = {item['case_id'] for item in review['reviews']} - set(index)
    if unknown:
        raise ValueError('Label review contains unknown case IDs: ' + ', '.join(sorted(unknown)))
    if review['dataset'] != current['dataset']:
        summary['issues'].append('Label review dataset identity or hash is stale; review the current inputs and expected answers.')
    if review['reviewer']['type'] != 'human' or not review['attested']:
        summary['issues'].append('Label review is not an attested human review; automated, simulated and pending reviews do not promote labels.')
    by_case = {item['case_id']: item for item in review['reviews']}
    invalid = set()
    for ident, item in by_case.items():
        for key in ('input_sha256', 'expected_sha256'):
            if item[key] != index[ident][key]:
                invalid.add(ident)
                summary['issues'].append('Label review ' + key + ' is stale for ' + ident + '.')
    # A dataset hash mismatch invalidates the entire attestation, rather than
    # silently applying the old scope to a changed selection.
    valid_attestation = (review['dataset'] == current['dataset']
                         and review['reviewer']['type'] == 'human' and review['attested'])
    for case in cases:
        ident = case['id']
        item = by_case.get(ident)
        if not item or ident in invalid or not valid_attestation or item['outcome'] == 'pending':
            continue
        case['metadata']['label_review'] = {
            'dataset': review['dataset'], 'reviewer': review['reviewer']['name'],
            'reviewer_type': 'human', 'reviewed_at': review['reviewed_at'],
            'input_sha256': item['input_sha256'], 'expected_sha256': item['expected_sha256'],
            'outcome': item['outcome'], 'notes': item.get('notes', ''),
            'review_sha256': summary['review_sha256']}
        if item['outcome'] == 'accepted':
            case['metadata']['label_status'] = 'human_reviewed'
            summary['reviewed_case_ids'].append(ident)
        else:
            summary['rejected_case_ids'].append(ident)
            summary['issues'].append('Expected answer rejected by the reviewer for ' + ident + '; repair it and obtain a new review.')
    summary['reviewed_case_ids'].sort()
    summary['rejected_case_ids'].sort()
    summary['unreviewed_case_ids'] = sorted(set(index) - set(summary['reviewed_case_ids']))
    summary['label_status'] = dataset_label_status(cases)
    return cases, summary
