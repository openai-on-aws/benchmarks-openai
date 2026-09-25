# CLI and configuration reference

Use this reference when authoring experiments, choosing providers, configuring
credentials, or invoking the CLI directly. For repository tasks and their
container runtimes, also read [Repository suites](suites.md). For pricing fields
and interpretation, read [Cost accounting](accounting.md).

Run commands from the project directory selected by the user. Set `BENCH_PLUGIN`
to the installed directory printed by `codex plugin add`; for version 0.3.0:

```bash
BENCH_PLUGIN="$HOME/.codex/plugins/cache/openai-on-aws/bedrock-bench/0.3.0"
```

From a source checkout, set it to that checkout's `plugins/bedrock-bench`
directory. Keep experiment files, `.bench-tools`, and results in the selected
project, outside the plugin cache. A repository explicitly named by the user
takes precedence over a chat that happens to be open in a different directory.

These sections describe the `starter` suite unless stated otherwise.

## Offline demo


```bash
python3.12 "$BENCH_PLUGIN/scripts/bench.py" doctor
python3.12 "$BENCH_PLUGIN/scripts/bench.py" tasks
python3.12 "$BENCH_PLUGIN/scripts/bench.py" demo --out bench-results
```

The starter loop supports Python 3.10+; use Python 3.12+ for repository suites.
The demo uses invented token counts, costs, and model latency,
including an intentional task failure. It makes no network calls.

Each run gets a new directory with `run.json`, `comparison.json`, `REPORT.md`,
`REPORT.html`, and per-attempt workspaces, event logs, and stderr. Results are saved after
every attempt. `trace_sha256` links an attempt to its captured events.

## Create a real experiment

Choose a model ID available to your account and endpoint. For example:

```bash
python3.12 "$BENCH_PLUGIN/scripts/bench.py" init \
  --runner native --provider bedrock-mantle \
  --model "$BEDROCK_MODEL" --region us-west-2 \
  --reasoning-effort low --out experiment.json
python3.12 "$BENCH_PLUGIN/scripts/bench.py" plan experiment.json
python3.12 "$BENCH_PLUGIN/scripts/bench.py" run experiment.json
```

Set `BEDROCK_MODEL` to your chosen model before this command. Omit
`--reasoning-effort` for models without that setting. Both `plan` and `run`
above only display the plan. To make model calls:

```bash
python3.12 -m pip install -r "$BENCH_PLUGIN/requirements.txt"
python3.12 "$BENCH_PLUGIN/scripts/bench.py" run experiment.json --execute --out bench-results
```

Install optional SDKs in a project-local virtual environment. CLI adapters
need their respective installed clients instead of the Python model SDKs.

Add targets to the generated JSON to compare several configurations. Each
target needs a unique `id`.

## Providers and authentication

This table describes the starter runners. Container-agent authentication is
covered in the [repository suite guide](suites.md#model-experiments).

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

## Compare completed runs

```bash
python3.12 "$BENCH_PLUGIN/scripts/bench.py" report \
  bench-results/RUN_A/run.json bench-results/RUN_B/run.json \
  --out bench-results/comparison
```

Markdown, JSON, and a self-contained HTML explorer are written together. Add
`--format html` to print the HTML path, or `--format inline` to also write and
print `REPORT.inline.html` for supported inline visualization hosts. The default
still prints the Markdown path. Inline output has a 1 MB limit; full HTML does
not impose that display limit.

Runs must have identical tasks, fixtures, repetitions, seed, and configured
limits. Synthetic/live mixes, duplicate run IDs, and interrupted runs are
rejected. Different runners compare complete agent systems; use the same
native loop when investigating model/provider differences.

## Inspect saved evidence

```bash
python3.12 "$BENCH_PLUGIN/scripts/bench.py" runs bench-results
python3.12 "$BENCH_PLUGIN/scripts/bench.py" inspect \
  bench-results/RUN_A/run.json --attempt ATTEMPT_ID
```

`runs` lists the selected directory and its immediate child run directories.
It includes interrupted runs and reports unreadable files separately.
`inspect` accepts an exact attempt ID and returns its recorded grading,
accounting, source paths, and up to 16 KiB from each supported text evidence
file. Previews can be truncated; workspace directories are identified but are
not recursively read. Evidence paths must resolve inside the source run directory.
Neither command invokes a model or prepares a runtime.

The HTML embeds result metadata and works offline. Deep evidence links depend on
the original run files. A supported Codex inline host offers contextual chat
actions; ordinary browsers provide copyable analysis prompts. Report filters
only change the view, and an outcome filter affects the attempt inspector rather
than recalculating success or cost from successful attempts alone.
