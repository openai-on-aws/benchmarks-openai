# GPT-6 on Bedrock: manual experiment pack

Prepared September 25, 2026 for Bedrock Bench **0.2.0**. These are ordinary
experiment JSON files consumed by the installed plugin. No scheduler or
automatic launch is configured.

Read the [Codex guide](../../docs/bedrock-bench-guide.md) for the chat workflow
and current feature/validation summary.

## Models and fixed settings

| Target | Model | Mantle region |
| --- | --- | --- |
| `gpt6-astra-bedrock` | `openai.gpt-6-astra` | `us-west-2` |
| `gpt6-sol-bedrock` | `openai.gpt-6-sol` | `us-east-1` |
| `gpt6-luna-bedrock` | `openai.gpt-6-luna` | `us-east-1` |

These are the latest general-purpose OpenAI models documented on Bedrock.
Availability and invocation rights in your account have not been verified.
They use the Responses API with `low` reasoning; Astra does not support `none`.
Both other targets also use `low` to keep this setting consistent.

Repository tasks use `runner: codex`, `provider: amazon-bedrock`, and Codex
`0.153.4`. The native starter uses `provider: bedrock-mantle`. All files use seed
42 and one repetition. Repository experiments allow 600 seconds for the agent,
600 for setup, 900 for the verifier, and 2,400 for the whole process. They run
serially with no automatic trial retries; internal client retries remain
client-managed. The native starter uses a 300-second wall limit, 10 calls, and
4,096 output tokens per response.

The full files preserve the smoke files' model settings and limits while
selecting every task in the bundled catalog. These settings are not an official
upstream leaderboard protocol. Increasing repetitions multiplies the counts
below. Timeouts bound execution time; they do not enforce a dollar budget.

## Choose one file

| File | Selection | Live attempts |
| --- | --- | ---: |
| [gpt6-starter-smoke.json](gpt6-starter-smoke.json) | All 3 starter tasks × 3 models | 9 |
| [gpt6-cdk-smoke.json](gpt6-cdk-smoke.json) | CDK repair × 3 models | 3 |
| [gpt6-terminal-smoke.json](gpt6-terminal-smoke.json) | `fix-git` × 3 models | 3 |
| [gpt6-swe-smoke.json](gpt6-swe-smoke.json) | `django__django-15098` × 3 models | 3 |
| [gpt6-aws-smoke.json](gpt6-aws-smoke.json) | CloudFormation resource discovery × 3 models | 3 |
| [gpt6-terminal-full.json](gpt6-terminal-full.json) | All 89 Terminal-Bench tasks × 3 models | 267 |
| [gpt6-swe-full.json](gpt6-swe-full.json) | All 500 SWE-bench Verified tasks × 3 models | 1,500 |
| [gpt6-aws-full.json](gpt6-aws-full.json) | All 9 AWS-Bench quickstart tasks × 3 models | 27 |

The AWS files deliberately have `aws_environment: null` and will not execute
until an environment is selected. Their filename's `smoke` means a small
**live** selection; AWS-Bench has no model-free reference smoke in this plugin.
The two full repository suites alone contain **1,767 attempts**. Review a smoke
run before choosing that scale. Do not run every JSON file with a shell glob:
full selections already include the smoke tasks.

## Pricing assumptions

The included rate cards use commercial Mantle in-region **Standard** list prices,
in USD per million tokens, for inputs of at most 272,000 tokens:

| Model | Input | Cache read | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| Astra | 11.00 | 1.10 | 13.75 | 55.00 |
| Sol | 2.20 | 0.22 | 2.75 | 11.00 |
| Luna | 0.11 | 0.011 | 0.1375 | 0.55 |

The AWS prices already include the in-region premium. Each card records its
source, retrieval date, region, model, and `service_tier: default` (Standard).
Prices can change; check them again before launching. Cards are local to these
experiments, rather than new defaults in the plugin.

The current upstream importer retains a positive cost estimate from the runner
when available; a matching rate card is a fallback when the runner supplies no
usable estimate. Inspect `cost_basis` before comparing dollars. A missing cost
is unknown, never zero.

The context guard is conservative for upstream agent totals: aggregate input
above 272,000 tokens can leave a trial unpriced even when individual requests
fit the short-context band. Cache-write detail and auxiliary calls depend on
the client's emitted usage. These estimates need live accounting validation
and exclude verifier/judge inference, infrastructure, and subscriptions.

Sources checked September 25, 2026:

- [AWS GPT-6 Astra model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-astra.html)
- [AWS GPT-6 Sol model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-sol.html)
- [AWS GPT-6 Luna model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-luna.html)
- [Codex on Bedrock: Mantle models](https://learn.chatgpt.com/docs/amazon-bedrock#in-region-inference-using-the-bedrock-mantle-endpoint)
- [OpenAI GPT-6 model guidance](https://developers.openai.com/api/docs/guides/latest-model)

## Plan from chat or a terminal

Select **Bedrock Bench** in Codex and ask it to review a named file without
executing it. Keep these files in your working directory, outside the plugin
cache. Copy the `examples/bedrock-bench` directory as a unit to keep its relative
paths: `tools_dir: ../../.bench-tools` resolves to your workspace's `.bench-tools`.

For the optional terminal workflow, use Python 3.12+ and run these commands from
the directory containing `examples`:

```bash
BENCH_PLUGIN="$HOME/.codex/plugins/cache/openai-on-aws/bedrock-bench/0.2.0"
python3.12 "$BENCH_PLUGIN/scripts/bench.py" doctor
python3.12 "$BENCH_PLUGIN/scripts/bench.py" plan \
  examples/bedrock-bench/gpt6-cdk-smoke.json
```

Use the installation path printed by `codex plugin add` if yours differs.
Planning neither authenticates nor makes model calls.

Prepare Harbor and optionally recheck the CDK reference before a live launch:

```bash
python3.12 "$BENCH_PLUGIN/scripts/bench.py" prepare \
  --suite aws-cdk-smoke --tools-dir .bench-tools --execute
python3.12 "$BENCH_PLUGIN/scripts/bench.py" smoke \
  --suite aws-cdk-smoke --tools-dir .bench-tools --execute
```

Those commands install dependencies and run Docker, without model inference.
SWE-bench's pinned images use `linux/amd64`; Apple Silicon hosts use emulation.

When you choose to make paid model calls, provide
`AWS_BEARER_TOKEN_BEDROCK` to the executing process through your authorized
credential setup, then run a single selected experiment:

```bash
python3.12 "$BENCH_PLUGIN/scripts/bench.py" run \
  examples/bedrock-bench/gpt6-cdk-smoke.json --execute --out bench-results
```

The starter additionally needs the optional dependencies from
`"$BENCH_PLUGIN/requirements.txt"` installed in your Python environment.

For AWS-Bench, first select `aws_environment` and, if needed,
`environment_profile` in a copy of the JSON. Prepare its separate runtime, then
use `aws-env show/setup/verify/cleanup` to review the selected environment's
lifecycle. `aws-env init` can create accounts; `setup` can provision billable
resources. Follow the [suite guide](../../plugins/bedrock-bench/skills/benchmark-agent-tasks/references/suites.md)
before executing those operations.

Each completed run already contains a comparison among its three models.
Use `report` to compare additional runs with the same suite and task protocol;
different suites and smoke/full selections produce separate comparisons.
