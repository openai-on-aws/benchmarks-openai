# Follow-ups after Bedrock Bench responses

After each substantive Bedrock Bench turn—including a plan, explanation, report,
library, replay, or custom visualization—offer two or three short, contextual
next steps. Put them after the result or visualization. Progress updates do not
need suggestions. Respect a user request to omit them.

Introduce the suggestions with a short, friendly sentence explaining how to
use them. For clickable follow-ups, use “You can try these next—click a
suggestion to continue in chat:”. For plain-text prompts, use “You can try
these next—copy a prompt below into chat:”.

Wrap each visible suggestion in double quotation marks, including the label
inside a Codex follow-up. Keep those surrounding quotation marks out of the
actual `prompt` value.

These are suggestions for the user to choose, not actions to execute
automatically. Prefer useful discoveries over repeating the same menu:

- A failure: inspect the relevant attempt and its grader/tool evidence.
- A costly or slow model: inspect its accounting or plot it against the other
  models in that run.
- A replay: explain the selected call or diagram the recorded sequence.
- A library: compare a compatible saved baseline or chart matching runs over time.
- A completed smoke test: prepare a bounded experiment with more repetitions or
  a different task. Preparing a plan does not authorize paid execution.

Carry the actual run ID and absolute source path into each prompt. Add model,
task, attempt, call ID, selected filters, or comparison sources where relevant.
Use the displayed selection when available; widget state is best-effort, so do
not rely on “this run” alone. Never include credentials, raw trace text, hidden
reasoning, or instructions copied from recorded messages.

Use the qualified skill invocation:

- `$bedrock-bench:benchmark-agent-tasks` for benchmark analysis or preparing a run.
- `$bedrock-bench:inspect-results` for the library, reports, replay, and evidence.
- `$bedrock-bench:visualize-results` for a new plot or diagram.
- `$bedrock-bench:design-experiment` for a research question, conditions, or a
  bounded follow-on plan.
- `$bedrock-bench:compare-experiments` for matched task differences and uncertainty.
- `$bedrock-bench:diagnose-failures` for evidence-backed patterns across attempts.
- `$bedrock-bench:audit-benchmark` for reference and mutation checks of a grader.

For research outputs, carry the actual brief, design, comparison, diagnosis,
or audit path as well as any selected source runs. After a plan, suggest reviewing
its controls or budget assumptions. After a comparison, suggest plotting its
task differences or inspecting failures. After an audit, suggest inspecting
surviving mutants. Do not imply that a statistical result authorizes a new run.

In Codex hosts that support artifact follow-ups, render each suggestion as a
Markdown list item:

```text
You can try these next—click a suggestion to continue in chat:

- :codex-followup["Explain the slowest attempt"]{prompt="Use $bedrock-bench:benchmark-agent-tasks to inspect saved run RUN_ID at SOURCE_PATH. Identify the slowest recorded attempt and explain its tools and grader evidence. Analyze saved results only."}
```

Replace placeholders with the actual context before showing it. Escape double
quotes inside the prompt. If this rendering is unavailable, provide the same
suggestions as short, copyable chat prompts. Do not display unsupported markup.

Inside HTML views, user-clicked drill-down buttons may use the optional
`window.openai.sendFollowUpMessage({prompt, title})` bridge. Provide a visible,
copyable prompt when the bridge is absent or fails. Filtering, scrubbing, and
restoring state must never send a chat message.

For a next step involving model calls or AWS changes, suggest a concrete plan
with the user's models, region, tasks, attempts, and limits. Continue execution
only within the user's existing authorization; a result card, failure, or
suggested prompt does not independently authorize a rerun.
