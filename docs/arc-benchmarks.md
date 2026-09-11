# ARC pilots on Amazon Bedrock

These runners compare OpenAI models **on Amazon Bedrock only**. They use the
OpenAI-compatible Responses API on Mantle or Runtime; no OpenAI SaaS key or
external model judge is needed. The existing nine suites remain available through
their individual runners and `run_astra.py`.

This is a first implementation for public-set pilots, not a reproduction of
OpenAI's headline scores or a submission to the ARC Prize leaderboard.

## What is included

| Component | Behaviour |
| --- | --- |
| Model discovery | Mantle model listings and active Runtime inference profiles, across specified regions |
| ARC-AGI-2 | Seeded sample of 20 public evaluation tasks; two independent attempts per test input |
| ARC-AGI-3 | Three versioned public games, local SDK scoring, action replays, 40-action ceiling per game |
| Comparison runner | One condition per advertised model ID by default; optional separate region/endpoint conditions |
| Results | Per-call output, usage, latency, estimated cost, provenance, completion status, and a Markdown comparison |

Discovery is not proof of invocation access. The default regions are Oregon,
Virginia, and Ohio; this is an inventory of the Responses routes returned there,
not a claim to inventory every native API or AWS region. Dated model snapshots
remain separate conditions. `--all-routes` also retains duplicate model conditions
across regions, Mantle, and Runtime inference profiles.

The inventory includes specialised Safeguard and Daybreak models where advertised.
They remain visible in the matrix, but ARC results for them are diagnostics, not
a recommendation to use safety classifiers or cyber models for general reasoning.
The separate CyberSOC/Daybreak **benchmark suite** remains follow-up work.

## Setup and model discovery

Use Python 3.12+ for the optional ARC-AGI-3 SDK:

```bash
python -m pip install -r requirements-arc.txt
export AWS_PROFILE=your-benchmark-profile
python run_bedrock_arc.py --discover --output-dir runs/bedrock-catalog
```

The catalogue records its creation time, regions, exact model IDs, and discovery
errors. Execution with `--models all` refuses an incomplete catalogue; an explicit
model selection can still use successfully discovered routes.

## Prepare public data

ARC-AGI-2 uses a pinned official checkout. The runner verifies the revision and
rejects locally modified evaluation data:

```bash
git clone https://github.com/arcprize/ARC-AGI-2.git runs/datasets/ARC-AGI-2
git -C runs/datasets/ARC-AGI-2 checkout f3283f727488ad98fe575ea6a5ac981e4a188e49
```

Download the three public ARC-AGI-3 games separately from paid inference:

```bash
python quality/arc_agi3.py --prepare \
  --environments-dir runs/datasets/arc3 --output-dir runs/arc3-preparation
```

Preparation uses the official public SDK service. Evaluation uses the downloaded
games in **offline** mode, with local scorecards and recordings; it does not send a
leaderboard submission. The pinned defaults are `ls20-9607627b`, `ft09-0d8bbf25`,
and `vc33-5430563c`. Runs record SDK versions and SHA-256 hashes of environment
files. No game source code, hidden state, or human action baselines enter the model
prompt.

## Plan and execute

Planning from a saved catalogue makes no discovery or model calls:

```bash
python run_bedrock_arc.py --catalog runs/bedrock-catalog/catalog.json \
  --output-dir runs/arc-plan
```

Start with a wiring smoke test across the discovered models:

```bash
python run_bedrock_arc.py --catalog runs/bedrock-catalog/catalog.json \
  --n 1 --max-actions 2 --budget-usd 20 \
  --output-dir runs/arc-smoke --execute
```

For the 20-task / 40-action pilot, omit the smoke-size overrides and choose the
budget after inspecting smoke results. Every output directory must be new.
Use `--models openai.gpt-6-astra,openai.gpt-5.6-luna` to select models, and
`--suites arc2` or `--suites arc3` to isolate a suite. Runtime IDs such as
`us.openai.gpt-5.6-sol` can also be selected explicitly.

The campaign divides its total estimated token-cost allowance equally among its
model/suite conditions. Each runner reserves a conservative input/output allowance
before a call, disables SDK retries, and stops before the next call when the
allowance is insufficient. Unknown prices stop execution rather than count as
zero. Direct runners accept explicit `--input-rate` and `--output-rate` overrides;
these must cover the entire context range being tested. Global Runtime prices
other than Astra and unverified long-context prices require overrides.

The default 100,000-byte request limit bounds growing histories. Direct runners
accept `--max-input-bytes`; no history is silently truncated. Estimated costs
exclude cache-write premiums and service fees, and are not invoice totals.
Unsettled reservations remain visible when a request fails without reported usage.

## Scoring and comparison rules

ARC-AGI-2 requires an exact match, including grid dimensions and every cell, for
**every test input in a task**. The two attempts get identical prompts and no gold
answer feedback. Invalid grids and incomplete responses are unsuccessful attempts.
Partial campaigns expose coverage and withhold aggregate accuracy, avoiding a score
based only on the tasks that finished. Test outputs are used only by the grader.
Use synthetic fixtures or training tasks for harness development; avoid tuning
prompts against public evaluation answers.

ARC-AGI-3 uses the SDK's Relative Human Action Efficiency score, not a made-up
win-rate substitute. Logs also distinguish wins, invalid actions, incomplete
responses, action ceilings, API errors, and budget limits. Small public-game
samples and low action ceilings are useful for integration tests but cannot
support claims about overall ARC performance.

Returned Responses output items are replayed between actions. GPT-OSS assistant
messages, and messages missing required fields, are normalised to equivalent input messages;
their original raw output remains in the log. Reasoning items are kept intact. Frontier models
request encrypted reasoning; GPT-OSS uses its supported standard response fields.
Whether reasoning was actually returned is inspectable in the saved output items.
Compaction is opt-in with `--compact-threshold` and has no automatic fallback if
the endpoint rejects it. A returned compaction item is retained when earlier
history is pruned. Backend support must be verified before comparing that condition
with a compaction-enabled OpenAI blog result.

Keep model ID, reasoning effort, output ceiling, task/game versions, attempt count,
action ceiling, and context policy fixed or explicitly labelled. The default
common effort is `low`; equal effort names do not imply equal compute.

## Validation

```bash
python -m unittest discover -s tests -p 'test_arc.py' -v
python -m unittest discover -s tests -v
```

The offline tests cover gold-answer isolation, exact grid parsing, all-test-input
scoring, incomplete coverage, budget checks before invocation, reasoning replay,
compaction, action validation, and catalogue selection. Live smoke runs are needed
to verify credentials, endpoint paths, and model-specific parameter support.

## Sources

- [ARC-AGI-2 data and task success criterion](https://github.com/arcprize/ARC-AGI-2)
- [ARC-AGI-3 toolkit](https://docs.arcprize.org/toolkit/overview)
- [ARC-AGI-3 scoring methodology](https://docs.arcprize.org/methodology)
- [OpenAI's ARC-AGI-3 harness analysis](https://openai.com/index/how-two-settings-tripled-our-arc-agi-3-scores/)
- [Responses compaction](https://developers.openai.com/api/docs/guides/compaction)
- [Bedrock pricing, including GPT-OSS and Safeguard](https://aws.amazon.com/bedrock/pricing/)
- [GPT-5.5 pricing](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-55.html)
- [Daybreak Red pricing](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-cyber.html)
- [Daybreak Blue pricing](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-daybreak-blue-56-sol.html)

Prices added for this pilot were checked on September 11, 2026. Existing frontier
rates are described in [the Astra coverage guide](astra-benchmarks.md).
