#!/usr/bin/env python3
"""Preview one Codex agent invocation. --execute is an explicit, optional live smoke test."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/eval-intake/scripts'))
from input_framework import load_document, local_path, validate


def prepare(config_path, variant_id, dataset_id, case_id):
    config_path = Path(config_path).resolve()
    doc = load_document(config_path)
    base = config_path.parent
    report = validate(doc, base, check_assets=True)
    if not report['valid']:
        raise ValueError('Configuration is invalid; run the validator for details')
    collections = {k: {x['id']: x for x in doc[k]} for k in ('variants', 'models', 'agents', 'prompts', 'datasets')}
    v = collections['variants'][variant_id]
    m, a, p = (collections[group][v[field]] for group, field in [('models','model_id'), ('agents','agent_id'), ('prompts','prompt_id')])
    if (m['provider'] != 'openai' or m['api'] != 'responses' or m['parameters'] or m['endpoint_env']
            or v['route'] != 'direct_model_api' or a['harness'] != 'codex' or a['runtime'] != 'local'
            or a['gateway_id'] or a['codex_profile'] or a['sandbox'] != 'read_only'
            or a['side_effects'] != 'read_only' or a['tool_allowlist']):
        raise ValueError('This smoke-test wrapper supports only a local, read-only Codex agent using the default OpenAI endpoint and no Gateway/profile overrides. Other routes are input-contract only.')
    if doc['optimization']['enabled']:
        raise ValueError('Prompt optimization belongs to the evaluation runner, not this smoke test')
    model = m['model_id'] or os.environ.get(m['model_id_env'] or '')
    if model and (model.startswith('-') or not re.fullmatch(r'[A-Za-z0-9_.:/-]+', model)):
        raise ValueError('Invalid resolved model identifier')
    dataset = collections['datasets'][dataset_id]
    if not all([dataset['path'], p['path'], a['workspace'], a['instructions_path']]):
        raise ValueError('Supply dataset, prompt, workspace and instructions paths before previewing the agent')
    rows = [json.loads(line) for line in local_path(base, dataset['path']).read_text().splitlines() if line.strip()]
    row = next((r for r in rows if r['id'] == case_id), None)
    if row is None:
        raise ValueError('Case was not found')
    template = local_path(base, p['path']).read_text()
    task = re.sub(r'{{\s*([a-z][a-z0-9_]*)\s*}}',
                  lambda match: str(row['input'][match.group(1)]), template)
    workspace = local_path(base, a['workspace'])
    instructions = local_path(base, a['instructions_path'])
    if instructions != workspace/'AGENTS.md':
        raise ValueError('This example requires instructions in the agent workspace AGENTS.md')
    if {f.name for f in workspace.iterdir()} != {'AGENTS.md', 'policy.md'}:
        raise ValueError('This example only copies AGENTS.md and policy.md into the agent workspace')
    schema_path = local_path(base, 'response.schema.json')
    output_schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(output_schema)
    return {'doc': doc, 'variant_id': variant_id, 'case_id': case_id,
            'model': model, 'model_env': m['model_id_env'], 'task': task,
            'workspace': workspace, 'output_schema': output_schema, 'validation': report}


def command_for(plan, workspace, output_dir):
    return ['codex', 'exec', '--ignore-user-config', '--skip-git-repo-check',
            '--ephemeral', '--sandbox', 'read-only', '-c', 'model_provider="openai"',
            '--model', plan['model'], '--cd', str(workspace), '--json',
            '--output-schema', str(output_dir/'response.schema.json'),
            '--output-last-message', str(output_dir/'response.json'), '-']


def execute(plan, output_parent):
    if not plan['model']:
        raise ValueError('Set the declared model environment variable before execution')
    timeout = plan['doc']['execution']['timeout_seconds']
    if timeout is None:
        raise ValueError('Set timeout_seconds before execution')
    if not shutil.which('codex'):
        raise ValueError('Codex CLI is not installed')
    out = Path(output_parent).resolve()/('smoke-' + uuid.uuid4().hex[:12])
    out.mkdir(parents=True, exist_ok=False)
    (out/'response.schema.json').write_text(json.dumps(plan['output_schema']))
    # Only the task, policy and instructions enter the agent's temporary working directory.
    # Filesystem read-only access is not a benchmark-grade data-isolation boundary.
    with tempfile.TemporaryDirectory(prefix='eval-codex-agent-') as directory:
        workspace = Path(directory)
        for name in ('AGENTS.md', 'policy.md'):
            shutil.copyfile(plan['workspace']/name, workspace/name)
        cmd = command_for(plan, workspace, out)
        start = time.monotonic()
        status, code = 'process_error', None
        with (out/'events.jsonl').open('w') as events, (out/'stderr.txt').open('w') as stderr:
            process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=events, stderr=stderr,
                                       text=True, start_new_session=True)
            try:
                process.communicate(plan['task'], timeout=timeout)
                code = process.returncode
                status = 'completed' if code == 0 else 'process_error'
            except subprocess.TimeoutExpired:
                import signal
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.communicate()
                status = 'timeout'
        response_valid = False
        response = out/'response.json'
        if response.is_file():
            try:
                parsed = load_document(response)
                response_valid = Draft202012Validator(plan['output_schema']).is_valid(parsed)
            except (ValueError, TypeError):
                pass
        if status == 'completed' and not response_valid:
            status = 'invalid_output'
        record = {'run_id': out.name, 'variant_id': plan['variant_id'], 'case_id': plan['case_id'],
                  'requested_model': plan['model'], 'actual_serving_model': None,
                  'actual_model_status': 'not independently verified; retain provider telemetry',
                  'status': status, 'exit_code': code,
                  'elapsed_ms': round((time.monotonic()-start)*1000, 2),
                  'output_schema_valid': response_valid, 'quality_passed': None, 'cost_usd': None,
                  'note': 'Smoke test only. Completion is not a quality pass. Cost is unknown, not zero; budget_usd is not enforced by this wrapper.',
                  'task_sha256': hashlib.sha256(plan['task'].encode()).hexdigest()}
        (out/'run.json').write_text(json.dumps(record, indent=2)+'\n')
    return out, record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--variant', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--execute', action='store_true', help='Call Codex; uses existing account credentials and may incur cost')
    parser.add_argument('--output', type=Path, default=ROOT/'runs')
    args = parser.parse_args(argv)
    try:
        plan = prepare(args.config, args.variant, args.dataset, args.case)
        if not args.execute:
            print(json.dumps({'mode': 'preview', 'model': plan['model'], 'model_environment_variable': plan['model_env'],
                              'variant': plan['variant_id'], 'case': plan['case_id'], 'agent_task': plan['task'],
                              'agent_files': ['AGENTS.md','policy.md'], 'harness': 'codex',
                              'target_runtime': 'local', 'expected_answers_in_agent_task': False,
                              'model_called': False, 'open_items': plan['validation']['open_items']}, indent=2))
            return 0
        out, record = execute(plan, args.output)
        print(json.dumps({'output': str(out), **record}, indent=2))
        return 0 if record['status'] == 'completed' else 1
    except (ValueError, KeyError, OSError) as error:
        print(f'Cannot prepare demo: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
