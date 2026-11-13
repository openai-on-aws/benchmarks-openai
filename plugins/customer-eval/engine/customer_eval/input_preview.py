"""Customer-readable coverage and exact outbound request previews."""
from collections import Counter
from html import escape
import json
from pathlib import Path

from .replay import message_text, request_body, validate_replay
from .transport import constrain

REMEDIES = {
    'tool_calls_unsupported': ('This record uses tools.', 'Use the application/agent adapter, or supply a text-only request; replay cannot execute the tools.'),
    'unsupported_message_or_tool_history': ('The message history contains tools or unsupported fields.', 'Use the application adapter; do not remove tool results and call the request equivalent.'),
    'unsupported_or_empty_content': ('A message is empty or contains a file, image, or other non-text content.', 'Supply extracted text to test the text stage, or add a document/application adapter to test the original input.'),
    'unsupported_request_setting': ('The request contains a setting this replay runner cannot preserve.', 'Inspect the original request and add a supported adapter or explicitly revise the experiment.'),
    'missing_request_or_response': ('The full request or completed response is missing.', 'Export both request and response bodies, or use a manual input with an expected answer.'),
    'missing_messages': ('The request messages are missing.', 'Export the full request body, including instructions and conversation history.'),
    'invalid_replay_or_missing_prompt': ('No usable editable instruction was found, or the conversation does not end with a user message.', 'Supply the prompt and complete request, then choose its instruction slot.'),
    'conflicting_reference': ('The same request has conflicting expected answers.', 'Review the references and resolve the disagreement before including this request.'),
    'duplicate_request': ('This request is already included.', 'Keep one copy; use configured repetitions rather than duplicate logs.'),
    'duplicate_case_id': ('Another record has the same ID.', 'Use unique IDs and check whether these are duplicate exports.'),
    'malformed_json': ('This row is not valid JSON.', 'Repair or re-export the row; JSONL requires one complete JSON object per line.'),
    'duplicate_json_key': ('An object repeats the same key.', 'Resolve the duplicate key; the importer does not choose one value silently.'),
    'incomplete_or_failed_response': ('The recorded response failed or was incomplete.', 'Keep it for operational failure analysis; provide a complete request and reference for replay.'),
    'incomplete_or_tool_response': ('The recorded response was cut off or requested a tool.', 'Supply the complete response or use an application adapter.'),
}


def instructions(case):
    body = case['replay']['request']
    slots = ([{'location': 'instructions', 'role': 'instructions', 'text': body['instructions']}]
             if body.get('instructions') else [])
    slots += [{'location': i, 'role': m['role'], 'text': message_text(m)}
              for i, m in enumerate(body['input']) if m['role'] in ('system', 'developer')]
    return slots


def exclusion_details(report):
    return [dict(row, explanation=REMEDIES.get(row['reason'], (
        'The record could not be imported faithfully (' + row['reason'] + ').',
        'Inspect this source row and export the complete request/response in a supported format.'))[0],
        remedy=REMEDIES.get(row['reason'], ('', 'Inspect this source row and its request/response fields.'))[1])
        for row in report['excluded_records']]


def html_document(title, body):
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>' + escape(title) + '</title><style>body{font:16px/1.55 system-ui;max-width:1080px;margin:32px auto;padding:0 24px;color:#202523}'
            'h1{font-size:32px}h2{margin-top:32px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f3;padding:18px;border-radius:8px}'
            'table{border-collapse:collapse;width:100%}th,td{padding:10px;border-bottom:1px solid #ccd5ce;text-align:left;vertical-align:top}'
            'details{margin:14px 0;border:1px solid #d5ddd7;padding:12px;border-radius:8px}summary{cursor:pointer;font-weight:600}'
            '.note{background:#eef3ef;padding:16px;border-left:4px solid #5b7465}</style><body>'
            '<h1>' + escape(title) + '</h1>' + body + '</body></html>')


