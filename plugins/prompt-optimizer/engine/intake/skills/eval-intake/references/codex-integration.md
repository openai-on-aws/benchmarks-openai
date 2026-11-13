# Codex intake and target agents

The intake agent runs `eval-intake` interactively in Codex. An evaluated agent can separately use the Codex harness, with model, prompt, instructions, tools and runtime recorded in a variant. The standalone intake example includes a local support-policy agent and preview-first CLI wrapper. In the Model Upgrade module, `customer_eval` also imports the intake handoff and supports Codex through the reviewed AgentCore worker and a signed Responses route. Use the module’s [customer walkthrough](../../../../CUSTOMER-WALKTHROUGH.md) for that execution path.

The model list does not promise every provider/model runs inside Codex. Verify the Codex version, protocol, credentials, profile and account. The standalone intake preview wrapper supports only local Codex, an OpenAI model on the default OpenAI endpoint, a read-only policy workspace, and no Gateway/custom profile.

## AgentCore

AgentCore Runtime is a deployment environment. Hosting Codex there requires separate validation of container architecture/dependencies, invocation contract, process lifecycle, sessions, credentials and telemetry. Use the module’s [runtime guide](../../../../RUN-AGENTCORE.md) to inspect the current deployment receipts and qualify the exact worker, model and route before execution.

AgentCore Gateway exposes MCP tools. Codex supports Streamable HTTP MCP and bearer-token references. A JWT-authenticated Gateway is a candidate integration, subject to testing audience, scopes, schemas and invocation. Do not assume native AWS SigV4 signing in Codex. IAM Gateway authorization requires a verified per-request signing adapter; a static Authorization header is insufficient.

Record whether the whiteboard's Gateway branches provide model-invocation tools or business tools. A model called as an MCP tool is not automatically the model driving Codex's internal loop. Preserve that distinction in experimental claims.

## Example boundaries

The package's `scripts/codex_agent_demo.py` previews without inference. Optional `--execute` makes one CLI invocation, records events, response validity, elapsed time and process status. It does not score quality, enforce monetary budgets, compute cost, optimize prompts, run the full comparison or prove serving-model identity.

The wrapper uses `--ignore-user-config` and explicit OpenAI provider selection to avoid inheriting unrelated model/MCP settings. Account authentication and managed restrictions still apply. Only CLI help and offline mocked execution were checked during construction; no live model calls were made.

## Official references checked October 26, 2026

- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk): local Codex agent integration.
- [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp#streamable-http-servers): Streamable HTTP and bearer-token references.
- [Agent runtime options](https://developers.openai.com/api/docs/guides/agents#compare-agent-runtime-options): managed Agents API Codex harness is a separate hosting route from local Codex in AgentCore Runtime.
- [AWS Gateway MCP flow](https://docs.aws.amazon.com/prescriptive-guidance/latest/semantic-layer-agentic-ai-ontology-reasoning-virtual-knowledge-graph/mcp-server.html): authorization and tool routing.
- [AWS Gateway client](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-cli-get-started.html): signed requests with runtime AWS credentials.
