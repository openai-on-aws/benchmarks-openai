# Evaluation workflow

`PLUGIN` below is the absolute installed plugin directory; `EVAL_PYTHON` is the Python
executable in the user's Python 3.12 workspace environment. Use the same
entrypoint for local and AgentCore execution.

Ask the initial workflow questions before environment setup. Before creating the
environment, check `python3.12 --version`. If it is not on PATH, use the absolute
path of an available Python 3.12 installation. Do not substitute the system
`python3` or downgrade dependencies to fit an older interpreter. If Python 3.12
is unavailable, keep collecting the draft and resolve that setup requirement
before running study commands.

```bash
export PLUGIN=/absolute/path/to/prompt-optimizer
export EVAL_WORKSPACE=/absolute/path/to/your-workspace
mkdir -p "$EVAL_WORKSPACE"
python3.12 -m venv "$EVAL_WORKSPACE/.venv"
export EVAL_PYTHON="$EVAL_WORKSPACE/.venv/bin/python"
"$EVAL_PYTHON" -m pip install -r "$PLUGIN/requirements.txt"
export EVAL_PROJECT="$EVAL_WORKSPACE/invoice-study"

"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" start --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" inspect --project "$EVAL_PROJECT" \
  --input /absolute/path/to/invoices.jsonl
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" configure --project "$EVAL_PROJECT" \
  --decisions "$EVAL_WORKSPACE/answers.json"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" prepare --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" plan --project "$EVAL_PROJECT"
```

If there are no expected answers, configure `quality.kind: "source_judge"` before
`inspect`; use the [source-judging guide](source-judging.md) for the decision patch.
An existing draft is reimported when its reference mode changes. Do not create
empty expected-answer objects to make a request importable.

The bundled synthetic OCR-text logs are under
`engine/examples/log-replay/logs/`. The
[support-ticket starter](../../../examples/support-ticket-triage/README.md)
uses the same entrypoint with its own explicit goal, inputs and decision
template. Use real customer material only within
the user's authorized scope. `--input` is repeatable; `--sources` instead
accepts a JSON array of `{path, mapping?}`. Inspect duplicate/exclusion counts.

Prepare a connection from `connection.template.json` outside the plugin.
It declares model IDs, Responses endpoints, Region, AWS profile references,
verified input/output/cache pricing, and optional existing cloud resources.
Fill `pricing_source`, `pricing_verified_at` and the integer
`pricing_max_input_tokens` context bound along with the prices; null template
values are unresolved settings, not free usage.
For a local run, `cloud.runtime_arn` can remain null; the other cloud fields
are not used. Populate them before preparing a study intended for both backends.
No credential values belong in the file.

Before cloud preparation, establish whether the user has a matching AgentCore
runtime, execution role and private artifact bucket or needs a deployment.
Use [AWS setup](../../../docs/LIVE-SETUP.md) for the exact resource plan and
authorized setup. `prepare` freezes references; `run` verifies existing
infrastructure and never creates it. Choose local-only while access is being
arranged, or keep a draft for both backends. Adding cloud configuration to an
already prepared local study requires a new project revision.

### Reuse infrastructure without changing the study

"Reuse our AgentCore environment" selects deployment reuse. It does not select
an old job, its results or its paid-run authorization. Import only the needed
connection/resource facts, verify their current validity and keep the new study
in its own project. An identical input file does not make two evaluations
equivalent: grading/adoption rules, split assignment, repetitions, selected
prompt and runtime/model settings can differ.

If a customer explicitly asks to review an old run, inspect that run and preserve
its original scope. If they ask to resume it, verify its frozen configuration and
durable receipt before continuing. If they want new criteria applied to saved
answers, label the operation retrospective regrading; it does not supply new
application latency/cost measurements or a new held-out test. Ask which task is
intended only when the request is ambiguous.

### Complete and reconcile intake

After inspecting inputs, continue with unresolved quality/adoption decisions,
model and optional optimizer/judge roles, execution scope and budget. Present
proposals progressively and keep them separate from confirmed answers. A request
for analysis at this stage can be answered with case coverage and concrete grading
examples; it does not require finding an older benchmark or submitting inference.

Before `prepare`, configure the agreed decisions and read back `project.json`
and readiness. After `prepare`, inspect the frozen `plan` before any paid calls.
Verify that split counts, repetitions, concurrency, limits, grading rules and model
roles match what the customer agreed. Resolve discrepancies
instead of leaving the report on one plan and the runner on its defaults. Keep
unknowns pending even if structural validation accepts a default. Qualification
and `run --live` make paid model calls and require current study authorization.
Record manual review requirements separately from machine-enforced selection;
an optional source check alone cannot implement an adoption veto.

