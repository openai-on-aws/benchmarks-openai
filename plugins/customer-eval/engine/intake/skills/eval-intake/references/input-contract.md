# Input contract v0.1

Schema-valid means recognized structure. `ready_for_builder` additionally requires all recorded planning inputs and open questions to be resolved. It never means a live deployment has been tested.

| Section | Meaning |
|---|---|
| experiment | Workflow, objective, decision and accountable owner. |
| models | Candidate and judge configurations. `model_id` is an actual identifier; `model_id_env` names a variable supplying it. Set at most one. |
| prompts | Versioned files and their `{{variable}}` names. |
| agents | Behavior, harness, instructions, workspace, evaluation runtime, optional planned deployment and tools. |
| gateways | AgentCore Gateway endpoint/authentication references. Role distinguishes ordinary tools from tools wrapping model invocations. |
| variants | Explicit combinations of model, prompt and agent; the rows to compare. |
| baseline_variant_id | The comparison's starting configuration. |
| datasets | JSONL files, version, development/holdout split, provenance and label status. |
| checks | One pass/fail quality question per check; optional independent judge and calibration reference. |
| success_criteria | Metric definition, operator, threshold and agreement status. Pass rates use fractions 0..1. |
| execution | Repetitions, per-dataset case limit, concurrency, timeout, requested spending limit and fallback policy. |
| optimization | Permitted prompt, development data, held-out data, objective criterion and iteration bound. |
| open_questions | Unresolved items, affected field and the blocked stage. |
| evidence | Source and user-confirmed/observed/proposed status for important fields. |

All local paths, including workspace and instructions, are relative to the configuration directory. Paths escaping it are rejected. Bring only permitted assets into the package; do not copy credentials or an entire private repository merely to satisfy validation.

For a normalized handoff, the manifest records that original asset directory. The helper resolves it automatically when the input hash matches. After moving assets or editing the normalized configuration, provide `--asset-base` explicitly and write a new handoff revision. The three handoff files alone do not contain the source assets.

Partial prompt/dataset records are allowed: retain their stable IDs and known facts while leaving paths, versions or dataset split null. The validator reports those as unresolved planning inputs instead of forcing the known records to be discarded.

For a model comparison, hold the agent, prompt, tools, dataset and budget policy constant. For a prompt comparison, hold the model and agent constant. For a harness comparison, record context/tool/budget differences instead of attributing every outcome to the harness.

## Dataset records

Each nonempty JSONL line needs a nonempty string `id` and an `input` object containing all prompt variables. `expected` holds evaluator labels; `metadata` can identify source, category, difficulty and provenance. Expected answers are not agent inputs.

A `human_reviewed` dataset requires an expected value for every case. An absent field or bare `null` is a missing label. `false` and `0` are valid labels; if the correct answer is intentionally null, represent it explicitly, for example `{"answer": null}`. Label presence does not prove a human actually reviewed it.

The validator detects duplicate IDs and identical input objects across development/holdout. It does not detect paraphrases, related commits or other semantic leakage. The local wrapper does not pass expected labels in the task. A read-only local sandbox is not a guarantee of isolation from other local data; formal evaluations need runner-enforced data isolation.

## Score definitions

Missing, malformed, errored and skipped outputs must not silently become correct answers. The example's all-required-checks pass rate uses every attempted case as denominator. Distinguish first visible answer latency from full task completion including tools. Cost includes failed attempts/retries and states whether judges, tools and infrastructure are included. Cost per success is undefined with zero successes. Unknown cost remains unknown.

These are input requirements; the package does not implement graders or compute scores. Proposed thresholds remain unconfirmed until agreed by the owner.

An `all_required_checks_pass_rate` criterion needs at least one required check once checks are supplied. A draft with no checks can still be saved, but remains incomplete. Total evaluation budget is separate from a cost-per-answer or cost-per-success target.

## Extensions

The model route can be direct or a Gateway tool wrapping a model invocation. Ordinary business tools attach to the agent. A described integration can remain unverified. Native multimodal payloads, multi-agent topology, simulator settings and framework-specific tuning need a versioned extension once runner contracts are agreed; do not conceal them in an unvalidated blob.
