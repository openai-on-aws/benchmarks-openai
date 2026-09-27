# Interpreting a saved experiment comparison

## What is estimated

Name the baseline, candidate, and intended contrast before inspecting which
metrics look best. The engine compares recorded execution identities under a
shared task protocol. It does not establish that the sources were randomized,
that external service conditions were constant, or that the chosen task sample
represents a customer's workload.

For task `t` and side `s`, let `n[t,s]` be its recorded attempt count. Task
success rate is successes divided by `n[t,s]`; task mean time or cost is the
mean over all its attempts. Every task/repetition cell must have the same
observation count on both sides. Completed schedules include failed attempts,
timeouts, and missing upstream trial results, each with its recorded outcome.

For success, mean duration, and mean cost per attempt:

```text
task_delta[t] = candidate_task_mean[t] - baseline_task_mean[t]
paired_delta = sum(task_delta[t] for each task) / number_of_distinct_tasks
```

Thus tasks receive equal weight. Repetitions stabilize the estimate for a
particular task; they are not extra independent tasks. Multiple saved runs
contribute observations inside each task. Their IDs are retained, but the
engine does not invent pairings between arbitrary chronologically ordered runs.
For the same target identity in disjoint saved run cohorts, use selector
objects with `run_ids`, or the CLI's repeatable `--baseline-run RUN_ID` and
`--candidate-run RUN_ID` flags. Merely selecting the same target ID twice would
reuse observations and is rejected.

Cost per success is a ratio of task-balanced means:

```text
side_cost_per_success =
    mean_over_tasks(cost_per_attempt_for_task)
    / mean_over_tasks(success_rate_for_task)
delta = candidate_cost_per_success - baseline_cost_per_success
```

It is not the mean of task-specific cost-per-success ratios. A zero-success
task's spend still contributes. With the required balanced design this equals
total side spend divided by total side successes.

Positive success deltas favor the candidate; negative duration or cost deltas
favor it. Multiply success deltas and interval endpoints by 100 to display
percentage points. A 0.05 success delta is five percentage points, not a
five-percent relative improvement. Report both sides' rates as context.

## What an interval means

The engine uses a paired task-cluster percentile bootstrap. For each seeded
draw it samples `T` task IDs with replacement from the `T` distinct observed
tasks. A sampled task carries both sides and all its repetitions/run
observations together. It then recomputes the task-balanced paired effect.
The 2.5th and 97.5th percentiles use linear interpolation at `(B - 1) * p`
among `B` bootstrap draws.

This assumes task clusters are exchangeable for the intended task population.
Dependence within a task is retained. Dependence between tasks caused by a
shared run, provider incident, environment, or task family is not modeled.
If that dependence dominates, describe the effect for these saved tasks and
avoid a population claim. The interval is conditional on the recorded
repetitions; it is not a prediction interval for another run.

One distinct task produces no confidence interval, regardless of its repetition
count. With fewer than ten tasks the engine emits a small-sample caution;
ten is a reporting heuristic, not a validity threshold. Few clusters can
produce unstable, discrete, or collapsed intervals. A `[0, 0]` interval when
every observed task delta is zero is an empirical property of those tasks,
not proof of equivalence or zero uncertainty on unobserved tasks.

Seed 42 and 2,000 draws are the defaults. More draws reduce Monte Carlo noise;
they do not add evidence. Do not try seeds until an interval excludes zero.
Intervals are marginal and unadjusted for multiple metrics or candidate
selection. An interval crossing zero is inconclusive for direction; it does
not prove the alternatives equivalent. An interval excluding zero does not
prove that a particular skill or setting caused the difference.

For cost per success, a bootstrap sample may have zero successes on either
side even when the whole dataset has successes. Those draws have undefined
ratios. The engine reports valid/undefined draw counts and withholds the
interval if any draw is undefined. Dropping those draws would condition the
result on success and hide this instability.

## Accounting and missingness

