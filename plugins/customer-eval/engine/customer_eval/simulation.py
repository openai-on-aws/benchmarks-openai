"""Explicit, deterministic workflow simulation. Never a Codex/model quality result."""
import json
import re


def execute(payload):
    outputs, calls, state = [], [], dict(payload['initial_state'])
    for turn in payload['turns']:
        if payload['workload'] == 'travel':
            outputs.append('I can help with flights and hotels. Please confirm before booking.')
        else:
            match = re.search(r'order ([A-Z0-9-]+)', turn)
            order = state.get('orders', {}).get(match[1], {}) if match else {}
            if match: calls.append({'name': 'lookup_order', 'arguments': {'order_id': match[1]}, 'result': order})
            age = order.get('age_days', 999)
            # Illustrative defect in the seed behavior; revised candidate applies opened-item rule.
            denied = age > 30 or ('check opened status' in payload['prompt'] and order.get('opened', False))
            outputs.append(json.dumps({'decision': 'deny' if denied else 'allow'}))
    return {'trial_id': payload['trial_id'], 'status': 'completed', 'outputs': outputs,
            'tool_calls': calls, 'state': state, 'latency_ms': 1, 'simulated': True,
            'harness': 'simulation', 'model_requested': payload['route']['model_id']}


def mutate(incumbent, feedback, k):
    return [incumbent + f'\ncheck opened status; simulation candidate {i+1}.' for i in range(k)]
