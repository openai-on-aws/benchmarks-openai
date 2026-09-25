# Bedrock Bench

Measure whether an agent finishes a task, what the attempt costs, and how long
it takes. The plugin bundles its complete Python implementation, so it also
works from Codex's installed plugin cache.

Use the installed **Bedrock Bench** plugin in chat:

> Run the AWS CDK smoke test and explain what passed.

> Use Terminal-Bench to compare these two models on the fix-git task.

> Plan an AWS-Bench run against my testing environment, including setup and cleanup.

The skill selects tasks, prepares the tools, runs the authorized experiment,
and explains its report. The commands below are also available for automation.

## Benchmark suites

| Suite | Execution | Tasks |
|---|---|---|
| `starter` | Built-in native loop or local Codex/OpenCode | Three deterministic JSON tasks |
| `aws-cdk-smoke` | Harbor containers | CDK repair: SQS → Lambda → DynamoDB |
| `terminal-bench` | Harbor | Pinned Terminal-Bench 2.0 tasks |
| `swe-bench` | Harbor | Pinned SWE-bench Verified task conversion |
| `aws-bench` | AWS-Bench | Pinned quickstart tasks against a real AWS environment |

Every suite uses `plan`, `run`, and `report`. Upstream selections default to one
task; `--task` and `--all-tasks` make expansion explicit. Models and prices are
never selected silently. Repository suites need Python 3.12+ and Docker.

```bash
python3.12 bench.py suites
python3.12 bench.py tasks --suite terminal-bench
python3.12 bench.py prepare --suite aws-cdk-smoke --execute
python3.12 bench.py smoke --suite aws-cdk-smoke --execute
```

The CDK smoke executes real compilation, synthesis, and grading in separate
containers. The unchanged baseline must fail and the reference repair must
pass. It makes no model calls and deploys no AWS resources. Reference smokes
are also available for the default Terminal-Bench and SWE-bench tasks.

To measure a real agent:

```bash
python3.12 bench.py init --suite aws-cdk-smoke \
  --runner codex --provider amazon-bedrock \
  --model "$BEDROCK_MODEL" --region us-west-2 --out cdk-experiment.json
python3.12 bench.py plan cdk-experiment.json
python3.12 bench.py run cdk-experiment.json --execute
```

Set `BEDROCK_MODEL` to an accessible model. Container agents require
`AWS_BEARER_TOKEN_BEDROCK`; local desktop authentication alone is not container
authentication. `--agent-version` pins the agent CLI. `--skill PATH` adds a
local skill to a Harbor target for controlled skill comparisons.

AWS-Bench uses a separate prepared runtime and an explicitly named AWS testing
environment. `aws-env init/setup/verify/reset/cleanup` exposes its lifecycle;
each command plans by default and requires `--execute` to act. Setup provisions
billable AWS resources. The default quickstart also uses a model judge.
See the [suite integration guide](skills/benchmark-agent-tasks/references/suites.md)
for the exact flow, provider support, limits, and interpretation.

Upstream results retain raw trial files, task commit/checksum, agent version,
and installed harness versions. Failed or missing trials remain visible.
Agent-only and total wall time are reported separately. Upstream cost figures
are estimates; infrastructure and verifier/judge costs are excluded.

The sections below describe the original `starter` suite unless stated otherwise.

## Try it without credentials

From the repository:

```bash
python3 bench.py doctor
python3 bench.py tasks
python3 bench.py demo --out bench-results
```

From the plugin folder, use `python3 scripts/bench.py` instead. Python 3.10+
is required. The demo uses invented token counts, costs, and model latency,
including an intentional task failure. It makes no network calls.

Each run gets a new directory with `run.json`, `comparison.json`, `REPORT.md`,
and per-attempt workspaces, event logs, and stderr. Results are saved after
every attempt. `trace_sha256` links an attempt to its captured events.

## Create a real experiment

Choose a model ID available to your account and endpoint. For example:

```bash
python3 bench.py init \
  --runner native --provider bedrock-mantle \
  --model "$BEDROCK_MODEL" --region us-west-2 \
  --reasoning-effort low --out experiment.json
python3 bench.py plan experiment.json
python3 bench.py run experiment.json
```

Set `BEDROCK_MODEL` to your chosen model before this command. Omit
`--reasoning-effort` for models without that setting. Both `plan` and `run`
above only display the plan. To make model calls:

```bash
python3 -m pip install -r plugins/bedrock-bench/requirements.txt
python3 bench.py run experiment.json --execute --out bench-results
```

The packaged requirements path is `requirements.txt` when working directly
inside an installed plugin. CLI adapters need their respective installed
clients, not the Python model SDKs.

Add targets to the generated JSON to compare several configurations. Each
target needs a unique `id`.

