# Start Prompt Optimizer in a new session

Use the Codex app or CLI with a user-owned workspace. The plugin supplies
the workflow and evaluator; your studies, environments and reports live
outside its installed cache.

## Install or refresh

For a first installation:

```bash
codex plugin marketplace add openai-on-aws/benchmarks-openai --ref feature/customer-eval-plugin
codex plugin add prompt-optimizer@openai-on-aws
```

If the marketplace already uses this feature branch, get the current version:

```bash
codex plugin marketplace upgrade openai-on-aws
codex plugin add prompt-optimizer@openai-on-aws
codex plugin list --marketplace openai-on-aws --json
```

If the marketplace is registered from `main`, follow the
[source-switch instructions](../README.md#1-install-in-codex). The feature
branch is still named `feature/customer-eval-plugin`; the plugin itself is
`prompt-optimizer`.

Start a **new** Codex app chat in your chosen workspace, or launch a fresh CLI
session:

```bash
mkdir -p ~/prompt-optimizer-work
codex -C ~/prompt-optimizer-work
```

In the app, select **Prompt Optimizer** or its **Evaluate Workflows** skill.
In the CLI, `/plugins` opens the plugin browser. Installed skills become
available in new sessions; see the
[official plugin guide](https://learn.chatgpt.com/docs/plugins#plugin-directory-in-codex-cli).

## Prompts to try

**Explore the recorded dashboard without model or AWS calls:**

```text
Use Prompt Optimizer. Copy the recorded demo into ./recorded-demo and open
dashboard.html. Walk me through the model quality, cost and latency tradeoffs.
Distinguish distinct cases from repeated attempts, and challenge what this
synthetic, unreviewed evidence can actually prove. Make no new model or AWS calls.
```

**Prepare your own model-selection study:**

```text
Use Prompt Optimizer to help me choose between two models for my workflow.
Start by asking what failure would be most costly, what quality I require,
and what latency I can tolerate. Keep one fixed prompt and two model routes.
Help me inspect my inputs and expected answers, then choose local, AgentCore,
or both before freezing the study. Prepare a reviewable plan before execution.
```

**Try the second example:**

```text
Use Prompt Optimizer's support-ticket triage starter. Prepare a local study
in ./triage-study. Show me the routing policy and ambiguous tickets, and let
me challenge the expected answers. Keep the prompt fixed. Show the request
preview and plan before making paid model calls.
```

**Use AgentCore for a prepared comparison:**

```text
Use Prompt Optimizer to prepare the same fixed-prompt study for local and
AgentCore execution. Confirm my AWS profile and Region, and check whether
I already have a matching runtime, private artifact bucket and scoped role.
If setup is missing, show the concrete deployment needed. Resolve deployment
and model-call authorization, then run the agreed study and build the dashboard.
```

**Open evidence from an existing study:**

```text
Use Prompt Optimizer to inspect the saved study at /absolute/path/to/my-study.
Build dashboard.html from its existing report.json files and open it. Compare
local and AgentCore only when their input fingerprints match. Do not submit
a new run or make model calls.
```

Replace example paths with your own. For a cloud run, provide the AWS profile,
Region and any existing runtime/bucket references. A local Bedrock run needs
model access but no AgentCore deployment. Preparing or rendering a dashboard
does not deploy resources or make model calls.

## Run the demo directly from a terminal

This fallback uses a repository checkout and Python's standard library:

```bash
mkdir -p ~/prompt-optimizer-work
cd ~/prompt-optimizer-work
git clone --branch feature/customer-eval-plugin \
  https://github.com/openai-on-aws/benchmarks-openai.git
python3 benchmarks-openai/plugins/prompt-optimizer/scripts/invoice_eval.py \
  demo --output recorded-demo
```

Open `~/prompt-optimizer-work/recorded-demo/dashboard.html` in a browser.
On macOS:

```bash
open ~/prompt-optimizer-work/recorded-demo/dashboard.html
```

The demo refuses to replace an existing directory. Choose a new output name
for another copy. To rebuild its dashboard with an updated renderer:

```bash
python3 benchmarks-openai/plugins/prompt-optimizer/scripts/invoice_eval.py \
  dashboard --project recorded-demo \
  --output recorded-demo/dashboard.html --force
```

For a fresh study's preparation and execution commands, use the
[workflow reference](../skills/evaluate-invoices/references/workflow.md) or
[triage starter guide](../examples/support-ticket-triage/README.md).
