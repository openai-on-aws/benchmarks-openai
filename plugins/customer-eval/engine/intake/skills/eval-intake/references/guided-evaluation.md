# Guided evaluation in the Model Upgrade package

Use this path when the customer supplies text logs or examples and wants an evaluation. The executable helper is scripts/customer_eval.py at the Model Upgrade module root. It reuses the existing importer, contract, qualifier, Runner and AgentCore coordinator. It does not provision a second runtime or replace an application with a demo agent. A standalone installation without the module should still produce the ordinary intake handoff.

## Work from the customer's answers

Begin with the use case and one example or available log file. Do not begin with the schema. Inspect files the customer supplied, reuse their prompt/history/settings and ask only for unresolved choices.

Use the repository virtual environment: <repository>/.venv/bin/python. Create a fresh customer project directory outside the reusable skill. The helper actions are:

    python scripts/customer_eval.py start --project /absolute/customer/project --goal "Customer's goal"
    python scripts/customer_eval.py inspect --project /absolute/customer/project --input /absolute/logs.jsonl
    python scripts/customer_eval.py configure --project /absolute/customer/project --decisions /absolute/new-answers.json
    python scripts/customer_eval.py prepare --project /absolute/customer/project
    python scripts/customer_eval.py status --project /absolute/customer/project

Write the small new-answers JSON yourself from what the customer said. Configure merges answers into saved state. Status lists unresolved choices; ask the next useful question instead of showing the whole internal list. Corrections are supported before preparation. Preparation freezes the assets and choices. Reuse a prepared project for status/results; a changed experiment needs a new project revision.

For custom logs, --sources takes a JSON array with a path and optional mapping per file. The mapping names complete request, response and optional id, expected and group paths. For example, request: event.request and response: event.response. Read representative raw records to infer mappings, then show what was detected. A reference file can separately map case IDs to expected JSON values.

## Input scope and privacy

Open input-preview.html or input-preview.json and explain accepted, excluded and selected record counts. Each excluded row has its source location and a remedy. Repair a record when the missing information is available; otherwise ask whether the selected subset is useful. Do not hide exclusions behind a passing score.

When there are multiple instruction layers, show their roles and text and ask which may change. Save prompt_location as the zero-based system/developer message index or the string instructions. Configure reimports the same sources using that scope. Other instruction layers remain in the captured request. Multiple prompt groups need an explicit prompt_group selection.

After prepare, the preview contains the exact source and target outbound request bodies, retained conversation history, output schema and settings changes. Explain that replay generates one new answer against captured history. If the customer wants to regenerate a tool-using application or test raw PDF ingestion, use the application adapter path in the full input contract.

The customer can provide anonymized records, manual examples, or only a prompt and synthetic cases. Do not require confidential production logs. Do not promise local-only processing: Codex sees material it reads; a live job uploads selected inputs, references and configuration to the configured customer S3 prefix. Candidate models receive request bodies without grading references. The optimizer receives development examples and feedback only.

## Define correctness in the customer's language

Ask what makes a wrong answer costly and where a correct answer comes from. Explain the difference between matching an old model response and checking an independently supplied answer. Keep every imported or generated label unreviewed unless an actual human explicitly reviews identified cases. Simulated customer answers cannot provide that review.

The guided helper currently builds deterministic JSON comparisons or required-text checks:

- quality.kind: json_equals compares the complete JSON by default. quality.fields can select the customer's important dotted field paths, such as invoice_id and total. Wrong values, missing fields, wrong types and invalid JSON become behavioral failures with expected/actual evidence. Extra fields fail whole-object equality; field-only checks cover only the selected fields.
- quality.kind: contains checks a required text phrase. This does not establish unrestricted answer correctness.
- quality.reference_source: recorded means agreement with historical outputs. supplied requires explicit expected answers in the records or a quality.reference_file mapping IDs to answers.
- quality.description records the customer's criterion in ordinary language.
- quality.rules records the supplied general business rules as text. Read an authorized rules file and save its contents here; keeping a sibling file alone does not send those rules to the optimizer. Include requirements such as how to treat missing values and conflicting totals. Keep case-specific answers, validation examples and held-out material out of this field. The rules guide prompt revisions; they are not appended to the original application request or substituted for the deterministic grader.

