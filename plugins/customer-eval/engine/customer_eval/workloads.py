"""Reviewed workload registrations shared by both harnesses.

Custom modules expose create(initial_state) -> (plain functions, snapshot callable).
Config files select registrations; they cannot import arbitrary code or commands.
"""
TOOL_NAMES = {
    'none': [],
    'customer_support': ['lookup_order'],
    'travel': ['search_flights', 'search_hotels', 'book_flight', 'book_hotel', 'get_bookings', 'cancel_booking'],
}
# Example: 'returns': {'module': 'customer_workloads.returns', 'tool_names': ['lookup_order']}
CUSTOM_WORKLOADS = {}


def names(workload):
    if workload in TOOL_NAMES: return TOOL_NAMES[workload]
    if workload in CUSTOM_WORKLOADS: return CUSTOM_WORKLOADS[workload]['tool_names']
    raise ValueError('Unknown workload; register its reviewed tools before execution')
