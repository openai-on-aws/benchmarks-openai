# Retail returns customer example

Start with the full operating prompt in prompts/p1.txt and the immutable policy in policy.txt. This constructed Northstar Home support example is synthetic and contains no customer records. Labels are policy-derived and human-unreviewed.

The intake configuration collects the models, harness, data, graders and execution choices. Codex is the default; Strands can use the same contract. The prompt can change while policy, tool permissions, response schema and grading stay fixed.

The search runs two candidates in each of two generations with patience two. Mutation receives measured development cost, latency, request/token counts, expected/actual behavior and previous candidate development results. Validation selects; final holdout remains untouched until selection is frozen. No 5 percent improvement requirement is imposed. The incumbent remains selected if no candidate provides an unambiguous improvement.

Primary latency is conversation time excluding harness setup and teardown. The report also records worker total, setup, teardown, authentication, provider request time, first output and tool durations. Provider timings include network and provider processing, not pure model compute. Every conversation starts a fresh harness; followup turns reuse that harness but are different tasks, not a controlled warm/cold comparison.

There are 12 development, 12 validation and 18 holdout conversations with three repeats. Build_cases.py generates these fixtures before execution. Do not regenerate or tune from holdout results.

Follow CUSTOMER-WALKTHROUGH.md in the parent module. Run plan and the local test suite before any authorized model calls. The generic simulation is a plumbing demo and does not predict this example's model quality or measured timing.
