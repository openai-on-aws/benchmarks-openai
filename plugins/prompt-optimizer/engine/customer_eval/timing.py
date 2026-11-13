"""Observed client timing; never label network-inclusive requests as model compute."""
import math
import statistics


def distribution(values, planned):
    values = [v for v in values if type(v) in (int, float) and math.isfinite(v) and v >= 0]
    return {'observations': len(values), 'planned': planned,
            'mean_ms': statistics.mean(values) if values else None,
            'p50_ms': statistics.median(values) if values else None,
            'p95_ms': sorted(values)[math.ceil(.95 * len(values)) - 1] if values else None}


def finish_worker_timing(result, started_ns, ended_ns):
    turns = result.get('turn_records', [])
    timing = {'version': 'client-timing-v1', 'worker_ms': (ended_ns-started_ns)/1e6,
              'setup_ms': None, 'conversation_ms': None, 'teardown_ms': None,
              'scope': 'Fresh isolated harness per conversation. Conversation is first user turn start through last answer; excludes harness setup/teardown and human think time. Provider duration includes transport and provider processing.'}
    if result.get('status', 'completed') == 'completed' and turns and all(t.get('answered_ns') is not None for t in turns):
        first, last = turns[0]['started_ns'], turns[-1]['answered_ns']
        timing.update(setup_ms=(first-started_ns)/1e6, conversation_ms=(last-first)/1e6,
                      teardown_ms=(ended_ns-last)/1e6)
    result['timing'] = timing
    result['latency_ms'] = timing['worker_ms']  # Backward-compatible, explicitly worker scope.


def summarize_timing(rows):
    results = [r['result'] for r in rows]
    calls = [c for r in results for c in r.get('route_calls', [])]
    turns = [turn for result in results for turn in result.get('turn_records') or []
             if isinstance(turn, dict) and type(turn.get('turn_index')) is int]
    planned = len(results)
    out = {key: distribution([r.get('timing', {}).get(key) for r in results], planned)
           for key in ('setup_ms', 'teardown_ms')}
    # Older completed results recorded worker elapsed time only as latency_ms.
    # Timeout envelopes in failed legacy results are not elapsed measurements.
    out['worker_ms'] = distribution([r.get('timing', {}).get('worker_ms',
        r.get('latency_ms') if r.get('status') == 'completed' else None) for r in results], planned)
    out['conversation_ms'] = distribution([r.get('timing', {}).get('conversation_ms')
        for r in results if r.get('status') == 'completed'], planned)
    # Fetching a durable receipt after a lost connection measures recovery,
    # not the original dispatch. A duplicate trial action can also hit a receipt.
    dispatches, recoveries = [], []
    for result in results:
        dispatch = result.get('dispatch_timing', {})
        if dispatch.get('action') == 'trial_result' or result.get('receipt_recovered'):
            recoveries.append(dispatch.get('client_ms'))
        elif dispatch.get('action') == 'trial':
            dispatches.append(dispatch.get('client_ms'))
    out['dispatch_ms'] = distribution(dispatches, planned)
    out['receipt_lookup_ms'] = distribution(recoveries, len(recoveries))
    for key in ('auth_ms', 'provider_ms', 'headers_ms', 'first_event_ms', 'first_output_ms'):
        out[key] = distribution([c.get('timing', {}).get(key) for c in calls], len(calls))
    def elapsed_ms(receipt, end_key):
        start, end = receipt.get('started_ns'), receipt.get(end_key)
        if type(start) is int and type(end) is int and end >= start >= 0:
            return (end - start) / 1e6
        return None

    for position in ('first', 'followup'):
        selected = [t for t in turns if (t['turn_index'] == 1) == (position == 'first')]
        out[position+'_turn_ms'] = distribution([elapsed_ms(t, 'answered_ns') for t in selected], len(selected))
    tools = [call for result in results for call in result.get('tool_calls') or [] if isinstance(call, dict)]
    out['tool_ms'] = distribution([elapsed_ms(call, 'ended_ns') for call in tools], len(tools))
    out['scope'] = 'Provider timings include network and provider processing, not pure model compute. First-output timing includes tool arguments. First and followup turns are different workloads, not a controlled cold/warm speed comparison.'
    return out
