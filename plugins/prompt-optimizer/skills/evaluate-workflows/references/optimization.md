# Compare models or optimize a prompt

Use the same progressive intake and engine for both modes. Confirm the customer's
goal and reuse their logs/configuration before asking for missing choices.

| Mode | Models | Cases used | Result |
|---|---|---|---|
| `compare` | Source and target | Validation and holdout; no unused development third | Original prompt on both models |
| `optimize` | Source, target and optimizer | Separate development, validation and holdout groups | Original prompt, candidates, selected prompt and held-out results |

Start an optimization study with:

```bash
"$EVAL_PYTHON" "$PLUGIN/scripts/workflow_eval.py" start \
  --project "$EVAL_PROJECT" --mode optimize --goal "Improve my support-ticket classification prompt"
```

Then inspect the customer's logs and configure their resolved decisions as in
[the shared workflow](workflow.md). Set `optimizer_model` to a key in the
connection file, independently of `source_model` and `target_model`. The optimizer
may use a different model and reasoning setting. Preserve exact requested routes.

For a worked starter, use `examples/support-ticket-triage/tickets.jsonl` and its
decision template. Set `optimization_enabled: true`, `optimizer_model` to the
chosen connection key, and put the general policy from `prompt.txt` in
`quality.rules` if the customer wants that policy preserved while wording changes.
Expected per-ticket answers must never be copied into general rules.

In captured-request replay, the editable prompt must retain the rules the
application needs. `quality.rules` gives the optimizer business context; it does
not inject those rules into application requests. The optimizer may remove
redundant wording, but must not assume a separate policy supplies instructions
that only exist in the editable prompt.

With 60 distinct cases and one repeat, optimization splits them into 20 development,
20 validation and 20 holdout cases. Development failures and general business rules
inform prompt revision. Fresh matched validation runs select candidates. The
selected prompt is frozen before holdout; held-out answers never guide mutation.
Intake flags a validation minimum before preparation. Repeats measure variability,
not additional independent cases.

Run `prepare`, `plan` and `preflight --backend local` or `agentcore`. Use the
customer's existing execution authorization, then `run --backend ... --live`.
The output includes `selected-prompt.json`, `report.json`, `decision.html` and a
dashboard. Report the source model, target model and optimizer model/reasoning
separately. Codex orchestrates this conversation using the model selected in Codex;
the replay application calls its configured model directly.

Show original and selected prompts even when identical. Explain rejection reasons
and any challenger tradeoffs. Do not promise a prompt change, lower latency or a
winner. Compare quality, cost per attempt, cost per successful result and p95 time;
missing usage is unknown, and zero successes have no defined cost per success.
The current automatic selection requires quality gates/non-regression and no
worse measured cost or latency; explicit customer thresholds apply only if supplied.

Raw PDF/image processing and live customer tools require an application adapter;
text-log replay does not execute those upstream application steps.
