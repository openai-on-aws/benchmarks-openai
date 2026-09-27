# Bedrock Bench development

This directory is an independently installable plugin. Its implementation lives
in `scripts/bedrock_bench`; `scripts/bench.py` is the packaged entrypoint. The
repository-root `bench.py` forwards to it. Runtime code and skill references
must work from the installed plugin cache without a neighboring source checkout.

Keep documentation focused on its reader:

- `README.md`: human onboarding, installation, first report, and chat examples.
- `skills/benchmark-agent-tasks/SKILL.md`: agent workflow and execution guidance.
- `skills/inspect-results/SKILL.md`: saved-result exploration and evidence guidance.
- `skills/visualize-results/SKILL.md`: on-demand charts and recorded-sequence diagrams.
- `skills/design-experiment/SKILL.md`: research briefs, controlled conditions,
  runnable configurations, and budget assumptions.
- `skills/compare-experiments/SKILL.md`: matched task comparisons and uncertainty.
- `skills/diagnose-failures/SKILL.md`: failure patterns grounded in saved evidence.
- `skills/audit-benchmark/SKILL.md`: local positive and negative grader controls.
- `skills/benchmark-agent-tasks/references/follow-ups.md`: suggestion behavior
  scoped to an actual Bedrock Bench skill invocation in the current turn, linked
  from every runtime skill. Keep it in the shipped skills; contributor
  instructions alone are not plugin runtime memory.
- `skills/benchmark-agent-tasks/references/`: conditional CLI, suite, and
  accounting details.
- This file: contributor guidance for changes to the plugin.

Working on the plugin does not invoke its runtime skills. Do not add Bedrock
Bench follow-up suggestions to plugin administration, code or documentation
maintenance, PR updates, or ordinary chat. Preserve the current-turn invocation
check in every skill entrypoint; an earlier benchmark invocation does not enable
suggestions for the rest of a conversation. Keep this behavior out of global
instructions and general-purpose message hooks.

For runtime or adapter changes, run the relevant checks from the repository root:

```bash
python3.12 -B -m unittest discover -s tests -p 'test_bedrock_bench*.py' -v
```

The two installed-harness schema checks need prepared Harbor/AWS-Bench
environments in `.bench-tools`. For documentation changes, check referenced
paths and anchors; validate skill frontmatter when changing the skill entrypoint.
Reference smoke checks and live model measurements are different validation
stages; describe which stage was actually exercised.

`assets/report.html`, `assets/library.html`, and `assets/replay.html` are the
standalone/inline view templates. Keep model replay positions independent and
the full tool list visible. Playback and scrubbing select the latest call reached,
holding it between events; clicking a call pauses playback for inspection.
Avoid hardcoded run IDs or local paths.
Render untrusted labels and evidence as text, keep evidence paths inside the
source run directory, and use the Python accounting aggregates in every view.
The explorer must remain usable without a Codex host bridge or network access.
Follow-up actions carry exact run/attempt context and qualified skill names.
Filtering and state restoration never send messages or execute benchmarks.

Research commands use the same saved schemas and accounting as the explorer.
Keep repeated attempts within their task when estimating uncertainty; one task
does not support a general ranking. Comparisons retain protocol and evidence-type
gates. Diagnosis may inventory different protocols but must label those groups.
Plans are not executions, budget estimates are not enforced spend caps, and
unavailable audit tooling is not evidence that a grader rejected a mutant.
Keep research helpers dependency-free where possible and exercise the installed
plugin from outside a source checkout.
