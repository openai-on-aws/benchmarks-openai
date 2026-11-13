# Choose local execution or AgentCore

You only need AgentCore infrastructure when you choose the **AgentCore
backend**. Installing the plugin does not deploy anything. Follow steps
**1–6**; step 3 is only for AgentCore. For the recorded demo, use the
[quickstart](QUICKSTART.md) instead.

## 1. Choose local, AgentCore, or both

| What you want to try | What you need | What incurs usage |
| --- | --- | --- |
| Recorded invoice dashboard | Python and the installed plugin | No new AWS or model calls |
| New local evaluation | Python environment, input data, access to two model routes and current prices | Model calls; the evaluator runs on your computer |
| New AgentCore evaluation | The same study plus a matching AgentCore runtime, execution role and private S3 bucket | Model calls, AgentCore, S3 and CloudWatch usage |

For the direct Amazon Bedrock routes in the connection template, local execution
still calls Bedrock using your AWS credentials. It does not need an AgentCore
runtime or an artifact bucket.

Choose the backend **before `prepare` freezes the study**. For AgentCore,
identify the AWS profile, account and Region, then choose a matching existing
deployment or authorize a new one. Agree the model routes, study size and
spending policy before execution.

Codex can guide these steps and use authorization already given. The current
`run` command expects resources to exist; it does not provision missing ones or
launch a deployment wizard. You can complete a useful draft or run locally while
the cloud setup is being arranged.

## 2. Set up the workspace and model connection

Complete the [plugin quickstart](QUICKSTART.md) and intake first. For a fresh
study, use the Python 3.12 environment created during intake with the plugin's
requirements installed; the triage starter names it `.venv`. Use absolute
paths outside the installed plugin cache:

```bash
export PLUGIN=/absolute/path/to/prompt-optimizer
export EVAL_WORKSPACE=/absolute/path/to/your-workspace
export EVAL_PYTHON="$EVAL_WORKSPACE/.venv/bin/python"
```

Copy [connection.template.json](../connection.template.json) into your workspace
and fill in your own references:

- The two model routes, endpoints, Region, AWS profile references and current
  input/output/cache prices, their source and verification date, and integer
  `pricing_max_input_tokens` context bound. Null values are deliberately
  unresolved.
- For AgentCore: `cloud.profile`, `cloud.account`, `cloud.region`,
  `cloud.runtime_arn`, `cloud.bucket` and the configured artifact `prefix`.
- For a local-only study, `cloud.runtime_arn` can be null. Complete the cloud
  fields before preparing a study intended for both backends.

The `source_model` and `target_model` decisions select entries in the connection's
`models` object. These fixed-prompt examples do **not** need an optimizer model.
Keep credentials outside the connection file; it contains references only.

## 3. Resolve AgentCore infrastructure, if selected

**Local only:** skip this step and continue to step 4. **AgentCore or both:**
choose **3A or 3B**, then continue to step 4. Do not create a second deployment
when a compatible one is already available.

### 3A. Reuse an existing deployment

Confirm that the runtime contains the **current plugin's engine**, not just
that its AWS status is `READY`. The implementation fingerprint must match.

Record its runtime ARN and private bucket in the connection from step 2.
Then continue to step 4.

### 3B. Create a deployment when you need one

Use this path only if step 3A did not identify a matching deployment.
Deploy once and reuse it for compatible studies. You need permission in the
selected account to create and configure the IAM role, private S3 bucket and
AgentCore runtime, and to pass the execution role to AgentCore. A platform owner
can provide these resources instead.

The bundled engine supports a Linux ARM64 container
([Dockerfile](../engine/runtime/Dockerfile)) or a Python 3.12 ZIP
([packager](../engine/scripts/package_agentcore.py)). The direct deployment helper
below uses the ZIP. The runtime ZIP is separate from the Codex plugin package.

#### 3B.1. Review the deployment defaults and lifecycle

Review these shipped deployment defaults before adopting them:

- The [deployment helper](../engine/scripts/deploy_agentcore.py) scopes model
  permissions to the two Sol model families in the template, plus access to its
  own workers, artifact prefixes and CloudWatch logs. Other model routes require
  an appropriate policy change.
