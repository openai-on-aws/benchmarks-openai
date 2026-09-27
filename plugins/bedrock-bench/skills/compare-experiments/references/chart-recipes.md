# Charts for a saved research comparison

Use these recipes with
[visualize-results](../../visualize-results/SKILL.md), passing the exact
`COMPARISON.json` path and its source/run context. The new uppercase research
schema is documented in [schema.md](schema.md); the existing lowercase report
`comparison.json` has different fields. Read numeric data from JSON or CSV,
not rounded Markdown labels.

Use an available host visualization skill for interactive inline work or
standard plotting tools for export figures. The comparison engine itself
needs no visualization dependency. Keep the resulting chart in the user's
results directory and identify it as synthetic/reference/live using the
recorded classification.

## Paired effect plot

Read `metrics.success_rate`, `mean_wall_seconds`, `mean_agent_seconds`,
`cost_per_attempt_usd`, or `cost_per_success_usd`. Plot `delta` with the
recorded `confidence_interval.low/high` and a zero reference line. Use
separate panels for different units. Multiply success fractions by 100 and
label percentage points.

Annotate both side estimates, distinct task count, repetitions, seed, resample
count, and “95% paired task-cluster percentile interval.” Positive quality
and negative cost/time effects favor the candidate. Do not invert only the
point estimate while leaving interval endpoints unchanged.

For `confidence_interval=null`, show the point when it is defined and an
explicit “interval unavailable” reason. For a null point, use an unavailable
marker or note, never a zero bar. A one-task result gets a point and a
single-task limitation, with no error bar.

## Task heterogeneity

Use `tasks[].deltas` or the `TASKS.csv` `delta_*` columns for a task effect dot
plot. Each row is one distinct task, with weight `1/T`. Show baseline and
candidate successes/attempts in the tooltip or adjacent table. A matrix with
tasks as rows and side success fractions as columns is useful when tasks
trade wins.

Do not compute task intervals from repeats or turn task-specific null cost
ratios into zeros. The overall cost/success effect is a ratio of task-balanced
means, so it is not the arithmetic mean of these task-specific ratio deltas.
Filtering to a few tasks changes the analysis: label that view as a subset
and keep the full engine estimate/interval separate.

## Cost versus success

For one point per side, use task-balanced success from
`metrics.success_rate[role]` and cost per success from
`metrics.cost_per_success_usd[role]`. Display `aggregates[role].successes`,
attempts, total cost, known subtotal, coverage, and cost basis with each point.
No side-specific confidence intervals are supplied by this schema; do not
reuse the paired-delta interval as an error bar around either side.

If cost is unknown or successes are zero, show the success result plus an
unavailable cost annotation. An optional subtotal bar must be labeled
“known-cost subtotal” and must not be ranked against a complete total. Keep
failed-attempt spend in all calculations even when the user drills into
successful attempts.

## Descriptive success by budget

Read `success_by_budget.wall_seconds.points` or
`agent_wall_seconds.points`, or filter `SUCCESS_BY_BUDGET.csv` by
`timing_field`. Draw right-continuous steps with x=`budget_seconds`,
y=`success_fraction`, and color=`role`. The inclusive test is duration
`<= budget`; hold the fraction constant between recorded durations.

Label the chart “Descriptive observed success by budget.” Include
`attempts`, `successful_attempts`, `ended_attempts`, and
`failed_ended_attempts` in tooltips. Failure and timeout durations occur in
the budget grid even when no success fraction changes. Curves plateau at the
observed success rate; do not normalize each side to its successful subset.
There are no curve confidence bands in the export.

Use separate wall and agent panels. Wall is trial duration, not a global
run clock. If the agent series is `unavailable`, show its missing count and
reason rather than drawing a partial-sample curve or substituting wall times.
The curve cannot answer how the same attempts would behave with a different
timeout.

## Outcome and duration inspection

`ATTEMPTS.csv` or `attempts[]` supports an outcome strip or scatter plot:
x=`wall_seconds` (or consistently available agent seconds), y=`task_id`,
color=`role`, shape=`status`. Keep failures and timeouts visible and show
run ID, attempt ID, repetition, cost coverage/basis, and source in tooltips.
Jitter may separate overlapping points, but do not label the attempts as
independent statistical samples or calculate a standard error across them.

A duration distribution over all attempts answers a different question from
latency conditional on success. If showing successful attempts alone, label
that conditioning and show the excluded failure count nearby. Never feed
the filtered rows back into the full comparison's cost or uncertainty claims.

## Data parsing and final checks

```python
import json
from pathlib import Path

comparison = json.loads(Path(comparison_json_path).read_text())
success = comparison["metrics"]["success_rate"]
point_pp = None if success["delta"] is None else 100 * success["delta"]
ci = success["confidence_interval"]
interval_pp = None if ci is None else [100 * ci["low"], 100 * ci["high"]]
```

For CSV, convert empty numeric cells to `None`, parse booleans explicitly,
and decode JSON cells for basis/failure lists. JSON preserves exact
identifiers; formula-leading CSV text has a protective apostrophe.

Check units, direction, both side denominators, task count, evidence label,
missing values, source paths/checksums, and tiny-sample limitations against
the engine output. Sorting or filtering a view must not rerun benchmarks,
change saved data, or silently recompute confidence intervals.

Offer chart/evidence next steps using the shared
[follow-up guidance](../../benchmark-agent-tasks/references/follow-ups.md).
For chart prompts, use `$bedrock-bench:visualize-results` with the exact
comparison artifact and recorded source/run IDs.