For semantic rubrics, business rules, tolerances, tool correctness or other unsupported checks, use the full grading contract and the relevant adapter. Do not approximate them with substring matching or claim the helper supports a grader it does not have.

No absolute quality percentage or cost/latency improvement cutoff is required. By default, the existing joint selector compares required-check pass rates and measured cost/time against the incumbent; a conflicting tradeoff is shown for review. minimum_quality and minimum_quality_owner are only for an absolute threshold the customer actually requests.

## Execution choices and existing setup

Reuse a supplied platform connection JSON containing version, models and cloud. It contains credential references, model IDs, model parameters, verified prices, endpoint URLs, profile, account, Region, runtime ARN and bucket. It contains no secret values. Ask for missing setup only when needed; a missing connection does not prevent discovery or a saved draft. This helper consumes an already deployed runtime; deployment remains the package's existing setup path.

The intake decision keys source_model, target_model and optimizer_model select entries from that connection file. They may refer to different approved providers; do not assume they must be OpenAI. Codex runs the intake conversation. Prompt replay calls the model directly in AgentCore; it does not use Codex as the application under test. The separate agent execution path still supports Codex by default and Strands as an option.

Other saved decisions are owner, title, provenance (customer, public or synthetic), intent, prompt_group, prompt_location, exclude_records, group_by (case field path), repetitions, iterations, candidates, concurrency, timeout_seconds, budget_policy and budget_usd. Budget policy must reflect actual authorization; no customer-simulator answer creates permission to spend. A group_by document/conversation identifier keeps related cases in one split. The default split is deterministic and approximately one third each for development, validation and final holdout.

Save the agreed cost denominator as cost_metric: cost_usd_per_success means total model spend including failed attempts divided by fully passing trials; cost_usd_per_session means mean spend across all attempted trials. The report shows both. Save primary_latency as conversation for the evaluated request through its answer, or worker when setup and teardown should count. Defaults for existing projects remain cost per attempt and conversation time. Use these decisions before prepare; do not manually patch a frozen configuration afterward.

Propose a concrete study using existing settings. The helper defaults to three repeats, two candidate prompts in one generation, concurrency two and a per-trial timeout of 180 seconds. These are visible experiment settings, not universal customer requirements. At least 20 validation observations are required by the existing joint selector; extra repeats do not create independent customer cases. Show the proposed counts, limits and credential route before execution, and use any existing authorization.

## Continue an authorized run

If the user also asked to run the evaluation and the execution inputs are resolved:

    python scripts/customer_eval.py run --project /absolute/customer/project --live
    python scripts/customer_eval.py status --project /absolute/customer/project
    python scripts/customer_eval.py results --project /absolute/customer/project

Run checks AWS identity/runtime readiness, qualifies the exact source/target routes with one development case, then submits the same prepared input to the existing AgentCore coordinator. Both qualification and the campaign preserve receipts. An existing or uncertain submission is never automatically submitted again. Poor answers in route qualification do not block evaluation if transport and model identity passed; those failures are what the optimizer may need to improve.

Status is a read-only cloud check and result download. On a paused/failed job, inspect the checkpoint and use the existing recovery commands. Do not retry a potentially billed request without a definitive receipt or explicit decision. If authentication expires, retain the project and request a session refresh while completing local work.

Show results/decision.html, the original and selected prompt files, candidate prompts and prompt.diff. Explain model comparison separately from prompt comparison, the actual grading scope, failures, included/excluded cost and observed latency. Report an unchanged prompt honestly; the optimizer is not required to select a rewrite. Do not call synthetic or historical-output agreement production accuracy.
