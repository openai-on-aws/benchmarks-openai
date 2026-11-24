# Recorded invoice comparison

Open `index.html` directly in a browser. This is a saved live run from 13 November 2026, not a fresh evaluation or a simulator. The invoices and reference answers are synthetic and unreviewed. The observed agreement does not establish production invoice accuracy.

The two backends each completed 96 evaluation trials, plus two route checks. Cost estimates cover model usage only. The interactive page links the underlying reports, summary JSON, CSV and editable AWS architecture.

The plugin's `demo` command also renders these saved reports into
`dashboard.html`, with overview, Test cases and Run details views.
It displays 96 held-out attempts across both backends; the other 96 evaluation
attempts were validation. Per-run model-cost totals exclude the four separate
route checks, while `comparison.json` includes them. Rendering the dashboard
makes no model or AWS calls.

Runtime account/resource identifiers in the cloud report were replaced with an explicit REDACTED placeholder. Trial answers, request IDs, scores, timing and token-cost aggregates are retained. Private deployment receipts, credentials, checkpoints and reusable connections are not shipped.
