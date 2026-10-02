# Daybreak Blue and Daybreak Red/Cyber benchmark plan

Research and implementation plan for testing the Ohio (`us-east-2`) Amazon
Bedrock models, publishing reproducible results, and contributing support to
open-source benchmark repositories.

Last verified against public documentation and live Ohio access: **2026-09-01**.

## Executive recommendation

Use a staged evaluation rather than treating “cyber benchmark performance” as
one number:

1. Establish service latency and reliability with this repository's Responses
   API streaming harness.
2. Establish passive defensive reasoning with CyberSOCEval malware analysis.
3. Use the maintained Cybench adapter as an agent/sandbox integration smoke,
   not as the headline model ranking.
4. Establish exploit-development depth and cost curves with ExploitBench.
5. Add real-world reproduction and detection/patching with CVE-Bench and
   SEC-Bench after the initial suites are stable.
6. Run CyberSecEval 4 safety/refusal suites separately. Do not blend safety and
   capability scores into one average.

The first publishable matrix should contain Daybreak Blue, Red/Cyber, general
GPT-5.6 Sol, Claude Fable 5, and Claude Opus 4.8. Sol controls for cyber
specialization; Opus controls for Fable's cyber-classifier fallback. Report
latency, task success, partial credit, refusals, cost, and errors independently.
Never rank the models from a composite score whose weights were chosen after
seeing the results. See `cyber-benchmark-publication-strategy.md` for the
paper-backed comparison thesis and execution roadmap.

## Exact models and access path

| Arm | Amazon Bedrock model ID | OpenAI API equivalent | Context | Ohio API |
|---|---|---|---:|---|
| Daybreak Blue | `openai.gpt-daybreak-blue-5.6-sol` | `gpt-daybreak-blue-latest`, an alias to `gpt-5.6-sol` | 1M tokens on its Bedrock card | `https://bedrock-mantle.us-east-2.api.aws/openai/v1` |
| Daybreak Red | `openai.gpt-5.6-cyber` | `gpt-daybreak-red-latest`, an alias to `gpt-5.6-cyber` | 272K input; OpenAI lists 128K max output | same |
| General Sol control | `openai.gpt-5.6-sol` | `gpt-5.6-sol` | 1M long-context tier | same |
| Claude Fable 5 | `us.anthropic.claude-fable-5` | N/A | provider model card | `bedrock-runtime` Converse |
| Claude Opus 4.8 | `us.anthropic.claude-opus-4-8` | N/A | provider model card | `bedrock-runtime` Converse |

Important constraints:

- Both Bedrock models use the OpenAI-compatible Responses API through
  `bedrock-mantle`. They do **not** have `bedrock-runtime` inference-profile
  IDs, global inference, or cross-region inference.
- The Claude controls use Bedrock Converse, so provider-portable prompts and
  common scoring are required. Provider-enforced structured output is not a
  portable comparison protocol.
- Fable requires the AWS account to opt into `provider_data_share`; the
  benchmark does not make that data-governance change.
- They are in-region in `us-east-2`; pin `AWS_REGION=us-east-2`.
- Both require Trusted Access for Cyber and model-specific approval. An AWS
  account team must enable Bedrock access; OpenAI API access is a separate
  approval tied to the exact organization, project, identity, model, and API
  surface.
- Standard service tier is supported. The Bedrock cards do not list Priority
  or Flex for these models.
- Approval does not imply Zero Data Retention. Confirm the data-control posture
  for the exact approved project before sending private findings or malware.

### Published list prices

USD per one million tokens, excluding any separate tool or infrastructure
costs:

| Surface/model | Input | Cached input read | Output | Long-context threshold |
|---|---:|---:|---:|---|
| Bedrock Daybreak Blue | $5.50 | $0.55 | $33.00 | Above 272K: $11.00 / $1.10 / $49.50 |
| Bedrock Red/Cyber | $13.75 | $1.375 | $82.50 | No long-context tier on the 272K card |
| Bedrock Claude Fable 5 | $11.00 | $1.10 | $55.00 | Five-minute cache write: $13.75 |
| Bedrock Claude Opus 4.8 | $5.50 | $0.55 | $27.50 | Five-minute cache write: $6.875 |
| OpenAI Daybreak Blue | $4.00 | $0.40 | $20.00 | Above 272K: $8.00 / $0.80 / $30.00 |
| OpenAI Red/Cyber | $12.50 | $1.25 | $75.00 | No long-context row listed |

