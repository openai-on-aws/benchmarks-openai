# Saved comparison API and schema

## Invocation

```python
from pathlib import Path
import sys

# Resolve the actual directory containing this skill's SKILL.md.
skill_directory = Path("/absolute/path/to/skills/compare-experiments")
sys.path.insert(0, str(skill_directory.parents[1] / "scripts"))
from bedrock_bench.research_compare import write_comparison

summary_path = write_comparison(
    ["/absolute/user/results/baseline/run.json",
     "/absolute/user/results/candidate/run.json"],
    "/absolute/user/results/research-comparison",
    baseline="baseline-target",
    candidate="candidate-target",
    resamples=2000,
    seed=42,
)
```

Replace example paths and selectors with the actual saved context. `sources`
is an iterable of saved schema-version-1 `run.json` paths (a single path is
also accepted). Paths resolve absolutely. The engine does not discover runs,
read arbitrary trace content, invoke agents, fetch prices, or launch tools.
Run the packaged API through the host's available local Python mechanism;
it does not require a host bridge or a repository-root CLI.

`directory` may exist, but none of the five output names may already exist.
The engine refuses existing files, directories, symlinks, and hard-linked
artifacts at those names, and uses exclusive file creation against races.
Validation and serialization precede publication. An interrupted write can
leave partial outputs after a process crash; choose a new destination on
retry. An ordinary caught write error removes only files created by that call.

The function returns an absolute `Path` to `COMPARISON.md`.
Invalid data, selectors, balance, protocols, or numeric parameters raise
`ValueError`; filesystem failures can raise `OSError`. The caller should show
the reason and choose compatible saved evidence, not offer a bypass switch.
`resamples` must be an integer at least 2; `seed` must be an integer, not a
boolean. When resampling is performed, fewer than 1,000 draws carry a Monte
Carlo limitation.

## Exact selectors

Each side must resolve to one execution identity. These forms are supported:

```python
baseline = "friendly-target-id"
candidate = "full-64-character-execution-identity-sha256"

# Scope a target ID to selected runs, including the same identity before/after.
baseline = {"target_id": "model-a", "run_ids": ["saved-before-run-id"]}
candidate = {"target_id": "model-a", "run_ids": ["saved-after-run-id"]}

# Disambiguate versions/settings with the key emitted by the explorer.
baseline = {"identity_key": "full-sha256", "run_ids": ["saved-run-id"]}
```

A selector object accepts `target_id` and/or `identity_key`, plus optional
`run_ids`. Both identity filters, when supplied, must match. Run IDs must be
unique and present, and the selected identity must occur in every requested
run. Omitted `run_ids` means all matching supplied observations. String IDs
match the recorded target label; string keys match the entire identity,
including any label aliases. No hash prefixes or fuzzy model names are used.

The key is the existing explorer/report fingerprint: SHA-256 of canonical JSON
containing every recorded target field except `id`, with `runner_version`
added from the attempt. This includes provider, model, region, reasoning,
tier, routing, requested agent version, skill paths and digests where recorded,
and any additional saved target settings. Paths are not normalized into a
different identity.

Ambiguity errors list the matching keys and exact identities. A selector cannot
hide a partial target run where versions/settings changed between attempts.
The two selections must have disjoint `(run_id, attempt_id)` observations.
The same execution identity can be compared across disjoint run cohorts.
Unselected targets are counted explicitly in `pairing.excluded`; supplied
runs still undergo validation in full.

The comparison CLI exposes optional, repeatable `--baseline-run RUN_ID` and
`--candidate-run RUN_ID`. For a supplied target ID, these wrap that side as
`{"target_id": ID, "run_ids": [...]}`. For example, the same `model-a` target
can be selected on both sides with baseline run `before` and candidate run
`after`. Repeat a side's flag to include several runs in that cohort, keeping
the cohorts disjoint and their task/repetition counts balanced. Without run
filters, existing target-ID and full-identity-key selectors are unchanged.

## Source validation and provenance

The engine reuses `explorer.load_run`, `report.compare`, `report.aggregate`,
and `library.reviewed_source` for their saved-data safeguards and accounting.
It additionally requires:

- A completed, nonempty run with experiment tasks, targets, repetitions, seed,
  and limits sufficient to reconstruct its entire declared schedule.
- No duplicate run ID across sources, duplicate attempt ID within a run, or
  duplicate execution-identity/task/repetition slot within a run. Attempt IDs
  can recur in different runs; their source/run context distinguishes them.
- Exactly one observation for every declared target/task/repetition slot.
  If a saved schedule is present, it must agree exactly with those slots.
  Terminal attempt failures, timeouts, and missing upstream results stay as
  failed observations in a completed schedule.
- Matching task protocol hashes and evidence types. Recorded protocol bodies,
  when present, must hash correctly and agree with experiment metadata.
  Matching hashes cannot hide changed tasks, repetitions, seed, limits, suite,
  or recorded AWS environment selection.
- No conflicting known task checksums for a matched task/repetition cell.
  Known upstream source commits/URLs must agree with the saved source pin.
  Missing upstream checksums are reported and retained, relying on the shared
  protocol for fixture pairing. Starter runs may have only a protocol hash;
  the engine does not regenerate old fixtures using current installed code.
- Finite numeric values throughout the input, nonnegative numeric costs and
  durations, boolean outcomes/accounting flags, and integer repetitions.
  Overflow JSON numbers, NaN/Infinity, duplicate JSON keys, malformed counters,
  inconsistent complete costs, and arithmetic overflow are rejected.

