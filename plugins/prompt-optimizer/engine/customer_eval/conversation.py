"""Trusted per-turn receipts, collected outside the model and shared by both harnesses."""
from contextlib import contextmanager
import json
import time
from pathlib import Path


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


class ConversationRecorder:
    def __init__(self, result, receipt, context):
        self.result, self.receipt, self.context = result, Path(receipt), Path(context)
        result.update(trace_version='turns-v1', turn_records=[])

    def refresh(self):
        if self.receipt.exists():
            receipt = json.loads(self.receipt.read_text())
            self.result['tool_calls'] = receipt['tool_calls']
            self.result['state'] = receipt['state']

    @contextmanager
    def turn(self, index, user):
        record = dict(turn_index=index, user=user, started_ns=time.monotonic_ns(),
                      output=None, status='error', tool_calls=[])
        write_json(self.context, {'turn_index': index})
        try:
            yield record
            if not isinstance(record['output'], str) or not record['output'].strip():
                raise ValueError('Empty assistant response')
            record['answered_ns'] = time.monotonic_ns()
            record['status'] = 'completed'
        finally:
            self.refresh()
            record['tool_calls'] = [c for c in self.result['tool_calls'] if c['turn_index'] == index]
            record['ended_ns'] = time.monotonic_ns()
            self.result['turn_records'].append(record)
            if record['output'] is not None:
                self.result['outputs'].append(record['output'])
            write_json(self.context, {'turn_index': None})
