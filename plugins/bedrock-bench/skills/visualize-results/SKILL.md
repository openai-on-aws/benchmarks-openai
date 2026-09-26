---
name: visualize-results
description: Create charts and diagrams from saved Bedrock Bench runs, including cost versus success, timing, token usage, compatible run history, and recorded tool sequences.
---

Create the plot or diagram that answers the user's question using saved evidence.
Use the packaged CLI at `../../scripts/bench.py`, relative to this directory.
Keep outputs in the user's selected project or results directory.

For the existing library, report, or replay, use
[inspect-results](../inspect-results/SKILL.md). For a new chart, read the
[chart data guide](references/chart-data.md) for the fields and accounting rules.

- Resolve the exact run sources from the user's request, selected view, or
  `runs RESULTS_DIRECTORY`. Identify live measurements, reference checks, and
  synthetic demos explicitly.
- Use `report RUN_JSON [RUN_JSON ...] --out REPORT_DIRECTORY` to validate
  compatible completed runs and generate `comparison.json`. Keep per-run
  aggregates as separate observations for a history plot; the combined report
  aggregates repetitions and is not a time series.
- Use `replay RUN_JSON --out REPLAY_DIRECTORY [--attempt ATTEMPT_ID]` for public
  prompts and recorded tool exchanges. `REPLAY.json` includes evidence sources,
  limits, and unavailable traces. An absent trace is not a zero-tool attempt.
- Prefer an available host visualization skill for interactive inline plots.
  Follow its rendering contract and preserve the artifact in the authorized
  output directory. Do not depend on a particular host, plugin version, or
  installed cache path. Without inline support, create a self-contained HTML/SVG
  chart or a static image and open/link it using the available file tools.
- Use Mermaid when a small static sequence or architecture diagram answers the
  question. For a detailed AWS architecture drawing, use an available AWS diagram
  skill when appropriate. Distinguish the task's intended architecture from
  resources actually verified in saved synthesis/grader evidence.
- A tool diagram shows observed commands, replies, and outcomes. Recorded order
  does not prove causation; parallel or asynchronous tools must remain distinct.
  Do not infer hidden reasoning, invent intermediate steps, or run recorded code.
- Include units, sample counts, source/run identifiers, and accounting coverage.
  Preserve unknowns. Use one common timing basis for compared marks and label it.
  A single attempt is one observation, not a latency distribution or a model ranking.
- Check the resulting view: accurate labels and denominators, working primary
  interaction, readable light/dark appearance, and narrow layout when inline.
  Use standard plotting tools for publication/export figures.

After the chart or explanation, offer contextual next steps using the shared
[follow-up guidance](../benchmark-agent-tasks/references/follow-ups.md).
New benchmark execution belongs to
[benchmark-agent-tasks](../benchmark-agent-tasks/SKILL.md); analyzing results
does not authorize more model calls.
