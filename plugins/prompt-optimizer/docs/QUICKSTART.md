# Start Prompt Optimizer in a new session

Use the Codex app or CLI with a user-owned workspace. The plugin supplies
the workflow and evaluator; your studies, environments and reports live
outside its installed cache. Follow steps **1–5** for Codex, or use the
[terminal-only alternative](#alternative-run-the-demo-directly-from-a-terminal).

## 1. Select the integration branch

This guide uses `codex/prompt-optimizer-integration-20261124` in
`openai-on-aws/benchmarks-openai`, under the marketplace name `openai-on-aws`.
Check existing registrations first:

```bash
codex plugin marketplace list --json
```

If `openai-on-aws` is not registered:

```bash
codex plugin marketplace add openai-on-aws/benchmarks-openai --ref codex/prompt-optimizer-integration-20261124
```

If that name already points to this repository and branch, refresh it with
`codex plugin marketplace upgrade openai-on-aws`. If it points to another branch
or local directory, review it before changing the source. An upgrade does not
switch branches. The [installation guide](../README.md#1-install-in-codex)
explains how to switch deliberately or use a local clone; both use the same
marketplace name. You can also load the clone's
`plugins/prompt-optimizer/skills/evaluate-workflows/SKILL.md` directly without
changing the registration.

## 2. Install and verify the plugin

```bash
codex plugin add prompt-optimizer@openai-on-aws
codex plugin list --marketplace openai-on-aws --json
```

Find `pluginId: "prompt-optimizer@openai-on-aws"` with `installed: true` and
`enabled: true`. This checks the plugin on this computer; a GitHub branch or an
available marketplace entry alone does not establish local installation.

Earlier review ZIPs used the separate `customer-eval-integration` marketplace.
That installed copy is not updated by these commands. Use the `openai-on-aws`
copy for this branch and verify it before removing any older installation.

## 3. Open a new session

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

## 4. Choose one starting prompt

Choose one of the following paths. For a first look, start with the recorded
dashboard; the other prompts are alternatives, not additional required steps.
For a question about your own workflow, Codex starts with one or two business
questions, then asks for your prompt, examples and policy. The bundled examples
are used only when you choose them; describing your labels as synthetic does
not select a starter or replace your own files.

**Recorded dashboard — no model or AWS calls:**

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
and what latency I can tolerate, one or two questions at a time.
Keep one fixed prompt and two model routes.
Help me inspect my inputs and expected answers, then choose local, AgentCore,
or both before freezing the study. Prepare a reviewable plan before execution.
```

**Assess extractions when you have source text but no expected answers:**

```text
Use Prompt Optimizer. I have invoice text and extraction requests, but no
expected answers. Help me decide which extraction errors matter and agree a
rubric from my output contract. Use source-based judging without inventing
labels. Ask only for the missing decisions, including a separate judge model.
Prepare a reviewable plan before making paid model calls.
```

This evaluates the supplied text stage, not raw PDF/OCR processing. Codex can
propose criteria, but you confirm what they should require. The
[source-judging guide](../skills/evaluate-workflows/references/source-judging.md)
shows the supported input formats and exact decision fields. The results are
judge assessments, not human-verified accuracy.

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

## 5. Open the results and decide what to try next

For the recorded prompt, open `recorded-demo/dashboard.html`. For a fresh or
existing study, open the dashboard path returned by Codex. Start in **Overview**
for model tradeoffs, then use **Test cases** to inspect individual answers
and **Run details** to check evidence coverage.

Each test case names its model and prompt version. Select the prompt link in
the answer details to read the full prompt in **Run details**. Failed checks
show saved field differences, missing or unexpected values, and schema errors.

A preparation prompt can stop at an input preview and a plan. Review those
before authorizing a live run; there will be no fresh results yet. Use the
[live setup guide](LIVE-SETUP.md) if the chosen backend needs model access or
AgentCore infrastructure. Reuse choices and authorization already supplied.

## Alternative: run the demo directly from a terminal

This path replaces steps 1–5 and uses a repository checkout plus Python's
standard library. It makes no model or AWS calls.

### A1. Clone this branch and choose a workspace

```bash
git clone --branch codex/prompt-optimizer-integration-20261124 https://github.com/openai-on-aws/benchmarks-openai.git
export EVAL_PLUGIN="$(pwd)/benchmarks-openai/plugins/prompt-optimizer"
mkdir -p ~/prompt-optimizer-work
cd ~/prompt-optimizer-work
```

If you already cloned this branch, set `EVAL_PLUGIN` to its absolute
`plugins/prompt-optimizer` path instead of cloning it again. No marketplace
registration is needed for this terminal-only path.

### A2. Copy the recorded evidence and render its dashboard

```bash
python3 "$EVAL_PLUGIN/scripts/workflow_eval.py" \
  demo --output recorded-demo
```

### A3. Open the dashboard

Open `~/prompt-optimizer-work/recorded-demo/dashboard.html` in a browser.
On macOS:

```bash
open ~/prompt-optimizer-work/recorded-demo/dashboard.html
```

The demo refuses to replace an existing directory. Choose a new output name
for another copy.

### A4. Refresh the dashboard when you update the renderer

After updating the checkout, rebuild from the same recorded evidence:

```bash
python3 "$EVAL_PLUGIN/scripts/workflow_eval.py" \
  dashboard --project recorded-demo \
  --output recorded-demo/dashboard.html --force
```

For a fresh study's preparation and execution commands, use the
[workflow reference](../skills/evaluate-workflows/references/workflow.md) or
[triage starter guide](../examples/support-ticket-triage/README.md).

## Python for a new study

Choose your input source and inspect the supplied prompt and examples before
setting up a study environment. Discovery does not require dependencies.

Study commands require Python 3.12. Check `python3.12 --version`; if that command
is absent, locate an existing interpreter before treating setup as blocked:

1. In Codex, use `load_workspace_dependencies` when available and take the
   Python executable path it returns. Verify its version; the bundled version
   is not assumed to be 3.12.
2. Otherwise inspect an existing workspace virtual environment or the user's
   configured uv, pyenv or mise toolchain for an installed Python 3.12.
3. Set `EVAL_BASE_PYTHON` to the verified absolute path. Do not hardcode another
   user's runtime path or substitute an older system Python.

Use the selected interpreter to create the workspace environment:

```bash
export EVAL_BASE_PYTHON=/absolute/path/to/verified/python
"$EVAL_BASE_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version; print(sys.executable)'
"$EVAL_BASE_PYTHON" -m venv "$EVAL_WORKSPACE/.venv"
export EVAL_PYTHON="$EVAL_WORKSPACE/.venv/bin/python"
"$EVAL_PYTHON" -m pip install -r "$EVAL_PLUGIN/requirements.txt"
```

`EVAL_WORKSPACE` and `EVAL_PLUGIN` are the chosen workspace and absolute plugin
directory. If no compatible interpreter is available, continue collecting the
customer's inputs and unresolved decisions, then resolve interpreter setup
before study commands. A missing PATH entry alone is not proof that Python 3.12
is unavailable. Keep the pinned dependencies unchanged.

## Optimize a prompt

In Codex, ask: “Use the evaluation skill to help improve my application prompt.
I have logs, but I’m not sure what else you need.” Answer the questions as they
arise. Source model, target model and optimizer model are separate choices.
If you have source text but no expected answers, select source-based judging
and a separate judge model. Astra can serve as the optimizer and judge when
chosen, while Sol performs extraction; those roles and reasoning settings are
explicit configuration, not defaults taken from the chat.
The [optimization walkthrough](../skills/evaluate-workflows/references/optimization.md)
shows the same entrypoint in optimize mode and the resulting prompt artifacts.
The installation steps above select this integration branch. A local clone can
also supply the same skill directly; older ZIPs retain their packaged version.