- It blocks public S3 access, enables bucket versioning and encryption, and uses
  the runtime's execution role for AWS calls.
- It allows direct Bedrock endpoints in the selected Region, concurrency 2,
  12 requests per trial and a 300-second trial ceiling. It sets a $100 ceiling
  for capped campaigns but also permits explicitly configured uncapped campaigns
  (`EVAL_ALLOW_UNLIMITED_BUDGET=1`). These are inherited defaults, not a suggested
  budget. Choose deployment limits and study spending policy deliberately.
- There is no production job queue or automatic failover. The coordinator
  checkpoints before its environment lifetime ends; recovery uses saved
  receipts/checkpoints.
- Runtime, IAM role, S3 objects and bucket cleanup is manual. S3 versioning means
  deleting the visible objects alone does not empty the bucket. CloudWatch logs
  are separate retained resources.

The helper's `create` action does not replace an already recorded runtime.
`configure-worker` reuses the original artifact; it does not deploy new engine
code. After an engine upgrade, build and deploy a new compatible version through
your platform's update process, or use a new runtime name and receipt directory.
Prepare a new study revision against that deployment.

Plugin tests cover local installation and offline behavior. Building the ARM64
package, provisioning resources and live qualification must also be validated
in the recipient's environment.

#### 3B.2. Set the deployment scope and output paths

Use the Python 3.12 workspace environment from step 2. All dependencies, build
output and deployment receipts stay **outside the plugin cache**:

```bash
export AWS_PROFILE=YOUR_AWS_PROFILE
export AWS_REGION=us-east-1
export EVAL_RUNTIME_NAME=prompt_optimizer_example
export EVAL_BUILD="$EVAL_WORKSPACE/agentcore-build-v1"
export EVAL_DEPLOY_RECEIPTS="$EVAL_WORKSPACE/agentcore-deployment-v1"
```

#### 3B.3. Build the Linux ARM64 runtime ZIP

Choose a new build directory for this runtime version.

```bash
mkdir "$EVAL_BUILD"

# Download and install dependencies for Linux ARM64/Python 3.12.
"$EVAL_PYTHON" -m pip install \
  --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 \
  --implementation cp --python-version 3.12 --only-binary=:all: \
  --target "$EVAL_BUILD/linux-arm64" \
  -r "$PLUGIN/engine/requirements-integration.txt"
"$EVAL_PYTHON" "$PLUGIN/engine/scripts/package_agentcore.py" \
  --package "$EVAL_BUILD/linux-arm64" \
  --output "$EVAL_BUILD/prompt-optimizer-runtime.zip"
```

Both platform tags are needed: the pinned Codex binary wheel uses
`manylinux_2_17_aarch64`, while other dependencies publish the equivalent
`manylinux2014_aarch64` tag.

#### 3B.4. Create resources and wait for readiness

These commands create IAM, S3 and AgentCore resources in the chosen account.
Use the deployment authorization already agreed before running them.

```bash
"$EVAL_PYTHON" "$PLUGIN/engine/scripts/deploy_agentcore.py" bootstrap \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" --name "$EVAL_RUNTIME_NAME" \
  --receipt-dir "$EVAL_DEPLOY_RECEIPTS"
"$EVAL_PYTHON" "$PLUGIN/engine/scripts/deploy_agentcore.py" create \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" --name "$EVAL_RUNTIME_NAME" \
  --receipt-dir "$EVAL_DEPLOY_RECEIPTS" \
  --artifact "$EVAL_BUILD/prompt-optimizer-runtime.zip"
"$EVAL_PYTHON" "$PLUGIN/engine/scripts/deploy_agentcore.py" status \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" --name "$EVAL_RUNTIME_NAME" \
  --receipt-dir "$EVAL_DEPLOY_RECEIPTS"
```

#### 3B.5. Configure worker invocation and record the deployment

After `status` reports `READY`, set the runtime's own worker ARN:

```bash
"$EVAL_PYTHON" "$PLUGIN/engine/scripts/deploy_agentcore.py" configure-worker \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" --name "$EVAL_RUNTIME_NAME" \
  --receipt-dir "$EVAL_DEPLOY_RECEIPTS"
"$EVAL_PYTHON" "$PLUGIN/engine/scripts/deploy_agentcore.py" status \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" --name "$EVAL_RUNTIME_NAME" \
  --receipt-dir "$EVAL_DEPLOY_RECEIPTS"
```

