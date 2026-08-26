/**
 * Deploy Vouch, with a gas shim for the testnet.
 *
 *   KEYFILE=path/to/key node tools/deploy.mjs studionet
 *   KEYFILE=path/to/key node tools/deploy.mjs bradbury
 *
 * ## Why the shim
 *
 * Bradbury's `eth_estimateGas` under-reports GenLayer consensus calls, and
 * genlayer-js hands the estimate straight to the signer with no buffer, so the
 * deploy is rejected with `intrinsic gas too low` before it reaches a validator.
 *
 * There is no gas argument to pass: `deployContract` accepts only
 * `code`/`args`/`kwargs`/`leaderOnly`/`consensusMaxRotations`, and the estimate
 * is taken privately inside the SDK. What the transport *does* re-read on every
 * request is the chain's RPC URL, and `createClient` accepts an `endpoint` that
 * sets it -- so the correction goes in front of the SDK rather than inside it.
 *
 * Only `eth_estimateGas` responses are touched; everything else is forwarded
 * byte for byte. This cannot change what a transaction does, only how much room
 * it is given to do it in. An unused gas limit costs nothing, since gas is
 * charged for what is consumed.
 *
 * Adapted from the same shim in Credent, where the behaviour was measured
 * rather than inferred.
 */

import { createServer } from 'node:http'
import { readFileSync } from 'node:fs'
import { createAccount, createClient } from '/opt/node22/lib/node_modules/genlayer/node_modules/genlayer-js/dist/index.js'
import { studionet, testnetBradbury } from '/opt/node22/lib/node_modules/genlayer/node_modules/genlayer-js/dist/chains/index.js'

const NETWORKS = {
  studionet: { chain: studionet, rpc: 'https://studio.genlayer.com/api', shim: false },
  bradbury: { chain: testnetBradbury, rpc: 'https://rpc-bradbury.genlayer.com', shim: true },
}

// Measured, not guessed. A deploy at a 20,000,000 limit consumed 18,604,377
// and reverted against the ceiling, so the floor sits well above what the
// contract actually needs and the cap bounds the up-front cost.
const MULTIPLIER = 5n
const FLOOR = 50_000_000n
const CAP = 60_000_000n

function startGasProxy(upstream) {
  const server = createServer((req, res) => {
    let body = ''
    req.on('data', (c) => { body += c })
    req.on('end', async () => {
      let parsed = null
      try { parsed = JSON.parse(body) } catch { parsed = null }
      const reply = (p) => {
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify(p))
      }
      try {
        const up = await fetch(upstream, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body,
          signal: AbortSignal.timeout(60_000),
        })
        const data = await up.json()
        if (parsed?.method === 'eth_estimateGas') {
          let want = FLOOR
          if (typeof data.result === 'string') {
            const got = BigInt(data.result)
            want = got * MULTIPLIER
            if (want < FLOOR) want = FLOOR
          }
          if (want > CAP) want = CAP
          return reply({ jsonrpc: '2.0', id: parsed.id, result: '0x' + want.toString(16) })
        }
        return reply(data)
      } catch (e) {
        return reply({ jsonrpc: '2.0', id: parsed?.id ?? null, error: { code: -32603, message: String(e) } })
      }
    })
  })
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      resolve({ url: `http://127.0.0.1:${port}`, stop: () => server.close() })
    })
  })
}

const netName = process.argv[2] || 'studionet'
const net = NETWORKS[netName]
if (!net) throw new Error(`unknown network ${netName}; use studionet or bradbury`)
const artifact = process.argv[3] || new URL('../contracts/vouch.py', import.meta.url).pathname

const account = createAccount(readFileSync(process.env.KEYFILE, 'utf8').trim())
const code = readFileSync(artifact)

let endpoint = net.rpc
let proxy = null
if (net.shim) {
  proxy = await startGasProxy(net.rpc)
  endpoint = proxy.url
  console.log('gas shim ', proxy.url, '->', net.rpc)
}

const client = createClient({ chain: net.chain, account, endpoint })

console.log('network  ', netName)
console.log('artifact ', artifact, code.length, 'bytes')
console.log('deployer ', account.address)

try {
  const hash = await client.deployContract({
    code,
    args: [3, 200000, 75, 15, 86400],
    leaderOnly: false,
  })
  console.log('tx', hash)
  const r = await client.waitForTransactionReceipt({ hash, status: 'ACCEPTED', retries: 400, interval: 5000 })
  console.log('status  ', r.statusName ?? r.status)
  console.log('ADDRESS ', r.data?.contract_address ?? r.contractAddress ?? r.recipient)
} finally {
  if (proxy) proxy.stop()
}
