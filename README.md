# OpenAI benchmarks on AWS

Compare OpenAI models on [Amazon Bedrock](https://aws.amazon.com/bedrock/) and
the OpenAI API (1P) by **task success, speed, and cost**. Run agent tasks, measure API performance, and
keep the evidence behind every result.

**Start with [Bedrock Bench](plugins/bedrock-bench/)**, the Codex plugin for
running AWS CDK repairs, Terminal-Bench, SWE-bench, and AWS-Bench from chat.

## Get started

[Install Bedrock Bench](plugins/bedrock-bench/README.md#install), select it in
Codex, and ask:

> Run the offline demo and explain success, timing, and cost per successful task.

Prefer the CLI? Try the same demo from a checkout with Python 3.12+:

```bash
git clone https://github.com/openai-on-aws/benchmarks-openai.git
cd benchmarks-openai
python3 bench.py demo --out bench-results
```

The demo uses synthetic results, needs no credentials or Docker, and saves a
readable report in `bench-results/`. Follow the
[plugin guide](plugins/bedrock-bench/README.md) to check a real CDK repair task
and prepare your first model comparison.

## Workflow

```mermaid
flowchart LR
    subgraph execution["1 · Configure and run"]
        direction TB
        plan["Plan a comparison<br/>Models, tasks, reasoning, limits<br/>e.g. GPT-6 Astra / Sol / Luna<br/>Bedrock regions, e.g.<br/>us-west-2 / us-east-1"]
        run["Run a benchmark<br/>Bedrock Bench: agent tasks<br/>Scripts: latency, quality,<br/>parity, ARC"]
        plan --> run
    end

    subgraph endpoints["2 · Main model endpoints"]
        direction TB
        mantle["Bedrock Mantle<br/>bedrock-mantle.&lt;region&gt;.api.aws<br/>openai.* model IDs"]
        runtime["Bedrock Runtime<br/>bedrock-runtime.&lt;region&gt;.amazonaws.com<br/>us.openai.* / global.openai.* profiles"]
        openai["OpenAI API · 1P<br/>api.openai.com<br/>gpt-* model IDs"]
        mantle ~~~ runtime ~~~ openai
    end

    subgraph results["3 · Evidence and comparison"]
        direction TB
        evidence["Timestamped evidence<br/>JSON results + run config<br/>Agent traces + verifier logs<br/>TXT parity checks"]
        metrics["Comparison metrics<br/>Success / accuracy / turns<br/>TTFT / ITL / end-to-end<br/>p50 / p95 / p99; tokens/sec<br/>Input/output tokens<br/>Reasoning/cache tokens<br/>Cost per successful task"]
        reports["Shareable outputs<br/>Markdown + PNG charts<br/>HTML / DOCX (performance)"]
        evidence --> metrics --> reports
    end

    execution --> endpoints --> results

    classDef runner fill:#eff6ff,stroke:#2563eb,color:#172554
    classDef bedrock fill:#fff4e6,stroke:#b45309,color:#451a03
    classDef firstparty fill:#f1f5f9,stroke:#475569,color:#0f172a
    classDef result fill:#ecfdf5,stroke:#047857,color:#064e3b
    class plan,run runner
    class mantle,runtime bedrock
    class openai firstparty
    class evidence,metrics,reports result
    style execution fill:#ffffff,stroke:#cbd5e1,color:#334155
    style endpoints fill:#ffffff,stroke:#cbd5e1,color:#334155
    style results fill:#ffffff,stroke:#cbd5e1,color:#334155
```

Choose models from the selected endpoint's catalogue; availability depends on
region and account access. See the [model and endpoint reference](docs/benchmark-reference.md#choosing-models)
and [prepared GPT-6 comparisons](examples/bedrock-bench/) for specific configurations.

Keep tasks and settings matched, and record the runner and pricing assumptions.
Providers, evidence, and metrics vary by suite; the ARC pilots use Bedrock only.
Agent results reflect both the model and its runner. TTFT is time to first token;
ITL is inter-token latency.

## Choose a benchmark

| What do you want to measure? | Start here |
| --- | --- |
| Agent task success and cost per successful task | **[Bedrock Bench](plugins/bedrock-bench/)** — starter tasks, CDK repair, Terminal-Bench, SWE-bench, AWS-Bench |
| Response latency and throughput | [Performance](docs/benchmark-reference.md#performance) — time to first token, tokens/sec, p50/p95/p99 |
| Accuracy, tool use, research, and work products | [Quality](docs/benchmark-reference.md#quality) — quick evals, agentic tasks, DeepSearchQA, GDPval |
| Responses API feature support | [API parity](docs/benchmark-reference.md#api-parity) — streaming, tools, structured output, and more |
| Grid reasoning and interactive learning | [ARC pilots](docs/arc-benchmarks.md) — ARC-AGI-2 and ARC-AGI-3 on Bedrock |

## Guides and results

- [GPT-6 agentic experiments](examples/bedrock-bench/): prepared Astra, Sol, and Luna smoke and full-suite plans. Review a plan before making paid model calls.
- [Astra suite guide](docs/astra-benchmarks.md): settings and commands for the API benchmark suites.
- [Command reference](docs/benchmark-reference.md): setup, authentication, model selection, and detailed examples.
- [Recorded model comparisons](comparisons/gpt-4.1-vs-gpt-5.6/) and [quality results](quality/RESULTS.md): saved evidence and methodology.
- [Migration workload proposal](docs/migration-workload-plan.md): a planned pack for evaluating workloads moving to Bedrock.

Contributions welcome: see [Contributing](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md).
Code is [MIT-0](LICENSE); documentation is [CC-BY-SA 4.0](LICENSE-DOCS.md).
