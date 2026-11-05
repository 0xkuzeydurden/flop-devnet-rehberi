import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from common import NoRedirect, REVIEWED_MODEL_HASH, REVIEWED_POLICY_HASH, compare_genesis, gateway_identity, request_json, reviewed_profile, select_release

spec = importlib.util.spec_from_file_location("prepare_env", SCRIPTS / "prepare-env.py")
prepare_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_env)


def fixture():
    manifest = {
        name: {"image": f"ghcr.io/flop-labs/flop-core/{name}:v0.1.1@sha256:{'a' * 64}"}
        for name in ("cli", "miner-worker", "miner-vllm-toploc")
    }
    manifest["models"] = {REVIEWED_MODEL_HASH: {
        "name": "Qwen/Qwen2.5-0.5B-Instruct",
        "decode_policies": {REVIEWED_POLICY_HASH: {"class": "TextGeneration", "version": 1}},
        "missing_decode_policies": [],
    }}
    return manifest


class ReleaseChecks(unittest.TestCase):
    def test_accepts_digest_pinned_official_release(self):
        result = select_release(fixture())
        self.assertEqual(result["model_hash"], REVIEWED_MODEL_HASH)

    def test_rejects_model_root_drift_before_using_bf16(self):
        value = fixture()
        value["models"]["0x" + "d" * 64] = value["models"].pop(REVIEWED_MODEL_HASH)
        with self.assertRaises(ValueError):
            select_release(value)

    def test_rejects_policy_drift(self):
        value = fixture()
        policies = value["models"][REVIEWED_MODEL_HASH]["decode_policies"]
        policies["0x" + "d" * 64] = policies.pop(REVIEWED_POLICY_HASH)
        with self.assertRaises(ValueError):
            select_release(value)

    def test_rejects_unpinned_image(self):
        value = fixture()
        value["cli"]["image"] = "ghcr.io/flop-labs/flop-core/cli:latest"
        with self.assertRaises(ValueError):
            select_release(value)

    def test_rejects_third_party_registry(self):
        value = fixture()
        value["miner-worker"]["image"] = value["miner-worker"]["image"].replace("flop-labs", "another-owner")
        with self.assertRaises(ValueError):
            select_release(value)

    def test_rejects_missing_or_ambiguous_model(self):
        value = fixture()
        value["models"]["0x" + "d" * 64] = next(iter(value["models"].values()))
        with self.assertRaises(ValueError):
            select_release(value)

    def test_rejects_unresolved_policy(self):
        value = fixture()
        next(iter(value["models"].values()))["missing_decode_policies"] = ["0x" + "d" * 64]
        with self.assertRaises(ValueError):
            select_release(value)

    def test_rejects_env_quote_injection(self):
        value = fixture()
        next(iter(next(iter(value["models"].values()))["decode_policies"].values()))["text"] = "a'b"
        with self.assertRaises(ValueError):
            select_release(value)

    def test_rejects_nonofficial_endpoint_before_network(self):
        with patch("urllib.request.OpenerDirector.open") as network:
            with self.assertRaises(ValueError):
                request_json("https://example.invalid/collect")
            network.assert_not_called()

    def test_redirect_is_rejected_before_followup(self):
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://example.invalid/")


class IdentityChecks(unittest.TestCase):
    def test_service_agreement_is_not_independent_verification(self):
        result = compare_genesis("0x" + "a" * 64, "0x" + "a" * 64)
        self.assertFalse(result["matches_expected"])

    def test_rejects_cross_service_mismatch(self):
        with self.assertRaises(ValueError):
            compare_genesis("0x" + "a" * 64, "0x" + "b" * 64)

    def test_rejects_wrong_expected_identity(self):
        with self.assertRaises(ValueError):
            compare_genesis("0x" + "a" * 64, "0x" + "a" * 64, "0x" + "c" * 64)

    def test_gateway_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            gateway_identity({"network": {"network": "devnet", "observed_genesis_hash": "0x" + "b" * 64}}, "0x" + "a" * 64)

    def test_missing_gateway_identity_is_not_verified(self):
        self.assertFalse(gateway_identity({"status": "ok"}, "0x" + "a" * 64)["gateway_matches_observed_chain"])

    def test_gateway_agreement_does_not_invent_expected_identity(self):
        result = gateway_identity({"network": {"network": "devnet", "observed_genesis_hash": "0x" + "a" * 64, "expected_genesis_hash": None}}, "0x" + "a" * 64)
        self.assertTrue(result["gateway_matches_observed_chain"])
        self.assertFalse(result["gateway_publishes_expected_genesis"])


