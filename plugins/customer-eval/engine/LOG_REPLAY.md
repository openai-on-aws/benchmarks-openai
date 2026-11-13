# Evaluate captured requests

Set `EVAL_PYTHON` to the absolute Python path in your workspace virtual environment; see [plugin setup](../README.md#run-your-own-study).

The existing runner can now evaluate one captured text request while retaining its earlier messages. A candidate prompt replaces one declared instruction slot. The recorded final response is used by the evaluator; it is not sent to the model.

## What is supported

| Input | Record shape |
|---|---|
| LiteLLM SpendLogs | `proxy_server_request` or its `body`, plus `response` |
| LiteLLM wrapped SDK | `messages`, request settings, metadata and `response` |
| Chat Completions export | `{request: {messages: ...}, response: ...}` or a flattened request with `choices` |
| Responses export | `{request: {instructions, input, ...}, response: ...}` |
| Custom application log | Explicit dotted paths to the request, response and optional ID/expected answer |
| Manual example | `{id, prompt, input, expected, settings?}` |

The files can be JSON arrays, JSONL, a single record, a `data` wrapper, or gzip-compressed. This is not a promise to parse arbitrary application logs. Each record needs the complete text request and response. Files that contain only token counts, timing, IDs or remote conversation references cannot be replayed.

Plain text conversations and structured JSON outputs are supported. Files/images/audio, tools/tool history, incomplete responses, unsupported generation controls, duplicate JSON keys, duplicate requests and conflicting references appear in the exclusion report. No tool from a captured log is executed. No log-provided Python is imported.

ModelShift's pinned ingestion code handles shape/response normalization. Our validation preserves the original roles and settings instead of using its merged system prompt. Its scoring, cost calculations and transport are not used. See `customer_eval/vendor/modelshift/UPSTREAM.md` and the included license.

## Import

From this module with the repository virtual environment:

```bash
"$EVAL_PYTHON" scripts/import_logs.py \
  --input /path/to/logs.jsonl \
  --output /path/to/new-import
```

For custom logs:

```json
{
  "id": "trace.id",
  "request": "application.request_payload",
  "response": "application.response_payload",
  "api": "responses"
}
```

Pass that file with `--mapping /path/to/mapping.json`. Request and response values must be full API objects, not snippets. `expected` can optionally map a separately supplied answer. The answer still remains unreviewed until someone reviews it.

The importer writes an `import-report.json`, then a prompt and JSONL dataset per prompt group. Review exclusions and the selected prompt slot. The default is `instructions` when present, otherwise the first system/developer message. Use `--prompt-location instructions` or a zero-based message index to select another slot. Preserve every other instruction message. Different original prompts are not silently combined into one optimization job.

Split examples by independent document/session into development, validation and holdout. Do not put alternate encodings or neighboring captures of the same task into different splits. Exact request duplicates are detected; semantic duplication still needs review.

Use `examples/log-replay/input.yaml` as the runnable configuration example:

```yaml
runner:
  mode: prompt
  input_mode: replay
  workload: none
```

The separate policy file is empty because protected policy is already in the recorded request. Keep the starting prompt file equal to the selected instruction slot. Configure the checks and model routes using the normal intake workflow.

## What changes in a run

- The candidate model is selected by configuration. Source/target use identical captured input.
- The selected prompt slot changes only for an optimization candidate.
- Chat message content and `response_format` are translated to Responses equivalents without merging roles.
- Calls use `store=false` and `stream=false`. Thus latency is complete response time; these runs do not measure time to first token.
- Recorded reasoning/output-format settings are retained unless the run explicitly overrides them. Per-trial `replay.settings_overrides` records effective changes. A captured output-token limit larger than the run envelope must be reconciled before launch.

The existing AgentCore worker, cost accounting, interleaved comparisons and optimizer are reused. Development feedback now includes the recorded context. Validation selects the prompt, and holdout is opened only after selection. `optimizer_limits` can set a separate optimizer output allowance without changing the application requests.

Recorded answers alone support an agreement measurement. Correctness requires a valid oracle or reviewed criteria. The invoice fixture uses exact parsed-JSON equality against constructed expected values. All labels are explicitly synthetic and unreviewed.

## Try the included example

`examples/log-replay/logs` has six different log formats, six distinct cases per format, a gzip example, a custom mapping and an intentionally invalid edge-case file. The 36 distinct cases cover negative credit-note amounts, currency corrections, Unicode, conflicting totals, missing totals and instructions embedded in document text. Each format contributes two cases to each of development, validation and holdout. These are OCR-text inputs, not raw PDFs and not a full PDF extraction benchmark.

```bash
"$EVAL_PYTHON" -m pytest -q
"$EVAL_PYTHON" scripts/run_optimization.py plan --config examples/log-replay/input.yaml
```

For a fresh generated copy, use `scripts/build_log_examples.py --output /path/to/new-examples`. Existing example/import directories are never overwritten.

Set `EVAL_BEDROCK_BASE_URL` to your verified Bedrock Responses endpoint. Update the profile, Region, runtime and prices for your deployment; the example contains the routes used for the authorized ADE test, not a claim that another account has access. Qualify with `scripts/qualify_agentcore.py` before submitting with `scripts/submit_agentcore.py`. Both commands provide their required arguments with `--help`.

Retain failed-attempt receipts, model usage, raw results, import exclusions and the frozen prompt. Report model cost separately from AgentCore, storage and review costs. A faster or cheaper result in these constructed cases does not establish a universal model advantage.
