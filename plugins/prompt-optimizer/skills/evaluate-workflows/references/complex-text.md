# Complex text studies

Use the existing `workflow_eval.py` entrypoint for long policies, account context,
document text and conversation histories. A case still generates one new model
answer. This mode does not execute customer tools or process raw PDFs.

The [complex support-case example](../../../examples/complex-support-resolution/README.md)
is a synthetic illustration. Use it only when the user selects that example.
Preserve its unreviewed provenance. A generated oracle or an agent's review is
not human label review.

## Fit the actual workload

The decision patch accepts partial `limits` and `optimizer_limits` objects. The
defaults preserve the smaller examples; larger studies should set their own
envelopes. For example:

```json
{
  "limits": {
    "max_requests": 1,
    "max_request_bytes": 240000,
    "max_output_tokens": 8192,
    "request_timeout_seconds": 300
  },
  "optimizer_limits": {
    "max_requests": 1,
    "max_request_bytes": 272000,
    "max_output_tokens": 16384,
    "request_timeout_seconds": 600
  },
  "timeout_seconds": 360
}
```

These are example allowances, not output targets or a requirement to spend them.
The outer trial timeout must exceed the application request timeout to leave room
for setup and cleanup. There is no overall study time limit in this setting.
Unknown billed attempts must be investigated, not silently retried.

`max_request_bytes` limits the serialized API request, including JSON escaping,
instructions and output schema. It is not a token count. The pricing guard uses
bytes as a conservative upper bound on input tokens; verify the selected route's
pricing band before raising it. Capture returned usage rather than estimating
actual charges from these limits.

The optimizer receives compact diagnostics for every failed development check,
including exact expected/actual or missing/unexpected values when available.
These diagnostics remain in the request when a long full example does not fit.
Oversized individual values are marked as omitted, with their size and hash;
an omission marker is not an answer or an empty value.

Complete development examples are then selected to fit the remaining request
envelope. The saved request identifies omitted examples; it does not replace
long documents with misleading prefix excerpts. If the mandatory diagnostics or
necessary complete failure evidence cannot fit, the optimizer call stops before
spending. Expand the verified envelope or explicitly revise the study. Do not
substitute validation or holdout examples.

## Check the decision independently

Use `quality.kind: "json_checks"` for named checks. Supply `quality.schema` for
independent output-shape validation and `quality.checks` with entries such as:

```json
[
  {"id": "credit_amount", "path": "credit_cents", "type": "json_equals", "required": true},
  {"id": "supporting_evidence", "path": "evidence_ids", "type": "json_set_equals", "required": true}
]
```

Expected values come from the supplied reference answer at the same field path.
`json_equals` checks values and types. `json_set_equals` compares unordered sets of
unique JSON scalar values, rejecting duplicates. Use exact evidence-set matching
only when the output contract defines that set unambiguously. JSON Schema checks
types, required fields and extra-field rules; local references are supported.

Name checks after business outcomes. Include every decision field that determines
success. If a narrative explanation is not graded, say so; passing structured
checks does not establish that the prose is correct. Customer requirements and
reference answers are separate from the model's request.

## Compare models and prompt changes separately

Compare the original prompt on the source and target models, then the original
and revised prompts on the target. The optimizer is a separately selected model.
Report each model ID, route, reasoning effort and service tier.

Run a separate pilot to detect input and grading problems. Freeze the main
study before execution and use its actual grouped split assignments. Report
case counts and size distributions for each split; repeated runs are not new
independent cases. Show all candidate prompts and selection reasons, including
rejected candidates and an unchanged original. Inspect proposed prompts against
the business policy before recommending them. A higher aggregate score does not
prove that every required rule was preserved, particularly when the starting
prompt already fails some checks. Keep invalid revisions and their costs as
diagnostic evidence; do not present them as usable improvements.

Report accuracy within the declared checks, cost per attempt, cost per successful
case, p50/p95 conversation time, cache usage and failures. Include failed-attempt
spend. Report optimizer spend and infrastructure exclusions separately. A monthly
volume projection is not evidence of throughput or concurrent-load capacity.
