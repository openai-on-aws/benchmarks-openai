"""Provision the single AgentCore Gateway with two inference provider targets, via boto3.

One gateway, two inference targets:
  - `openai`  -> OpenAI first-party (SOURCE model, GPT-4.1), API key from an
                 AgentCore API-key credential provider.
  - `bedrock` -> Amazon Bedrock     (TARGET model, newest Sonnet), gateway IAM role.

The agent (and the notebook) then talk to ONE endpoint and select the backend purely
by the request `model` field — model-based routing, the single-seam design.

Why boto3 and not the AgentCore CLI: the GA CLI's `add gateway-target` provisions
MCP/HTTP/Runtime targets only, not inference targets. The control-plane API
(`bedrock-agentcore-control`) does expose inference targets; the tested boto3
1.43.116 service model includes them. The CLI is still used elsewhere (Runtime deploy, custom
evaluators, `eval ondemand simulate`).

Usage:
    python gateway/setup_gateway.py create     # role + credential provider + gateway + 2 targets
    python gateway/setup_gateway.py endpoint    # print the resolved /inference/v1 base_url
    python gateway/setup_gateway.py status      # show gateway + target status
    python gateway/setup_gateway.py teardown    # delete everything this script created

Everything created is recorded in gateway/.teardown.json so `teardown` removes
exactly what `create` made, in reverse order, and nothing leaks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

GATEWAY_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("EVAL_GATEWAY_STATE_DIR", GATEWAY_DIR))
RESOLVED_PATH = STATE_DIR / ".resolved.json"
TEARDOWN_PATH = STATE_DIR / ".teardown.json"
OPENAI_TARGET = GATEWAY_DIR / "target-openai.json"
BEDROCK_TARGET = GATEWAY_DIR / "target-bedrock.json"

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
GATEWAY_NAME = os.environ.get("EVAL_GATEWAY_NAME", "model-upgrade-gw")
OPENAI_KEY_NAME = os.environ.get("EVAL_OPENAI_KEY_NAME", "model-upgrade-openai-key")
ROLE_NAME = os.environ.get("EVAL_GATEWAY_ROLE_NAME", "model-upgrade-gateway-role")

acc = boto3.client("bedrock-agentcore-control", region_name=REGION)
iam = boto3.client("iam")


# --- teardown manifest ------------------------------------------------------
def _manifest() -> dict:
    return json.loads(TEARDOWN_PATH.read_text()) if TEARDOWN_PATH.exists() else {}


def _record(**kw) -> None:
    TEARDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    m = _manifest()
    m.update(kw)
    TEARDOWN_PATH.write_text(json.dumps(m, indent=2))


def _save_resolved(**kw) -> None:
    RESOLVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    cur = json.loads(RESOLVED_PATH.read_text()) if RESOLVED_PATH.exists() else {}
    cur.update(kw)
    RESOLVED_PATH.write_text(json.dumps(cur, indent=2))


def inference_base_url(gateway_url: str) -> str:
    # gatewayUrl is the MCP/base URL; inference is served under /inference/v1.
    return gateway_url.rstrip("/").removesuffix('/mcp') + "/inference/v1"


# --- IAM role ---------------------------------------------------------------
GATEWAY_TRUST = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}
GATEWAY_INLINE_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            # The bedrock-runtime CONNECTOR uses the native Amazon Bedrock
            # InvokeModel API (the deprecation-safe path; we do NOT use the older
            # bedrock-mantle endpoint). It needs InvokeModel + model discovery.
            # Scoped to * for this workshop role; scope to specific model/inference
            # -profile ARNs in production.
            "Sid": "BedrockInvoke",
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:Converse",
                "bedrock:ConverseStream",
                # Read-only discovery: the connector enumerates models/profiles at
                # target activation (ListFoundationModels, ListInferenceProfiles,
                # ListProvisionedModelThroughputs, ...). List*/Get* covers them all
                # without granting any write/management action.
                "bedrock:List*",
                "bedrock:Get*",
            ],
            "Resource": "*",
        },
    ],
}


def _ensure_role() -> str:
    try:
        r = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(GATEWAY_TRUST),
            Description="Model Upgrade module: AgentCore Gateway inference role (Bedrock target).",
        )
        _record(role_name=ROLE_NAME)  # Preserve cleanup identity even if policy creation fails.
        iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="bedrock-invoke",
                            PolicyDocument=json.dumps(GATEWAY_INLINE_POLICY))
        _record(role_name=ROLE_NAME)
        print(f"  created IAM role {ROLE_NAME}")
        time.sleep(10)  # let the role propagate before the gateway assumes it
        return r["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"  reusing existing IAM role {ROLE_NAME}")
            return iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        raise


def _load_target(path: Path) -> dict:
    data = json.loads(path.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def api_key_policy(provider_arn: str, secret_arn: str, workload_arn: str) -> dict:
    """Grant this Gateway access to one provider and its backing secret."""
    parts = provider_arn.split(":", 5)
    if len(parts) != 6 or parts[2] != "bedrock-agentcore" or "/apikeycredentialprovider/" not in parts[5]:
        raise ValueError("Expected an AgentCore API-key provider ARN")
    prefix = ":".join(parts[:5])
    secret_prefix = f"arn:{parts[1]}:secretsmanager:{parts[3]}:{parts[4]}:secret:"
    if not secret_arn.startswith(secret_prefix):
        raise ValueError("Credential provider and secret must share an account and Region")
    identity_directory = prefix + ":workload-identity-directory/default"
    if not workload_arn.startswith(identity_directory + "/workload-identity/"):
        raise ValueError("Gateway workload identity must share the provider account and Region")
    if any(character in arn for arn in (provider_arn, secret_arn, workload_arn) for character in ('*', '?')):
        raise ValueError("Credential access requires exact resource ARNs")
    # GetResourceApiKey authorizes both the identity and credential resources.
    # https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/scope-credential-provider-access.html
    identity_resources = [identity_directory, workload_arn]
    vault_arn = provider_arn.split('/apikeycredentialprovider/', 1)[0]
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "GatewayIdentity", "Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken"],
         "Resource": identity_resources},
        {"Sid": "SelectedApiKey", "Effect": "Allow", "Action": ["bedrock-agentcore:GetResourceApiKey"],
         "Resource": identity_resources + [vault_arn, provider_arn]},
        {"Sid": "SelectedSecret", "Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
         "Resource": [secret_arn]},
    ]}


def _grant_api_key_access(provider: dict) -> None:
    if _manifest().get("role_name") != ROLE_NAME:
        raise ValueError("Refusing to change an unowned Gateway role; use a dedicated role name")
    gateway = acc.get_gateway(gatewayIdentifier=_manifest()["gateway_id"])
    workload_arn = gateway["workloadIdentityDetails"]["workloadIdentityArn"]
    policy = api_key_policy(provider["credentialProviderArn"], provider["apiKeySecretArn"]["secretArn"], workload_arn)
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="openai-credentials", PolicyDocument=json.dumps(policy))
    _record(credential_policy_name="openai-credentials", credential_provider_arn=provider["credentialProviderArn"])


def _wait_ready(gateway_id: str, timeout: int = 180) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        g = acc.get_gateway(gatewayIdentifier=gateway_id)
        status = g["status"]
        if status == "READY":
            return status
        if status in ("FAILED", "UPDATE_UNSUCCESSFUL"):
            raise RuntimeError(f"gateway {gateway_id} status {status}: {g.get('statusReasons')}")
        time.sleep(5)
    raise TimeoutError(f"gateway {gateway_id} not READY within {timeout}s")


def create(with_openai: bool = False) -> None:
    """Provision the single Gateway. Default: Bedrock inference target only (no API
    key needed — runnable with just Bedrock access). Pass with_openai=True to also
    add the OpenAI first-party example target (requires OPENAI_API_KEY). The Azure
    example target is provisioned the same way from target-azure-openai.json; wire
    it in by analogy when you need it.
    """
    if _manifest():
        raise RuntimeError('Existing resources are recorded; inspect status/teardown before creating another Gateway')
    if with_openai and not os.environ.get('OPENAI_API_KEY'):
        raise ValueError('--with-openai requires OPENAI_API_KEY before provisioning starts')
    print("1/4 IAM role ...")
    role_arn = _ensure_role()

    print("2/4 gateway ...")
    g = acc.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        authorizerType="AWS_IAM",
        protocolType="MCP",
        description="Model Upgrade module: single inference seam (Bedrock default; OpenAI/Azure optional).",
    )
    gateway_id = g["gatewayId"]
    _record(gateway_id=gateway_id)
    print(f"  created gateway {gateway_id}, waiting for READY ...")
    _wait_ready(gateway_id)

    print("3/4 Bedrock inference target (default live path) ...")
    bedrock_cfg = _load_target(BEDROCK_TARGET)
    t2 = acc.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=bedrock_cfg["name"],
        targetConfiguration=bedrock_cfg["targetConfiguration"],
        credentialProviderConfigurations=bedrock_cfg["credentialProviderConfigurations"],
    )
    _record(bedrock_target_id=t2["targetId"])
    _wait_target_ready(gateway_id, t2['targetId'])
    print(f"  created target {t2['targetId']}")

    if with_openai:
        openai_key = os.environ.get("OPENAI_API_KEY")
        if not openai_key:
            sys.exit("--with-openai needs OPENAI_API_KEY set (stored in the credential provider).")
        print("4/4 OpenAI first-party inference target (example) ...")
        try:
            cp = acc.create_api_key_credential_provider(name=OPENAI_KEY_NAME, apiKey=openai_key)
            _record(credential_provider_name=OPENAI_KEY_NAME)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ConflictException", "ValidationException") and "exist" in str(e).lower():
                cp = acc.get_api_key_credential_provider(name=OPENAI_KEY_NAME)
            else:
                raise
        _grant_api_key_access(cp)
        cp_arn = cp["credentialProviderArn"]
        openai_cfg = _load_target(OPENAI_TARGET)
        openai_cfg["credentialProviderConfigurations"][0]["credentialProvider"][
            "apiKeyCredentialProvider"]["providerArn"] = cp_arn
        t1 = acc.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=openai_cfg["name"],
            targetConfiguration=openai_cfg["targetConfiguration"],
            credentialProviderConfigurations=openai_cfg["credentialProviderConfigurations"],
        )
        _record(openai_target_id=t1["targetId"])
        _wait_target_ready(gateway_id, t1['targetId'])
        print(f"  created target {t1['targetId']}")
    else:
        print("4/4 skipping OpenAI/Azure example targets (Bedrock-only). Use --with-openai to add OpenAI.")

    base_url = inference_base_url(g["gatewayUrl"])
    _save_resolved(gateway_id=gateway_id, gateway_url=g["gatewayUrl"],
                   inference_base_url=base_url, region=REGION)
    print(f"\nGateway ready. Inference base_url:\n  {base_url}")


def _wait_target_ready(gateway_id: str, target_id: str, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        target = acc.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        if target['status'] == 'READY': return
        if target['status'] in ('FAILED', 'UPDATE_UNSUCCESSFUL'):
            raise RuntimeError(f'Target {target_id} failed to become ready')
        time.sleep(5)
    raise TimeoutError(f'Target {target_id} did not become READY')


def status() -> None:
    m = _manifest()
    gid = m.get("gateway_id")
    if not gid:
        sys.exit("No gateway recorded. Run `create` first.")
    g = acc.get_gateway(gatewayIdentifier=gid)
    print("gateway:", gid, g["status"])
    for key in ("openai_target_id", "bedrock_target_id"):
        tid = m.get(key)
        if tid:
            t = acc.get_gateway_target(gatewayIdentifier=gid, targetId=tid)
            print(f"  {key}: {tid} {t['status']}")


def endpoint() -> None:
    if not RESOLVED_PATH.exists():
        sys.exit("No resolved gateway yet. Run `create` first.")
    print(json.loads(RESOLVED_PATH.read_text())["inference_base_url"])


def teardown() -> None:
    """Remove recorded resources, preserving the manifest until deletion is confirmed."""
    m = _manifest()
    if not m:
        print("Nothing recorded to tear down.")
        return

    def remove(label, delete, get=None):
        missing = {"ResourceNotFoundException", "NoSuchEntity"}
        try:
            try:
                delete()
            except ClientError as exc:
                if exc.response["Error"]["Code"] in missing: return True
                raise
            if get is not None:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    try:
                        get()
                    except ClientError as exc:
                        if exc.response["Error"]["Code"] in missing: return True
                        raise
                    time.sleep(2)
                raise TimeoutError("Deletion not yet confirmed")
            return True
        except (ClientError, TimeoutError) as exc:
            print(f"{label}: cleanup incomplete ({type(exc).__name__}); manifest retained")
            return False

    gid = m.get("gateway_id")
    for key in ("openai_target_id", "bedrock_target_id"):
        tid = m.get(key)
        if tid and gid and not remove(key,
            lambda: acc.delete_gateway_target(gatewayIdentifier=gid, targetId=tid),
            lambda: acc.get_gateway_target(gatewayIdentifier=gid, targetId=tid)):
            return
    if gid and not remove("gateway",
        lambda: acc.delete_gateway(gatewayIdentifier=gid),
        lambda: acc.get_gateway(gatewayIdentifier=gid)):
        return
    if m.get("credential_provider_name") and not remove("credential provider",
        lambda: acc.delete_api_key_credential_provider(name=m["credential_provider_name"])):
        return
    if m.get("role_name"):
        if m.get("credential_policy_name") and not remove("owned credential policy",
            lambda: iam.delete_role_policy(RoleName=m["role_name"], PolicyName=m["credential_policy_name"])):
            return
        if not remove("owned inline policy",
            lambda: iam.delete_role_policy(RoleName=m["role_name"], PolicyName="bedrock-invoke")):
            return
        if not remove("owned role", lambda: iam.delete_role(RoleName=m["role_name"])):
            return
    for path in (TEARDOWN_PATH, RESOLVED_PATH):
        path.unlink(missing_ok=True)
    print("teardown complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "endpoint", "status", "teardown"])
    parser.add_argument("--with-openai", action="store_true",
                        help="also add the OpenAI first-party example target (needs OPENAI_API_KEY)")
    args = parser.parse_args()
    if args.action == "create":
        create(with_openai=args.with_openai)
    else:
        {"endpoint": endpoint, "status": status, "teardown": teardown}[args.action]()


if __name__ == "__main__":
    main()