def write_preview(directory, cases, report, *, config=None):
    directory = Path(directory)
    details = exclusion_details(report)
    groups = Counter(c['metadata']['prompt_group'] for c in cases)
    rows = []
    # Every selected request is available, including its full retained history.
    for case in cases:
        slots = instructions(case)
        item = {'id': case['id'], 'source_file': case['metadata']['source_file'],
                'source_path': case['metadata'].get('source_path'),
                'source_row': case['metadata']['source_row'], 'prompt_group': case['metadata']['prompt_group'],
                'instruction_slots': slots, 'editable_location': case['replay']['prompt_location'],
                'history_messages': len(case['replay']['request']['input']),
                'captured_controls': case['metadata'].get('captured_controls', {}),
                'reference_kind': case['metadata']['reference_kind'],
                'label_status': case['metadata']['label_status']}
        if config:
            item['outbound_requests'] = {}
            item['settings_changes'] = {}
            for name in ('source', 'target'):
                variant = config[name]
                route = config['routes'][variant['model_id']]
                body = constrain(request_body(case['replay'], config['prompt']), route,
                                 config['document']['runner']['limits'])
                item['outbound_requests'][name] = body
                # Import removes routing/storage controls from the normalized
                # request, but retains their actual captured values in metadata.
                original = dict(case['replay']['request'], **case['metadata'].get('captured_controls', {}))
                item['settings_changes'][name] = {k: {'captured': original.get(k), 'outbound': v}
                    for k, v in body.items() if k not in ('input', 'instructions') and original.get(k) != v}
        else:
            item['captured_request'] = case['replay']['request']
        rows.append(item)
    preview = {'coverage': {k: report[k] for k in ('found', 'accepted', 'dropped', 'reconciles')},
               'prompt_groups': dict(groups), 'excluded_records': details, 'cases': rows,
               'data_flow': [
                   'The import and draft are local files. Codex processes the material you provide or let it read.',
                   'A live AgentCore run uploads the selected requests, grading references and configuration to your configured S3 bucket.',
                   'Candidate models receive the request bodies shown below; expected answers and grading rules are not included.',
                   'The optimizer receives the editable prompt, supplied general business rules and development-case feedback. Validation and holdout cases are withheld from it.',
                   'Runtime results and traces are saved in the same customer S3 prefix.'] }
    (directory / 'input-preview.json').write_text(json.dumps(preview, indent=2, ensure_ascii=False))
    lines = ['# Input preview', '', f"{report['accepted']} of {report['found']} records can be replayed; {report['dropped']} excluded.",
             'Excluded records remain outside the measured quality denominator; they are not counted as passing.', '',
             'The run evaluates one new answer using recorded history. It does not regenerate earlier assistant messages.',
             '', '## Data sent and stored', ''] + ['- ' + line for line in preview['data_flow']]
    body = '<p class="note">' + escape(lines[2] + ' ' + lines[3]) + '</p>'
    body += '<p>' + escape(lines[5]) + '</p><h2>Data sent and stored</h2><ul>' + ''.join('<li>' + escape(x) + '</li>' for x in preview['data_flow']) + '</ul>'
    body += '<h2>Records to repair or exclude</h2>'
    for item in details:
        text = f"{item.get('source_path', item['source_file'])} row {item['source_row']}: {item['explanation']} {item['remedy']}"
        lines += ['', '- ' + text]
        body += '<p>' + escape(text) + '</p>'
    body += '<h2>Requests and instruction scope</h2>'
    for item in rows:
        lines += ['', f"## {item['id']} — {item['source_file']} row {item['source_row']}",
                  f"Editable slot: {item['editable_location']}; retained messages: {item['history_messages']}.",
                  'All other instruction slots remain protected.',
                  'Full captured/outbound request and instruction text: input-preview.json or input-preview.html.']
        body += '<details><summary>' + escape(str(item['id'])) + ' — editable slot ' + escape(str(item['editable_location'])) + '</summary><pre>' + escape(json.dumps(item, indent=2, ensure_ascii=False)) + '</pre></details>'
    (directory / 'input-preview.md').write_text('\n'.join(lines) + '\n')
    (directory / 'input-preview.html').write_text(html_document('Input preview', body))
    return preview
