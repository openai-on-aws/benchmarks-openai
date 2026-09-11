# GPT-6 Astra benchmark coverage

This adds Astra as a candidate to every runnable suite on `main`, with configuration,
offline compatibility checks, and a campaign runner. **No Astra scores have been measured
or published by this change.** CyberSOC/Daybreak work on the unfinished local branch is
a separate follow-up.

## Model settings

Verified against the [OpenAI model guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra)
and [AWS model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-astra.html)
on September 11, 2026:

| Backend | Model ID | Scope |
| --- | --- | --- |
| Bedrock Mantle | `openai.gpt-6-astra` | `us-west-2` only |
| Bedrock Runtime | `us.openai.gpt-6-astra` | US geographic inference profile; source-region availability applies |
| Bedrock Runtime, optional | `global.openai.gpt-6-astra` | Global inference profile; source-region availability applies |
| OpenAI API | `gpt-6-astra` | Standard API |

Astra supports `low`, `medium`, `high`, `xhigh`, and `max`. These runners choose
**`low` explicitly** when Astra's effort is omitted and reject `none`/`minimal`
before dataset loading or candidate calls. Other models retain their existing defaults.
The older runners omit temperature for Astra and record that omission.
Tool workloads use Responses and preserve reasoning items across tool calls.
Astra calls request Standard service tier.

Output ceilings remain the suite's existing budgets, including reasoning tokens.
Astra may exhaust small ceilings before answering; inspect `status=incomplete` before
interpreting quality. Such runs need a separately recorded budget-matched comparison.
The existing performance `otps` metric divides all reported output tokens (including
reasoning) by generation time after the first text delta. It is not a visible-text
throughput measure; use TTFT/E2E and the raw reasoning-token counts when interpreting Astra.
Parity uses a 2,048-token minimum for Astra feature checks while retaining the
16-token enforcement test. Its two unsupported sampling checks are marked `SKIP`.
Other feature failures remain visible and require review; a parity failure is not
automatically a harness regression.

## Coverage

| Suite | Coverage | Smoke profile | Full profile |
| --- | --- | --- | --- |
| Performance | Same streaming harness on all three backends | 1k input, 2 calls, 2,048 output ceiling | 4 input sizes, existing output sweeps, 25 calls/config |
| Quick quality | AIME, GPQA, MMLU-Pro, MATH-500, GSM8K, HumanEval | 2 questions per task | Existing samples: 60 / 198 / 140 / 100 / 100 / 164 |
| Agentic | Both core and hard task sets | 1 repeat of every task | 5 repeats of every task |
| GPQA Diamond | Canonical gated dataset, legacy runner, all three backends | 2 questions × 1 repeat | 198 questions × 5 repeats |
| AIME legacy | Existing **2024** dataset; historical filename is `aime_2025.py` | 2 questions × 1 repeat | All 2024 questions × 5 repeats |
| HLE | Gated text-only dataset, all three backends | 2 questions | All text-only questions |
| DeepSearchQA | Existing Tavily search/fetch loop | Frozen indices 0 and 1 | Frozen stratified 50 |
| GDPval | Existing text-only deliverables and rubric judge | 2 tasks | 24 tasks |
| API parity | Existing 34 feature checks on each selected backend | All checks | All checks |

“Full” means this repository's standard run settings, not every record in every upstream
dataset. Smoke runs exercise wiring and cannot support benchmark claims.
Legacy `benchmark_bedrock.py`, `benchmark_openai_saas.py`, and `generate_report.py`
are retained for historical provenance; new performance runs use `performance/benchmark.py`.
The expanded shell scripts retain their frozen model arms; `run_astra.py` supplies
the Astra campaign.

The published GPT-4.1/GPT-5.6 package contains six additional workloads whose original
inference/grading harness is absent: synthetic routing, synthetic invoice extraction,
Banking77, CORD original images, CORD provided OCR, and ExtractBench. Its other six
workloads overlap quick quality, but prompts, dataset snapshots, budgets, and scoring
differ. Running the local suite does **not** create comparable cells for that package.
Adding Astra to its charts requires the colleague's source harness, matching cases,
new measurements, and regenerated evidence/manifest. Historical scorecards are preserved.