Bedrock's current Cyber cache-write price is $17.1875 per million tokens. The
cyber adapters record and charge cache-write tokens when the API exposes them;
cost remains labeled as an estimate because provider usage fields and cache
retention modes can differ.

### Access preflight

Live Ohio invocations succeeded on 2026-09-01 for Daybreak Blue, GPT-5.6
Cyber, general GPT-5.6 Sol, and Claude Opus 4.8. Claude Fable 5 was discoverable
but rejected the default retention mode before inference. Re-run discovery for
the active principal before every benchmark session:

```bash
export AWS_REGION=us-east-2
python performance/benchmark.py --backend bedrock --list-models |
  grep -E 'openai\.gpt-(daybreak-blue-5\.6-sol|5\.6-cyber)'
```

Do not continue if either exact ID is absent. A generic Bedrock model listing
does not prove the active principal has cyber-model approval.

## What to measure

### 1. Platform performance and latency

For every call, capture:

- **TTFE**: request start to the first server-sent event. This isolates
  connection/routing responsiveness.
- **TTFT**: request start to the first visible output-text delta.
- **TTFT−TTFE**: an observable pre-answer interval. It can include hidden
  reasoning and service work, but must not be described as exact “reasoning
  time.”
- **ITL**: gaps between visible text deltas.
- **Visible output tokens/s**: `(output tokens − reasoning tokens)` divided by
  visible generation time. Using all billed output tokens would overstate
  visible throughput for reasoning-heavy models.
- **E2E**, input/output/reasoning/cached tokens, retries, status, and error rate.

Run cold-ish and warmed experiments separately. Do not mix sequential latency
with load tests:

| Experiment | Concurrency | Repeats | Purpose |
|---|---:|---:|---|
| Smoke | 1 | 2 per cell | Verify access, schema, and billing |
| Core latency | 1 | 10 initially, then 30+ | Stable p50/p95 comparison |
| Load curve | 2, 4, 8, then 16 if quotas allow | 20+ per level | Tail latency, throttling, throughput |
| Long context | 1 | 10+ | 20K now; later 64K/128K/256K generated fixtures |

Use identical prompt bytes and output budgets. Alternate model order across
replications (`Blue→Red`, then `Red→Blue`) to reduce time-of-day and warm-pool
bias. Keep reasoning effort fixed and record omission as “model default,” not
as `none`.

Commands:

```bash
# Cheapest access check
PROFILE=smoke ./performance/run_daybreak.sh

# Main pre-publication matrix
PROFILE=core RUNS=30 ./performance/run_daybreak.sh

# Separate load curve
PROFILE=core RUNS=30 CONCURRENCY=4 ./performance/run_daybreak.sh

# Reverse order for the next replication
PROFILE=core RUNS=30 \
  MODELS="openai.gpt-5.6-cyber openai.gpt-daybreak-blue-5.6-sol" \
  ./performance/run_daybreak.sh
```

The `full` profile makes 300 measured calls per model plus warmups. At published
prices and assuming every model consumes every output budget, its rough upper
bound is about **$35 for Blue + $88 for Red**, before cache-write charges and
without infrastructure costs. Actual cost depends heavily on completion and
reasoning-token usage. The `core` profile is roughly an order of magnitude
cheaper; always run `smoke` first.

### 2. Passive defensive reasoning

CyberSOCEval's malware-analysis task is the best first domain benchmark here:
609 multi-select questions grounded in Hybrid Analysis detonation reports,
with attack type, difficulty, and topic metadata. It is defensive, requires no
live target, and tests report-grounded reasoning rather than generic trivia.

This repository's adapter:

- reads an external PurpleLlama checkout and its CyberSOCEval data submodule;
- uses Responses API structured output;
- treats report text as untrusted evidence;
- records both source commits;
- records no full report or prompt in its result JSON;
- uses exact-set accuracy as the primary score;
- also reports Jaccard, option precision/recall/F1, parse failures, latency,
  token use, estimated cost, and subgroup results.

