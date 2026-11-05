#!/usr/bin/env node
// Original MIT-licensed helper for the documented FLOP devnet sponsorship protocol.
// Dry run is the default: no network connection, secret read or signature is made.
import { lstat, open, readFile } from 'node:fs/promises';
import { constants as fsFlags } from 'node:fs';
import { isAbsolute } from 'node:path';
import { createRequire } from 'node:module';

const FAUCET = 'https://faucet.flop.network';
const RPC = 'wss://rpc.devnet.flop.network';
const GATEWAY = 'https://api.flop.network/health';
const TIMEOUT_MS = 12_000;
const MAX_KEY_BYTES = 4096;
const HASH = /^0x[0-9a-fA-F]{64}$/;

class SafeError extends Error {}
const fail = (message) => { throw new SafeError(message); };

function options(argv, genesis) {
  const result = { submit: false, expectedGenesis: genesis, keyFile: '/run/flop/miner.seed' };
  const seen = new Set();
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (seen.has(arg)) fail('Duplicate argument. Run with --help for usage.');
    seen.add(arg);
    if (arg === '--help') return { help: true };
    if (arg === '--submit') { result.submit = true; continue; }
    if (arg !== '--expected-genesis' && arg !== '--key-file') {
      fail('Unknown argument. Endpoint overrides are not supported.');
    }
    const value = argv[++index];
    if (!value || value.startsWith('--')) fail('An argument value is missing.');
    if (arg === '--expected-genesis') result.expectedGenesis = value;
    else result.keyFile = value;
  }
  if (result.expectedGenesis !== null && !HASH.test(result.expectedGenesis)) {
    fail('--expected-genesis must contain a 32-byte 0x-prefixed hash.');
  }
  if (!isAbsolute(result.keyFile)) fail('--key-file must be an absolute path.');
  if (result.expectedGenesis.toLowerCase() !== genesis) {
    fail('Requested genesis differs from the reviewed devnet profile. Review the profile after a network reset.');
  }
  return result;
}

async function faucetJson(path, init = {}) {
  let response;
  try {
    response = await fetch(`${FAUCET}${path}`, {
      ...init,
      redirect: 'error',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    fail('The official faucet request failed, timed out or redirected. No retry was submitted.');
  }
  if (!response.ok) fail(`The official faucet returned HTTP ${response.status}.`);
  let value;
  try { value = await response.json(); }
  catch { fail('The official faucet did not return valid JSON.'); }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail('The official faucet returned an unexpected response shape.');
  }
  return { value, status: response.status };
}

async function rpcGenesis() {
  if (typeof WebSocket !== 'function') {
    fail('This helper requires a Node runtime with the built-in WebSocket API (Node 22+).');
  }
  return await new Promise((resolve, reject) => {
    let socket;
    let finished = false;
    const finish = (error, value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      try { socket?.close(); } catch { /* Do not print transport internals. */ }
      if (error) reject(new SafeError(error));
      else resolve(value);
    };
    const timer = setTimeout(() => finish('The official RPC genesis query timed out.'), TIMEOUT_MS);
    try { socket = new WebSocket(RPC); }
    catch { finish('The official RPC connection could not be opened.'); return; }
    socket.addEventListener('open', () => {
      try {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'chain_getBlockHash', params: [0] }));
      } catch { finish('The official RPC genesis query could not be sent.'); }
    });
    socket.addEventListener('message', (event) => {
      let message;
      try { message = JSON.parse(String(event.data)); }
      catch { finish('The official RPC returned invalid JSON.'); return; }
      if (!message || message.id !== 1) return;
      if (message.jsonrpc !== '2.0' || message.error || !HASH.test(message.result)) {
        finish('The official RPC did not return a valid genesis hash.'); return;
      }
      finish(null, message.result.toLowerCase());
    });
    socket.addEventListener('error', () => finish('The official RPC connection failed.'));
    socket.addEventListener('close', () => finish('The official RPC closed before returning genesis.'));
  });
}

async function privateSeed(path) {
  let handle;
  try {
    const metadata = await lstat(path);
    if (!metadata.isFile() || metadata.isSymbolicLink() || (metadata.mode & 0o077) !== 0) {
      fail('The key must be a regular private file with no group/other permissions (for example, mode 0600).');
    }
    handle = await open(path, fsFlags.O_RDONLY | fsFlags.O_NOFOLLOW);
    const current = await handle.stat();
    if (!current.isFile() || current.ino !== metadata.ino || current.dev !== metadata.dev ||
        (current.mode & 0o077) !== 0 || current.size < 1 || current.size > MAX_KEY_BYTES) {
      fail('The private key file changed or has an invalid size/permissions.');
    }
    return (await handle.readFile({ encoding: 'utf8' })).trim();
  } catch (error) {
    if (error instanceof SafeError) throw error;
    fail('The private key file could not be read securely.');
  } finally { await handle?.close(); }
}

