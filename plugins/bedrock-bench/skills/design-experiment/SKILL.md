---
name: design-experiment
description: Turn a benchmark question into a bounded, reproducible Bedrock Bench experiment plan with explicit conditions, runnable configs, task counts, and budget assumptions before execution.
---

Use the packaged CLI at `../../scripts/bench.py`, resolved from this skill
directory. `design BRIEF --out NEW_DIRECTORY` calls the installed
`bedrock_bench.design.write_design` engine. Planning uses the standard library;
it validates configurations without model calls, downloads, containers, or AWS
operations. Keep the brief and outputs in the user's selected workspace.

Read the [brief format](references/brief-format.md) when authoring a brief.
For a skill ablation, causal question, sample-size decision, or customer-facing
conclusion, also read [experiment design](references/experiment-design.md).
Use the [offline example](assets/offline-demo.brief.json) to exercise the
planning workflow. Start AWS/CDK skill ablations from the
[pilot template](assets/aws-cdk-skills-ablation.brief.json); its missing model,
region, and local skill must be filled from the user's choices.

- Translate the question into explicit target conditions. Preserve the chosen
  models, providers, regions, runners, task IDs, and limits. Discover exact tasks
  with `tasks --suite NAME`; the CDK catalog currently contains one task.
  Repeating it does not create a larger or representative AWS task suite.
- Use one normal runner experiment as the shared base. The first condition is
  unchanged; later conditions explicitly patch each base target. Use
  `single-factor` for one changed field such as `skills` or `model`.
  `system-comparison` describes a complete settings bundle and supports no
  claim that one component caused the difference. Neither allows changes to
  tasks, grading, seed, repetitions, environment, or limits across conditions.
- For skills, hold runner/version, provider/model/region, reasoning, and limits
  fixed within each base target. Inject actual local skill directories only
  through the Harbor runner's `skills` field. The engine resolves relative
  tools and skill paths against the input brief and records skill content hashes.
- Save the brief, generate the design into a new directory, and read
  `DESIGN.json` plus the returned `DESIGN.md`. Present distinct tasks,
  repetitions, conditions, exact total attempts, limit support, preparation
  gaps, and the scope of the eventual conclusion. A plan is not a run result.
- Use `experiment.json` for the planned interleaved run. Files under
  `conditions/` are alternatives for separate runs, not additional trials to
  execute alongside it. Report attempts for the chosen layout.
- Keep unknown pricing unknown. Token assumptions describe a whole attempt,
  including unsuccessful attempts and all calls. Match supplied rate cards
  exactly; record their source and date. An inference estimate, budget reserve,
  or timeout is not an enforced dollar cap or a complete AWS bill.
- Route execution through
  [benchmark-agent-tasks](../benchmark-agent-tasks/SKILL.md), preserving existing
  authorization. Do not change a brief, extend trials, or perform paid execution
  merely to make an estimate fit. For a revised plan, write a new brief/output
  directory and preserve the prior design.

Route follow-on work by purpose:

- [compare-experiments](../compare-experiments/SKILL.md) for matched task
  differences and uncertainty across compatible completed runs.
- [diagnose-failures](../diagnose-failures/SKILL.md) for recurring failure
  patterns and missing attempts across saved evidence.
- [audit-benchmark](../audit-benchmark/SKILL.md) for grader validity, alternate
  correct answers, or false-positive/false-negative checks.
- [inspect-results](../inspect-results/SKILL.md) for one attempt, accounting
  evidence, the library, or recorded replay.
- [visualize-results](../visualize-results/SKILL.md) for a new plot or diagram.

Preserve downstream protocol, completion, evidence-type, and accounting checks.
A planned condition label never overrides a rejected comparison.

After a substantive plan or explanation, offer two or three contextual prompts
using the shared
[follow-up guidance](../benchmark-agent-tasks/references/follow-ups.md).
Use its exact pre-text and quoted clickable labels. For a plan, carry the
absolute brief/design/config paths and concrete attempts/limits instead of
inventing a run ID. Suggestions to execute remain subject to existing
authorization; suggestions to compare, diagnose, audit, or visualize saved
results carry their actual run and attempt sources.