Setup and smoke run:

```bash
git clone --recurse-submodules https://github.com/meta-llama/PurpleLlama.git ../PurpleLlama
export AWS_REGION=us-east-2

python quality/cyber/cybersoc_eval.py \
  --purplellama-dir ../PurpleLlama \
  --backend mantle \
  --model openai.gpt-daybreak-blue-5.6-sol \
  --n 10 \
  --max-output-tokens 2048

python quality/cyber/cybersoc_eval.py \
  --purplellama-dir ../PurpleLlama \
  --backend mantle \
  --model openai.gpt-5.6-cyber \
  --n 10 \
  --max-output-tokens 2048
```

Then run the full 609-item task with the same seed and input profile. Prefer the
full report profile for both models. If any Cyber inputs exceed its 272K
context, use `--truncate-input` for **both** models and label the run as the
upstream-derived compact profile. Never compare full-input Blue against
truncated-input Red.

Run a priced smoke test before scaling. In a 2026-09-01 10-question full-report
pilot, inputs averaged roughly 66K tokens per item. The two arms cost about
$10.80 in total, and a 50-question-per-model run projected to roughly $54 at
that observed sample mix. Reasoning-only incomplete responses occurred at 512
tokens for both models and at 1,024 tokens for Cyber, so the adapter now
defaults to 2,048 and reports incomplete reasons separately.

The broader CyberSecEval 4 runner currently uses Chat Completions in its native
OpenAI adapter. These Daybreak models are Responses-only, which is why this
repository includes a direct adapter. A useful upstream contribution is to add
a Responses API provider to PurpleLlama and then retire local duplication.

### 3. Agentic CTF capability

Cybench contains 40 professional CTF challenges across cryptography, web,
reverse engineering, forensics, exploitation, and miscellaneous categories.
The maintained Inspect Evals adapter currently exposes 39; `motp` is omitted
because of its GPL artifact. It pins challenge artifacts and container images
and verifies downloaded files.

Install the optional runner:

```bash
pip install 'inspect-evals[cybench]'
```

Start with one or two named tasks:

```bash
LIMIT=2 EVAL_NAMES=avatar ./quality/cyber/run_cybench.sh
```

The wrapper defaults to Kubernetes. Configure the Inspect K8s sandbox and
enforce an egress allowlist. The upstream Docker mode provides Kali,
installation privileges, and unrestricted internet; use it only in a dedicated
throwaway environment after explicit acknowledgement:

```bash
SANDBOX=docker CYBENCH_ACKNOWLEDGE_RISKS=1 LIMIT=1 \
  ./quality/cyber/run_cybench.sh
```

The wrapper explicitly supplies
`https://bedrock-mantle.us-east-2.api.aws/openai/v1`. As of Inspect AI 0.3.261,
its endpoint-path inference recognizes standard `gpt-5.*` names but not the
`gpt-daybreak-*` alias and otherwise selects the incompatible `/v1` path.
Upstream support should classify Daybreak aliases as frontier OpenAI models and
add a regression test for the derived Mantle path.

Report:

- challenge solve rate and score by domain/difficulty;
- attempts and tool calls;
- wall time and model tokens/cost per solved task;
- tool, sandbox, network, and infrastructure failures separately;
- refusal rate and the point in the trajectory where refusal occurred;
- pass@1 and pass@3. If reporting repeated independent success, also report
  `pass^k` (all k succeed) because reliability matters for operational agents.

### 4. Real-world patching and exploit validation

Add these only after Cybench is stable:

- **SEC-bench** (MIT): reproducible OSV/CVE-derived detection, proof-of-concept,
  and patching tasks in Docker. It is a strong phase-two test, but needs Python
  3.12, more than 200GB disk, and an isolated host.
- **BountyBench** (Apache-2.0): 25 target systems and 40 public bug-bounty
  tasks spanning detect, exploit, and patch. It is realistic but materially
  more complex and dual-use.
