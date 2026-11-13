# Choose local execution or AgentCore

You only need AgentCore infrastructure when you choose the **AgentCore
backend**. Installing the plugin does not deploy anything.

| What you want to try | What you need | What incurs usage |
| --- | --- | --- |
| Recorded invoice dashboard | Python and the installed plugin | No new AWS or model calls |
| New local evaluation | Python environment, input data, access to two model routes and current prices | Model calls; the evaluator runs on your computer |
| New AgentCore evaluation | The same study plus a matching AgentCore runtime, execution role and private S3 bucket | Model calls, AgentCore, S3 and CloudWatch usage |

For the direct Amazon Bedrock routes in the connection template, local execution
still calls Bedrock using your AWS credentials. It does not need an AgentCore
runtime or an artifact bucket.

The setup conversation should happen **before `prepare` freezes the study**:

1. Choose local, AgentCore, or both.
2. For AgentCore, identify your AWS profile, account and Region. Reuse a matching
   deployment if one exists; otherwise choose and authorize a new deployment.
3. Put the deployment references, model routes and verified prices in the
   connection file. Agree the study size and spending policy.
4. Review the input preview, prepare the study, inspect its plan, then authorize
   the live run if you have not already done so.

Codex can guide these steps and use authorization already given. The current
`run` command expects resources to exist; it does not provision missing ones or
launch a deployment wizard. You can complete a useful draft or run locally while
the cloud setup is being arranged.

## Reuse an existing deployment

Complete the [plugin quick start](../README.md) and intake first. Confirm that
the runtime contains the **current plugin's engine**, not just that its AWS
status is `READY`. The implementation fingerprint must match.

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

Once the intake decisions and connection are complete, return to the
[workflow](../skills/evaluate-invoices/references/workflow.md) to configure,
prepare and review the plan. A frozen study cannot be edited in place: use a new
project revision if deployment references, inputs, models or engine code change.

Use absolute paths to the installed plugin and your external workspace. Reuse
the Python environment created during intake; the triage starter names it
`.venv`. Set `EVAL_PROJECT` to your existing prepared study:

```bash
export PLUGIN=/absolute/path/to/prompt-optimizer
export EVAL_WORKSPACE=/absolute/path/to/your-workspace
export EVAL_PYTHON="$EVAL_WORKSPACE/.venv/bin/python"
export EVAL_PROJECT=/absolute/path/to/your-prepared-study

"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" run \
  --project "$EVAL_PROJECT" --backend agentcore --live
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" status \
  --project "$EVAL_PROJECT" --backend agentcore
"$EVAL_PYTHON" "$PLUGIN/scripts/invoice_eval.py" results \
  --project "$EVAL_PROJECT" --backend agentcore
```

`run` checks AWS identity and runtime readiness, makes two paid route
qualification calls, then submits the frozen study. Qualification verifies that
the routes work; it does not establish application accuracy. Existing or
uncertain submission receipts are retained rather than submitted again.

The runtime hosts a coordinator and worker sessions; separate runtime
deployments per worker are not needed. S3 stores the frozen study, grading
references, checkpoints and results. Grading references are excluded from
candidate model requests. The HTML dashboard is downloaded to your workspace as
`results/decision.html`; this workflow does not create a hosted dashboard.
For local execution, use `--backend local`; the report is `local/decision.html`.
Running both backends performs two evaluations. Each records its own model
spending; the study budget is not a combined account-wide ceiling for both runs.

## Create a deployment when you need one

Deploy once and reuse it for compatible studies. You need permission in the
selected account to create and configure the IAM role, private S3 bucket and
AgentCore runtime, and to pass the execution role to AgentCore. A platform owner
can provide these resources instead.

The bundled engine supports a Linux ARM64 container
([Dockerfile](../engine/runtime/Dockerfile)) or a Python 3.12 ZIP
([packager](../engine/scripts/package_agentcore.py)). The direct deployment helper
below uses the ZIP. The runtime ZIP is separate from the Codex plugin package.

Use a Python 3.12 workspace environment with the plugin's requirements installed,
as described in the [quick start](../README.md). Run from any directory. All
dependencies, build output and deployment receipts stay **outside the plugin
cache**:

```bash
export PLUGIN=/absolute/path/to/prompt-optimizer
export EVAL_WORKSPACE=/absolute/path/to/your-workspace
export EVAL_PYTHON="$EVAL_WORKSPACE/.venv/bin/python"
export AWS_PROFILE=YOUR_AWS_PROFILE
export AWS_REGION=us-east-1
export EVAL_RUNTIME_NAME=prompt_optimizer_example
export EVAL_BUILD="$EVAL_WORKSPACE/agentcore-build-v1"
export EVAL_DEPLOY_RECEIPTS="$EVAL_WORKSPACE/agentcore-deployment-v1"

# Choose a new build directory for this runtime version.
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

# These commands create IAM, S3 and AgentCore resources in your account.
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

Both platform tags are needed: the pinned Codex binary wheel uses
`manylinux_2_17_aarch64`, while other dependencies publish the equivalent
`manylinux2014_aarch64` tag.

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
needed for investigation and cleanup. Return to intake before preparing the study.

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
