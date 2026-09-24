# Bedrock Bench: agent task economics

Status: first implementation complete; offline validation passed. Live model
and CLI smoke runs are the next validation stage.

Build an installable **Bedrock Bench** plugin, published by **OpenAI on AWS**,
inside `openai-on-aws/benchmarks-openai`. Its first question is: **What does it
cost to finish a task correctly, and how long does it take?**

## Product and experiment design

Codex and OpenCode are agent runners. OpenRouter is a model gateway. Record
runner, runner version, provider, model, region, reasoning setting, task
revision, limits, and pricing provenance separately.

Two experiment types answer different questions:

- Hold the native agent loop, tools, tasks, and limits fixed to compare model
  and provider behavior.
- Run the same task fixtures through Codex and OpenCode to compare complete
  agent systems. Their prompts, tools, caching, and internal loops differ;
  their results are not isolated model comparisons.

The existing `performance/` suite remains the entry point for streaming
latency and concurrency tests. Existing `quality/` suites remain available for
knowledge, coding, and agentic evaluations. The new task runner complements
these with portable workspaces, explicit scoring, and consistent accounting.

## First release

1. A self-contained plugin with its own name, icon, skills, and executable
   Python tools. It must work from Codex's installed cache without depending
   on a neighboring source checkout.
2. A small, versioned task suite with generated local fixtures and deterministic
   artifact validators: reconcile invoices, plan a dependency-aware deployment,
   and diagnose a failed inference workload from request logs.
3. A native agent loop using filesystem tools and the Responses API for
   Bedrock/OpenAI, plus Chat Completions for OpenRouter.
4. Codex CLI (`exec --json`) and OpenCode CLI (`run --format json`) adapters.
   Use a fresh task directory and session per attempt; retain structured
   events, tool counts, runner failures, and measured wall time.
5. A dependency-free offline demo, dry-run planning, and a doctor command.
   Paid execution is explicit. Models and rates are caller-selected.
6. JSON results and a Markdown comparison report. Preserve per-attempt
   evidence, configuration hashes, and scoring details. Reject comparisons
   with different task protocols.

These starter tasks validate the measurement system; they are not a
representative public leaderboard or a replacement for customer workloads.

## Accounting contract

- Cost per attempt = all attributable inference spend for that attempt.
- Cost per successful task = total spend for **all attempts**, including failed
  attempts, divided by the number of successful attempts.
- Zero successes yields no finite cost per success.
- An unknown charge or unobserved usage remains unknown, never zero. Reports
  show known-cost subtotal and cost coverage separately.
- OpenRouter's `usage.cost` is provider-reported account cost. Retain any
  upstream-provider cost as separate evidence, not an additional charge.
- Codex token usage is not an invoice. A supplied rate card yields an estimate;
  a ChatGPT subscription run does not establish marginal API spend.
- OpenCode's emitted `cost` is a harness estimate unless independently
  reconciled with a provider billing source. A zero estimate may indicate
  missing catalog prices and requires an explicit rate card.
- Normalize cached input and reasoning token semantics by runner. Cached
  tokens are a subset of total input; reasoning tokens are a subset of total
  output in the normalized schema. Do not charge these subsets twice.
- Rate cards include provider, exact model ID, region, service tier, date,
  source URL, and rates per million tokens. Unsupported pricing dimensions
  remain unknown. Do not use remembered model prices.
- Preserve errors and partial usage on failed or timed-out runs. SDK retries
  are disabled in the native loop so hidden retry spend does not disappear.
- Report inference cost scope explicitly. Runner infrastructure, subscriptions,
  external tools, and judges need their own accounting before claiming total
  business cost.

## Bounds and reproducibility

All native tasks have maximum turns, output tokens per response, and wall time.
CLI runners have a wall-time limit; their internal request/output limits differ
and are recorded as such. An observed dollar stopping threshold cannot guarantee
an exact billing cap while a request is in flight.

Native tools can access only the attempt workspace. Grading uses immutable
expected data retained by the controller, outside the candidate workspace.
CLI agents retain their installed client's permission behavior; a temporary
directory is not itself an OS security boundary. OpenCode receives a task-only
agent configuration with external network tools, shell, and delegation disabled.
Codex uses its workspace-write sandbox. No generated code is executed by the
artifact graders.

Fixtures, task prompts, tool schemas, limits, and repetitions contribute to
comparison identity. A fixed seed creates the same task instances for every
target. Model sampling is not claimed to be deterministic.

## Subsequent releases

- Customer task packs and genuine repository repair tasks with grading in
  disposable containers.
- Controlled provider routing, cache-state experiments, repeat confidence
  intervals, and paired comparisons at useful sample sizes.
- Import existing `quality/agentic_evals.py` trajectories and inspect-format
  results into the same accounting/report schema.
- Durable jobs and an MCP interface: `plan_benchmark`, `start_benchmark`,
  `get_run`, `cancel_run`, and `compare_runs`.
- Distributed request-rate and concurrency sweeps, quota diagnostics, and
  per-attempt retry accounting for the existing latency suite.
- Optional AWS Labs RFC once the server interface and broader Bedrock use
  cases are proven.

## Validation and release boundary

Offline checks cover artifact scoring, path containment, rate provenance,
cache/reasoning accounting, missing costs, failed attempts, process timeouts,
event parsing, comparison matching, and execution gates. Test the plugin from
an isolated copied package as well as the repository entry point.

CLI adapter tests use recorded-shape synthetic events and controlled fake
executables. A live smoke run is a separate validation stage requiring the
chosen client, model access, and an explicit run configuration. Synthetic
demonstration reports are visibly labeled and are never model measurements.
CLI auxiliary model calls that do not appear in emitted events are outside
the first release's measured cost coverage.

For the first live pilot, select one available Bedrock model, one region, and
one repetition of the three tasks. Run the native loop first, inspect its
usage and grading evidence, then repeat with the same fixtures through Codex
or OpenCode. Add current, sourced rates before interpreting estimated dollar
costs. A provider-reported charge can be recorded without a rate card.

## Sources

- [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [OpenCode CLI](https://opencode.ai/docs/cli/)
- [OpenCode configuration](https://opencode.ai/docs/config/)
- [OpenRouter usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)
- [OpenRouter request schema](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)
- [Existing agentic evaluations](../quality/agentic_evals.py)
