---
name: benchmark-agent-tasks
description: Run AWS CDK repair, Terminal-Bench, SWE-bench, AWS-Bench, or starter agent tasks from chat; compare success, latency, token usage, and cost per successful task across Bedrock, OpenAI, OpenRouter, Codex, and OpenCode.
---

Use the packaged CLI at `../../scripts/bench.py`, resolved relative to this
skill directory. It contains the runner, fixtures, graders, and reporting code;
an installed plugin does not need a neighboring repository checkout.

Read the [CLI reference](references/cli.md) when configuring experiments or
provider authentication, and the [accounting reference](references/accounting.md)
when configuring pricing or interpreting costs. Use Python 3.12+ for upstream
suites; the starter loop also supports Python 3.10+.

The user interacts through chat. Select the suite, prepare its runtime, execute
authorized checks, and explain the results yourself; terminal commands are an
implementation detail unless the user asks for them.

- Use `suites` and `tasks --suite NAME` to discover the packaged task catalogs.
  `starter` uses the existing fixed agent loop. `aws-cdk-smoke`, `terminal-bench`,
  and `swe-bench` use Harbor. `aws-bench` uses the upstream AWS-Bench runner.
- For repository benchmarks, read the [suite integration guide](references/suites.md).
  The same `init --suite`, `plan`, `run`, and `report` flow covers these suites.
  Default upstream selections contain one task; use exact task names and expand
  the selection only to match the user's request.
- To check the CDK benchmark itself, use `prepare --suite aws-cdk-smoke --execute`
  followed by `smoke --suite aws-cdk-smoke --execute`. This runs real containers,
  compilation, synthesis, and grading, without model inference or AWS deployment.
  The reference must pass and the unchanged baseline must fail. Describe it as
  reference validation, not proof that a live Bedrock agent works.

- Use `doctor`, `suites`, `tasks`, `init`, and `plan` to prepare a concrete experiment.
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
- Use the repository or output directory selected by the user, even when the
  chat started in another directory. Keep experiment files, prepared runtimes,
  and results there, outside the installed plugin. Never write credentials into
  experiment JSON or reports. Use existing authentication or environment variables.
- Upstream container agents use their own authentication. Codex/OpenCode on
  Bedrock through Harbor require a Bedrock bearer token and explicit region.
  Installing or signing into the desktop client alone does not establish
  credentials inside a container.
- AWS-Bench needs an explicitly selected testing environment. `aws-env` exposes
  planning and execution for setup, verification, and cleanup. Preserve existing
  authorization; do not infer permission to create accounts or deploy AWS
  infrastructure from a request to install tools or run the local CDK smoke.
- Preserve identical tasks, seed, repetitions, and limits for comparisons.
  `report` rejects differing task protocols and duplicate run IDs.
- Use provider-reported cost when available. Rate cards need an exact model,
  provider, region, tier, retrieval date, and source. Unknown cost is unknown.
  Codex subscription usage and OpenCode's catalog estimates are not invoices.
- Report cost per successful task using all attempt costs, including failures.
  Report success count, cost coverage, and the cost basis alongside any dollar
  figure. Include runner/provider/model/settings and links to `run.json`,
  `REPORT.html`, and `REPORT.md`. Use
  [inspect-results](../inspect-results/SKILL.md) to open interactive reports,
  compare saved runs, or investigate a selected attempt.
- A positive upstream cost remains a runner estimate; an exact rate card fills
  missing estimates when usage is sufficient. Verifier/judge and infrastructure
  costs are excluded. Keep missing trial results and failed attempts visible;
  never turn an absent cost into zero.
- Distinguish synthetic demos, reference/oracle validation, and live model
  measurements. A reference smoke does not validate provider authentication,
  model tool use, or real inference accounting.

Starter tasks test the benchmark plumbing. They do not establish a general
model ranking. For customer conclusions, extend the task suite with the
customer's workload and a deterministic or separately accounted grader.

For streaming latency or load generation in the source repository, consult
its existing `performance/` suite. Durable jobs, a standalone MCP server, and
distributed load generation remain separate extensions.