Wait for `READY` again, then copy `created.runtime_arn` and `created.bucket`
from `$EVAL_DEPLOY_RECEIPTS/deployment.json` into the connection file. Keep that
receipt directory: it records the deployment identity, requests and resources
needed for investigation and cleanup. Continue to step 4 before preparing the study.

## 4. Freeze the study and review the plan

Once the intake decisions and connection are complete, return to the
[workflow](../skills/evaluate-invoices/references/workflow.md) to configure,
prepare and review the plan. A frozen study cannot be edited in place: use a new
project revision if deployment references, inputs, models or engine code change.

## 5. Run the reviewed study

Set `EVAL_PROJECT` to the study prepared in step 4. Use the workspace and Python
environment from step 2:

```bash
export EVAL_PROJECT=/absolute/path/to/your-prepared-study

"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" run \
  --project "$EVAL_PROJECT" --backend agentcore --live
```

For a local run, use the same command with `--backend local`. To compare both,
run each backend against the same prepared study. These are paid model calls;
reuse authorization already supplied or resolve it before execution.

For AgentCore, `run` checks AWS identity and runtime readiness, makes two paid
route qualification calls, then submits the frozen study. Qualification verifies
that the routes work; it does not establish application accuracy. Existing or
uncertain submission receipts are retained rather than submitted again.

The runtime hosts a coordinator and worker sessions; separate runtime
deployments per worker are not needed. S3 stores the frozen study, grading
references, checkpoints and results. Grading references are excluded from
candidate model requests. Running both backends performs two evaluations. Each
records its own model spending; the study budget is not a combined account-wide
ceiling for both runs.

## 6. Retrieve the evidence and render the dashboard

Inspect the existing cloud job and download its results without submitting
another evaluation:

```bash
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" status \
  --project "$EVAL_PROJECT" --backend agentcore
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" results \
  --project "$EVAL_PROJECT" --backend agentcore
```

For local evidence, replace `--backend agentcore` with `--backend local`.
The backend reports are `results/decision.html` for AgentCore and
`local/decision.html` for local runs.
Build the shared interactive dashboard after the reports are available:

```bash
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" dashboard \
  --project "$EVAL_PROJECT" --output "$EVAL_PROJECT/dashboard.html"
```

Open the returned `dashboard.html` in your browser. Compatible local and
AgentCore reports appear together. Rendering makes no model or AWS calls and
does not create a hosted dashboard. To refresh an existing dashboard after
retrieving updated results, rebuild with `--force`.

## Optional: OpenAI through Gateway

The initial examples use direct Bedrock routes. Gateway is an additional setup
choice, not a prerequisite for these examples.

The bundled [Gateway setup helper](../engine/gateway/setup_gateway.py),
[OpenAI target](../engine/gateway/target-openai.json) and
[credential updater](../engine/gateway/refresh_openai_credential.py) support
provider routing. Credentials are supplied server-side; never put them in a
connection file or source archive. Network-restricted employee credentials
cannot be assumed to work from AWS.

The credential updater requires an existing target/provider binding and checks
its account and Region before reading a secret. It uses a hidden Terminal prompt
by default; the optional 1Password source requires the official CLI. It pins the
target to the US OpenAI Responses endpoint and does not create a provider, widen
IAM permissions or synchronize rotations automatically.

For Gateway inference, use SigV4 service `bedrock-agentcore`, the real Gateway
endpoint and the discovered target-qualified model ID. The runtime also needs
the Gateway hostname in its allowed endpoints and scoped invocation permission;
the Gateway role needs access to the API-key provider/backing secret. These are
additional changes beyond the direct-Bedrock deployment helper. Verify actual
inference for both model routes; discovery or a `READY` target is not an
inference test.

## Retry and resume

Inspect the existing job before retrying. Use the bundled
[resume helper](../engine/scripts/resume_agentcore.py) and
[trial recovery helper](../engine/scripts/recover_agentcore_trial.py) with the
saved receipts. Check whether the original coordinator is still running; never
run two coordinators against one checkpoint or submit a new job solely because
a local request timed out.
