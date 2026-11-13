"""A single isolated conversation, reusable locally and inside AgentCore Runtime."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from importlib.metadata import version
from pathlib import Path

from .contract import ROOT, implementation_fingerprint
from .transport import relay
from .workloads import names
from .conversation import ConversationRecorder
from .timing import finish_worker_timing


def execute(payload):
    started_ns = time.monotonic_ns()
    result = {'trial_id': payload['trial_id'], 'status': 'error', 'outputs': [], 'trace': [],
              'tool_calls': [], 'state': {}, 'route_calls': [], 'simulated': False,
              'harness': payload['harness'], 'model_requested': payload['route']['model_id']}
    try:
        if payload.get('implementation_fingerprint', implementation_fingerprint()) != implementation_fingerprint():
            raise ValueError('Worker code differs from the frozen run implementation')
        with tempfile.TemporaryDirectory(prefix='customer-eval-') as temporary:
            root = Path(temporary)
            workspace = root / 'workspace'
            workspace.mkdir()
            tool_settings = root / 'tools.json'
            receipt = root / 'receipt.json'
            context = root / 'turn-context.json'
            recorder = ConversationRecorder(result, receipt, context)
            tool_settings.write_text(json.dumps({'workload': payload['workload'],
                                                 'initial_state': payload['initial_state'],
                                                 'receipt_path': str(receipt), 'context_path': str(context)}))
            command = [sys.executable, str(ROOT / 'customer_eval/tools_server.py'), str(tool_settings)]
            instructions = payload['policy'] + '\n\n' + payload['prompt']
            route = dict(payload['route'])
            route['allowed_tool_names'] = names(payload['workload'])
            with relay(route, payload['limits']) as (url, token, calls):
                result['route_calls'] = calls
                if payload['harness'] == 'direct':
                    replay = payload.get('replay')
                    history = [{'role': 'developer', 'content': instructions}]
                    for index, user_turn in enumerate(payload['turns'], 1):
                        with recorder.turn(index, user_turn) as record:
                            if replay:
                                from .replay import request_body, fingerprint
                                from .transport import constrain
                                if len(payload['turns']) != 1:
                                    raise ValueError('A captured request is one evaluated continuation')
                                request = request_body(replay, payload['prompt'], payload['policy'])
                                effective = constrain(request, route, payload['limits'])
                                result['replay'] = {'captured_request_sha256': replay['request_sha256'],
                                    'effective_request_sha256': fingerprint(effective),
                                    'settings_overrides': {k: {'captured': replay['request'].get(k), 'effective': v}
                                        for k, v in effective.items() if k not in ('input', 'instructions', 'tools')
                                        and replay['request'].get(k) != v}}
                            else:
                                history.append({'role': 'user', 'content': user_turn})
                                request = {'model': payload['route']['model_id'], 'input': history}
                            body = json.dumps(request).encode()
                            with urllib.request.urlopen(urllib.request.Request(
                                    url + '/responses', data=body,
                                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})) as response:
                                output = json.load(response)
                            if output.get('status') != 'completed' or any(i.get('type') not in ('message', 'reasoning') for i in output.get('output', [])):
                                raise ValueError('Incomplete or unsupported direct model response')
                            record['output'] = ''.join(c.get('text', '') for item in output.get('output', [])
                                           for c in item.get('content', []) if c.get('type') == 'output_text')
                            if not replay:
                                history.extend(output['output'])
                elif payload['harness'] == 'codex':
                    from openai_codex import Codex, CodexConfig, Sandbox, ApprovalMode
                    from openai_codex.client import _resolve_codex_bin
                    # Pinned SDK binary; ignore user MCP servers, plugins and profile settings.
                    binary = str(_resolve_codex_bin(CodexConfig()))
                    args = [binary]
                    overrides = {
                        'model_provider': 'evaluation',
                        'model_providers.evaluation.name': 'Evaluation route',
                        'model_providers.evaluation.base_url': url,
                        'model_providers.evaluation.env_key': 'EVAL_RELAY_TOKEN',
                        'model_providers.evaluation.wire_api': 'responses',
                        'model_providers.evaluation.requires_openai_auth': False,
                        'model_providers.evaluation.request_max_retries': 0,
                        'model_providers.evaluation.stream_max_retries': 0,
                        'model_providers.evaluation.supports_websockets': False,
                        'features.shell_tool': False, 'features.apply_patch_freeform': False,
                        'features.apps': False, 'features.multi_agent': False,
                        'web_search': 'disabled',
                    }
                    if payload['workload'] != 'none':
                        overrides.update({'mcp_servers.customer.command': command[0],
                                          'mcp_servers.customer.args': command[1:],
                                          'mcp_servers.customer.required': True,
                                          'mcp_servers.customer.default_tools_approval_mode': 'approve',
                                          'mcp_servers.customer.enabled_tools': route['allowed_tool_names']})
                    for key, value in overrides.items():
                        args.extend(['-c', key + '=' + json.dumps(value)])
                    args.extend(['app-server', '--listen', 'stdio://'])
                    # CodexConfig merges env overrides; blank inherited secrets for its subprocess.
                    child_env = {key: '' for key in os.environ if any(s in key.upper() for s in ('KEY', 'TOKEN', 'SECRET', 'CREDENTIAL'))}
                    child_env.update(EVAL_RELAY_TOKEN=token)
                    # CODEX_HOME is the CLI's documented config location; this override
                    # applies only to the child and leaves the user's config untouched.
                    codex_config_dir = root / 'codex-config'
                    codex_config_dir.mkdir()
                    child_env['CODEX_HOME'] = str(codex_config_dir)
                    with Codex(CodexConfig(launch_args_override=tuple(args), cwd=str(workspace), env=child_env)) as codex:
                        thread = codex.thread_start(model=payload['route']['model_id'], model_provider='evaluation',
                                                    base_instructions=instructions, sandbox=Sandbox.read_only,
                                                    approval_mode=ApprovalMode.deny_all, ephemeral=True)
                        result['session_id'] = thread.id
                        result['harness_version'] = version('openai-codex')
                        result['harness_cli_version'] = subprocess.check_output([binary, '--version'], text=True).strip()
                        for index, user_turn in enumerate(payload['turns'], 1):
                            with recorder.turn(index, user_turn) as record:
                                answer = thread.run(user_turn)
                                record['output'] = answer.final_response
                                items = [item.model_dump(mode='json') for item in answer.items]
                                result['trace'].extend(items)
                                if any(i.get('type') == 'mcpToolCall' and i.get('status') == 'failed' for i in items):
                                    raise ValueError('A tool invocation failed')
                elif payload['harness'] == 'strands':
                    from contextlib import ExitStack
                    from mcp import StdioServerParameters, stdio_client
                    from strands import Agent
                    from strands.models.openai_responses import OpenAIResponsesModel
                    from strands.tools.mcp import MCPClient
                    from openai import DefaultAsyncHttpxClient
                    with ExitStack() as stack:
                        tools = []
                        if payload['workload'] != 'none':
                            client = stack.enter_context(MCPClient(lambda: stdio_client(StdioServerParameters(command=command[0], args=command[1:]))))
                            tools = client.list_tools_sync()
                        # This client connects only to our loopback relay; do not send it
                        # through an inherited corporate HTTP/SOCKS proxy.
                        class LoopbackResponsesModel(OpenAIResponsesModel):
                            def _resolve_client_args(self):
                                args = super()._resolve_client_args()
                                # Strands creates/closes an SDK client for each model call.
                                args['http_client'] = DefaultAsyncHttpxClient(trust_env=False)
                                return args
                        model = LoopbackResponsesModel(model_id=payload['route']['model_id'], stateful=False,
                            client_args={'base_url':url, 'api_key':token, 'max_retries':0})
                        agent = Agent(model=model, system_prompt=instructions, tools=tools, callback_handler=None)
                        result['harness_version'] = version('strands-agents')
                        for index, user_turn in enumerate(payload['turns'], 1):
                            with recorder.turn(index, user_turn) as record:
                                answer = agent(user_turn)
                                record['output'] = str(answer)
                        result['trace'] = agent.messages
                        if any(c.get('toolResult', {}).get('status') == 'error' for m in agent.messages for c in m.get('content', [])):
                            raise ValueError('A tool invocation failed')
                else:
                    raise ValueError('Unsupported harness')
                result['route_calls'] = calls
                if not calls or any(call['status'] != 'completed' for call in calls):
                    raise ValueError('One or more provider calls did not complete')
            if receipt.exists(): result.update(json.loads(receipt.read_text()))
            if len(result['outputs']) != len(payload['turns']):
                raise ValueError('Incomplete conversation')
            result['status'] = 'completed'
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        import traceback
        result['error_location'] = [{'function': f.name, 'line': f.lineno, 'file': Path(f.filename).name}
                                    for f in traceback.extract_tb(exc.__traceback__)[-4:]]
        cause = exc.__cause__ or exc.__context__
        if cause:
            result['cause_type'] = type(cause).__name__
            result['cause_location'] = [{'function': f.name, 'line': f.lineno, 'file': Path(f.filename).name}
                                       for f in traceback.extract_tb(cause.__traceback__)[-4:]]
        # Do not persist arbitrary SDK error strings with potential secrets.
    finish_worker_timing(result, started_ns, time.monotonic_ns())
    return result


def isolated(payload, timeout):
    with tempfile.TemporaryDirectory(prefix='eval-invoke-') as temporary:
        request_path, output_path = Path(temporary) / 'request.json', Path(temporary) / 'output.json'
        request_path.write_text(json.dumps(payload))
        process = subprocess.Popen([sys.executable, '-m', 'customer_eval.worker', str(request_path), str(output_path)],
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return {'trial_id': payload['trial_id'], 'status': 'timeout', 'outputs': [],
                    'latency_ms': timeout * 1000, 'simulated': False}
        if process.returncode != 0 or not output_path.exists():
            return {'trial_id': payload['trial_id'], 'status': 'worker_error', 'outputs': [], 'simulated': False}
        return json.loads(output_path.read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('request')
    parser.add_argument('output')
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(execute(json.loads(Path(args.request).read_text()))))