| Runner | Provider | Model field | Authentication |
|---|---|---|---|
| `native` | `bedrock-mantle` | Exact Mantle model ID | AWS credentials/profile or `AWS_BEARER_TOKEN_BEDROCK` |
| `native` | `bedrock-runtime` | ID accepted by that model's Runtime Responses endpoint | Same Bedrock credentials |
| `native` | `openai` | Exact OpenAI API model ID | `OPENAI_API_KEY_SAAS` or `OPENAI_API_KEY` |
| `native` | `openrouter` | OpenRouter's model ID, including its organization prefix | `OPENROUTER_API_KEY` |
| `codex` | `openai` / `amazon-bedrock` | Exact ID accepted by the configured Codex provider | Installed Codex authentication or provider credentials |
| `opencode` | Installed OpenCode provider ID | Model part of OpenCode's `provider/model` identifier | Installed OpenCode authentication or environment |

`aws_profile` and `region` are optional target fields; Bedrock requires `region`.
The Codex adapter requires a CLI supporting `--ignore-user-config` and the
chosen built-in provider. It uses workspace-write sandboxing and disables web
search. OpenCode uses a dedicated agent with read/glob/grep/edit permissions,
a step limit, and denied shell, delegation, and external-directory access.
Managed and other client configuration can still affect CLI runs.

For native OpenRouter targets, optional `"routing": ["provider-name"]` fixes
provider preference and disables fallback. Otherwise, OpenRouter chooses the
upstream provider; returned provider identity is retained when available.

Native requests use a fixed tool loop with `list_files`, `read_file`, and
`write_file`. Bedrock/OpenAI use Responses; OpenRouter uses Chat Completions.
API differences and model support remain part of the recorded configuration.
Native SDK retries are disabled. CLI retries are client-managed and may not
be exposed individually in their events.

## Limits and scoring

`limits` contains `timeout_seconds`, `max_turns`, and `max_output_tokens`.
The defaults are 120 seconds per task, 10 native model calls, and 2,048 output
tokens per native response. OpenCode receives the step limit; Codex's internal
call count and both CLI clients' output-token limits are client-managed.
The controller stops a timed-out process group and preserves partial evidence.

Every attempt uses a fresh directory. The controller grades `answer.json`
against expected data held outside that directory. Inputs changing during an
attempt cannot change the expected answer. Graders do not execute generated
code. This is an artifact benchmark; the fixture directory is not an OS
sandbox for a CLI client.

The tasks cover invoice reconciliation, dependency-aware deployment planning,
and inference-log triage. They are small starter tasks for validating the
measurement system, not a representative quality leaderboard.

## Cost accounting

`cost_per_success = total spend across all attempts / successful attempts`.
Failures contribute to the numerator. Zero successes produces no finite cost
per success. Missing usage/cost produces `null`, with a known-cost subtotal
and cost coverage reported separately.

Cost bases:

- `provider_reported`: OpenRouter's reported account charge, excluding any
  separately reported upstream cost from the total to avoid double counting.
- `rate_card_estimate`: an explicit rate card applied to recorded usage.
- `runner_estimate`: OpenCode's emitted catalog-based estimate.
- `synthetic`: invented demonstration data.
- `reference_no_model`: a reference/oracle check, excluded from model comparisons.
- `unknown`: insufficient pricing or usage evidence.

An OpenCode estimate of zero can mean missing catalog prices. It remains
unknown unless an explicit rate card supports the cost calculation.

Codex token usage does not reveal a subscription's marginal dollar cost.
For API-equivalent estimates, add a rate card matching the exact provider,
model, region (or `null`), and service tier. No live model prices are bundled.

Each entry in `rate_cards` requires:

| Field | Meaning |
|---|---|
| `provider`, `model`, `region`, `service_tier` | Exact target identity |
| `as_of` | Source date, `YYYY-MM-DD` |
| `source` | HTTPS pricing reference |
| `input_usd_per_million` | Ordinary input rate |
| `output_usd_per_million` | Output rate, including reasoning where reported in output |
| `cached_input_usd_per_million` | Required to price observed cache reads |
| `cache_write_input_usd_per_million` | Required to price observed cache writes |
| `max_input_tokens` | Optional applicability boundary for a flat context-price band |

Input totals include cache reads/writes; output totals include reasoning.
Pricing subtracts cached/write tokens from ordinary input before charging
their separate rates. Reasoning tokens are not added again to output.
Missing cache details or an exceeded context-price boundary leaves the estimate
unknown. OpenCode totals are normalized from its separately reported subsets.
Configure a price band that actually applies to the task context; conditional
pricing not expressed in a rate card is outside the first release.

Inference costs exclude runner infrastructure, subscriptions, external tools,
and judges. CLI usage events may omit auxiliary model calls, such as client
housekeeping. These reports account for emitted usage, not a reconciled
account bill. No model-based judge is used by the starter suite.

