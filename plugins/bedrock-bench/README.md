# Bedrock Bench

Measure whether an agent finishes a task, what the attempt costs, and how long
it takes. The plugin bundles its complete Python implementation, so it also
works from Codex's installed plugin cache.

The first release provides a native tool loop, Codex and OpenCode CLI adapters,
three deterministic filesystem tasks, and JSON/Markdown reports.

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

Use `$plugin-creator` to add this plugin folder to a local marketplace, then
install **Bedrock Bench** from that source. The manifest supplies the display
name, composer icon, and brand color for its native plugin mention.

The Codex plugin package and OpenCode's JavaScript plugin system are separate.
OpenCode is supported here as a benchmark runner, invoked by the packaged CLI.
The `SKILL.md` can also be used by clients supporting the agent skills format.

## Validation status

Offline tests exercise task grading, accounting, documented CLI event shapes,
process execution/timeout, native tool-loop behavior, and copied-package use.
Run them from the repository with:

```bash
python3 -m unittest discover -s tests -p test_bedrock_bench.py -v
```

Live model and CLI compatibility must be checked with the user's installed
versions and account access before treating their output as validated results.

Code: MIT-0. Documentation: CC-BY-SA-4.0. See the included license files.