Every source record includes absolute path, byte SHA-256, run ID, saved times,
protocol hash, recorded cost scope, and rate-card metadata. Explicit reviewed
copies require the original checksum, an in-directory audit, and unchanged
run/settings/outcome/timing fields under the existing library review gate.
Their provenance includes original/audit paths and checksums. A rejected
explicit review fails; it is not silently replaced by the original.
Passing the original source uses that file even if a reviewed sibling exists.
Sources are checked again before publication.

## COMPARISON.json, schema version 1

This uppercase filename is distinct from the existing report's
`comparison.json`; use a fresh directory to avoid case-insensitive collisions.
The schema is a saved analysis, not a new result-run schema.

| Field | Meaning |
| --- | --- |
| `schema_version`, `analysis` | `1`, `paired_saved_results` |
| `evidence_type`, `synthetic`, `validation_only` | Explicit synthetic/reference/live classification |
| `protocol_hash`, `protocol`, `scope` | Shared hash, recorded body if available, inference cost scope |
| `sources[]` | Exact input provenance and optional checked accounting review provenance |
| `selections.baseline`, `.candidate` | Input selector, resolved identity/key, labels, run IDs, attempt count |
| `identity_differences[]` | Field, baseline value, candidate value for differing recorded identity fields |
| `pairing` | Task count, repetitions, fixture strata, counts per side, run-pairing policy, exclusions |
| `method` | Effect direction, task weights, bootstrap unit, seed, configured/performed draw counts, quantile and undefined-draw policy |
| `aggregates.baseline`, `.candidate` | Existing accounting aggregates plus mean wall/agent seconds and agent-time coverage |
| `metrics` | Task-balanced point estimates and paired-delta intervals described below |
| `tasks[]` | Task ID, equal weight, fixture records, both side aggregates, task-specific deltas |
| `attempts[]` | Every selected observation with role, run/source/key, fixture, outcome, both durations and accounting |
| `success_by_budget` | Separate wall/agent curve status, missing counts and descriptive points |
| `limitations[]` | Stable reason codes plus explanatory messages |
| `artifacts` | Relative output filenames |

Metric keys are `success_rate`, `mean_wall_seconds`, `mean_agent_seconds`,
`cost_per_attempt_usd`, and `cost_per_success_usd`. Each contains:

```json
{
  "unit": "fraction",
  "baseline": 0.5,
  "candidate": 0.5,
  "delta": 0.0,
  "confidence_interval": {
    "level": 0.95,
    "low": -1.0,
    "high": 1.0,
    "method": "paired_task_cluster_percentile"
  },
  "interval_unavailable_reason": null,
  "valid_resamples": 2000,
  "undefined_resamples": 0
}
```

These numbers illustrate the schema, not live measurements. `delta` always
means candidate minus baseline. Success is a fraction, not a percentage.
Time uses seconds; cost uses USD per attempt or success. Intervals apply to
the paired delta. Undefined points and intervals are JSON `null`.

Interval absence is explained by `undefined_point_estimate`,
`one_distinct_task`, or `undefined_bootstrap_draws`. Draw counts are zero when
the single-task bootstrap is skipped. If a point is undefined but some draws
are defined, counts still describe them; an interval remains withheld.
No interval conditions on a subset of valid ratio draws.

`method.configured_resamples` records the requested draw count.
`method.resamples` retains that same configured value for backward
compatibility. `method.performed_resamples` records the number of draws actually
performed: zero for one distinct task, otherwise the configured count. Each
metric's `valid_resamples + undefined_resamples` equals `performed_resamples`.
Undefined draws count as performed even when their interval is withheld;
skipped resampling produces no draws at all. Markdown reports both counts and
explains the single-task skip.

Fixture records identify task/repetition via the shared protocol hash and
`fixture_key`, with recorded protocol source, known checksum, and checksum
observation coverage. A fixture key is a pairing identifier, not independent
proof that a historical workspace's bytes were correct.

All matched task/repetition cells have equal observation counts between
sides. Additional saved runs contribute additional observations within their
task, without arbitrary run-to-run pairing or an increased task count.
`pairing.unmatched_attempts` is zero on successful output because unmatched
panels are rejected. Excluded, nonselected observations are separately counted.

## CSV exports

- `TASKS.csv`: one row per distinct task. `baseline_*` and `candidate_*`
  include attempts, successes, success rate, total and known-subtotal cost,
  cost coverage/basis, cost per attempt/success, mean wall/agent duration,
  agent-time coverage, and failure counts. `delta_*` contains the task's
  paired metrics. `weight` is `1 / distinct_tasks`.
- `ATTEMPTS.csv`: one row per selected attempt, including failed, timed-out,
  and incomplete upstream outcomes. Fields include role, identity key, target
  ID, run ID, source/checksum, task/repetition/fixture key/checksum, attempt ID,
  status/success, wall and agent seconds, cost/subtotal/basis/completeness.
- `SUCCESS_BY_BUDGET.csv`: curve points with timing field, `descriptive=True`,
  role, budget seconds, total attempts, ended attempts, successful attempts,
  failed ended attempts, and task-balanced success fraction.

CSV lists/maps are JSON strings, booleans are `True`/`False`, and missing
numeric cells are empty. Parse empty cells as null, never as zero. Potential
spreadsheet formula strings receive a leading apostrophe; use JSON for exact
untrusted identifiers. Numeric negative deltas remain numeric.

With the same saved bytes/paths, selectors, seed, resample count, and Python
implementation, exports are byte reproducible. Sources and observations are
sorted before analysis; caller source ordering does not affect the result.
There is no current-time field or random output identifier.
