"""Public-data helpers. No keys, account creation or transaction submission."""

import json
import re
from pathlib import Path
import urllib.request

MANIFEST_URL = "https://flop.network/releases.json"
FAUCET_CHAIN_URL = "https://faucet.flop.network/chain"
RPC_URL = "https://rpc.devnet.flop.network"
HEALTH_URL = "https://api.flop.network/health"
MODELS_URL = "https://api.flop.network/v1/models"
MODEL_NAME = "Qwen/Qwen2.5-0.5B-Instruct"
REVIEWED_MODEL_HASH = "0x1a52c1abe781db051e70ae357f72bb70161ca0c092df0b618f22a9292ba63306"
REVIEWED_POLICY_HASH = "0xeca6a3f9c5a9fa1556892182ffef15b1273c075146a2d1a87814ae83ecea3053"
HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")
ALLOWED_URLS = {MANIFEST_URL, FAUCET_CHAIN_URL, RPC_URL, HEALTH_URL, MODELS_URL}


def reviewed_profile():
    profile = json.loads(Path(__file__).with_name("devnet-profile.json").read_text())
    if profile.get("network") != "devnet":
        raise ValueError("Unexpected network profile")
    require_hash(profile.get("genesis_hash"), "reviewed devnet genesis")
    return profile


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise ValueError("Redirect refused before contacting the new endpoint")


def request_json(url, payload=None):
    if url not in ALLOWED_URLS:
        raise ValueError("Only the fixed official public endpoints are supported")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data,
        headers={"Accept": "application/json", "User-Agent": "flop-devnet-rehberi/1.0", **({"Content-Type": "application/json"} if data else {})},
    )
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=15) as response:
        if response.url != url:
            raise ValueError("Endpoint redirect refused")
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Unexpectedly large response")
    return json.loads(raw)


def require_hash(value, label):
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    return value.lower()


def select_release(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be an object")
    images = {}
    for component in ("cli", "miner-worker", "miner-vllm-toploc"):
        image = manifest.get(component, {}).get("image")
        pattern = rf"ghcr\.io/flop-labs/flop-core/{re.escape(component)}:v[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{{64}}"
        if not isinstance(image, str) or not re.fullmatch(pattern, image):
            raise ValueError(f"Missing official digest-pinned image: {component}")
        images[component] = image
    models = manifest.get("models")
    if not isinstance(models, dict):
        raise ValueError("Missing model registry")
    matches = [(root, item) for root, item in models.items() if isinstance(item, dict) and item.get("name") == MODEL_NAME]
    if len(matches) != 1:
        raise ValueError("Expected exactly one documented Qwen2.5-0.5B model")
    root, model = matches[0]
    require_hash(root, "model root")
    if root != REVIEWED_MODEL_HASH:
        raise ValueError("Model root changed; review its precision and update the guide before continuing")
    policies = model.get("decode_policies")
    if not isinstance(policies, dict) or len(policies) != 1 or model.get("missing_decode_policies"):
        raise ValueError("Model policy is missing or ambiguous; review the official manifest")
    policy_hash, policy = next(iter(policies.items()))
    require_hash(policy_hash, "decode policy hash")
    if policy_hash != REVIEWED_POLICY_HASH:
        raise ValueError("Decode policy changed; review it before continuing")
    if not isinstance(policy, dict) or policy.get("class") != "TextGeneration":
        raise ValueError("Unexpected decode policy")
    encoded = json.dumps(policy, separators=(",", ":"), ensure_ascii=True)
    if any(character in encoded for character in ("'", "\n", "\r")):
        raise ValueError("Policy cannot be safely represented in the env file")
    return {"images": images, "model_hash": root, "policy_hash": policy_hash, "policy_json": encoded}


def compare_genesis(rpc_hash, faucet_hash, expected=None):
    rpc_hash = require_hash(rpc_hash, "RPC genesis")
    faucet_hash = require_hash(faucet_hash, "faucet genesis")
    if rpc_hash != faucet_hash:
        raise ValueError("RPC and faucet are on different chains")
    if expected is not None:
        expected = require_hash(expected, "expected genesis")
        if expected != rpc_hash:
            raise ValueError("Observed genesis does not match the expected value")
    return {"genesis": rpc_hash, "matches_expected": expected is not None}


def gateway_identity(health, genesis):
    network = health.get("network")
    if not isinstance(network, dict):
        return {"gateway_matches_observed_chain": False, "gateway_publishes_expected_genesis": False}
    if network.get("network") != "devnet":
        raise ValueError("Gateway does not identify the development network")
    observed = network.get("observed_genesis_hash")
    expected = network.get("expected_genesis_hash")
    if observed is not None and require_hash(observed, "gateway observed genesis") != genesis:
        raise ValueError("Gateway and RPC are on different chains")
    if expected is not None and require_hash(expected, "gateway expected genesis") != genesis:
        raise ValueError("Gateway expected identity disagrees with the RPC")
    return {"gateway_matches_observed_chain": observed is not None, "gateway_publishes_expected_genesis": expected is not None}
