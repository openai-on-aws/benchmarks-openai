# Follow-ups after Bedrock Bench responses

After each substantive Bedrock Bench turn—including a plan, explanation, report,
library, replay, or custom visualization—offer two or three short, contextual
next steps. Put them after the result or visualization. Progress updates do not
need suggestions. Respect a user request to omit them.

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

In Codex hosts that support artifact follow-ups, render each suggestion as a
Markdown list item:

```text
- :codex-followup[Explain the slowest attempt]{prompt="Use $bedrock-bench:benchmark-agent-tasks to inspect saved run RUN_ID at SOURCE_PATH. Identify the slowest recorded attempt and explain its tools and grader evidence. Analyze saved results only."}
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
