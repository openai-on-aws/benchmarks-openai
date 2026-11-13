# Run with your own AgentCore resources

This guide uses the bundled engine. Installing this plugin does not deploy infrastructure or run models. The commands below become live actions when you run them. Use your own approved account, routes, data and study settings; the author's past authorization does not apply to this copy.

## Existing runtime

1. Complete the plugin quick start and invoice intake in ../README.md.
2. Confirm your AWS identity, Region, deployed runtime version, artifact bucket and access to the chosen model routes. Use the current package's runtime code; an old deployment may have different validation, grading or selection behavior.
3. Copy `../connection.template.json` to a local file outside source control. Have the platform owner fill in the real runtime ARN, account, profile, bucket, endpoints, model IDs and current prices. The template's null prices deliberately prevent treating old prices as current. It contains credential references only.
4. Give that connection file to the intake skill. The decisions `source_model`, `target_model` and `optimizer_model` choose independent entries in `models`. Review prepared request previews, labels, grading, budget and study size before a paid run. Do not patch a frozen prepared project; create a new project/revision for changed inputs.
5. From the plugin's `engine/` directory, with the workspace virtual environment:

```bash
"$EVAL_PYTHON" scripts/customer_eval.py run --project /absolute/path/to/your-project --live
"$EVAL_PYTHON" scripts/customer_eval.py status --project /absolute/path/to/your-project
"$EVAL_PYTHON" scripts/customer_eval.py results --project /absolute/path/to/your-project
```

`run` verifies AWS identity and runtime readiness, qualifies the source and target, then submits the saved configuration. Qualification and the full campaign incur usage. A distinct optimizer route still needs its own platform qualification: the built-in qualification script checks the source and target only. Existing/uncertain submission receipts are retained; do not start a duplicate job.

`results` writes `results/decision.html`, the before/after and candidate prompts, `prompt.diff`, model comparisons, grade failures, usage/cost and timing. Existing synthetic labels remain synthetic/unreviewed in the report. A quality score is only as complete as its checks.

## New runtime

The source includes two packaging options: `runtime/Dockerfile` for a Linux ARM64 image, and `scripts/package_agentcore.py` for a Python 3.12 ZIP. The ZIP packager requires a directory of already installed Linux ARM64 dependencies and checks the Codex executable architecture. It is not the same ZIP as the installed plugin package.

For the existing ZIP deployment path, from the plugin's `engine/` directory:

```bash
export EVAL_PYTHON=/absolute/workspace/.invoice-venv/bin/python
export AWS_PROFILE=YOUR_AWS_PROFILE
export AWS_REGION=us-east-1
export EVAL_RUNTIME_NAME=customer_eval_review
export EVAL_DEPLOY_RECEIPTS="$PWD/deployment-receipts"

# Install deployment dependencies for Linux ARM64/Python 3.12 into a NEW directory.
"$EVAL_PYTHON" -m pip install \
  --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 \
  --implementation cp --python-version 3.12 --only-binary=:all: \
  --target build/linux-arm64 -r requirements-integration.txt
"$EVAL_PYTHON" scripts/package_agentcore.py \
  --package build/linux-arm64 --output build/customer-eval-runtime.zip

# These next commands create IAM, S3 and AgentCore resources in your account.
"$EVAL_PYTHON" scripts/deploy_agentcore.py bootstrap \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" --name "$EVAL_RUNTIME_NAME" \
  --receipt-dir "$EVAL_DEPLOY_RECEIPTS"
"$EVAL_PYTHON" scripts/deploy_agentcore.py create \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" --name "$EVAL_RUNTIME_NAME" \
  --receipt-dir "$EVAL_DEPLOY_RECEIPTS" --artifact build/customer-eval-runtime.zip
"$EVAL_PYTHON" scripts/deploy_agentcore.py status \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" --name "$EVAL_RUNTIME_NAME" \
  --receipt-dir "$EVAL_DEPLOY_RECEIPTS"
```

Both Linux platform tags are needed: the pinned Codex binary wheel uses
`manylinux_2_17_aarch64`, while other dependencies publish the equivalent
`manylinux2014_aarch64` tag. Using only the latter fails dependency resolution.

Wait for READY before running `configure-worker` with the same profile, Region, name and receipt directory, then check `status` again. This sets the runtime's own worker ARN. Copy the created runtime ARN and bucket from `deployment-receipts/deployment.json` into your connection. Keep this receipt directory private; it records deployment identities needed for updates and cleanup.

Platform review is required before adopting these deployment defaults:

- `scripts/deploy_agentcore.py` starts with scoped permissions for the two Sol model families in the example. Change the policy for other models or routes, including the optimizer, before deploying them.
- The runtime initially allows direct Bedrock endpoints, concurrency 2, 12 requests per trial and a 300-second trial ceiling. It also permits explicitly configured uncapped campaigns (`EVAL_ALLOW_UNLIMITED_BUDGET=1`); set your approved deployment and study ceilings before a live run. These are inherited implementation defaults, not a suggested budget.
- A background cloud coordinator checkpoints before its environment lifetime ends. There is no production job queue or automatic failover. Recovery uses saved receipts/checkpoints; never run two coordinators against one checkpoint.
- Resource cleanup is manual for the runtime, IAM role and S3 objects/bucket. The Gateway helper has its own teardown command, which is separate from runtime cleanup.

The ARM64 package build, IAM/resource creation and live qualification must be validated in the recipient's environment. Plugin tests cover local installation and offline behavior, not deployment in another AWS account. A deployed runtime must match the bundled engine's implementation fingerprint.

## OpenAI through Gateway

`gateway/setup_gateway.py` contains the provider-routing setup, `gateway/target-openai.json` the OpenAI target, and `gateway/refresh_openai_credential.py` the secure credential-update helper. Credentials are supplied server-side; never put them in the connection file or source archive. Employee credentials restricted to a corporate network do not work from AWS. Use the managed credential process approved for your organization.

The credential updater is for an EXISTING target/provider binding. It checks account, Region and binding before reading a secret. Its default source is a hidden Terminal prompt; `--credential-source 1password --credential-ref op://YOUR_VAULT/YOUR_ITEM/api-key` is optional when the official CLI is configured. It pins that target to the US OpenAI Responses endpoint. Confirm that endpoint matches the approved credential and data policy before use. It does not create a provider, widen IAM or synchronize rotations automatically.

For Gateway inference, configure SigV4 service `bedrock-agentcore`, the actual Gateway endpoint, and the target-qualified model ID returned by discovery. Direct Bedrock Responses uses service `bedrock`. The runtime needs the Gateway hostname in its allowed endpoint list and scoped Gateway invocation permission; the Gateway role needs scoped access to the API-key provider/backing secret. These are additional steps beyond the direct-Bedrock deploy helper.

After provisioning, verify inference, tool calls and continuation with each application model, and verify the optimizer's actual request settings separately. A READY target or successful model discovery alone is not an inference test. Approved model access and pricing are account-specific.

## Retry and resume

Use `scripts/resume_agentcore.py --help`, `scripts/recover_agentcore_trial.py --help` and the saved receipts to recover a paused job. Check whether the original coordinator is still running first. Do not retry potentially billed work with a new job ID just because a local request timed out.
