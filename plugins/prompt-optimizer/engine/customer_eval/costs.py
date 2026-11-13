"""Usage-based list-price estimates, distinct from reservations and invoices.

Responses input_tokens includes cache reads/writes; output_tokens already includes
reasoning. This is NOT the separate-counter Converse API accounting convention.
Missing usage or prices is unknown, never a free request.
"""
import math


def price_usage(route, usage):
    if not isinstance(usage, dict):
        return None
    details = usage.get('input_tokens_details') or {}
    counts = [usage.get('input_tokens'), usage.get('output_tokens'),
              details.get('cached_tokens', 0), details.get('cache_write_tokens', 0)]
    if any(type(n) is not int or n < 0 for n in counts):
        return None
    inputs, outputs, reads, writes = counts
    if reads + writes > inputs or inputs > route.get('pricing_max_input_tokens', float('inf')):
        return None
    tokens = [inputs - reads - writes, outputs, reads, writes]
    keys = ['input_usd_per_million', 'output_usd_per_million',
            'cached_input_usd_per_million', 'cache_write_usd_per_million']
    total = 0
    for count, key in zip(tokens, keys):
        rate = route.get(key)
        if count and (type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0):
            return None
        if count:
            total += count * rate / 1_000_000
    return total


def trial_cost(config, row):
    result = row['result']
    route = config['routes'].get(row.get('model_id'))
    if not route:
        route = next((r for r in config['routes'].values()
                      if r['model_id'] == result.get('model_requested')), {})
    calls = result.get('route_calls', [])
    if result.get('simulated') or not calls:
        return {'known_cost_usd': 0, 'estimated_cost_usd': None,
                'unpriced_requests': 1, 'request_count': len(calls)}
    values = [price_usage(route, c.get('usage')) for c in calls]
    unknown = sum(v is None for v in values)
    # Missing traces after infrastructure failure can conceal additional calls.
    if result['status'] != 'completed':
        unknown = max(1, unknown)
    known = sum(v for v in values if v is not None)
    return {'known_cost_usd': known, 'estimated_cost_usd': known if not unknown else None,
            'unpriced_requests': unknown, 'request_count': len(calls)}


def run_costs(config, records, simulated=False, *, unresolved_attempts=0):
    if type(unresolved_attempts) is not int or unresolved_attempts < 0:
        raise ValueError('unresolved_attempts must be a nonnegative integer')
    totals = {'agent': 0., 'optimizer': 0., 'judge': 0.}
    # An operation can fail after dispatch without producing a usage receipt.
    # Its reservation is retained separately; it is not a measured charge.
    unknown = unresolved_attempts
    for record in records:
        if 'trial_id' in record:
            cost = trial_cost(config, {'result': record})
            totals['agent'] += cost['known_cost_usd']
            unknown += cost['unpriced_requests']
        elif 'usage' in record and 'text' in record:
            value = price_usage(config['routes'].get(record.get('model_id'), {}), record['usage'])
            if value is None:
                unknown += 1
            else:
                totals[record.get('purpose', 'optimizer')] += value
    return {'estimated_model_cost_usd': None if unknown or simulated else sum(totals.values()),
            'known_model_cost_usd': None if simulated else sum(totals.values()),
            'by_purpose_usd': totals if not simulated else None,
            'unpriced_requests_or_incomplete_traces': unknown,
            'scope': 'Unique executed requests, including development, validation, comparison, holdout, optimizer and judges. List-price estimate; excludes infrastructure and human review.'}
