# Charts for research workflows

Use this reference after an experiment comparison, failure diagnosis, or grader
audit. Calculate statistics with the packaged command first; use the saved
numbers to draw the chart. The chart should make one research question easier
to answer and keep the relevant task or attempt a click away.

## Choose the view

| Question | Analysis to generate first | View and useful interaction |
| --- | --- | --- |
| Which tasks improved or regressed? | `compare-experiments` | Paired task dots or a task × target heatmap. Select a cell to inspect its attempts. |
| Is the change consistent across tasks? | `compare-experiments` | A difference plot with the task-cluster bootstrap interval, if available. Show distinct task and repetition counts. |
| How much spend produced successful work? | `compare-experiments` | Success versus cost, with failed-attempt spend included and incomplete cost shown separately. |
| What fails repeatedly? | `diagnose` | Category bars or a task × category heatmap, filtered by evidence type and protocol. Select a group to show its supporting attempts. |
| Does the grader catch each defect? | `audit --execute` | Control × expected/observed outcome matrix. Highlight surviving mutants and rejected correct solutions. |
| What will the next experiment cover? | `design` | Conditions × task coverage and attempt counts. Show budget assumptions beside the plan. |

Small comparisons rarely need a dashboard. Prefer aligned bars or paired dots
over a complicated chart with one observation per model. For several models,
calculate each comparison against the same baseline rather than mixing
independently selected task subsets. Treat multiple comparisons as exploratory;
one favorable interval among many is not a confirmatory finding.

## Keep uncertainty and missing data visible

- Resampling must preserve task clusters. Use the comparison's saved method,
  seed, sample count, intervals, and warnings; do not manufacture intervals in
  JavaScript or resample repetitions as independent tasks.
- With one distinct task, show the observations and the unavailable interval.
  Repetitions can describe variability for that task, not general capability.
- Pairwise task differences use candidate minus baseline. Label the direction:
  higher success is favorable; lower time or cost is favorable.
- Use the same timing basis for every compared mark. Include timed-out and
  failed attempts in the analysis; a successful-attempt-only latency view must
  be explicitly labeled and cannot stand in for overall task performance.
- Unknown cost is a gap or an “unknown” row, never a zero-dollar point. Show
  coverage and cost basis in the detail view. A partial subtotal is not a total.
- A cost/success frontier is descriptive. If the task samples are small, do not
  designate a definitive winner or interpret overlapping estimates as a tie.
- A diagnosis chart counts observed categories, not proven causes. Preserve
  “unknown,” interrupted runs, missing attempts, and evidence truncation.
- An audit matrix distinguishes planned, passed, failed, and unavailable
  execution. Infrastructure errors do not count as successfully rejected mutants.
  Passing the shipped controls establishes only those controls' coverage.

## Drill-down and delivery

Use `COMPARISON.json`, `DIAGNOSIS.json`, `AUDIT.json`, or `DESIGN.json` and their
documented tabular outputs. Their owning skills explain the schemas and
limitations. Keep the original report path and source run checksums with the
visualization; do not extract rounded numbers from Markdown or screenshots.

Embed only the needed summary and public evidence identifiers. A selected
task/attempt can offer **Inspect evidence**, **Explain this difference**, or
**Plan another condition** using the shared
[follow-up guidance](../../benchmark-agent-tasks/references/follow-ups.md).
Pass the exact analysis path, source runs, target IDs, and selected task.
Filtering or selection changes the view; it does not launch a benchmark.

For a research paper or a figure to share with colleagues, generate a standalone
SVG, PDF, or PNG with standard plotting tools and save the plotting script and
input JSON beside it. For exploration inside Codex, use the host's available
inline visualization capability and preserve a self-contained offline fallback.