- **CyberSecEval 4 AutoPatch**: useful for patch generation but operationally
  heavy. Upstream estimates roughly 500GB for 20 samples, 2TB for lite, and 3TB
  for full; the first image build can take hours and Apple Silicon is not a
  supported benchmark host.

Do not let a candidate model reach arbitrary public IPs. The only permissible
targets are benchmark-owned containers or a scoped cyber range.

### 5. Safety, refusals, and false refusals

CyberSecEval 4 includes MITRE compliance/false-refusal, secure code generation,
prompt injection, code-interpreter abuse, vulnerability exploitation canaries,
spear phishing, autonomous offensive operations, AutoPatch, malware analysis,
and threat-intelligence reasoning.

Treat capability and policy behavior as separate axes:

- benign defensive task completion;
- false refusal rate on allowed work;
- correct refusal or escalation on out-of-scope work;
- partial compliance before refusal;
- unsafe tool attempt rate;
- scope violations, network-policy denials, and human-approval requests.

Use fixed, pre-registered categories. A refusal that avoids a dangerous action
is not a quality failure; a refusal on a clearly authorized defensive task is
not a safety success.

## Open-source benchmark landscape

| Project | What it measures | License/data issue | Fit |
|---|---|---|---|
| Cybench | 40 CTF tasks; agent shell/network workflow | Apache-2.0 upstream; maintained Inspect adapter is MIT; one GPL task excluded | **Adopt first** for agentic capability |
| CyberSecEval 4 / PurpleLlama | Safety, secure code, exploitation canaries, AutoPatch, SOC reasoning | MIT code; CyberSOCEval reports have source-specific terms | **Adopt defensive subsets first**; keep data external |
| SEC-bench | Reproducible CVE detection, PoC, patching | MIT; large Docker footprint | Phase two |
| BountyBench | Detect/exploit/patch on realistic systems | Apache-2.0; substantial dual-use risk | Phase three |
| CTIBench | CTI MCQ, CVSS, ATT&CK extraction/attribution | CC BY-NC-SA 4.0, so not suitable for unrestricted commercial redistribution | Optional external loader only |
| SecBench | 3,000 released bilingual security QA items | No explicit upstream license; HF mirror says `other` | Do not vendor or publish derived data without permission |
| JPMorgan CyberBench | NER, classification, summarization, MCQ | Apache-2.0, but repository archived 2026-05-26 | Baseline only; not a first integration |

“Open source code” does not imply every bundled document, malware report,
challenge binary, or dataset is freely redistributable. Pin source commits and
container digests, preserve notices, and keep restricted/non-commercial data
outside this MIT-0 repository.

## Experimental design and statistics

### Freeze the protocol before the full run

Record a machine-readable manifest containing:

- exact model ID/alias, endpoint, region, API surface, and access tier;
- benchmark repository and data commits;
- container image digests;
- prompt/template hash;
- solver and tool versions;
- reasoning effort, token limit, timeout, retries, concurrency, and seed;
- sandbox type, egress policy, CPU/RAM architecture, and run timestamps;
- scorer version and any excluded tasks with reasons.

Run the frozen smoke set on every code change. Only promote a harness version
after both models complete it without schema or scorer errors.

### Sample size and uncertainty

- Latency: use at least 30 successful sequential calls per cell for initial
  distribution estimates; 50–100 is preferable for stable p95/p99.
- Paired question sets: report paired bootstrap 95% confidence intervals for
  accuracy/cost differences and McNemar's test for discordant binary outcomes.
- Agentic tasks: repeat tasks with independent trajectories. Report the raw
  numerator/denominator and Wilson intervals; small challenge counts make
  percentages look more certain than they are.
- Costs and latency are usually skewed. Publish median, p90/p95/p99, mean, and
  bootstrap intervals rather than only a t-test.
- Correct for multiple comparisons when testing many domains or effort levels.
  Mark subgroup analysis exploratory unless it was pre-registered.

Store raw per-attempt records. A mean cannot be audited or re-scored.

### Contamination and benchmark saturation

Public CTFs and MCQs may appear in training data. Mitigations:

