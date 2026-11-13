"""Isolated mock MCP tools shared by Codex and Strands. No model or judge here."""
from __future__ import annotations

import functools
import json
import sys
import importlib
import inspect
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_server(workload, initial_state, receipt_path, context_path=None):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations
    server = MCPServer('customer-workload')
    calls = []
    lock = threading.RLock()
    if workload == 'travel':
        from agent import travel_agent as travel
        travel.reset_state()
        travel.bookings.update(initial_state.get('bookings', {}))
        travel.booking_counter = initial_state.get('booking_counter', len(travel.bookings))
        functions = [getattr(tool, '__wrapped__', tool) for tool in travel.ALL_TOOLS]
        state = lambda: {'bookings': travel.bookings}
    elif workload == 'customer_support':
        orders = dict(initial_state.get('orders', {}))
        lookup_errors = {k: list(v) for k, v in initial_state.get('lookup_errors', {}).items()}

        def lookup_order(order_id: str) -> dict:
            """Look up an order's age and whether it was opened; this tool cannot issue refunds."""
            if lookup_errors.get(order_id):
                return {'error': lookup_errors[order_id].pop(0)}
            return orders.get(order_id, {'error': 'order_not_found'})

        functions = [lookup_order]
        state = lambda: {'orders': orders}
    elif workload == 'none':
        functions, state = [], lambda: {}
    else:
        from customer_eval.workloads import CUSTOM_WORKLOADS
        registration = CUSTOM_WORKLOADS[workload]
        functions, state = importlib.import_module(registration['module']).create(initial_state)
        if set(f.__name__ for f in functions) != set(registration['tool_names']):
            raise ValueError('Custom tool names do not match the reviewed registration')

    def persist():
        from customer_eval.conversation import write_json
        write_json(receipt_path, {'tool_calls': calls, 'state': state()})

    def traced(function):
        @functools.wraps(function)
        def invoke(*args, **kwargs):
            with lock:
                bound = inspect.signature(function).bind(*args, **kwargs)
                bound.apply_defaults()
                index = json.loads(Path(context_path).read_text())['turn_index'] if context_path else None
                if context_path and (type(index) is not int or index < 1):
                    raise ValueError('Tool invocation outside an active turn')
                call = {'call_id': f'tool-{len(calls)+1}', 'turn_index': index,
                        'started_ns': time.monotonic_ns(), 'name': function.__name__,
                        'arguments': dict(bound.arguments), 'status': 'error'}
                try:
                    result = function(*args, **kwargs)
                    call.update(result=result, status='completed')
                    return result
                except Exception as exc:
                    call['error_type'] = type(exc).__name__
                    raise
                finally:
                    call['ended_ns'] = time.monotonic_ns()
                    calls.append(call)
                    persist()
        return invoke

    for function in functions:
        server.add_tool(traced(function), annotations=ToolAnnotations(
            readOnlyHint=function.__name__ in ('lookup_order', 'search_flights', 'search_hotels', 'get_bookings'),
            destructiveHint=False, openWorldHint=False))
    persist()
    return server


if __name__ == '__main__':
    settings = json.loads(Path(sys.argv[1]).read_text())
    make_server(settings['workload'], settings['initial_state'], settings['receipt_path'], settings.get('context_path')).run()
