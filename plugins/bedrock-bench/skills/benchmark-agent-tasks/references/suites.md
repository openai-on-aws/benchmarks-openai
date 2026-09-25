# Repository suites

Resolve `scripts/bench.py` from the installed plugin root. All commands below
are executed by the assistant in the user's workspace. Keep `.bench-tools`,
experiment JSON, and `bench-results` outside the plugin cache.

| Suite | Harness | Default selection |
|---|---|---|
| `aws-cdk-smoke` | Harbor | `cdk-sqs-lambda-dynamodb` |
| `terminal-bench` | Harbor | Terminal-Bench 2.0 `fix-git` |
| `swe-bench` | Harbor | SWE-bench Verified `django__django-15098` |
| `aws-bench` | AWS-Bench | Quickstart `describe-cloudformation-stack-resources` |

`tasks --suite NAME` lists all task names included in the bundled registry
snapshot. Git task sources are pinned to commits. Selecting `swe-bench` uses
Harbor's converted SWE-bench Verified tasks, not the separate SWE-bench CLI.
The suite version and selected commits are recorded in the experiment plan.

## Preparation and reference smoke

1. `doctor` checks local executable/package presence.
2. `prepare --suite NAME` shows the isolated installation plan.
3. `prepare --suite NAME --execute` installs its pinned runtime using Python
   3.12+. Harbor and AWS-Bench use separate virtual environments because
   AWS-Bench pins its own Harbor version.
4. `smoke --suite NAME` plans a reference check. Add `--execute` to run it.

Smoke accepts `aws-cdk-smoke`, `terminal-bench`, and `swe-bench`, with their
fixed default task. The CDK smoke runs the unchanged baseline and the reference
repair; success means the baseline fails grading and the repair passes.
The other two run the upstream reference solution and expect reward=1.
These checks validate the container/task/grader/report path, not live model
performance. They download dependencies and images and need Docker.
The pinned SWE-bench task images require `linux/amd64`; the plugin selects
that platform automatically. Apple Silicon hosts use Docker's emulation,
so their timings should not be treated as native Linux measurements.

On macOS, a Docker credential helper can stall an otherwise public image pull.
If this occurs, check Docker's build log. For public-image checks, a temporary
Docker client configuration without stored credentials can be used with
`DOCKER_HOST` set to the existing Docker context's socket. Preserve the user's
normal Docker configuration; record the temporary configuration in the run.

The CDK task's verifier uses a fresh copy of the environment. Only
`lib/stack.ts` and `lambda/handler.js` are copied into it. TypeScript compilation,
CDK synthesis, template assertions, and handler behavior checks run without AWS
credentials or deployments. Dependency versions are locked.

## Model experiments

`init --suite NAME --runner codex --provider PROVIDER --model MODEL --out experiment.json`
creates one selected task and one repetition. For Bedrock use
`--provider amazon-bedrock --region REGION`.

Use repeated `--task EXACT_NAME`, or deliberately use `--all-tasks` for the
whole bundled catalog. `--repetitions`, `--timeout-seconds`, and
`--agent-version` control repeat count, agent time, and the CLI package version.
`--skill /absolute/path/to/skill` injects a local skill into Harbor and records
its content digest. To compare variants, add distinct targets to the generated
experiment, keeping task sources, repetitions, seed, and limits identical.

The provider and model remain separate. For OpenCode use its provider name
(such as `openai`, `openrouter`, or `amazon-bedrock`) and an unprefixed model
ID; the adapter constructs the required `provider/model` identifier.
Codex supports OpenAI and Amazon Bedrock. Use a bare model or inference-profile
ID for Codex; slash-containing ARNs are rejected because the upstream adapter
strips slash prefixes.

`plan experiment.json` is read-only. `run experiment.json` displays the same
plan. `run experiment.json --execute` starts the selected trials and saves
`run.json`, `REPORT.md`, and each upstream job's original outputs.

Runs use one trial per process, serial execution, and no automatic trial
retries. Agent, setup, verifier, and whole-process timeout settings are in
`limits`. Model-call counts and output-token limits remain client-managed.
Recorded wall time includes setup and verification; `agent_wall_seconds` is
the upstream agent phase alone.

Execution checks the installed harness version against the suite pin and
records package versions. Re-run `prepare --suite NAME --execute` after a
plugin upgrade if an older runtime is rejected.

Harbor's Bedrock adapters read `AWS_BEARER_TOKEN_BEDROCK` at execution time,
forward it into the container, and configure the requested provider/region.
They do not put token values in the authored experiment/job JSON.
An AWS profile by itself is insufficient for these container adapters.
OpenAI and OpenRouter use their client/provider environment credentials.

## AWS-Bench environment lifecycle

AWS-Bench is a live AWS operations benchmark. Its quickstart includes a model
judge and requires a provisioned testing environment; it has no no-inference
`smoke` mode in this plugin.
The quickstart grader uses a Bedrock judge model, so grading needs its own
model access even when the candidate uses OpenAI or OpenRouter.

Include `--env-name NAME` when initializing the experiment and optionally
`--environment-profile PROFILE` for the management account. That profile is
separate from the model provider. AWS environment operations use `us-east-1`;
the selected Bedrock model region remains explicit in the target.

Use `aws-env ACTION experiment.json` to show a concrete command. The supported
actions are `show`, `init`, `setup`, `verify`, `reset`, and `cleanup`.
Add `--execute` only when the operation is within the user's authorization.
`init` can create AWS Organizations accounts; `setup` deploys billable
infrastructure. `run --execute` runs tasks against the selected environment.
`cleanup` removes its scenario resources; account termination is not exposed.

The plugin retains AWS-Bench's drift verification, scoped credentials, and
scenario verifiers. It never provisions an environment implicitly from `run`.
Skill injection is currently exposed for Harbor suites only. AWS-Bench on
Bedrock currently uses its Codex adapter.

## Results

`report RUN_A/run.json RUN_B/run.json --out comparison` checks task protocol
compatibility before comparing. Reference validation cannot be mixed with
model measurements. A missing/mismatched upstream result or verifier failure
is a failed attempt with unknown cost where accounting is incomplete.

Use total agent inference spend, including failed attempts, divided by
successful tasks. Preserve unknown cost and coverage. Upstream `cost_usd`
is labelled `runner_estimate`; zero does not establish a free model call.
An exact rate card can price observed Codex usage. Other adapters may not
provide all cache fields needed for an estimate.

Infrastructure, subscriptions, external tools, and verifier/judge inference
are outside this cost figure. Neither small subsets nor changed timeouts
establish an official upstream leaderboard result.
