# Handoff to eval-builder

For this integrated Model Upgrade package, `customer_eval.contract.load` imports the original input YAML or the unchanged normalized `evaluation-input.json` directly. Use `scripts/run_optimization.py plan` for executable validation and `scripts/submit_agentcore.py` for an explicitly requested cloud run. Keep the original asset directory available when using the normalized handoff. See the module's `CUSTOMER-WALKTHROUGH.md` for the customer chat flow.

The notes below describe the separate general `Interactive Learning/eval-builder` workflow, not the integrated Model Upgrade runner.

Reviewed source: AWS workshop commit `91a861ee84edd7e5d9afb70652f6ad7b8e1d6a29`, `Interactive Learning/eval-builder/SKILL.md`. The builder discovers workloads, samples failures, proposes evaluators, creates `evals/eval_config.yaml`, runs evaluations and generates a report. It has no importer for this new contract.

Give it the original input configuration, brief, normalized JSON, manifest and access to the original asset directory. The manifest contains the original directory and hashes; relative paths in normalized JSON still use that base.

The intake helper validates an unchanged normalized handoff using its manifest's asset directory. On a different computer, restore the referenced files with the same relative layout and pass `--asset-base /absolute/path/to/restored-inputs`. Missing source files remain a gap. Keep prior handoffs and create a new revision after changes.

Suggested instruction:

> Use this input package as the starting requirements. Check the manifest's asset_base and validate referenced files. Read the brief and open items, inspect the actual workload, and distinguish supplied assumptions from repository evidence. Preserve chosen variants, baseline, criteria and run limits. Ask only for missing or conflicting information. Design evaluators from observed failures and map the contract into your generated configuration. Identify unsupported integrations. A configuration does not authorize running or deploying.

| Input | Builder responsibility |
|---|---|
| Objective, decision, evidence | Verify scope and retain decision criteria. |
| Models, prompts, agents, variants | Confirm invocation paths; generate supported adapters. |
| Datasets, expected outcomes | Inspect failures; calibrate checks. |
| Checks, success criteria | Implement without quietly changing agreed definitions. |
| Execution requirements | Enforce timeout, concurrency and spending limits in the runner. |
| Optimization | Keep holdout examples and labels out of prompt tuning. |
| Open questions | Resolve before the affected execution/interpretation step. |

The builder remains responsible for evaluator design and execution under the user's instructions. This package does not patch that repository or claim its HTML helper already displays the new contract.
