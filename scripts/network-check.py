#!/usr/bin/env python3
"""Read public service state. Never fund, register, sign or submit work."""

import argparse
import json
import sys

from common import FAUCET_CHAIN_URL, HEALTH_URL, MODELS_URL, RPC_URL, compare_genesis, gateway_identity, request_json, reviewed_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-genesis", help="Optional check; must equal the reviewed profile")
    args = parser.parse_args()
    profile = reviewed_profile()
    expected = profile["genesis_hash"]
    if args.expected_genesis is not None and args.expected_genesis.lower() != expected:
        raise ValueError("Requested identity differs from the reviewed devnet profile")
    chain = request_json(FAUCET_CHAIN_URL)
    rpc = request_json(RPC_URL, {"jsonrpc": "2.0", "id": 1, "method": "chain_getBlockHash", "params": [0]})
    identity = compare_genesis(rpc.get("result"), chain.get("genesisHash"), expected)
    health = request_json(HEALTH_URL)
    models = request_json(MODELS_URL)
    catalog = models.get("data")
    if health.get("status") != "ok" or not isinstance(catalog, list) or not catalog:
        raise ValueError("Gateway is not healthy or no model is listed")
    gateway = gateway_identity(health, identity["genesis"])
    if not gateway["gateway_matches_observed_chain"]:
        raise ValueError("Gateway did not publish a comparable chain identity")
    print(json.dumps({"genesis": identity["genesis"], "matches_reviewed_profile": identity["matches_expected"], "profile_checked_at": profile["checked_at"], **gateway, "gateway_status": health["status"], "models": [item.get("id") for item in catalog if isinstance(item, dict)]}, indent=2))
    print("Ağ kontrolü tamam. Bu, miner kaydı veya kalibrasyon testi değildir.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Stopped: {error}. No signed action was performed.", file=sys.stderr)
        raise SystemExit(1)