class PrivateFileChecks(unittest.TestCase):
    def test_file_is_private_and_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            prepare_env.write_private_env(path, "placeholder=example\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                prepare_env.write_private_env(path, "changed\n")
            self.assertEqual(path.read_text(), "placeholder=example\n")

    def test_symlink_destination_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("keep")
            link = Path(directory) / ".env"
            link.symlink_to(target)
            with self.assertRaises(FileExistsError):
                prepare_env.write_private_env(link, "changed")
            self.assertEqual(target.read_text(), "keep")

    def test_generated_credentials_are_distinct_and_not_empty(self):
        text = prepare_env.render_env(select_release(fixture()))
        pairs = dict(line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#"))
        self.assertEqual(len(pairs["INFERENCE_API_KEY"]), 64)
        self.assertNotEqual(pairs["INFERENCE_API_KEY"], pairs["SESSION_AUTH_TOKEN"])


class SponsorGuards(unittest.TestCase):
    def run_helper(self, *arguments):
        return subprocess.run(["node", str(SCRIPTS / "sponsor.mjs"), *arguments], capture_output=True, text=True, timeout=5)

    def test_dry_run_does_not_read_missing_key_or_connect(self):
        result = self.run_helper("--key-file", "/does-not-exist/miner.seed")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["networkRequests"])
        self.assertFalse(data["secretRead"])
        self.assertFalse(data["signatureCreated"])

    def test_dry_run_uses_reviewed_profile_without_manual_hash(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["expectedGenesis"], reviewed_profile()["genesis_hash"])

    def test_unreviewed_genesis_override_is_refused_before_network(self):
        result = self.run_helper("--submit", "--expected-genesis", "0x" + "a" * 64)
        self.assertEqual(result.returncode, 1)
        self.assertIn("differs", result.stderr)

    def test_endpoint_override_is_refused(self):
        result = self.run_helper("--faucet", "https://example.invalid")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not supported", result.stderr)

    def run_mock_network(self, rpc_hash, faucet_hash, gateway_hash):
        with tempfile.TemporaryDirectory() as directory:
            preload = Path(directory) / "mock-network.mjs"
            preload.write_text("""
const values = JSON.parse(process.env.FLOP_TEST_IDENTITIES);
globalThis.WebSocket = class {
  listeners = {};
  constructor() { queueMicrotask(() => this.listeners.open?.({})); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  send() { queueMicrotask(() => this.listeners.message?.({data: JSON.stringify({jsonrpc:'2.0',id:1,result:values.rpc})})); }
  close() {}
};
globalThis.fetch = async (url, init={}) => {
  if (init.method === 'POST') throw new Error('A sponsorship POST must never happen in this test');
  return {ok:true,status:200,json:async () => url.endsWith('/chain')
    ? {genesisHash:values.faucet}
    : {status:'ok',network:{network:'devnet',observed_genesis_hash:values.gateway,expected_genesis_hash:values.gateway}}};
};
""")
            env = os.environ.copy()
            env["FLOP_TEST_IDENTITIES"] = json.dumps({"rpc": rpc_hash, "faucet": faucet_hash, "gateway": gateway_hash})
            return subprocess.run(["node", "--import", str(preload), str(SCRIPTS / "sponsor.mjs"), "--submit", "--key-file", "/must-not-be-read/miner.seed"], capture_output=True, text=True, env=env, timeout=5)

    def test_network_reset_stops_before_key_read_or_signing(self):
        expected = reviewed_profile()["genesis_hash"]
        result = self.run_mock_network("0x" + "a" * 64, expected, expected)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Genesis mismatch", result.stderr)
        self.assertIn("No key was read", result.stderr)

    def test_wrong_gateway_stops_before_key_read_or_signing(self):
        expected = reviewed_profile()["genesis_hash"]
        result = self.run_mock_network(expected, expected, "0x" + "b" * 64)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Gateway identity", result.stderr)
        self.assertIn("No key was read", result.stderr)

if __name__ == "__main__":
    unittest.main()
