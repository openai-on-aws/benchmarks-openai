# Reference checks with separate source judgments

Use this when reliable reference facts can check structured decisions or numbers,
while an LLM assesses the explanation against the source material. This does not
require a separate runner or skill. Use the shared `workflow_eval.py` entrypoint.

The customer defines the business requirements. Reference correctness, detection
of uncertainty and explanatory detail are distinct outcomes. A wrong amount or
unsupported match may be a required-check failure; a concise but sufficient
explanation should not fail solely for style. Decide those boundaries before
optimization, not after seeing which model won.

## Configure the two assessment layers

Keep `quality.kind: "json_checks"`, supplied/recorded reference declarations and
the existing named JSON checks. Add `quality.source_checks`, an array with the
same `id`, `description`, `rubric` and `required` fields as source-only rubrics.
Every source check in this mixed mode must set `required: false` or omit it.
At least one JSON value check must remain required. IDs must be unique across
both lists; `output_schema` is reserved.

For example, after the customer agrees these fields and requirements:

```json
{
  "judge_model": "explanation_judge",
  "quality": {
    "kind": "json_checks",
    "reference_source": "supplied",
    "checks": [
      {"id": "amounts", "type": "json_equals", "path": "balances", "required": true},
      {"id": "matches", "type": "json_unordered_equals", "path": "matches", "required": true}
    ],
    "source_checks": [
      {
        "id": "explanation_support",
        "description": "Does the explanation accurately describe the material findings?",
        "rubric": "Check factual claims against the supplied records and fixed policy. Accept concise explanations that identify the material finding and supporting records. Do not require every equivalent arithmetic restatement. Fail unsupported claims, not stylistic differences. Use needs_review when the source cannot resolve the judgment, and cite exact source evidence.",
        "required": false
      }
    ]
  }
}
```

This is a configuration illustration, not a supplied customer contract. Add the
customer's output JSON Schema with `quality.schema`, exact reference answers and
all decision fields that determine correctness. Set `judge_limits` separately
from application and optimizer limits. A chosen `judge_model` must name a model
connection; the Codex chat model does not determine it.

`json_unordered_equals` permits array reordering at every depth in its selected
field. It rejects duplicate array elements, including duplicate IDs inside a
record, and compares object keys and scalar values/types exactly. Use regular
`json_equals` for ordered sequences. Money can be represented as fixed decimal
strings under an explicit schema; arithmetic/reference generation should use
integer minor units or decimal arithmetic, not floating-point rounding.

## Keep evidence boundaries intact

Source text, general business policy and the candidate output go to the LLM
judge. Expected answers, application model identity and the editable prompt do
not. Required reference checks use the frozen expected answer separately.
Historical responses remain agreement references unless independently established
as expected answers. Synthetic generated facts remain unreviewed until a person
reviews the identified scope; an automated audit is not human calibration.

Import, prepare, preview and execute through the ordinary workflow. Main cases,
reference answers, source rubric and policy are frozen before inference. Optimizer
feedback uses development cases only. Validation chooses the candidate; final
cases remain outside optimization and selection.

## Read the result

The dashboard's **Reference check pass rate** and cost per successful result use
required reference checks only. Optional LLM results appear separately, with
pass/fail/needs-review status, reasons and exact source quotations. Source hashes
and rubric hashes are verified alongside the matching reference answers. A grader
error remains an error; an uncertain judge judgment is not a verified correction.

Application-model cost and latency exclude judge and optimizer calls. Their model
names, reasoning settings, request counts and costs are shown separately.
For migration, inspect all three application configurations: source model/original
prompt, target model/original prompt and target model/revised prompt. The model
used to write a prompt or assess an explanation is not an application baseline.
