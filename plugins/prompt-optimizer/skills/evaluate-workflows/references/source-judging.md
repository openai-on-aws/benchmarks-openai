# Judge outputs against source text

Use this mode when the customer has source inputs but no expected answers. An
application model produces an answer. A separately chosen judge assesses that
answer against the fixed source text and customer-confirmed rubric. No reference
answers are generated or inferred from historical outputs.

This evaluates one new text response per case. It can preserve a recorded text
conversation, but does not execute a tool workflow, read raw PDFs or assess OCR
quality. If the customer starts with PDFs, first agree whether the study covers
an extracted-text stage or requires a document adapter.

## Collect decisions progressively

Ask what the application does and which errors matter most. Inspect the supplied
prompt, source requests and output contract before proposing checks. If the
customer has only model outputs, ask for the matching source text or complete
requests. Outputs alone cannot establish source support.

Propose a few named criteria from the customer's requirements and have them
confirm the rubric before freezing it. Do not silently adopt invoice fields,
a bundled example, acceptance thresholds or synthetic reference answers. Keep
unanswered decisions in the draft. Human agreement with the rubric does not
establish that the judge is accurate; review representative judgments too.

Choose the roles independently:

- `source_model` and `target_model`: connection keys for the application. They
  may use the same key when optimizing one model's prompt.
- `optimizer_model`: required only for optimization; generates prompt changes.
- `judge_model`: explicitly chosen for source judging; assesses each output.

For example, a customer may choose Sol for extraction and Astra with high
reasoning for both optimization and judging. Those are separate configured roles,
not mandatory models or settings. Neither is inferred from the Codex chat model.

## Import source inputs without labels

A manual JSONL row has `prompt` and `input`, with an optional case `id`:

```json
{"id":"doc-001","prompt":"Extract invoice_id, total and currency as JSON. Use null when a value is absent.","input":"Invoice INV-001. Total USD 20.00."}
```

This is a format illustration, not a default customer schema or study dataset.
There is **no `expected` key**. Omit it entirely; `null` and `{}` are not
unlabeled substitutes. Explicit expected answers conflict with this mode and
are rejected. Use a reference-based study if the customer wants those answers
to determine grades.

Captured Chat Completions requests, Responses requests, LiteLLM requests and
mapped application logs are also supported. Full supported text history and
fixed instructions are preserved. Historical response fields are optional and
are not promoted to reference answers. For a custom mapping, `request` is
required; `response` is optional in source-judging mode. Imported datasets use
`label_status: "not_provided"`.

Set the source-judging mode **before `inspect`**. An existing draft automatically
reimports its original files when `configure` changes the reference mode, so
previously excluded request-only records can be recovered. Review the accepted,
excluded and duplicate counts; do not silently discard incompatible records.
Requests with raw file/image inputs, tool execution or unresolved remote history
remain unsupported by text replay.

## Configure the rubric and judge

Save this initial mode patch as `source-mode.json` in the workspace:

```json
{"quality":{"kind":"source_judge"}}
```

After inspecting the customer's assets and confirming their requirements, add
the checks and selected connection keys. The following is an illustrative patch;
replace its criterion and keys with the actual agreed choices:

```json
{
  "source_model": "extractor",
  "target_model": "extractor",
  "optimizer_model": "rewriter",
  "judge_model": "grader",
  "quality": {
    "kind": "source_judge",
    "checks": [
      {
        "id": "source_support",
        "description": "Are the extracted values supported by the source?",
        "rubric": "Check each non-null extracted value against the supplied source. Fail unsupported or contradicted values. Use needs_review when the source does not support a reliable decision. Explain the specific issue and cite exact source text.",
        "required": true
      }
    ]
  }
}
```

A rubric needs at least one required check. Each check accepts only `id`,
`description`, `rubric` and `required`; IDs must be unique. `output_schema` is
reserved. Add `quality.schema` containing the customer's JSON Schema to check
shape and types independently of the judge. A source-support check alone does
not measure missing fields, complete coverage or every business rule: add named
criteria for those requirements when the customer needs them.

Do not set `quality.reference_source`, `quality.reference_file` or
`quality.fields` in this mode. `quality.rules` may hold fixed general business
policy; it must not contain invented case answers. If essential policy exists
only in the editable application prompt, preserve it independently in the
agreed rubric or general rules so judging cannot change with prompt wording.

