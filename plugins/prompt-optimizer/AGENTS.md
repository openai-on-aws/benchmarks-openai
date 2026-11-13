# Prompt Optimizer development

The plugin must run from its installed cache without the source repository.
Keep the public interface focused on fixed-prompt structured-output comparisons:
invoice OCR-text extraction and support-ticket triage share one skill,
`evaluate-invoices`, and one entrypoint, `scripts/invoice_eval.py`. Retain those
identifiers for compatibility; do not add a runner or skill for each example.

Treat the plugin directory as read-only input. Projects, environments, reports,
receipts and build output belong in the user's workspace. The `demo` command
copies historical evidence and must never invoke models or AWS.

The default invoice study freezes the prompt and requires only source and
target models. Existing engine callers retain their optimization default.
Do not weaken label provenance, input fingerprints, split separation, spend
accounting or durable recovery to make a demo pass.

Run the installation checks and engine tests documented in README.md.
Distinguish offline/loopback verification from live AWS execution.
Do not include account-specific deployments or credentials in the plugin.
Preserve the engine and vendored importer MIT-0 license notices.
