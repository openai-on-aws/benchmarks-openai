# Brief format and planning API

The brief schema is version 1. The engine rejects unknown fields, duplicate JSON
keys, invalid values, unrecognized tasks, unsupported target settings, duplicate
condition identities, and missing local skills. The
[structural JSON schema](../assets/brief.schema.json) documents the outer format;
normal experiment semantics and cross-field checks come from the installed
runner and `bedrock_bench.design`.

## Authoring a brief

| Field | Required | Meaning |
|---|---|---|
| `schema_version` | Yes | Integer `1`; separate from the embedded runner schema |
| `question` | Yes | Nonempty benchmark question |
| `contrast` | Yes | `single-factor` or `system-comparison` |
| `base_experiment` | Yes | Inline normal runner experiment: schema 1 for starter, schema 2 for repository suites |
| `conditions` | Yes | Ordered array of at least two conditions; first is unchanged baseline |
| `budget` | No | Planning ceiling, reserve, and explicit token assumptions |
| `notes` | No | Array of nonempty context or interpretation notes |

The embedded experiment uses the runner's own validation and defaults. Make
`seed`, `repetitions`, and all relevant limits explicit when drafting a brief.
No model, provider, region, rate card, or task is chosen by the planner. Targets
within the base can represent different models or systems; an ablation compares
conditions **within** each base target.

Each condition has `id`, optional `label`, and required `target_changes`.
IDs use 1–32 letters, digits, underscores, or hyphens, beginning with a letter
or digit, and are unique without regard to case for portable filenames.
The first condition has `"target_changes": {}`. Every later condition
maps **every base target's exact ID** to an object of supported target settings.
Values replace whole fields; skill and routing arrays are not appended.

```json
{
  "id": "with-skills",
  "label": "Chosen skill injected",
  "target_changes": {
    "candidate": {"skills": ["./skills/aws-cdk"]}
  }
}
```

Changes can use the existing target fields: `runner`, `provider`, `model`,
`region`, `reasoning_effort`, `service_tier`, `aws_profile`, and `routing`.
Repository suite targets also support `agent_version` and `skills`, subject to
the normal adapter restrictions. Target `id` is not patchable. Unknown fields
such as prompts, tools, temperatures, `max_turns`, or tasks are not accepted as
target patches. A supported field combination can still be invalid for the
selected adapter; runner validation remains authoritative.

`single-factor` requires exactly one field to differ across all variants.
`system-comparison` allows several supported target fields, while retaining the
same task protocol. It is a descriptive system comparison, not an escape hatch
for incompatible task sets or graders. Variants that change nothing, or target
settings that duplicate another condition cell, are rejected because the runner's
report groups by execution settings rather than friendly IDs. Use `repetitions`
to repeat the same settings.

Runner target IDs combine condition and base target IDs with `--`. Long derived
IDs receive a stable hash suffix to fit the runner's 64-character limit. The
original base IDs and exact model IDs remain in the design; the target mapping
is authoritative.

Relative `tools_dir` and all base/variant `skills` paths resolve against the
resolved **input brief's parent**, never the shell's directory or generated
`conditions/` directory. Omitted suite `tools_dir` becomes `.bench-tools` beside
the brief. `~` expands normally; environment-variable interpolation is not
performed. Generated configs contain absolute paths. They remain usable after
moving the plan on the same machine while those dependencies remain in place;
for another machine, revise a copied brief and regenerate.

## Budget inputs

Budget fields are optional. Missing values do not establish zero cost.

- `max_usd`: nonnegative finite USD planning ceiling or `null` (unspecified).
- `reserve_fraction`: finite fraction from 0 through 1; default 0, recorded
  explicitly in the output. This sets aside part of the ceiling for uncertainty
  or excluded expenses; it does not estimate those expenses.
- `usage_per_attempt`: array of unique condition/base-target assumptions.