The `answers.json` patch records only known choices. Relative file references in
that JSON resolve against the answer file’s directory and are saved as absolute
paths, so resuming from another working directory is safe. For a fixed-prompt study
it needs `owner`, `source_model`, `target_model`, `connection`,
`prompt_location`, `provenance`, `quality` and `budget_policy`; a capped policy
also needs `budget_usd`. Do not invent missing values. Model choices select
keys in the connection's `models` object.

- `prompt_location`: zero-based instruction-message index, or `"instructions"`
  for top-level Responses instructions. It identifies the preserved prompt.
- `quality.kind`: `"json_equals"` for both labeled starters, or `"source_judge"`
  for source inputs without expected answers.
- `quality.reference_source` (reference-based modes only): `"supplied"` for explicit labels, or `"recorded"`
  for historical output agreement.
- `quality.fields`: `[""]` for whole-object equality, or agreed dotted paths,
  such as `["invoice_id", "total", "needs_review"]`.
  The triage starter uses whole-object equality, including types and unexpected fields.
- `quality.description`: the user's correctness criterion.
- `quality.kind: "json_checks"`: named structured decision checks and an optional
  output schema; see [complex text studies](complex-text.md) for the contract.
- `quality.reference_file`: optional mapping of case IDs to expected answers in
  reference-based modes; omit it for source judging.
- `quality.source_checks`: optional source-based explanation rubrics alongside
  `json_checks`; see [mixed assessment](reference-and-source.md).
- `judge_model`: a separately chosen connection key for source judging, including
  optional source checks alongside reference checks. Source-only mode puts named
  rubrics in `quality.checks`; mixed mode puts them in `quality.source_checks`.
  Add `quality.schema` for deterministic output validation. See [source judging](source-judging.md).
- `quality.rules`: supplied general business rules, kept separate from
  case-specific expected answers.
- `provenance`: `"synthetic"`, `"customer"` or `"public"`. Supplied labels stay unreviewed until a scoped human review is recorded; source-judged datasets use `not_provided` because expected answers are absent; see [label review](label-review.md).
- `label_review_file`: optional human-completed review file tied to case inputs and expected-answer hashes.
- `repetitions`, `concurrency`, `timeout_seconds`: propose and explain these
  settings; engine defaults are 3, 2 and 180 respectively.
- `limits`, `optimizer_limits` and `judge_limits`: partial overrides for serialized request size,
  output-token allowance and provider request timeout. The replay path uses one
  new application request per case; source judging makes a separate judge request
  for each completed output, covering all named source criteria together. Judge
  requests have their own envelope and cost. See [complex text studies](complex-text.md).
- `budget_policy`: `"capped"` or `"unlimited"` according to actual authorization.
  An uncapped study still has case, concurrency, request and timeout limits.
- `cost_metric`: `"cost_usd_per_session"` or `"cost_usd_per_success"`.
- `primary_latency`: `"conversation"` or `"worker"`.
- `minimum_quality` and `minimum_quality_owner`: only for an explicitly
  requested absolute acceptance gate.
- `optimization_enabled`: starts as false. Turning it on is a scope extension
  requiring an optimizer model and separate prompt-change agreement.

Fixed comparison needs at least two independent groups, divided into validation
and holdout; all selected cases are evaluated. Prompt optimization additionally
uses development cases and requires at least 20 validation observations. Intake
checks these requirements before preparation. Repetitions are not independent cases. Group related records with `group_by`
when applicable so related documents do not cross splits.

```bash
# Run the frozen study locally. This makes real model calls.
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" run \
  --project "$EVAL_PROJECT" --backend local --live

# Run the same study in an already deployed AgentCore coordinator.
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" run \
  --project "$EVAL_PROJECT" --backend agentcore --live

# Inspect the same job. These commands do not submit a new evaluation.
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" status \
  --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" results \
  --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" results \
  --project "$EVAL_PROJECT" --backend local

# Render compatible saved results together, without AWS or model calls.
python3 "$PLUGIN/scripts/workflow_eval.py" dashboard \
  --project "$EVAL_PROJECT" --output "$EVAL_PROJECT/dashboard.html"
```

AgentCore execution qualifies the configured application routes before submission;
qualification adds model calls recorded separately from the evaluation. `results/decision.html` is the cloud report; `local/decision.html` is
the local report. Reports preserve usage, timing, checks and prompt evidence.
The `dashboard` command combines the saved backend reports when their input
fingerprints match. It also supports a single backend. Open `dashboard.html`
for the overview, Test cases and Run details views. It is an offline
snapshot; retrieve results, then rebuild with `--force` to refresh it.

For unsupported log formats or grading needs, inspect the bundled engine's
`LOG_REPLAY.md` and `intake/skills/eval-intake/references/input-contract.md`.
Use `docs/LIVE-SETUP.md` for an authorized deployment. Do not confuse an
existing runtime being READY with it containing this plugin's engine:
the implementation fingerprint must match.
