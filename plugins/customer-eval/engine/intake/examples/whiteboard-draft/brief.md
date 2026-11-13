# Whiteboard evaluation — intake draft

Experiment: whiteboard_evaluation
Objective: Unknown
Decision: Unknown
Owner: Unknown

Status: Draft; resolve the open items below.
This package does not authorize execution or establish runtime compatibility.

## Explicit comparison variants

| Variant | Agent | Model reference | Prompt | Route |
|---|---|---|---|---|
| m1_p1 | a1 | m1 | p1 | Not selected |
| m2_p1 | a1 | m2 | p1 | Not selected |

Baseline: Not selected

## Criteria

- quality: Quality measure and denominator to agree for the chosen agent workflow.; threshold not agreed; unknown.
- latency: 95th percentile wall-clock latency from task dispatch to final response, including tool calls.; threshold not agreed; unknown.
- cost: Total model, judge, tool and infrastructure spend over all attempts / successful cases; undefined when there are zero successes.; threshold not agreed; unknown.

## Models and agent setup

- m1: model ID not selected; provider unknown; API unknown; compatibility unverified.
- m2: model ID not selected; provider unknown; API unknown; compatibility unverified.
- a1: harness codex; runtime unknown; Gateway none specified; Agent under evaluation; its customer workflow is not yet specified.
  Planned deployment: agentcore_runtime; this does not establish the current evaluation runtime.
- Gateway ag: AgentCore Gateway; role not selected; authentication not selected; compatibility unverified.

## Prompts and data

- Prompt p1: file not supplied; version unknown.

## Evidence and assumptions

- /agents/0/harness [user_confirmed]: User confirmed Codex for both intake and the agents being evaluated.
- /gateways/0/kind [user_confirmed]: User clarified AG means AgentCore Gateway.
- /agents/0/description [user_confirmed]: User clarified A means Agents; no specific workflow supplied yet.
- /agents/0/planned_runtime [proposed]: ACR is interpreted as AgentCore Runtime from the whiteboard; deployment details remain to be confirmed.
- /variants [proposed]: Working interpretation of M1/M2 and P1 on the whiteboard; concrete model and prompt selections are pending.
- /optimization [observed]: Whiteboard shows Prompt Optimizer / Iterate on Score; exact optimization settings remain to be agreed.

## Open items

- experiment.objective
- experiment.decision
- experiment.owner
- Add at least one datasets entry
- Add at least one checks entry
- models.m1: model identifier or environment reference
- models.m1.provider
- models.m1.api
- models.m2: model identifier or environment reference
- models.m2.provider
- models.m2.api
- prompts.p1.path
- prompts.p1.version
- gateways.ag.role
- gateways.ag.endpoint_env
- gateways.ag.auth
- gateways.ag.credential_ref
- agents.a1.runtime
- agents.a1.sandbox
- agents.a1.side_effects
- variants.m1_p1.route
- variants.m2_p1.route
- Choose baseline_variant_id for the comparison
- success_criteria.quality: obtain agreement on threshold
- success_criteria.latency: obtain agreement on threshold
- success_criteria.cost: obtain agreement on threshold
- execution.repetitions
- execution.max_cases_per_dataset
- execution.concurrency
- execution.timeout_seconds
- execution.budget_usd
- optimization.development_dataset_ids
- optimization.holdout_dataset_ids
- optimization.max_iterations
- optimization.objective_criterion_id
- workflow (planning): What customer task should the agent perform, and what decision should the evaluation support?
- gateway_role (planning): Does AgentCore Gateway expose model-invocation tools or business tools while the agent calls its model directly?
- model_ids (execution): Which actual models/providers should M1 and M2 represent?
- prompt_data (planning): Where are P1 and the representative test cases, and which expected outcomes have been reviewed?
- runtime (execution): Where will the first evaluation run, and is AgentCore Runtime the planned deployment?
- targets (interpretation): What quality, latency, cost and run-budget requirements are agreed?

## Runtime verification still required

- m1: verify exact model, API, endpoint, permissions, parameters and pricing
- m2: verify exact model, API, endpoint, permissions, parameters and pricing
- ag: verify Gateway tool discovery, authorization and invocation
- a1: verify Codex version, profile, model compatibility and effective tool permissions
- Optimizer must never receive holdout labels or cases while revising prompts

## Builder handoff

Read evaluation-input.json as supplied requirements, then inspect the actual workload.
Use repository evidence to confirm or challenge these inputs; retain their provenance.
Design evaluators from observed failures. Do not silently change model, prompt, baseline or thresholds.
Map these inputs into the builder-generated eval_config.yaml. No automatic importer exists in the reviewed workshop.
Preserve attempted, invalid, failed, skipped and scored counts; report quality, time and total cost separately.
The input contract describes desired behavior; enforcement belongs to the runner and gateway integrations.