`limits`, `optimizer_limits` and `judge_limits` are separate partial override
objects. Each accepts `max_requests`, `max_output_tokens`, `max_request_bytes`
and `request_timeout_seconds`; text replay keeps `max_requests` at `1`. Size the
judge envelope for the complete fixed source context, output and rubric. Do not
truncate source evidence to make a request fit. A completed application output
is assessed in one judge request covering all named source criteria together.

Use the shared Python environment and connection setup from
[the workflow guide](workflow.md). Connection keys refer to its `models` object;
model IDs, endpoints, reasoning, prices and credential references live there.

```bash
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" start \
  --project "$EVAL_PROJECT" --mode optimize --goal "Improve my extraction prompt using source-based checks"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" configure \
  --project "$EVAL_PROJECT" --decisions "$EVAL_WORKSPACE/source-mode.json"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" inspect \
  --project "$EVAL_PROJECT" --input "$EVAL_WORKSPACE/source-inputs.jsonl"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" configure \
  --project "$EVAL_PROJECT" --decisions "$EVAL_WORKSPACE/resolved-decisions.json"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" prepare --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" plan --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" preflight \
  --project "$EVAL_PROJECT" --backend agentcore
```

`resolved-decisions.json` also contains the choices required by the shared
workflow: owner, connection, prompt slot, provenance, execution/budget choices
and run settings. A manual row's prompt becomes instruction slot `0`. For
captured requests, choose the actual editable instruction index or
`"instructions"`; do not assume the last user message is the entire source.
Use `--mode compare` without an optimizer for a fixed-prompt comparison.

Preparation freezes the study; `plan` follows `prepare`. Inspect the preview
before freezing and resolve missing decisions as they arise. Preflight is not
paid-run authorization. Once execution is authorized, use the shared
`run --backend agentcore --live` or `run --backend local --live` command. Resuming
an existing job must reuse its saved receipts; never start a second controller
because a request timed out.

## What is sent to the judge

The judge receives the candidate answer, fixed rubric, general policy and all
fixed text instructions/messages from the recorded request. The declared
editable prompt slot and application model identity are excluded. Earlier
assistant messages retain their role and are treated as context, not verified
answers. Source IDs preserve the projection order.

The judge is instructed to treat source text and candidate output as untrusted
data, including any embedded instructions to change grades. It returns a status,
reason and source citations per criterion. The evaluator verifies cited source
IDs and exact quotations and records source/rubric hashes. These checks make
judgments inspectable; they do not prove the judge's interpretation is correct.

## Read results and optimization feedback

- **Pass:** the judge says the criterion is satisfied and supplies valid source
  evidence. Required checks, including any schema check, determine success.
- **Fail:** the judge identifies a criterion violation with source evidence.
- **Needs review:** the evidence does not support a reliable decision. This is
  not counted as a pass or treated as a verified correction.
- **Grading error:** a judge request or response could not be validated. Keep
  this separate from an application error or a rubric failure.

Report **source-assessed quality**, the criterion results and review/error
counts. Do not call them human-verified accuracy. Review representative passes,
failures and uncertain cases before using the result for an adoption decision.

During optimization, development failures and their source evidence inform
prompt revisions. Uncertain judgments are kept distinct from confirmed rubric
failures. The optimizer cannot change the rubric, schema or source inputs.
Validation selects a prompt; holdout judgments are excluded from mutation.
See [optimization](optimization.md) for selection and tradeoffs.

Application cost and latency describe the extraction. Judge and optimizer spend
are reported separately in model-cost totals by purpose. Application latency
excludes grading time. Preserve failed-attempt spend and mark missing usage as
unknown; infrastructure and human-review costs are excluded. More criteria in
one judge request do not mean separate requests per criterion, but longer
sources, rubrics or outputs can increase judge usage.

## When reference facts also exist

Source-only mode intentionally rejects expected answers. For structured reference
checks plus an LLM assessment of explanation quality, use
[reference checks with source judgments](reference-and-source.md). That explicit
mode preserves both reference evidence and the judge's source evidence; it does
not relabel generated or historical answers as verified human truth.