- include recent CVE/range tasks whose cutoff is documented;
- create private holdouts or randomized challenge variants;
- compare unguided and scaffolded conditions;
- inspect trajectories for memorized flags or unexplained answer leakage;
- report benchmark/version dates beside the model knowledge cutoff;
- never claim broad cyber superiority from one public benchmark.

## Security operating procedure

Trusted Access should be paired with technical controls:

1. Use a dedicated AWS account/project and ephemeral benchmark credentials.
2. Keep production, customer, and corporate credentials out of the sandbox.
3. Prefer Kubernetes network policy with deny-by-default egress.
4. Allow only benchmark-owned targets and required package/artifact hosts.
5. Use an isolated filesystem and destroy it after each task.
6. Cap runtime, tokens, spend, processes, disk, and outbound bytes.
7. Log model messages, tool calls, network denials, and human approvals.
8. Fail closed if scope, target ownership, or approval cannot be proven.
9. Require human approval before any action outside a benchmark container.
10. Review logs for secrets and exploit material before publication.

Do not run the autonomous-offensive, SEC-bench, or BountyBench phases on a
developer laptop connected to a trusted corporate network.

## Upstream contribution strategy

Make small, reviewable contributions:

1. Land this repository's performance schema v3 and document migration from
   billed-token OTPS to visible-token OTPS.
2. Contribute an OpenAI Responses provider to PurpleLlama, including custom
   base URL and bearer-token support without logging credentials.
3. Add Daybreak/Cyber examples to Inspect's Bedrock OpenAI provider
   documentation if no code change is required.
4. Add smoke fixtures and mocked provider tests; upstream CI must not need
   approved cyber-model access.
5. Publish result manifests and scorer outputs, not restricted source reports.
6. Include exact reproduction commands, cost caveats, security controls, and
   known benchmark exclusions in each pull request.

Before opening a pull request, run the target project's formatter/tests,
preserve its contributor certificate/signoff requirements, and verify that no
challenge data, access token, customer information, or generated exploit
artifact is staged.

## Definition of done

A model can be added to the public benchmark table only when:

- exact access and model IDs are documented;
- smoke, core latency, and at least one defensive quality suite pass;
- raw versioned results and environment metadata are committed or attached;
- errors/refusals are included in denominators or explicitly separated;
- costs and confidence intervals are present;
- safety controls and benchmark licenses have been reviewed;
- a second operator can reproduce the run from a clean environment;
- limitations cover contamination, incomplete coverage, and access gating.

## Primary sources

Model and access documentation:

- [OpenAI GPT-5.6-Cyber model](https://developers.openai.com/api/docs/models/gpt-5.6-cyber)
- [OpenAI Trusted Access for Cyber and Daybreak aliases](https://developers.openai.com/api/docs/guides/safety-checks/cybersecurity#authorized-access-and-agentic-workflows)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [AWS Daybreak Red / GPT-5.6 Cyber card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-cyber.html)
- [AWS Daybreak Blue / GPT-5.6 Sol card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-daybreak-blue-56-sol.html)
- [AWS model endpoint availability](https://docs.aws.amazon.com/bedrock/latest/userguide/models-endpoint-availability.html)
- [AWS model/API compatibility](https://docs.aws.amazon.com/bedrock/latest/userguide/models-api-compatibility.html)

Benchmark sources:

- [Cybench project](https://cybench.github.io/)
- [Cybench repository](https://github.com/andyzorigin/cybench)
- [Inspect Evals Cybench adapter](https://github.com/UKGovernmentBEIS/inspect_evals/tree/main/src/inspect_evals/cybench)
- [Inspect AI model providers](https://inspect.aisi.org.uk/providers.html)
- [PurpleLlama / CyberSecEval 4](https://github.com/meta-llama/PurpleLlama)
- [CyberSOCEval data](https://github.com/CrowdStrike/CyberSOCEval_data)
- [SEC-bench](https://github.com/SEC-bench/SEC-bench)
- [BountyBench](https://github.com/bountybench/bountybench)
- [CTIBench](https://github.com/maveryn/cti-bench)
- [SecBench](https://github.com/secbench-git/SecBench)
- [JPMorgan CyberBench](https://github.com/jpmorganchase/CyberBench)
