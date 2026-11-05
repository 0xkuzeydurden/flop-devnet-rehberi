#!/usr/bin/env python3
"""Inspect the official release; optionally create a fresh private .env."""

import argparse
import json
import os
from pathlib import Path
import secrets
import sys

from common import MANIFEST_URL, request_json, select_release


def write_private_env(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)


def render_env(release):
    images = release["images"]
    return "\n".join([
        "# Official manifest selection; recheck network identity before signing.",
        f"FLOP_CLI_IMAGE={images['cli']}",
        f"FLOP_WORKER_IMAGE={images['miner-worker']}",
        f"FLOP_BACKEND_IMAGE={images['miner-vllm-toploc']}",
        "FLOP_RPC=wss://rpc.devnet.flop.network",
        "FLOP_MAILBOX_URL=https://mailbox.devnet.flop.network",
        "MODEL_NAME=Qwen/Qwen2.5-0.5B-Instruct",
        f"MODEL_HASH={release['model_hash']}",
        "MODEL_PRECISION=BF16", "TOPLOC_PRECISION=bf16",
        "OPENROUTER_MODEL_SLUG=qwen/qwen2.5-0.5b-instruct",
        f"DECODE_POLICY_HASH={release['policy_hash']}",
        f"DECODE_POLICY_JSON='{release['policy_json']}'",
        "WORKER_NAME=flop-miner-01", "SESSION_PUBLIC_HOST=",
        "FLOP_CHECKPOINT_DIR=./private/checkpoint",
        "FLOP_SIGNER_FILE=./private/miner.seed",
        "FLOP_ENCLAVE_FILE=./private/session.seed",
        f"INFERENCE_API_KEY={secrets.token_hex(32)}",
        f"SESSION_AUTH_TOKEN={secrets.token_hex(32)}", "",
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Create a new mode-0600 .env; never replace an existing file")
    args = parser.parse_args()
    release = select_release(request_json(MANIFEST_URL))
    print(json.dumps({"source": MANIFEST_URL, "images": release["images"], "model_hash": release["model_hash"], "policy_hash": release["policy_hash"]}, indent=2))
    if not args.write:
        print("Read-only: nothing written, no key generated, no image pulled.")
        return 0
    destination = Path(__file__).resolve().parent.parent / ".env"
    write_private_env(destination, render_env(release))
    print("Created private .env. Existing files, miner identity and containers were not changed.")
    print("Set SESSION_PUBLIC_HOST and review the configuration locally. Do not print or publish .env.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Stopped: {error}. No install or signed action was performed.", file=sys.stderr)
        raise SystemExit(1)