Each usage assumption requires `condition`, `target` (the **base** target ID),
and a nonempty `source` describing the assumption or actual saved pilot source.
Token fields are nonnegative integers or `null`:
`input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
`output_tokens`, `reasoning_output_tokens`. Missing fields become `null`.
Input totals already include cache reads/writes; output already includes
reasoning. Subsets cannot exceed their totals.

```json
{
  "max_usd": 20,
  "reserve_fraction": 0.25,
  "usage_per_attempt": [
    {
      "condition": "baseline",
      "target": "candidate",
      "source": "Illustrative planning assumption, not measured usage",
      "input_tokens": 10000,
      "cached_input_tokens": 0,
      "cache_write_input_tokens": 0,
      "output_tokens": 2000,
      "reasoning_output_tokens": null
    }
  ]
}
```

This illustration supplies no pricing. Put user-selected, dated, sourced rate
cards in `base_experiment.rate_cards` using the
[accounting contract](../../benchmark-agent-tasks/references/accounting.md).
Matching uses provider, model, region, and service tier exactly. No network
lookup or default price is used. Missing cache rates for nonzero cache usage,
missing input/output/cache assumptions, or an exceeded rate-card input band
leave a cell's estimate unknown. Price-band checks conservatively use the
whole-attempt input assumption, matching aggregate accounting.

For each condition/base-target cell:

```text
attempts = distinct tasks × repetitions
estimated inference = attempts × rate_card_estimate(whole-attempt token assumptions)
inference allowance = max_usd × (1 - reserve_fraction)
```

All attempts contribute, including anticipated failures; no success rate is
invented. Totals are `null` unless every attempt has an estimate. The known
subtotal and estimated-attempt coverage remain available with partial inputs.
`within_inference_allowance` is `null` for an incomplete estimate or missing
ceiling. A complete estimate above the allowance is flagged without changing
the experiment. `enforced_dollar_cap` is always `false`; `all_in_total_usd`
remains `null` because excluded expenses are unpriced.

## Installed-only usage and outputs

Resolve `BENCH_PLUGIN` from this skill's location (two parent directories).
Use Python 3.12+ and absolute user workspace paths:

```bash
python3.12 -B "$BENCH_PLUGIN/scripts/bench.py" design "$BRIEF" --out "$DESIGN_DIRECTORY"
python3.12 -B "$BENCH_PLUGIN/scripts/bench.py" plan "$DESIGN_DIRECTORY/experiment.json"
```

The reusable API is `bedrock_bench.design.write_design(brief_path, directory)`,
returning an absolute `pathlib.Path` to `DESIGN.md`. Import it with the installed
plugin's `scripts/` on `sys.path`; no source checkout or optional SDK is needed.
Invalid briefs raise `ValueError`. An existing destination, even an empty
directory, raises `FileExistsError`. Use a new output directory outside the
installed plugin and input skills. Inputs are read only. The planner validates
before publishing and refuses plans above 100,000 materialized attempts; it
never silently truncates task selections or repetitions.

| Output | Contract |
|---|---|
| `DESIGN.md` | Human question, counts, conditions, limits, budget, scope, and provenance |
| `DESIGN.json` | Versioned machine plan described below |
| `brief.json` | Exact input bytes, preserved as a snapshot |
| `experiment.json` | Normal validated runner config containing all condition targets |
| `conditions/CONDITION_ID.json` | Normal validated alternative config for that one condition |

Prefer the combined config to interleave conditions. Running all alternatives
has the same count as the combined config, but different ordering and possible
time-block effects. Running both representations doubles the attempts.
Do not edit generated configs and then treat the original design hashes as
current provenance.

`DESIGN.json` has `schema_version: 1`, `kind: "bedrock-bench-design"`, and
`status: "planned"`. Its fields are:

- `question`, `contrast`, `varied_fields`, `notes`: the hypothesis and declared
  changes; `varied_fields` comes from normalized differences.
- `suite`, `tasks`, `seed`, `limits`, `protocol_hash`: fixed task protocol.
- `counts`: `distinct_tasks`, `repetitions`, `base_targets`, `conditions`,
  `target_condition_cells`, `attempts_per_cell`, `attempts_per_condition`,
  `total_attempts`, and `catalog_tasks`.
- `experiment`: primary `path` and the public loader's `plan()` result.
- `conditions`: ordered IDs, labels, original `target_changes`, normalized
  `changed_fields`, base-to-generated `target_ids`, relative `experiment` path,
  and that file's validated `plan()`.
- `execution`: `executed: false`, primary config, alternatives flag, concurrency,
  retry count, summed controller timeout allowances, and exact primary
  `schedule`. Each row has a one-based `index`, `attempt_id`, condition/base
  target/runner target, task, one-based repetition, and starter `fixture_seed`
  (repository tasks use `null`). No model sampling seed is set.
- `budget`: inputs, per-cell usage sources/rate cards/estimates, complete total or
  `null`, known subtotal, coverage, reserve and allowance math, and excluded costs.
- `provenance`: original brief path and hash, snapshot path, plugin/Python
  versions, planner hash, task sources, skill hashes, and SHA-256 hashes of the
  snapshot and emitted runner configs. No runtime authentication is inspected.
- `limitations`: objects with stable `code` and human `detail`, including pilot
  scope, unpinned agent versions, catalog limitations, and incomplete budgets.

The seed and schedule match the current runner's shuffle of repetition → task →
target. Repository task source descriptors or CDK source hashes come from
`plan()`. Starter provenance records its fixture/scoring revision and full
protocol hash. Skill hashes use the same digest as execution; dependencies
under excluded build-cache directories are not content pinned.

The [offline example](../assets/offline-demo.brief.json) generates 12 synthetic
attempts in the primary config, or 6 in each alternative. It is runnable with
the demo runner entirely offline; the design operation itself only plans.
The [AWS/CDK template](../assets/aws-cdk-skills-ablation.brief.json) intentionally
fails validation until the exact model, region, and real skill path are supplied.
Its default scope is 1 task × 3 repetitions × 2 conditions = 6 attempts.
