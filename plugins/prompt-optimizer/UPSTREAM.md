# Source and licensing

The MIT-0 evaluation engine derives from https://github.com/aws-samples/sample-gen-ai-evaluations-workshop, branch `feat/model-upgrade-module`, base commit `6de05624c160f0ae6688e1ba83747cc50c4d20a1`, plus the customer-evaluation integration snapshot prepared on 10 November 2026 and reviewed through 13 November 2026. Its source is bundled under `engine/`; it needs no neighboring checkout.

Packaging adds the invoice entrypoint, one discoverable Codex skill, manifests, installation tests, a recorded report and the AWS architecture. The guided engine also accepts `optimization_enabled: false` so this entrypoint does not require a third model or change the prompt. The input schema permits a null optimizer reference for that disabled mode. Existing engine callers retain their previous optimization default.

Plugin 0.1.1 adds a synthetic support-ticket triage starter using the same
entrypoint, clarifies the guided AgentCore setup before preparation, and fixes
the connection template's duplicate unsupported cache-price field. These
changes do not modify the bundled engine.

Plugin 0.1.2 renames the public package and marketplace entry to
`prompt-optimizer`. The bundled engine and its deployment artifact prefixes
remain unchanged.

The independently vendored ModelShift importer retains its MIT-0 license and exact upstream reference under `engine/customer_eval/vendor/modelshift/`.

`source-manifest.json` records imported file hashes before these documented integration edits. This plugin and the importer use MIT-0; it does not change the licenses of neighboring repository content.
