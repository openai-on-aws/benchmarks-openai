# Fixed-prompt evaluation workflow

`PLUGIN` below is the absolute installed plugin directory; `EVAL_PYTHON` is the Python
executable in the user's Python 3.12 workspace environment. Use the same
entrypoint for local and AgentCore execution.

```bash
export PLUGIN=/absolute/path/to/customer-eval
export EVAL_WORKSPACE=/absolute/path/to/your-workspace
mkdir -p "$EVAL_WORKSPACE"
python3.12 -m venv "$EVAL_WORKSPACE/.venv"
export EVAL_PYTHON="$EVAL_WORKSPACE/.venv/bin/python"
"$EVAL_PYTHON" -m pip install -r "$PLUGIN/requirements.txt"
export EVAL_PROJECT="$EVAL_WORKSPACE/invoice-study"

"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" start --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" inspect --project "$EVAL_PROJECT" \
  --input /absolute/path/to/invoices.jsonl
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" configure --project "$EVAL_PROJECT" \
  --decisions "$EVAL_WORKSPACE/answers.json"
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" prepare --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" plan --project "$EVAL_PROJECT"
```

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

The `answers.json` patch records only known choices. For a fixed-prompt study
it needs `owner`, `source_model`, `target_model`, `connection`,
`prompt_location`, `provenance`, `quality` and `budget_policy`; a capped policy
also needs `budget_usd`. Do not invent missing values. Model choices select
keys in the connection's `models` object.

- `prompt_location`: zero-based instruction-message index, or `"instructions"`
  for top-level Responses instructions. It identifies the preserved prompt.
- `quality.kind`: `"json_equals"` for both starters.
- `quality.reference_source`: `"supplied"` for explicit labels, or `"recorded"`
  for historical output agreement.
- `quality.fields`: `[""]` for whole-object equality, or agreed dotted paths,
  such as `["invoice_id", "total", "needs_review"]`.
  The triage starter uses `["category", "priority", "queue", "needs_review"]`.
- `quality.description`: the user's correctness criterion.
- `quality.reference_file`: optional mapping of case IDs to expected answers.
- `quality.rules`: supplied general business rules, kept separate from
  case-specific expected answers.
- `provenance`: `"synthetic"`, `"customer"` or `"public"`. Labels stay unreviewed.
- `repetitions`, `concurrency`, `timeout_seconds`: propose and explain these
  settings; engine defaults are 3, 2 and 180 respectively.
- `budget_policy`: `"capped"` or `"unlimited"` according to actual authorization.
  An uncapped study still has case, concurrency, request and timeout limits.
- `cost_metric`: `"cost_usd_per_session"` or `"cost_usd_per_success"`.
- `primary_latency`: `"conversation"` or `"worker"`.
- `minimum_quality` and `minimum_quality_owner`: only for an explicitly
  requested absolute acceptance gate.
- `optimization_enabled`: starts as false. Turning it on is a scope extension
  requiring an optimizer model and separate prompt-change agreement.

The existing joint comparison requires at least 20 validation observations;
repetitions are not independent cases. Group related records with `group_by`
when applicable so related documents do not cross splits.

```bash
# Run the frozen study locally. This makes real model calls.
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" run \
  --project "$EVAL_PROJECT" --backend local --live

# Run the same study in an already deployed AgentCore coordinator.
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" run \
  --project "$EVAL_PROJECT" --backend agentcore --live

# Inspect the same job. These commands do not submit a new evaluation.
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" status \
  --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" results \
  --project "$EVAL_PROJECT"
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" results \
  --project "$EVAL_PROJECT" --backend local
```

AgentCore execution qualifies two routes before submission, adding two model
calls. `results/decision.html` is the cloud report; `local/decision.html` is
the local report. Reports preserve usage, timing, checks and prompt evidence.
The installed demo combines the historical reports into a comparison dashboard;
new runs currently produce one decision dashboard per backend.

For unsupported log formats or grading needs, inspect the bundled engine's
`LOG_REPLAY.md` and `intake/skills/eval-intake/references/input-contract.md`.
Use `docs/LIVE-SETUP.md` for an authorized deployment. Do not confuse an
existing runtime being READY with it containing this plugin's engine:
the implementation fingerprint must match.
