import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("refresh_key", ROOT / "gateway/refresh_openai_credential.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ACCOUNT = "123456789012"
PREFIX = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:"
PROVIDER = {"credentialProviderArn": PREFIX + "token-vault/default/apikeycredentialprovider/key"}
TARGET = {
    "gatewayArn": PREFIX + "gateway/test",
    "targetConfiguration": {"inference": {"provider": {
        "endpoint": "https://api.openai.com",
        "operations": [
            {"path": "/v1/chat/completions", "models": [{"model": "test-model"}]},
            {"path": "/v1/responses", "models": [{"model": "test-model"}]},
        ],
    }}},
    "credentialProviderConfigurations": [{
        "credentialProviderType": "API_KEY",
        "credentialProvider": {"apiKeyCredentialProvider": {
            "providerArn": PROVIDER["credentialProviderArn"],
            "credentialLocation": "HEADER", "credentialParameterName": "Authorization",
            "credentialPrefix": "Bearer ",
        }},
    }],
}


def test_retains_models_and_binding_but_aligns_endpoint_and_api():
    original = copy.deepcopy(TARGET)
    result = module.prepare_target(TARGET, PROVIDER, ACCOUNT, "us-east-1")
    assert TARGET == original
    upstream = result["inference"]["provider"]
    assert upstream["endpoint"] == "https://us.api.openai.com"
    assert upstream["operations"] == [original["targetConfiguration"]["inference"]["provider"]["operations"][1]]


@pytest.mark.parametrize("change", ["account", "provider", "header"])
def test_rejects_unintended_destination(change):
    target = copy.deepcopy(TARGET)
    if change == "account":
        target["gatewayArn"] = target["gatewayArn"].replace(ACCOUNT, "999999999999")
    else:
        binding = target["credentialProviderConfigurations"][0]["credentialProvider"]["apiKeyCredentialProvider"]
        binding["providerArn" if change == "provider" else "credentialParameterName"] = "different"
    with pytest.raises(ValueError):
        module.prepare_target(target, PROVIDER, ACCOUNT, "us-east-1")


def test_receipts_redact_even_unexpected_provider_error_echo(tmp_path):
    key = "sk-test-" + "A" * 25
    module.write_receipt(tmp_path, "error.json", {"message": "Rejected " + key}, key)
    text = (tmp_path / "error.json").read_text()
    assert key not in text
    assert json.loads(text)["message"] == "Rejected [REDACTED]"


def test_failed_target_stops_without_repeating_updates():
    control = Mock()
    control.get_gateway_target.return_value = {"status": "FAILED", "statusReasons": ["access denied"]}
    with pytest.raises(RuntimeError, match="access denied"):
        module.wait_ready(control, "gateway", "target", sleep=lambda _: None)
    assert control.get_gateway_target.call_count == 1
    control.update_gateway_target.assert_not_called()


def test_ready_state_required():
    control = Mock()
    control.get_gateway_target.side_effect = [{"status": "UPDATING"}, {"status": "READY"}]
    assert module.wait_ready(control, "gateway", "target", sleep=lambda _: None)["status"] == "READY"


def test_noninteractive_prompt_fails_without_echo(monkeypatch):
    args = Mock(credential_source="prompt")
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    with pytest.raises(RuntimeError, match="interactive terminal"):
        module.read_key(args)