## Plan and run

Use Python 3.10+ with `pip install -r requirements.txt`. Planning uses only the standard
library and never loads credentials or calls an API:

```bash
python run_astra.py --region us-west-2
python run_astra.py --region us-west-2 --profile full
python run_astra.py --backends runtime --region us-east-2 --suites performance,quick,agentic
```

The default selects Mantle, Runtime and OpenAI. Configure AWS IAM credentials or
`AWS_BEARER_TOKEN_BEDROCK`, plus `OPENAI_API_KEY` for OpenAI. Quality scripts also support
`OPENAI_API_KEY_SAAS`; performance/model discovery requires `OPENAI_API_KEY`.
Canonical GPQA and HLE require accepted dataset terms and a Hugging Face login.
DeepSearchQA requires `TAVILY_API_KEYS` for uncached searches.

Execute a bounded first pass, then inspect its manifest and results:

```bash
python run_astra.py --region us-west-2 --backends mantle --suites performance,quick,agentic \
  --execute --output-dir runs/astra-first-smoke
```

Run every available suite after those checks:

```bash
python run_astra.py --region us-west-2 --profile full --execute \
  --output-dir runs/astra-full-low
```

Execution makes paid model calls, plus web-search calls where needed. Each campaign
requires a **new** output directory, preflights every selected backend's model listing,
and saves a manifest with exact commands, models, effort, region, file paths and status.
Model discovery does not prove invocation authorization.
The runner stops on a nonzero exit or saved API errors, including runners that otherwise
exit zero. Incomplete responses and parity failures are marked `review_required`.
Retry just the affected suites/backends in a new directory. Dataset downloads and judge
availability are checked by their respective runners, not by model discovery.

Add `--judge` to grade only files created by this campaign. Judges remain fixed:
GPT-5.5 on OpenAI for DeepSearchQA/GDPval and Claude Haiku on Bedrock for HLE.
Judge errors are recorded in judged artifacts and marked `review_required` in the manifest.
Judging incurs additional calls and requires the relevant credentials even when all
candidates use Bedrock.

Individual runners still accept explicit `--model` and `--effort`:

```bash
python quality/hle.py --backend runtime --model us.openai.gpt-6-astra --effort low --max-questions 2
python parity/run_parity.py --backend mantle --model openai.gpt-6-astra --effort low
python quality/rescore_hle.py --file path/to/hle_result.json
```

## Costs and reports

Astra estimates use September 11 Standard list prices, USD per million tokens:

| Route | Input, ≤272k | Output, ≤272k | Input, >272k | Output, >272k |
| --- | ---: | ---: | ---: | ---: |
| Mantle / US Runtime | 11 | 55 | 22 | 82.5 |
| Global Runtime / OpenAI | 10 | 50 | 20 | 75 |

Sources: [AWS](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-astra.html),
[OpenAI](https://developers.openai.com/api/docs/pricing). These are **uncached Standard
estimates**, excluding cache-write premiums, cache-read discounts, search/tool charges,
and judge fees. They are not invoice totals. Historical models' stored rates are
unchanged. Unknown prices propagate as `null`, including trajectory totals.
Legacy full-eval scripts report accuracy rather than token-cost summaries.

Generate a report for one campaign and one latency effort:

```bash
python performance/report.py --effort low \
  --results-dir runs/astra-full-low/performance \
  --quality-results-dir runs/astra-full-low/quality
```

The report includes Astra when measurements exist and labels the selected effort.
Latency charts currently compare Mantle with OpenAI; Runtime measurements remain
available in JSON and the campaign manifest. Quality tables include Runtime.
Neither the report nor this change publishes new numbers to the old comparison package.

## Offline checks

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s comparisons/gpt-4.1-vs-gpt-5.6 -p 'test_*.py'
```

Tests cover request compatibility, preserved tool-call reasoning, geographic/context
pricing, older runners' model selection, plan coverage, saved-error detection, and
effort-specific report rendering. Live access, dataset gating, model outputs, and
endpoint-specific feature support still require the smoke campaign.