Whole-side cost is unknown if any attempt cost is unknown. Preserve the known
subtotal and attempt-level cost coverage; a timeout can incur spend after its
last recorded event. Never replace absent spend with zero or estimate a total
from the successful/priced subset without a separately justified method.

Unknown values propagate to the affected whole-comparison point estimate and
interval. Per-task known values remain available for inspection, with explicit
coverage. Zero successes makes cost per success undefined even if known spend
is zero. Reference/oracle zero inference cost measures the reference check,
not a model's efficiency.

Read the existing [accounting reference](../../benchmark-agent-tasks/references/accounting.md)
for provider charges versus rate-card/runner estimates, subscription limits,
and excluded judge/infrastructure costs. Different bases and pricing sources
must accompany the comparison. Source byte checksums identify exactly which
pricing/accounting copy was analyzed; they do not turn an estimate into an
invoice.

## Fixtures, settings, and causal claims

Matching task labels alone is insufficient. The existing protocol gate fixes
tasks, fixture sources/content, seed, repetitions, limits, and scoring.
Recorded hashes and bodies must agree. Known conflicting task checksums or
source commits reject comparison. Missing per-attempt checksums are retained
with a limitation because failed upstream trials may never produce one;
the shared protocol supplies the pairing basis.

Targets can differ in model, provider, runner, version, reasoning setting,
or saved skill identity where the existing protocol permits it. The output
lists all recorded differences. To discuss a skill effect, verify that other
relevant conditions match; otherwise describe the result as a comparison of
the complete recorded systems. A skill path or digest is not proof that the
agent followed that skill.

Changing a timeout, task seed, tool protocol, or grader can be a useful future
experiment, but the current compatibility contract does not permit treating
its saved result as a paired target comparison. Explain the rejection and the
necessary compatible design. Never forge equal hashes, drop hard tasks, or
add a blanket override to obtain a result.

## Completion versus time budget

The exported curve uses observed attempt durations:

```text
success_fraction_at_budget =
    mean_over_tasks(
        successful attempts whose recorded duration <= budget
        / all attempts for that task
    )
```

All attempts remain in the denominator at every budget. Failed attempts,
timeouts, and missing-result outcomes contribute to ended/failure counts and
never become successes. A curve plateaus at the observed success fraction,
not automatically at one.

This is descriptive success by budget, not survival analysis or a prediction
of changing the configured timeout. An observed failure might have remained
a failure with more time; a success reported at trial end may have completed
its artifact earlier. The results do not identify these counterfactuals.

`wall_seconds` covers the recorded trial, potentially including harness setup,
verification, and cleanup. `agent_wall_seconds` covers only the agent when
recorded. The latter is never inferred by subtracting guessed overhead.
If any agent time is missing on either side, both agent-budget curves are
unavailable. The attempt export still retains those rows and their wall time.
Neither duration series is the run's elapsed clock or a sum of concurrent
tool durations.

## Analytical validation resources

The [fixture resource](../resources/analytical-fixtures.json) contains invented
observations, not live results. Its columns are success, status, wall seconds,
agent seconds, full cost, and known subtotal. Tests expand these compact
observations into the saved run schema in temporary directories.

`paired_mixed_outcomes` has three tasks with two attempts per side per task.
Baseline has 3/6 successes and cost 10; candidate has 4/6 and cost 14, including
failed-attempt spend. Hand-calculated deltas are:

| Metric | Candidate minus baseline |
| --- | ---: |
| Success rate | `1/6` |
| Mean wall seconds | `-1/3` |
| Mean agent seconds | `-1/6` |
| Cost per attempt | `2/3` |
| Cost per success | `14/4 - 10/3 = 1/6` |

`opposing_tasks` has task success deltas `+1` and `-1`. Resampling whole tasks
can produce `-1`, `0`, or `+1`; duplicating its repetitions must not shrink the
task interval. The tests also vary the number of distinct tasks, check seeded
percentiles and undefined ratio draws, and exercise missing spend, failure
curves, compatibility/duplicate gates, provenance, publication protection, and
an isolated plugin import without site packages or network execution.
