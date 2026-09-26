# Saved data for charts and diagrams

Use Python's Bedrock Bench aggregates as the source of metrics. Do not recover
numbers from rendered labels, whose values may be rounded.

| Question | Saved input | Useful visual |
| --- | --- | --- |
| Which model meets a quality target cheaply? | `comparison.json` → `targets` | Cost/success scatter or aligned bars with passed/attempts |
| Where is time spent? | Attempt wall/agent seconds; recorded Harbor phase timestamps | Shared-scale timing bars or phase lanes |
| Does a later run improve things? | Each compatible run's own aggregates and start time | Per-target history, with each run identified |
| Which tasks fail? | `comparison.json` → `tasks`; `run.json` → `attempts` | Target/task grid with success counts |
| How were tokens used? | Per-attempt `usage` | Input/cache/output breakdown with missing categories shown |
| What did the agent do? | `REPLAY.json` → `models[].calls` and `promptSources` | Recorded sequence, tool timeline, or attempt diagram |

`library RESULTS_DIRECTORY --out LIBRARY_DIRECTORY` saves `LIBRARY.json` with
each run's `source`, evidence type, `comparisonGroup`, per-target aggregates, and
available replay data. It is a snapshot, not live monitoring. The library checks
reviewed accounting copies against the original checksum and unchanged outcomes.
Retain its provenance/warnings if using those copies.

## Costs and counts

- `total_cost_usd` is the whole target's recorded inference cost.
- `cost_per_success_usd` includes failed-attempt spend. It is undefined when
  any cost is unknown or no attempts succeed. Never chart that as zero.
- `known_cost_subtotal_usd` can be shown separately, labeled as a subtotal.
- Retain `attempts`, `successes`, `cost_coverage`, and `cost_basis` with a cost
  comparison. A reference solution's zero inference spend is not a model price.
- A view's failure filter must not silently remove failed-attempt costs from a
  cost-per-success denominator.
- Estimates exclude infrastructure, subscriptions, external tools, and graders.
  See [accounting](../../benchmark-agent-tasks/references/accounting.md) for pricing scope.

## Times, tokens, and history

- `median_agent_seconds` is agent-only time when available for every attempt
  in the aggregate. `median_wall_seconds` includes trial setup and verification.
  `controller_wall_seconds` is elapsed time for the whole run. Do not sum
  concurrent trial durations and call it elapsed run time.
- Use agent time for all compared targets only when all have it; otherwise use
  trial time for all, or visibly separate the two metrics.
- Cached read/write tokens are subsets of `input_tokens`; reasoning tokens are
  a subset of `output_tokens`. Do not double-count these in stacked totals.
  Unknown categories stay unknown.
- Compare only matching task protocols and evidence types. Keep different
  model/provider/runner versions and settings identifiable. For trends, require
  a stable target identity; matching friendly names alone are insufficient.
- Show gaps for missing data. Do not invent confidence intervals or turn one
  smoke sample into a distribution.

## Replay evidence

Replay currently reads timestamped Harbor/Codex session files. Each item has an
attempt ID, full model ID, task, agent interval, saved prompt, session checksums,
and bounded calls. Call `source`/`outputSource` identify files and lines;
`recordedCallId` is the original ID. Times are seconds from the run's start;
subtract the attempt's `agent.start` for its local replay clock.

Honor `replyRecorded`, `commandClipped`, `outputClipped`, `promptClipped`,
`warnings`, and `unavailable`. A running-process reply is not process completion.
Tool-call wall time may include waiting and may overlap other calls. Derive
non-overlapping intervals before plotting time shares; do not add durations
blindly or assign all other agent time to reasoning.
