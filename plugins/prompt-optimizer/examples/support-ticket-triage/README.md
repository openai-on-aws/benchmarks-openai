# Starter: support-ticket triage

Compare two models on a fixed prompt that assigns a support ticket's
**category, priority, destination queue and human-review flag**. This uses the
same Prompt Optimizer entrypoint as the invoice example. It evaluates one
structured decision per ticket; it does not create tickets, send replies,
change permissions or execute help-desk tools.

The starter contains **60 distinct synthetic tickets with unreviewed expected
answers**. There are no live model results for this example. It is a starting
point for designing a study, not a benchmark of real customer-support quality.

## What is included

| File | Purpose |
| --- | --- |
| [prompt.txt](prompt.txt) | The proposed classification and escalation policy. |
| [tickets.jsonl](tickets.jsonl) | Self-contained manual replay records: fixed prompt, ticket text, output settings and expected answer. |
| [cases.md](cases.md) | A readable inventory of all tickets and expected labels. |
| [output.schema.json](output.schema.json) | The four-field JSON response schema used by every record. |
| [decisions.template.json](decisions.template.json) | Proposed evaluation settings, with owner, connection and budget choices left unset. |

The cases cover billing, account access, bugs, feature requests, security
incidents and other requests. Boundary cases include working workarounds,
resolved incidents, misleading "urgent" subjects, explicit corrections,
contradictory descriptions, unrelated issues in one ticket and an instruction
embedded in quoted ticket text.

The policy and references are deliberately visible. Review them with someone
who owns triage before adopting them. The prompt is repeated inside each replay
record; editing `prompt.txt` alone does not change the requests.

## 1. Start in Codex

Install Prompt Optimizer using the [plugin instructions](../../README.md), then
ask its **Evaluate Workflows** skill:

> Use the support-ticket-triage starter. Show me its routing policy and some
> difficult cases. Help me decide which mistakes matter, then prepare a
> fixed-prompt comparison of two models. Do not run models yet.

Both starters use the `evaluate-workflows` skill and
`scripts/workflow_eval.py`. Codex resolves the installed plugin path and keeps
your project, connection file, environment and reports in your workspace.

## 2. Inspect from a terminal

For terminal use, set the absolute plugin directory. From the repository root:

```bash
export EVAL_PLUGIN="$PWD/plugins/prompt-optimizer"
export EVAL_WORKSPACE="$HOME/prompt-optimizer-workspace"
mkdir -p "$EVAL_WORKSPACE"

python3.12 -m venv "$EVAL_WORKSPACE/.venv"
export EVAL_PYTHON="$EVAL_WORKSPACE/.venv/bin/python"
"$EVAL_PYTHON" -m pip install -r "$EVAL_PLUGIN/requirements.txt"

"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" start \
  --project "$EVAL_WORKSPACE/triage-study-v1" \
  --goal "Compare support-ticket triage quality, model cost and latency using one fixed policy."
"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" inspect \
  --project "$EVAL_WORKSPACE/triage-study-v1" \
  --input "$EVAL_PLUGIN/examples/support-ticket-triage/tickets.jsonl"

cp "$EVAL_PLUGIN/examples/support-ticket-triage/decisions.template.json" \
  "$EVAL_WORKSPACE/triage-decisions.json"
cp "$EVAL_PLUGIN/connection.template.json" "$EVAL_WORKSPACE/connection.json"
```

For an installed copy, use its absolute directory for `EVAL_PLUGIN` instead
of the repository path. Choose a new project name for another revision.

Open `triage-study-v1/input-preview.html`. Expect 60 accepted records, no
exclusions and one prompt group. These steps make no model calls and do not
need AgentCore infrastructure.

## 3. Choose the models and prepare

Edit the workspace copies, or ask Codex to collect the missing choices:

- In `connection.json`, set the two model routes, your AWS profile reference,
  Region, verified input/output/cache prices, pricing source, verification
  date and the integer `pricing_max_input_tokens` bound covered by those
  prices. For a local-only study, set `cloud.runtime_arn`
  to `null`; the other cloud fields are unused. For both backends, fill in
  your matching AgentCore runtime and private bucket before preparation.
- In `triage-decisions.json`, set `owner` and the **absolute** `connection`
  path. Confirm that `source_model` and `target_model` select the intended
  `m1` and `m2` connection entries.
- Choose `budget_policy`: `"capped"` with a positive `budget_usd`, or
  `"unlimited"` only if that is your intended authorization. The template
  leaves both unset. A saved budget does not itself authorize a live run.
- Review the required whole-object JSON check and the proposed settings. Keep
  `provenance: "synthetic"` for these bundled fixtures; their labels stay
  `unreviewed`. No acceptance threshold or model winner is predetermined.

Then run:

```bash
"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" configure \
  --project "$EVAL_WORKSPACE/triage-study-v1" \
  --decisions "$EVAL_WORKSPACE/triage-decisions.json"
"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" prepare \
  --project "$EVAL_WORKSPACE/triage-study-v1"
"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" plan \
  --project "$EVAL_WORKSPACE/triage-study-v1"
```

With the supplied settings, the plan contains 30 validation and 30 holdout
cases, one repetition, two models and no prompt optimization. All 60 selected
cases produce **120 evaluation trials per backend**.
AgentCore adds **two route qualification calls** before submission.
No development cases are reserved in fixed-prompt mode. Preparation and planning make no model calls.

Preparation freezes the inputs and engine fingerprint. To change the policy,
references, connection or settings afterward, start a new project revision.

## 4. Run and read the report

When you have authorized paid model calls, run the prepared study locally:

```bash
"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" run \
  --project "$EVAL_WORKSPACE/triage-study-v1" --backend local --live
"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" results \
  --project "$EVAL_WORKSPACE/triage-study-v1" --backend local
```

Open `triage-study-v1/local/decision.html` for field checks, failed cases,
model usage, estimated model cost and latency.

For AgentCore, first follow [AWS setup](../../docs/LIVE-SETUP.md). Installation
does not create the runtime, role or bucket. With those resources already
included in the prepared connection:

```bash
"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" run \
  --project "$EVAL_WORKSPACE/triage-study-v1" --backend agentcore --live
"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" status \
  --project "$EVAL_WORKSPACE/triage-study-v1"
"$EVAL_PYTHON" "$EVAL_PLUGIN/scripts/workflow_eval.py" results \
  --project "$EVAL_WORKSPACE/triage-study-v1"
```

The cloud report is `triage-study-v1/results/decision.html`. Use `status`
and `results` to inspect the existing submission rather than submitting
another job to see progress. New studies produce a dashboard per backend;
the combined invoice dashboard is a separate recorded example.

## Questions worth settling before extending it

- Is missing a security escalation worse than over-escalating ordinary
  requests? The current checks count field mismatches, not business cost.
- Is a human-review flag an acceptable answer for an ambiguous ticket, or
  should the model ask a clarifying question? This starter only checks the
  flag; it does not evaluate a conversation with a customer.
- Who will review the references and adjudicate disagreements about priority?
  Agreement with these constructed answers is not established accuracy.
- Which real traffic, languages and rare failures should replace or extend
  this balanced synthetic set? Twenty validation tickets are a small sample,
  and the deterministic split does not guarantee class balance within each
  split.
- Are you evaluating the model's decision or the whole support agent?
  Actual tool use, permissions, retries and customer outcomes need additional
  tests beyond this text-only replay.

For real exports, supply supported logs or manual records with independently
reviewed expected answers. Group related records from the same conversation
with `group_by` so they cannot cross development, validation and holdout.
Keep a fresh holdout when changing the prompt or policy in response to errors.
