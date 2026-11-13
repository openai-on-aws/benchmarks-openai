# Customer evaluation from Codex

Set `EVAL_PYTHON` to the absolute Python path in your workspace virtual environment; see [plugin setup](../README.md#run-your-own-study).

Start in Codex with the evaluation intake skill at intake/skills/eval-intake/SKILL.md. For example:

> Use the evaluation intake skill in this module. We extract invoice fields from OCR text and want to compare our current model with a new model. I have application logs but I am not sure how to evaluate them. Help me work through it.

Codex asks for the next useful input, inspects supplied files and saves the answers. The customer does not need to fill out an evaluation YAML file. The integrated helper is scripts/customer_eval.py; it connects to the existing evaluation runner and AgentCore runtime.

The conversation should establish:

1. What the application does and which stage the test covers.
2. Which supplied examples can be replayed, and which instruction may change.
3. What makes an answer correct and where the expected answers come from.
4. Which model routes and existing platform setup to use.
5. The proposed study and the customer's cost, latency and quality preferences.

Use customer-provided code, application configuration and approved connection information when available. Codex can assemble the connection file from those sources and verified route/pricing information. Missing credentials or deployment details remain explicit; they do not prevent a saved draft. A platform connection contains references and routes, not API keys.

## What the customer receives before a run

The saved project contains project.json with the decisions received so far. The input preview shows accepted and excluded records, source locations, remediation suggestions, retained conversation history, protected instructions and the editable prompt slot.

After preparation, input-preview.html shows the exact request bodies for the original prompt on each model, including settings overrides. prepared/input.yaml contains the generated evaluation contract, and the other prepared files contain prompts, grading rules, split datasets and endpoint references. No absolute quality percentage or cost/latency improvement cutoff is added by default.

The customer can supply anonymized records, manual examples or synthetic data. A logged response is an agreement reference, not automatically a correct answer. JSON comparisons can cover all fields or selected fields; the report states their scope. Semantic grading and application/tool tests continue to use the existing full grading and application contracts.

## Run on an existing AgentCore deployment

From the Model Upgrade directory, use the repository Python environment:

    "$EVAL_PYTHON" scripts/customer_eval.py run --project /absolute/customer/project --live
    "$EVAL_PYTHON" scripts/customer_eval.py status --project /absolute/customer/project
    "$EVAL_PYTHON" scripts/customer_eval.py results --project /absolute/customer/project

Run verifies AWS identity and runtime readiness, qualifies the exact model routes, then submits the frozen intake output to the existing AgentCore coordinator. The coordinator runs evaluation and prompt optimization in AgentCore. A malformed model answer is a quality failure; a failed route qualification prevents the full run.

The job is saved before dispatch. Calling run again returns the saved receipt instead of submitting another job. Status and results read the existing job. Completed attempts remain in the durable checkpoint. Interrupted requests are not silently retried. An expired AWS session requires a refresh, not a new project or campaign.

## Read the result

Open results/decision.html. It separates the model comparison from the prompt comparison, states the quality measure and shows held-out results, field errors, costs and observed latency. Original, selected and candidate prompt files are adjacent, with prompt.diff describing the selected change. Retaining the original is a valid result.

Prompt selection uses matched validation results before the held-out evaluation. An automatic replacement must pass the configured checks, avoid regressions in quality, the agreed cost metric and p95 latency, and improve at least one of them. Qualifying candidates are ranked by highest required-check pass rate, then lowest cost, then lowest p95 latency. For a customer choosing cost per successful result, that cost includes failed-attempt spend. A cheaper candidate wins an accuracy tie even when another qualifying candidate is faster; the report keeps that faster alternative visible. Exact ties with the original retain it. Customer-requested thresholds still apply; no minimum improvement percentage is added by default. Historical selections are not recalculated when reading old reports.

Model cost uses returned usage and configured route prices, including cache accounting and failed-attempt spend where available. Qualification and campaign costs are shown separately and together. Infrastructure and human review are excluded. Missing usage remains unknown. Latency is observed conversation time; provider timing includes network and provider processing.

The current replay path tests one new answer using recorded history. It does not regenerate all previous assistant turns, execute application tools or read raw PDFs. OCR-text examples test the model stage after OCR. Use the application adapter path when those other stages must be included.

## Implemented changes

- customer_eval/project.py saves progressive intake and produces the full configuration from customer decisions.
- customer_eval/input_preview.py explains input coverage and renders exact request previews.
- customer_eval/json_grading.py treats invalid JSON and missing/wrong fields as model behavior, while retaining real grader errors separately.
- customer_eval/feedback.py includes distinct failing cases and explicitly bounded excerpts for large failures.
- customer_eval/project_cloud.py uses the existing qualification/submission scripts and preserves run identity.
- customer_eval/decision_report.py generates the recommendation, comparisons, failure examples and prompt artifacts from the shared Runner report.

The intake runs in Codex. Text replay uses direct model calls inside AgentCore. The existing application-agent path still supports Codex by default and Strands as an option.
