# Bedrock Bench

Compare AI agents on real tasks and see **how often they succeed, how long they
take, and what each successful task costs**. Run AWS CDK repairs, terminal tasks,
and software-engineering benchmarks from a Codex conversation.

Install the plugin once, then select **Bedrock Bench** in chat and describe what
you want to test.

## Install

You need the **Codex app**, a Codex CLI with plugin support, and **Python 3.12+**.
Docker is needed for the CDK, Terminal-Bench, SWE-bench, and AWS-Bench suites.
You can try the offline demo without Docker or AWS credentials.

Run these commands in a terminal:

```bash
codex plugin marketplace add openai-on-aws/benchmarks-openai \
  --ref main \
  --sparse .agents/plugins --sparse plugins/bedrock-bench
codex plugin add bedrock-bench@openai-on-aws
```

Open the project folder where you want to keep your benchmark plans and results.
Start a fresh Codex chat and choose **Bedrock Bench** from the plugin picker.
The installed plugin works without cloning this repository.

<details>
<summary>Update an existing installation</summary>

```bash
codex plugin marketplace upgrade openai-on-aws
codex plugin add bedrock-bench@openai-on-aws
```

If your marketplace still follows the old preview branch, remove that
registration and repeat the installation above to follow `main`:

```bash
codex plugin marketplace remove openai-on-aws
```

Check the installed version with `codex plugin list --marketplace openai-on-aws`.

</details>

## Get your first report

With Bedrock Bench selected, ask:

> Run the offline demo and explain the results.

You will get a report for six synthetic attempts, including one intentional
failure. This shows how success, timing, and cost appear in a report. It makes
no model calls and needs no credentials.

Then check a real coding task:

> Prepare and run the AWS CDK reference smoke test. Explain what passed.

Codex sets up the benchmark tools and runs the checks in Docker. The broken app
should fail and the reference repair should pass. This downloads dependencies
and images, but makes no model calls and deploys no AWS resources.

## Compare models on Bedrock

For live runs, you need access to the selected Bedrock models. Container agents
also need `AWS_BEARER_TOKEN_BEDROCK` in the environment that starts the run;
your Codex desktop login alone does not provide it. See
[provider setup](skills/benchmark-agent-tasks/references/cli.md#providers-and-authentication)
for authentication options. Keep credentials out of chat and experiment files.

Start by asking for a plan:

> Plan the CDK repair benchmark for GPT-6 Astra, Sol, and Luna on Amazon Bedrock.
> Use Codex as the runner, low reasoning, and one attempt per model. Show the
> model IDs, regions, limits, and pricing assumptions. Do not run it yet.

The models being benchmarked are listed in the plan; they are independent of
the model you selected for the current chat. The
[prepared GPT-6 experiments](https://github.com/openai-on-aws/benchmarks-openai/tree/main/examples/bedrock-bench)
provide smoke and full-suite configurations you can review and reuse.

When you are happy with the plan:

> Run that experiment and compare success, time, and cost per successful task.

This step makes paid model calls. Start with the small smoke selection before
expanding to a full suite. Current runs have timeouts but no enforced dollar cap.

## Choose a benchmark

| Benchmark | What it tests | Available tasks |
| --- | --- | ---: |
| **Starter** | Invoice reconciliation, deployment ordering, and inference-log triage | 3 |
| **AWS CDK repair** | Fix an SQS → Lambda → DynamoDB app and its message handler | 1 |
| **Terminal-Bench 2.0** | Terminal work and repository tasks | 89 |
| **SWE-bench Verified** | Fix real software issues | 500 |
| **AWS-Bench** | Complete scenarios in a provisioned AWS testing environment | 9 |

Ask for a specific task or say what you want to measure. Codex starts repository
benchmarks with one selected task unless you request a larger set. AWS-Bench
requires its own testing environment and model-based grader; review setup and
cleanup before using it.

You can also compare Codex with OpenCode, attach an AWS skill to one target,
or use the starter's fixed native agent loop to compare model/provider behavior.
The [suite guide](skills/benchmark-agent-tasks/references/suites.md) covers those
options and which runners each suite supports.

## Understand the results

Each run saves a readable `REPORT.md`, a `run.json`, and per-attempt evidence in
your project's `bench-results` directory. Ask Codex to explain the report or
compare compatible runs.

**Cost per successful task** includes spend on failed attempts. Check cost
coverage and whether the figure comes from a rate card or runner estimate.
Missing cost remains unknown. Reported agent inference spend excludes AWS
infrastructure, subscriptions, and verifier/judge costs.

Reference checks have passed for the default CDK, Terminal-Bench, and SWE-bench
tasks. Your first live smoke run still needs to establish model access,
agent behavior, and usable accounting. Small smoke runs do not establish a
general model ranking.

## Go further

- [Full Codex walkthrough](https://github.com/openai-on-aws/benchmarks-openai/blob/main/docs/bedrock-bench-guide.md)
- [CLI and configuration reference](skills/benchmark-agent-tasks/references/cli.md)
- [Cost accounting reference](skills/benchmark-agent-tasks/references/accounting.md)
- [Agent operating instructions](skills/benchmark-agent-tasks/SKILL.md)
- [Contributor guidance](AGENTS.md)
- [Upstream task sources and licenses](benchmarks/UPSTREAM.md)

Code: MIT-0. Documentation: CC-BY-SA-4.0.
