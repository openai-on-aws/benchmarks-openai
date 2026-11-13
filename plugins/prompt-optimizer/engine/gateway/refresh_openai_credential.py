"""Refresh an existing Gateway API-key provider without writing the key locally.

Example:
  python gateway/refresh_openai_credential.py --profile YOUR_AWS_PROFILE \
    --region us-east-1 --expected-account 123456789012 \
    --gateway-id <existing-gateway> --target-id <existing-target> \
    --provider-name <existing-provider> --receipt-dir <nonsecret-receipts>

The default credential source is a hidden terminal prompt. For rotation, an
installed/authenticated 1Password CLI can read the stable reference in memory.
This command never creates providers, changes IAM, or changes model selection.
"""
import argparse
import copy
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config

OP_REF = "op://YOUR_VAULT/YOUR_ITEM/api-key"
ENDPOINT = "https://us.api.openai.com"


def redact(value, key=None):
    text = str(value)
    if key:
        text = text.replace(key, "[REDACTED]")
    return re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", text)


def write_receipt(directory, name, value, key=None):
    directory.mkdir(parents=True, exist_ok=True)
    text = redact(json.dumps(value, indent=2, default=str), key)
    temporary = directory / (name + ".tmp")
    temporary.write_text(text + "\n")
    temporary.replace(directory / name)


def prepare_target(target, provider, account, region):
    prefix = f"arn:aws:bedrock-agentcore:{region}:{account}:"
    if not target["gatewayArn"].startswith(prefix + "gateway/"):
        raise ValueError("Gateway account or Region does not match")
    arn = provider["credentialProviderArn"]
    if not arn.startswith(prefix + "token-vault/"):
        raise ValueError("Credential provider account or Region does not match")
    bindings = target["credentialProviderConfigurations"]
    if len(bindings) != 1 or bindings[0]["credentialProviderType"] != "API_KEY":
        raise ValueError("Expected one existing API-key binding")
    binding = bindings[0]["credentialProvider"]["apiKeyCredentialProvider"]
    if binding["providerArn"] != arn:
        raise ValueError("Target is bound to a different credential provider")
    if (binding.get("credentialLocation"), binding.get("credentialParameterName"),
            binding.get("credentialPrefix")) != ("HEADER", "Authorization", "Bearer "):
        raise ValueError("Unexpected API-key header configuration")
    configuration = copy.deepcopy(target["targetConfiguration"])
    upstream = configuration["inference"]["provider"]
    responses = [op for op in upstream["operations"] if op["path"] == "/v1/responses"]
    if len(responses) != 1 or not responses[0].get("models"):
        raise ValueError("Expected one Responses operation with explicit evaluation models")
    upstream["operations"] = responses
    upstream["endpoint"] = ENDPOINT
    return configuration


def read_key(args):
    if args.credential_source == "1password":
        binary = shutil.which("op")
        if not binary:
            raise RuntimeError("1Password CLI is not installed; use the hidden terminal prompt")
        result = subprocess.run([binary, "read", args.credential_ref],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("1Password could not read the approved credential reference")
        key = result.stdout.strip()
    elif args.credential_source == "env":
        key = os.environ.get(args.credential_env, "").strip()
    else:
        if not sys.stdin.isatty():
            raise RuntimeError("Hidden credential input requires an interactive terminal")
        key = getpass.getpass("Paste the managed api-key from 1Password (hidden), then press Return: ").strip()
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{20,4096}", key):
        raise ValueError("Input is not an OpenAI API key; no credential was updated")
    return key


def wait_ready(control, gateway_id, target_id, *, timeout=180, sleep=time.sleep):
    until = time.monotonic() + timeout
    while True:
        target = control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        status = target["status"]
        if status == "READY":
            return target
        if status in {"FAILED", "DELETING"}:
            raise RuntimeError("Gateway target " + status + ": " + "; ".join(target.get("statusReasons", [])))
        if time.monotonic() >= until:
            raise TimeoutError("Gateway update is still pending; inspect the saved receipt before retrying")
        sleep(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "region", "expected-account", "gateway-id", "target-id", "provider-name"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--credential-source", choices=("prompt", "1password", "env"), default="prompt")
    parser.add_argument("--credential-ref", default=OP_REF)
    parser.add_argument("--credential-env", default="OPENAI_AGENTCORE_API_KEY")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    key = None
    stage = "preflight"
    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        config = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 0})
        identity = session.client("sts", config=config).get_caller_identity()
        if identity["Account"] != args.expected_account:
            raise ValueError("AWS account does not match; no credential was read")
        control = session.client("bedrock-agentcore-control", config=config)
        target = control.get_gateway_target(gatewayIdentifier=args.gateway_id, targetId=args.target_id)
        provider = control.get_api_key_credential_provider(name=args.provider_name)
        configuration = prepare_target(target, provider, args.expected_account, args.region)
        print(f"Verified AWS account {args.expected_account}, Region {args.region}.", flush=True)
        if not args.verify_only:
            write_receipt(args.receipt_dir, "before.json", {
                "time": datetime.now(timezone.utc).isoformat(),
                "gateway_id": args.gateway_id, "target_id": args.target_id,
                "provider_arn": provider["credentialProviderArn"],
                "credential_source": args.credential_source, "reference": args.credential_ref,
                "old_target_configuration": target["targetConfiguration"],
                "new_target_configuration": configuration,
            })
            print(f"Destination: existing provider {args.provider_name}; upstream {ENDPOINT}.", flush=True)
            stage = "credential_input"
            key = read_key(args)
            stage = "credential_update"
            updated = control.update_api_key_credential_provider(name=args.provider_name, apiKey=key)
            write_receipt(args.receipt_dir, "credential-updated.json", {
                "time": datetime.now(timezone.utc).isoformat(),
                "provider_arn": updated["credentialProviderArn"],
                "secret_arn": updated.get("apiKeySecretArn"),
            }, key)
            # An unexpected new secret needs its IAM scope reviewed; never widen it here.
            if updated.get("apiKeySecretArn") != provider.get("apiKeySecretArn"):
                raise RuntimeError("Credential updated but backing secret changed; verify scoped Gateway IAM before continuing")
            stage = "target_update"
            control.update_gateway_target(
                gatewayIdentifier=args.gateway_id, targetId=args.target_id,
                name=target["name"], targetConfiguration=configuration,
                credentialProviderConfigurations=target["credentialProviderConfigurations"],
            )
        stage = "readiness"
        ready = wait_ready(control, args.gateway_id, args.target_id)
        if ready["targetConfiguration"] != configuration:
            raise RuntimeError("Ready target does not match the approved US Responses configuration")
        write_receipt(args.receipt_dir, "ready.json", {
            "time": datetime.now(timezone.utc).isoformat(), "status": "READY",
            "gateway_id": args.gateway_id, "target_id": args.target_id,
            "target_configuration": ready["targetConfiguration"],
            "provider_arn": provider["credentialProviderArn"],
            "inference_tested": False,
        }, key)
        print("Credential installed. Gateway target is READY on the US Responses endpoint.", flush=True)
        print("The key was not printed or written to a local file. Live inference still needs qualification.", flush=True)
        return 0
    except Exception as exc:
        message = redact(exc, key)
        write_receipt(args.receipt_dir, "error.json", {
            "time": datetime.now(timezone.utc).isoformat(), "stage": stage,
            "type": type(exc).__name__, "message": message,
        }, key)
        print(f"Stopped at {stage}: {message}", file=sys.stderr, flush=True)
        return 1
    finally:
        key = None


if __name__ == "__main__":
    raise SystemExit(main())
