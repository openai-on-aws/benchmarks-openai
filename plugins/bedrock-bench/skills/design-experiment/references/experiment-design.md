# Designing interpretable agent experiments

Read this for skill ablations, causal questions, decisions about task coverage,
or conclusions intended beyond the selected fixtures. A small pipeline check
usually only needs the brief format and offline example.

## State the decision and keep its comparison identifiable

Write a question the selected grader can answer. “Does injecting this CDK skill
improve repair success for this model on these tasks?” maps to artifact pass/fail
and task-level paired outcomes. “Which model is best at AWS?” cannot be answered
by repeating one local CDK fixture.

Use the unchanged first condition as the baseline. For a skill ablation, keep
runner/version, provider/model/region, reasoning, tier, task source, environment,
seed, repetitions, grader, and limits fixed within each base target. Change only
`skills`. A base with several models creates matched strata: estimate the skill
effect separately for each model before discussing an overall pattern.

Record exactly which skill directories are injected and their hashes. A path
alone is not a treatment identity. Keep solutions, verifier files, and saved
successful trajectories out of injected skills. Distinguish access to skill
content from evidence that the agent actually used it; inspect saved public tool
activity after execution if that distinction matters.

Use `system-comparison` when the question intentionally concerns a bundle such
as runner + model + provider. Record all changed fields. A changed result cannot
then be attributed to one component. This mode retains the same task protocol;
it does not permit uncontrolled tasks, grader changes, or comparison bypasses.
Unsupported tool sets, system prompts, sampling parameters, or custom graders
require runner/task implementation work before they can become runnable
conditions. Do not encode them in notes and claim they will execute.

## Coverage, repeats, and pilot scope

Choose distinct tasks for the decision before increasing repetitions. Repeats
measure variability on selected tasks. They do not add independent task families.
For starter fixtures, `seed + repetition - 1` changes generated task data.
Repository suites repeat the selected pinned task sources. The seed randomizes
trial order, not provider sampling, client retries, or service behavior.

The shipped catalog currently has:

| Suite | Catalog scope | Consequence |
|---|---|---|
| `starter` | Three deterministic fixture families | Pipeline and accounting checks, not a quality leaderboard |
| `aws-cdk-smoke` | One SQS/Lambda/DynamoDB repair task | Skill-injection pilot; repeated success is still one task |
| `terminal-bench` | Packaged Terminal-Bench 2.0 snapshot | Deliberately select exact task IDs; do not infer AWS coverage from the name |
| `swe-bench` | Packaged SWE-bench Verified conversion | Repository repair evidence; container platform can affect timing |
| `aws-bench` | Nine quickstart AWS discovery tasks in the snapshot | Live testing environment and model judge; Harbor skill injection is not exposed |

Use `suites` and `tasks --suite NAME` for the installed snapshot's actual counts
and names. Do not invent unavailable AWS/CDK tasks or relabel repeats as
distinct scenarios. If the desired workload is absent, produce the supported
pilot and identify the task/grader work needed for a broader study.

Start with a bounded pilot when variance, failure modes, or cost are unknown.
Use it to check injection, authentication, runtime behavior, and accounting
coverage. Define any later expansion in a new brief with its own counts and
authorization. A successful reference/oracle smoke checks the task/container/
grader path, not model access, tool competence, or inference economics.

Do not invent a “statistically sufficient” repeat count. Power depends on the
expected paired effect, discordant outcomes, task diversity, and intended
decision. With no pilot data, label the chosen count as a resource-bounded pilot.
For broader inference, report uncertainty over distinct task units; repeated
trials of one task are clustered observations, not extra independent tasks.

## Order, limits, and stopping

Prefer the combined generated `experiment.json`: conditions are target variants
in one seeded, serial runner schedule, reducing baseline-then-treatment time
effects. The design includes exact attempt IDs and ordering. Per-condition JSON
files support separately scheduled runs but have their own shuffles; record
external timing and ordering if those alternatives are used. Never execute both
layouts as though they were one budget.

Preserve task sources, seed, repetitions, limits, and grader for downstream
protocol checks. Treat completed runs with changed agent versions or changed
skill content as different execution identities, even if planned labels match.
Do not rewrite saved protocol hashes, discard failed attempts, or merge
incompatible runs merely to obtain a comparison.

Native starter limits bound model calls and per-response output tokens as well
as controller time. Codex/OpenCode internal calls or output tokens can remain
client-managed. Repository suites bound agent/setup/verifier/process time,
with one trial per process and no automatic upstream trial retries. External
service retries, internal client activity, setup overhead, and partial accounting
still matter. The sum of timeouts is neither a precise duration prediction nor
an enforced whole-run deadline.

Use the finite attempt schedule as the planned stopping point. If the pilot
reveals an unexpectedly costly or invalid setup, retain the partial evidence
and reassess through the execution workflow; do not silently refill failures or
increase repetitions until a result looks favorable. A reserve or estimate does
not implement automatic dollar-based stopping.

## Budget and the analysis handoff

Use supplied whole-attempt usage assumptions and exact sourced rate cards.
Avoid guessing prices, assuming every attempt passes, or pricing only successful
attempts. Document source dates, assumed cache use, unpriced targets, and
infrastructure/judge exclusions. A changed model or region can invalidate an
otherwise available rate card. For cache-heavy or long-context workloads, check
the rate card's applicability boundary. The planner does no price lookup.

Before interpreting results, establish:

- **Comparison:** completed compatible runs, the exact base/condition mapping,
  actual versions and skill hashes, paired task/repetition outcomes, and counts.
- **Diagnosis:** saved failed attempt IDs, grader output, bounded tool evidence,
  and missing/truncated traces. A plausible explanation is not a proved cause.
- **Accounting audit:** usage/cost coverage, cost basis, failed-attempt spend,
  exclusions, and provider-reported charges versus estimates. Unknowns remain
  unknown; zero successes has no finite cost per success.
- **Grader audit:** independent correct solutions, alternate valid answers,
  malformed artifacts, and deliberate semantic mutants before trusting a
  suspicious pass/fail pattern.
- **Visualization:** the concrete comparison or recorded sequence to show, with
  task counts, units, uncertainty where supportable, and source links.

Use [compare-experiments](../../compare-experiments/SKILL.md) for matched
differences and uncertainty,
[diagnose-failures](../../diagnose-failures/SKILL.md) for failure inventories,
[audit-benchmark](../../audit-benchmark/SKILL.md) for grader checks,
[inspect-results](../../inspect-results/SKILL.md) for individual attempts or
accounting evidence, and
[visualize-results](../../visualize-results/SKILL.md) for new plots and diagrams.
Execution or expansion uses
[benchmark-agent-tasks](../../benchmark-agent-tasks/SKILL.md). Follow the
[shared suggestion rules](../../benchmark-agent-tasks/references/follow-ups.md)
for quoted next steps with the user's actual design or saved-result paths.
