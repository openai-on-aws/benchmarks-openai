# Run Bedrock Bench from Codex

For a first run, start with the [plugin quickstart](../plugins/bedrock-bench/README.md).
This walkthrough covers the full feature set and prepared model comparisons.

Bedrock Bench **0.3.0** is a Codex plugin for comparing agent task
success, time, token usage, and cost per successful task. Select **Bedrock Bench**
in the chat composer and describe the experiment. The skill handles the
underlying commands.

The implementation was merged in [PR #5](https://github.com/openai-on-aws/benchmarks-openai/pull/5).
Its code, task fixtures, graders, and reports live in
[`plugins/bedrock-bench`](../plugins/bedrock-bench/). The installed package works
without a neighboring source checkout.

## Current features

| Suite | Packaged catalog | What the agent does |
| --- | ---: | --- |
| `starter` | 3 tasks | Reconcile invoices, order deployments, and triage inference logs using file tools. |
| `aws-cdk-smoke` | 1 task | Repair an SQS → Lambda → DynamoDB CDK application and message handler. |
| `terminal-bench` | 89 tasks | Complete Terminal-Bench 2.0 terminal and repository tasks through Harbor. |
| `swe-bench` | 500 tasks | Repair real issues from SWE-bench Verified using Harbor's converted tasks. |
| `aws-bench` | 9 tasks | Complete AWS-Bench quickstart scenarios in a selected AWS testing environment. |

The plugin includes:

- **Chat-driven discovery, preparation, planning, execution, and reporting.**
  `run` displays a plan until `--execute` is supplied.
- **Multiple targets per experiment**, with explicit models, regions, reasoning
  effort, repetitions, and timeouts. Repository suites pin task sources and the
  harness version; an agent CLI version can also be pinned.
- **Runner choices:** the starter suite supports a fixed native loop, Codex, or
  OpenCode. Repository suites use Codex or OpenCode through their upstream
  harness. Bedrock, OpenAI, and OpenRouter have adapter-specific support.
- **Skill comparisons** for Harbor suites: attach a local skill to a target,
  keep a target without that skill, and compare both on the same tasks. Skill
  contents are fingerprinted.
- **Deterministic CDK grading** in a separate verifier: compilation, synthesis,
  resource wiring, scoped IAM, table preservation, and batch failure handling.
- **Evidence and accounting:** per-attempt outcomes, agent and total wall time,
  usage, cost coverage, raw upstream outputs, source/version metadata, and
  interactive HTML and Markdown/JSON comparison reports. Failed attempts contribute to spend.
- **Results exploration:** filter targets and tasks, inspect recorded attempts,
  search a library of saved runs, compare matching runs, and open saved evidence.
- **Recorded replay:** select a model, scrub its own clock, and inspect stable
  tool-call lists and public task prompts from Harbor/Codex sessions.
- **Charts and follow-ups:** ask for plots or diagrams from saved evidence;
  each substantive response offers contextual next-step prompts.
- **AWS-Bench lifecycle commands** to plan or execute environment setup,
  verification, reset, and cleanup. A model run does not create an environment.

The repository's streaming performance, quality, and API-parity tools remain
available separately. The plugin currently has serial execution and no enforced
dollar budget, durable background jobs, or automatic resume. Repository-agent
model-call and output-token limits are managed by the client. Judge and AWS
infrastructure costs are outside the reported agent inference spend.

## Install or update

Use a Codex CLI with plugin support. Register the repository's marketplace and
install the package from the merged `main` branch:

```bash
codex plugin marketplace add openai-on-aws/benchmarks-openai \
  --ref main \
  --sparse .agents/plugins --sparse plugins/bedrock-bench
codex plugin add bedrock-bench@openai-on-aws
codex plugin list --marketplace openai-on-aws
```

For an existing marketplace already following `main`:

```bash
codex plugin marketplace upgrade openai-on-aws
codex plugin add bedrock-bench@openai-on-aws
```

An installation that still follows the old feature branch needs its marketplace
source changed to `main`; upgrading refreshes the configured branch. Remove that
marketplace registration, then repeat the add/install commands above:

```bash
codex plugin marketplace remove openai-on-aws
```

Open the project folder where you want to save plans and results. Start a fresh
Codex chat there and select **Bedrock Bench** from the plugin picker.

## Start with checks that do not invoke a model

Ask in chat:

> Use Bedrock Bench to show the installed version, available suites, and local
> prerequisites. Then run the synthetic demo and explain its report.

The demo contains six synthetic attempts, including one intentional failure.
It tests the reporting workflow.

Next:

> Use Bedrock Bench to prepare Harbor and run the AWS CDK reference smoke.
> Explain whether the unchanged baseline fails and the reference repair passes.

This downloads dependencies/images and runs Docker containers, but makes no
model calls and deploys no AWS resources. The Terminal-Bench `fix-git` and
SWE-bench `django__django-15098` reference smokes are also available.

Previously validated: 41 plugin tests, package/skill validation, and real
reference container runs for all three of these tasks. The installed package
also passed CDK and SWE-bench reference runs. Live model access, agent behavior,
and inference accounting still need their first measured smoke runs. A
reference solution passing does not establish that a candidate model succeeds.

## Prepared OpenAI-on-Bedrock comparisons

The [dated experiment pack](../examples/bedrock-bench/README.md) contains
configuration files for all three latest general-purpose GPT-6 models, checked
against official documentation on **September 25, 2026**:

| Model | Mantle model ID | Region |
| --- | --- | --- |
| GPT-6 Astra | `openai.gpt-6-astra` | `us-west-2` |
| GPT-6 Sol | `openai.gpt-6-sol` | `us-east-1` |
| GPT-6 Luna | `openai.gpt-6-luna` | `us-east-1` |

All targets use `low` reasoning, Standard pricing, and the same task/timeout
settings within each experiment. Repository tasks use Codex **0.153.4** through
Harbor **0.23.0**, or AWS-Bench's separately pinned runtime. These are explicit
experiment pins; model/CLI compatibility still needs live validation.

The Codex provider is `amazon-bedrock`, which uses Mantle. The native starter
provider is `bedrock-mantle`. Astra's different region is part of the measured
configuration and can affect latency. Earlier GPT-5.6/5.5/5.4 models remain
selectable as additional baselines.

Planning prompt:

> Use Bedrock Bench to review the prepared GPT-6 experiments in
> `examples/bedrock-bench`. Show the model IDs, regions, task counts, timeouts,
> pricing assumptions, and missing prerequisites. Do not execute them.

The CDK, Terminal-Bench, and SWE-bench smoke experiments each have one task
per model: **nine live attempts total** if you later run all three. The optional
native starter smoke adds nine attempts. Full-catalog plans are separate files
so expanding the workload is explicit.

When you decide to launch a specific file:

> Use Bedrock Bench to run `examples/bedrock-bench/gpt6-cdk-smoke.json` now.
> Save the results in `bench-results` and explain task success, cost coverage,
> and cost per successful task for each model.

Repository agents require a Bedrock bearer token in the executing process's
`AWS_BEARER_TOKEN_BEDROCK` environment variable. A desktop login or AWS profile
alone does not supply container authentication. Use an existing authorized
credential source; keep credentials out of chat, JSON, and reports.

AWS-Bench plans have no environment selected. Choose the testing environment,
review its setup/cleanup plan and judge requirements, then provision and verify
it before running those scenarios.

## Read the results

Each run writes `run.json`, `comparison.json`, `REPORT.html`, `REPORT.md`, and
per-attempt evidence under a new directory in `bench-results`. Ask:

> Open the results explorer for my latest saved run. Show the failed attempts.

The explorer works as an offline HTML file. Supported Codex inline views add
**Explain in chat** and **Compare a baseline** actions; standalone browsers
provide copyable prompts. Evidence inspection uses the original run files.

Browse and drill down from chat:

> Show my recent run library. Open the latest live run and replay its tools.

> Plot cost versus success for that run, with the sample counts and cost coverage.

> Diagram the selected attempt's recorded commands, replies, and grader outcome.

The library has search, evidence-type filters, matching-run comparisons, and
time/cost bars. Replay keeps one model's tools visible while you move its slider.
Both are snapshots of saved evidence. Use **Refresh in chat** to rebuild the
library after new results arrive. Follow-up buttons and suggested prompts can
create a new view; they do not automatically run another benchmark.

Compare completed runs only when they have
identical suites, task sources, repetitions, seed, and limits:

> Use Bedrock Bench to compare these two `run.json` files. Show success rate,
> agent time, total time, token usage, cost basis, and cost per successful task.

`cost per success = total attempt spend / successful attempts`. Failed attempts
remain in the numerator. Zero successes or incomplete cost coverage do not
produce a trustworthy finite cost-per-success value. Check the cost basis:
runner estimates and explicit rate-card estimates are not reconciled invoices.
Small smoke subsets establish readiness, not a general model ranking.

The experiment pack includes exact optional terminal commands and the sourced
rate cards. Its files are prepared for manual launch; creating or inspecting
them does not start a benchmark.
