---
name: benchmark-agent-tasks
description: Plan and run reproducible agent task benchmarks, then compare task success, latency, token usage, and cost per successful task across Bedrock, OpenAI, OpenRouter, Codex, and OpenCode.
---

Use the packaged CLI at `../../scripts/bench.py`, resolved relative to this
skill directory. It contains the runner, fixtures, graders, and reporting code;
an installed plugin does not need a neighboring repository checkout.

Read the [usage guide](../../README.md) for configuration, provider mappings,
and cost accounting. Use Python 3.10 or newer.

- Use `doctor`, `tasks`, `init`, and `plan` to prepare a concrete experiment.
  These commands do not authenticate or make model calls.
- Choose a runner separately from a provider. The native loop holds tools and
  agent logic fixed for model/provider comparisons. Codex and OpenCode measure
  complete agent systems. OpenRouter is a provider gateway.
- Preserve the user's model choices and existing authorization. Before live
  execution, make model IDs, region, task count, and run limits explicit.
  Ask only for missing decisions that prevent execution. Installation alone
  does not authorize a paid benchmark.
- `run` displays a plan unless `--execute` is supplied. Use `demo` to validate
  the workflow without credentials; identify all demo metrics as synthetic.
- Keep experiment files and results in the user's working directory, outside
  the installed plugin. Never write credentials into experiment JSON or
  reports. Use the runner's existing authentication or environment variables.
- Preserve identical tasks, seed, repetitions, and limits for comparisons.
  `report` rejects differing task protocols and duplicate run IDs.
- Use provider-reported cost when available. Rate cards need an exact model,
  provider, region, tier, retrieval date, and source. Unknown cost is unknown.
  Codex subscription usage and OpenCode's catalog estimates are not invoices.
- Report cost per successful task using all attempt costs, including failures.
  Report success count, cost coverage, and the cost basis alongside any dollar
  figure. Include runner/provider/model/settings and links to `run.json` and
  `REPORT.md`.

Starter tasks test the benchmark plumbing. They do not establish a general
model ranking. For customer conclusions, extend the task suite with the
customer's workload and a deterministic or separately accounted grader.

For streaming latency or load generation in the source repository, consult
its existing `performance/` suite. Durable jobs, MCP tools, distributed load
generation, and arbitrary repository execution are later stages of this plugin.
