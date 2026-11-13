# Returns assistant with every turn evaluated

This example exercises a customer workflow with Codex by default and Strands as an alternative. The intake skill prepares the configuration from the policy and supplied examples; the customer does not need to author YAML. The evaluated agent uses a read-only mock order lookup. Real order systems and refund actions are outside this example.

The example has 8 development, 10 validation and 12 holdout conversations. Cases cover opened items, the 30-day boundary, overdue and missing orders, corrected identifiers, missing identifiers, reuse of prior evidence, transient and persistent tool failures, and attempted policy overrides. Labels are generated from the stated policy and remain **unreviewed by a human**. Two repeats per case demonstrate repeat execution; they do not establish statistical superiority or customer adoption readiness.

Each conversation is graded using:

- `trace`: every turn has a consistent answer, arguments, tool result and timing receipt.
- `responses`: every JSON answer has the right current order, decision and reason, with no unsupported extra fields.
- `tools`: every lookup uses allowed arguments, produces supporting evidence, and follows the specified retry behavior. Clarification needs no lookup. An unchanged order can reuse earlier evidence.

`policy.txt` defines behavior. `grading.json` defines the schema and graders. `data/*.jsonl` contains the user turns, mock state and per-turn expectations. `prompts/p1.txt` is the original mutable prompt. The optimizer changes only that prompt; the policy and scoring rules remain fixed. `build_cases.py` documents how the synthetic examples were authored; do not regenerate them to tune to observed holdout results.

Use the module's [customer walkthrough](../../CUSTOMER-WALKTHROUGH.md) for intake, bundling, execution and report review. This richer example requires a real local or AgentCore run for agent behavior; the older offline simulation fixtures validate plumbing only. `plan` and the test suite require no model calls.

Cost and latency are both preferences. A candidate must preserve the configured quality requirement and have no observed accuracy regression. A conflicting cost/latency tradeoff is presented for review, and an unchanged selected prompt is a valid result. There are no default minimum percentage improvements.
