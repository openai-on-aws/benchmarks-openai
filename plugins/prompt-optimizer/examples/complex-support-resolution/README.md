# Complex support-case resolution

This synthetic example evaluates one substantial text decision: resolve a fictional
enterprise SaaS service-credit claim and recommend the appropriate operational
handling. It uses the same **Evaluate Workflows** skill and `workflow_eval.py`
entrypoint as the other examples. It does not call customer tools, issue credits,
retrieve documents or operate a ticketing system.

The packet includes four contract versions, invoice line items, incident intervals,
maintenance notices, claim corrections, prior credits, security assessments and
current service-status updates. The model must choose the applicable contract,
reconcile invoice scope, union overlapping outage intervals, apply notice/deadline
rules and monthly caps, and recommend escalation with the correct source IDs.

## Files

| File | Purpose |
| --- | --- |
| `prompt.txt` | Complete original business instructions; not an intentionally broken prompt. |
| `cases.jsonl` | 180 distinct main-study cases, with 60 cases in each size band. |
| `pilot.jsonl` | 20 separate cases with different IDs, random seeds and account facts; not drawn from the main dataset. |
| `output.schema.json` | Structured decision contract; no extra response fields. |
| `decisions.template.json` | Generic grading and execution settings, with owner, connection and spend authorization unset. |
| `build_cases.py` | Reproducible local generator and reference oracle. No model or network calls. |
| `*-facts.json` | Source facts for independent oracle audit. These are not appended to candidate model requests. |
| `*-inventory.json` | Case IDs, scenarios, input sizes and constructed references. |
| `fixture-manifest.json` | Provenance, counts, hashes and size statistics. |
| `test_cases.py` | Hand-calculated boundary checks and asset validation. |

Every expected answer is **synthetic and unreviewed**. Passing these checks means
agreement with the constructed policy and references, not established customer or
production accuracy. An independent code audit does not change that label status.

## Size and coverage

Small packets contain 12 invoice lines and roughly 13 incident rows; medium packets
contain 50 and 61; large packets contain 100 and 121. Some scenarios add a pending
or overlapping monitor record. More records increase the actual scope reconciliation
and interval arithmetic. They are not repetitions of the same text to inflate cost.
Input text ranges from about 17 KB to 119 KB, plus the original instructions and
response schema. The manifest's character/4 token estimates are approximate;
recorded model usage is the authoritative measurement after a live run.

Twelve scenario families cover threshold boundaries, overlapping monitors, late
claims, corrected deadline timestamps, active and contained security incidents,
pending evidence, conflicting claims, exhausted caps, previously credited claims,
ineligible plans and ongoing outages. All seven financial dispositions and all three
priority levels occur in the main dataset. The operative contract varies between
the base agreement and a signed amendment. Future and unsigned terms are present
as evidence that must be rejected.

Each case is a separate fictional account and claim. There are no alternate formats
or duplicate versions of the same case. The engine hashes the case ID with
`split_seed` to create 60 development, 60 validation and 60 holdout cases for
optimization; inspect the actual per-split scenario/size distribution before launch.
Do not tune the prompt using held-out responses.

## Grading

`quality.kind: json_checks` applies a required JSON Schema check, strict scalar
checks for financial/operational decisions, and order-independent set checks for
actions and source IDs. Duplicate set values fail. The prompt explicitly defines
the required citation set, so a valid answer is not penalized for array ordering.
These checks assess the structured decision and required evidence IDs. They do not
claim to judge prose quality, arbitrary explanations or an actual tool-driven workflow.

The reference oracle uses `Decimal` half-up rounding and an interval-union algorithm.
Tests separately check hand-calculated amounts, overlapping/timezone intervals,
deadline equality, maintenance notice equality, contract validity, duplicate-credit
precedence, uncertainty and current security/operations state.

## Reference correction in 0.5.4

The earlier generated references omitted `explain_credit_decision` when the
financial disposition was `require_review`. The policy requires both that action
and `reconcile_claim_evidence`. Version 0.5.4 corrects 30 main and four pilot
references and adds a hand-written action truth table for all seven dispositions.
The original prompt, case inputs and split membership are unchanged.

Do not treat accuracy, cost per successful result or prompt selection from this
example in versions 0.5.0–0.5.3 as valid without correcting the references. Preserve
those runs. A regrade can diagnose the effect, but optimization that used the
incorrect feedback needs a new run. Reusing an earlier verification set does not
make it a new, unseen test set.

## Run through the plugin

Choose this example in the intake conversation, then use the existing `start`,
`inspect`, `configure`, `prepare`, `plan`, `preflight` and `run` commands. Supply
`cases.jsonl` as manual replay records and use prompt location `0`. Select the exact
source, target and optimizer routes in the connection file. The template proposes
Astra as source and Sol as target; it does not establish access, price validity,
execution authorization or a model winner.

For an initial plumbing check, use a separate project with `pilot.jsonl`, comparison
mode and one repetition. Preserve those results, then create a different project
for the main study. Main-study optimization uses three repetitions, concurrency
four and two candidate prompts. The editable request limits are 240,000 bytes and
8,192 output tokens; the optimizer uses 272,000 bytes and 16,384 output tokens.
These are ceilings, not token usage or promised cost. Resolve model pricing and
account limits before a live run. Review the estimated plan and existing authorization.

Report the original prompt on both models, the original and selected prompt on the
target model, the selection reason, held-out required-check pass rate, model cost per
attempt and per successful case, and median/p95 complete-response time. Include
failed-attempt spend and cache usage. Report optimizer spend separately, and keep
AgentCore infrastructure and human-review costs separate from model inference.

To reproduce and test the fixtures with the workspace Python environment:

```bash
python build_cases.py --output /absolute/workspace/new-complex-fixtures
python -m unittest discover -s /absolute/plugin/examples/complex-support-resolution -p 'test_cases.py'
```

The generator reads the original prompt beside itself. Changing that prompt requires
regenerating replay records, because each record contains its own prompt copy.