async function submit(config) {
  // Verify both live services against the explicit expected identity BEFORE reading a key.
  const expected = config.expectedGenesis.toLowerCase();
  const fromRpc = await rpcGenesis();
  const { value: chain } = await faucetJson('/chain');
  if (!HASH.test(chain.genesisHash)) fail('The faucet response has no valid genesisHash.');
  if (fromRpc !== expected || chain.genesisHash.toLowerCase() !== expected) {
    fail('Genesis mismatch: RPC, faucet and the expected network identity must all agree. No key was read.');
  }
  let gateway;
  try {
    const response = await fetch(GATEWAY, { redirect: 'error', signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!response.ok) fail('The official gateway health check failed.');
    gateway = await response.json();
  } catch { fail('The official gateway health check failed. No key was read.'); }
  const gatewayNetwork = gateway?.network;
  if (gateway.status !== 'ok' || gatewayNetwork?.network !== 'devnet' ||
      gatewayNetwork.observed_genesis_hash?.toLowerCase() !== expected ||
      (gatewayNetwork.expected_genesis_hash != null && gatewayNetwork.expected_genesis_hash.toLowerCase() !== expected)) {
    fail('Gateway identity does not match the reviewed devnet profile. No key was read.');
  }

  let Keyring, cryptoWaitReady, mnemonicValidate;
  try {
    const bundled = createRequire('/app/package.json');
    ({ Keyring } = bundled('@polkadot/keyring'));
    ({ cryptoWaitReady, mnemonicValidate } = bundled('@polkadot/util-crypto'));
    if (!(await cryptoWaitReady())) fail('The signing library could not initialize.');
  } catch { fail('The approved CLI image must provide the Polkadot signing libraries under /app.'); }

  const secret = await privateSeed(config.keyFile);
  let account;
  try {
    if (!/^0x[0-9a-fA-F]{64}$/.test(secret) && !mnemonicValidate(secret)) {
      fail('The key file must contain a valid mnemonic or a 32-byte hex seed. Public development URIs are refused.');
    }
    account = new Keyring({ type: 'sr25519', ss58Format: 42 }).addFromUri(secret);
  } catch { fail('The private key file does not contain a supported secret.'); }
  const issuedAt = String(Date.now());
  const message = ['flop-sponsor/v1', 'miner', expected, account.address, issuedAt].join('|');
  let signature;
  try { signature = `0x${Buffer.from(account.sign(Buffer.from(message, 'utf8'))).toString('hex')}`; }
  catch { fail('The sponsorship message could not be signed.'); }

  const { value, status } = await faucetJson('/sponsor/miner', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ address: account.address, issuedAt, signature }),
  });
  if (value.success !== true) {
    fail('The sponsor did not confirm success. Inspect on-chain state before any manual retry.');
  }
  console.log(JSON.stringify({
    mode: 'submit', network: 'devnet', address: account.address,
    genesis: expected, httpStatus: status, sponsorReportedSuccess: true,
    next: 'Independently verify finalized miner registration and stake; this response is not calibration or paid-work evidence.',
  }, null, 2));
}

async function main() {
  let profile;
  try { profile = JSON.parse(await readFile(new URL('./devnet-profile.json', import.meta.url), 'utf8')); }
  catch { fail('The reviewed devnet profile is missing or invalid.'); }
  if (profile.network !== 'devnet' || !HASH.test(profile.genesis_hash)) fail('Invalid reviewed devnet identity.');
  const config = options(process.argv.slice(2), profile.genesis_hash.toLowerCase());
  if (config.help) {
    console.log('Usage: node sponsor.mjs [--key-file /absolute/path] [--submit]\nDefault: print a plan only; no network, secret read or signature.\nSubmission compares RPC, faucet and gateway against devnet-profile.json before reading a key. Endpoints are fixed.');
    return;
  }
  if (!config.submit) {
    console.log(JSON.stringify({
      mode: 'dry-run', network: 'devnet', faucet: FAUCET, rpc: RPC,
      expectedGenesis: config.expectedGenesis, keyFile: config.keyFile,
      profileCheckedAt: profile.checked_at,
      networkRequests: false, secretRead: false, signatureCreated: false,
      next: 'Review the devnet profile, then explicitly add --submit. A reset or service disagreement stops before reading a key.',
    }, null, 2));
    return;
  }
  await submit(config);
}

main().catch((error) => {
  // Third-party errors can include input material: expose only our fixed safe messages.
  console.error(`Error: ${error instanceof SafeError ? error.message : 'Unexpected failure; no internal or secret details are printed.'}`);
  process.exitCode = 1;
});
