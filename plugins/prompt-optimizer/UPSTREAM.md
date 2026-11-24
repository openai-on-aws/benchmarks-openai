# Source and licensing

The MIT-0 evaluation engine derives from https://github.com/aws-samples/sample-gen-ai-evaluations-workshop, branch `feat/model-upgrade-module`, base commit `6de05624c160f0ae6688e1ba83747cc50c4d20a1`, plus the customer-evaluation integration snapshot prepared on 10 November 2026 and reviewed through 13 November 2026. Its source is bundled under `engine/`; it needs no neighboring checkout.

Packaging adds the shared `scripts/workflow_eval.py` entrypoint, one discoverable Codex skill, manifests, installation tests, a recorded report and the AWS architecture. The guided engine also accepts `optimization_enabled: false` so this entrypoint does not require a third model or change the prompt. The input schema permits a null optimizer reference for that disabled mode. Existing engine callers retain their previous optimization default.

Plugin 0.1.1 adds a synthetic support-ticket triage starter using the same
entrypoint, clarifies the guided AgentCore setup before preparation, and fixes
the connection template's duplicate unsupported cache-price field. These
changes do not modify the bundled engine.

Plugin 0.1.2 renames the public package and marketplace entry to
`prompt-optimizer`. The bundled engine and its deployment artifact prefixes
remain unchanged.

The independently vendored ModelShift importer retains its MIT-0 license and exact upstream reference under `engine/customer_eval/vendor/modelshift/`.

Plugin 0.4.0 uses the `evaluate-workflows` skill and `scripts/workflow_eval.py`
entrypoint for both examples. The rename mapping is recorded in
`INTEGRATION-PROVENANCE.json`; the evaluation engine is unchanged.

`source-manifest.json` records imported file hashes before these documented integration edits. This plugin and the importer use MIT-0; it does not change the licenses of neighboring repository content.

Plugin 0.5.6 keeps actionable diagnostics for every failed development check in
the optimizer request even when a complete long example is omitted. It preserves
exact small expected/actual and missing/unexpected values, marks oversized values
explicitly, and stops before a model call when necessary failure evidence cannot
fit. Optimizer instructions also preserve policy precedence and cumulative actions.
These are general feedback changes; the engine contains no service-credit rules.

## Dashboard evidence comparison (0.5.7)

Overview connects same-model prompt changes to measured outcomes: both cost
measures, complete output-token usage, required-check success, criterion-level
failures and a paired worked example. The default example is selected
deterministically from saved outcomes; all matched pairs, including regressions,
remain inspectable. Full inputs, references and prompt differences are preserved.
Unverified criteria and incomplete or unmatched evidence do not receive a
comparison claim. These are offline reporting changes; the evaluation engine,
cloud runtime, grading and saved prompt selection are unchanged.

## Test data notice (0.5.8)

Overview uses a short label for synthetic demo cases and a link to Run details.
The full expected-answer review status remains under About the test data.
Study-specific case history belongs there too. Results, grading and selection
are unchanged.
