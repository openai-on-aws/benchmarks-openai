# Evaluate a whole conversation

Reuse supplied conversations and policies. Draft what should happen after each user message, then ask the customer only about ambiguous behavior. For example: should the agent ask for a missing order ID, retry a temporary lookup error, or reuse a result already obtained for the same order? These are workflow choices, not fields the customer must fill manually.

Store the reviewed expectations in each dataset row's `expected.turns`, in the same order as its `turns`. The schema is `assets/turn-expectations.schema.json`. Keep uncertain expectations null in a draft and identify their source and label status. The runner rejects incomplete executable expectations; draft validation and execution readiness are distinct.

Example expectations for one return decision:

```json
{
  "response": {"order_id": "A1", "decision": "deny"},
  "tools": {
    "allowed": [{"name": "lookup_order", "arguments": {"order_id": "A1"}}],
    "required": [{"name": "lookup_order", "arguments": {"order_id": "A1"},
                  "result_contains": {"opened": true}, "scope": "turn"}]
  }
}
```

An empty `allowed` list means no tools are permitted on that turn; an empty `required` list means no tool evidence is needed. Required evidence defaults to the current turn. Use `scope: conversation` only when earlier evidence is valid for the current request; evidence from a future turn never qualifies. Additional calls are allowed only if their name and full arguments match an allowed entry. Required evidence checks result fields, not just the presence of a tool name. These rules do not impose an exact trace or an arbitrary minimum number of calls.

Configure the three deterministic checks in `grading.json`:
Use optional `min_count` on required evidence and `max_calls` on an allowed invocation when the policy specifies a retry count. For example, a persistent temporary error with one retry requires two matching error results and allows at most two calls. Leave these bounds out when the customer has not specified them.

```json
{
  "trace": {"type": "turn_trace"},
  "responses": {"type": "turn_response", "response_schema": {
    "type": "object", "required": ["order_id", "decision"],
    "properties": {"order_id": {"type": ["string", "null"]},
                   "decision": {"enum": ["allow", "deny", "needs_review", "request_order_id"]}},
    "additionalProperties": false
  }},
  "tools": {"type": "turn_tools"}
}
```

The worker records turn indices, user messages, answers, tool arguments/results and monotonic timings outside the model. Both Codex and Strands produce the same normalized receipts. The grader verifies receipt consistency, the agreed response schema and expected field values, and the required tool evidence before the answer. Old traces without these receipts cannot be reported as passing the stronger checks.

These response checks cover structured JSON contracts. Free-form explanations need an explicitly defined semantic rubric and calibration, using the existing `llm_rubric` facility or a reviewed customer grader. A lookup check does not prove all free-form claims are correct. For a constrained JSON demo, forbid unsupported action claims through the schema and check every allowed decision/reason field.

Before live evaluation, test the grader with deliberately wrong intermediate answers, wrong identifiers, missing results, invalid JSON and unsupported claims, plus correct clarification and reuse examples. Keep original results immutable; publish revised grades separately. Use development feedback for prompt edits, validation for selection, and a fresh holdout after freezing selection. Report actual cost and latency separately from quality and introduce hard limits only when requested.