## Compare completed runs

```bash
python3 bench.py report \
  bench-results/RUN_A/run.json bench-results/RUN_B/run.json \
  --out bench-results/comparison
```

Runs must have identical tasks, fixtures, repetitions, seed, and configured
limits. Synthetic/live mixes, duplicate run IDs, and interrupted runs are
rejected. Different runners compare complete agent systems; use the same
native loop when investigating model/provider differences.

## Install in Codex

The repository includes an `openai-on-aws` marketplace pointing to this
self-contained plugin. Install the current preview from GitHub:

```bash
codex plugin marketplace add openai-on-aws/benchmarks-openai \
  --ref codex/bedrock-bench-agentic-harness \
  --sparse .agents/plugins --sparse plugins/bedrock-bench
codex plugin add bedrock-bench@openai-on-aws
codex plugin list --marketplace openai-on-aws
```

After the feature branch is merged, use `--ref main` to follow the main branch.
If you already have this revision checked out, you can instead register the
checkout with `codex plugin marketplace add .` from the repository root.
Both sources use the same marketplace name; choose one source.

In the Codex app, select **Bedrock Bench** from the plugin picker and try:

> Show the offline Bedrock Bench demonstration and explain cost per successful task.

Or prepare a live experiment without running it:

> Plan a three-task benchmark using the native runner on Amazon Bedrock in
> us-west-2. Ask me which model to use and show the run limits before execution.

The manifest supplies the display name, composer icon, and brand color for the
native plugin mention. This is repository distribution; it is not a listing in
OpenAI's public Plugins Directory.

### Test the installed package from a terminal

The following commands use Python 3.12 and the installed `0.2.0` package.
`codex plugin add` prints the installed directory; use that directory if your
Codex home or package version differs. Run from your own working directory so
results are saved outside the plugin cache.

```bash
BENCH_PLUGIN="$HOME/.codex/plugins/cache/openai-on-aws/bedrock-bench/0.2.0"
python3.12 "$BENCH_PLUGIN/scripts/bench.py" doctor
python3.12 "$BENCH_PLUGIN/scripts/bench.py" tasks
python3.12 "$BENCH_PLUGIN/scripts/bench.py" demo --out ./bench-results
```

The demo runs six synthetic attempts, including one intentional task failure.
The printed path points to the report. It does not call a model.

For a native Bedrock run, set `BEDROCK_MODEL` to a model ID available to your
account, and use your existing AWS credentials or `AWS_PROFILE`. Install the
optional dependencies in a virtual environment:

```bash
python3.12 -m venv .bench-venv
.bench-venv/bin/python -m pip install -r "$BENCH_PLUGIN/requirements.txt"
.bench-venv/bin/python "$BENCH_PLUGIN/scripts/bench.py" init \
  --runner native --provider bedrock-mantle \
  --model "${BEDROCK_MODEL:?Set BEDROCK_MODEL to your Bedrock model ID}" \
  --region us-west-2 --repetitions 1 --timeout-seconds 120 \
  --out bedrock-experiment.json
.bench-venv/bin/python "$BENCH_PLUGIN/scripts/bench.py" plan bedrock-experiment.json
```

This prepares three tasks and makes no model calls. Add a sourced rate card to
the experiment if you want dollar estimates; Bedrock usage without a matching
rate card leaves cost unknown. When ready, explicitly run the experiment:

```bash
.bench-venv/bin/python "$BENCH_PLUGIN/scripts/bench.py" run \
  bedrock-experiment.json --execute --out ./bench-results
```

For a Codex-runner experiment, use `init --runner codex --provider amazon-bedrock`
with a model ID supported by your installed Codex client and an explicit region.
The `plan` and `run` commands are the same. The Codex adapter uses the installed
client's authentication and does not need the optional Python SDK dependencies.

To pick up a later revision from the configured Git branch:

```bash
codex plugin marketplace upgrade openai-on-aws
codex plugin add bedrock-bench@openai-on-aws
```

The Codex plugin package and OpenCode's JavaScript plugin system are separate.
OpenCode is supported here as a benchmark runner, invoked by the packaged CLI.
The `SKILL.md` can also be used by clients supporting the agent skills format.

## Validation status

Offline tests exercise task grading, accounting, documented CLI event shapes,
process execution/timeout, native tool-loop behavior, and copied-package use.
Run them from the repository with:

```bash
python3.12 -m unittest discover -s tests -p 'test_bedrock_bench*.py' -v
```

Live model and CLI compatibility must be checked with the user's installed
versions and account access before treating their output as validated results.
Reference Docker smokes validate the task/grader/report path independently
of model quality. Pinned upstream sources and license references are listed
in [benchmarks/UPSTREAM.md](benchmarks/UPSTREAM.md).

Code: MIT-0. Documentation: CC-BY-SA-4.0. See the included license files.
