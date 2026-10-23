import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue'
import { eligibleAgents, parseBatchIds, selectedAgents } from '../src/utils/batchSelection.js'

const nodes = [{ id: 1, hostname: 'one', collection_mode: 'agent' }, { id: 2, hostname: 'two', collection_mode: 'agent' }, { id: 3, hostname: 'ssh', collection_mode: 'ssh' }]
const flush = async () => { await nextTick(); await Promise.resolve(); await nextTick() }
test('automatic and manual selections contain only existing Agent nodes', () => {
  assert.deepEqual(eligibleAgents(nodes).map(n => n.id), [1, 2])
  assert.deepEqual(selectedAgents(nodes, [2,3,999,2]).map(n => n.id), [2])
  assert.deepEqual(parseBatchIds('1,1,0,-3,no,2,1.5,9007199254740992'), [1,2])
})
function harness(overrides = {}) {
  const calls = [], unmounts = [], timers = [], route = reactive({ query: { nodes: '1,2,3,999' } })
  const access = reactive({ authenticated: true, error: '', handleError(e) { this.error = e.response?.data?.detail || 'failed'; if (e.response?.status === 401) this.authenticated = false } })
  const snapshot = { id: 'batch-one', total: 2, status: 'running', counts: { queued: 2 }, items: [{ server_id: 1, username: 'root', status: 'needs_credentials' }, { server_id: 2, username: 'root', status: 'queued' }] }
  const context = {
    computed, reactive, ref, watch, AbortController, parseBatchIds, selectedAgents,
    useRoute: () => route, useRouter: () => ({ replace(target) { calls.push(['route', target]) } }),
    useOperatorSession: () => access, useServers: () => ({ servers: ref(nodes), subscribe() {} }),
    onMounted() {}, onBeforeUnmount: fn => unmounts.push(fn),
    setInterval: fn => { timers.push(fn); return timers.length }, clearInterval() {},
    window: { dispatchEvent() {} }, CustomEvent: class {},
    api: {
      agentBatches: async () => ({ data: [] }),
      createAgentBatch: async ids => { calls.push(['create', [...ids]]); return { data: snapshot } },
      agentBatch: async () => ({ data: snapshot }),
      retryAgentBatchNode: async (batch, node, body) => { calls.push(['retry', batch, node, { ...body }]); return { data: snapshot } },
      stopAgentBatch: async id => { calls.push(['stop', id]); return { data: { ...snapshot, status: 'cancelled', cancelled: true } } },
      trustHost: async (id, fingerprint) => calls.push(['trust', id, fingerprint]), ...overrides,
    },
  }
  vm.createContext(context)
  const source = readFileSync(new URL('../src/views/AgentBatchDeploy.vue', import.meta.url), 'utf8')
  const script = source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const scope = effectScope()
  scope.run(() => vm.runInContext(script + '\nthis.h = { start, refresh, inputFor, retry, submitManual, stop, batch, busy, credentials, confirmed, openBatch }', context))
  return { ...context.h, calls, access, route, snapshot, cleanup() { unmounts.forEach(fn => fn()); scope.stop() } }
}
test('opening the batch page never starts deployment; explicit start filters stale and SSH selections', async () => {
  const h = harness()
  try {
    await flush(); assert.equal(h.calls.length, 0)
    await h.start()
    assert.deepEqual(h.calls.find(c => c[0] === 'create'), ['create', [1,2]])
    assert.equal(h.batch.value.id, 'batch-one')
    assert.equal(h.calls.filter(c => c[0] === 'create').length, 1)
  } finally { h.cleanup() }
})
test('manual fallback submits only the selected node then clears the password; stop cancels the queue', async () => {
  const h = harness()
  try {
    await flush(); await h.start()
    h.inputFor(h.snapshot.items[0]); h.credentials.username = 'manual'; h.credentials.password = 'ephemeral-secret'
    await h.submitManual()
    assert.deepEqual(h.calls.find(c => c[0] === 'retry'), ['retry','batch-one',1,{ username:'manual',password:'ephemeral-secret' }])
    assert.equal(h.credentials.password,'')
    await h.stop(); assert.equal(h.batch.value.status,'cancelled')
  } finally { h.cleanup() }
})
test('unknown fingerprints require a deliberate confirmation before retrying', async () => {
  const h = harness()
  try {
    await flush(); await h.start()
    h.batch.value.items[0] = { server_id: 1, status:'needs_trust', fingerprint:'SHA256:verified-key' }
    h.inputFor(h.batch.value.items[0]); await h.submitManual()
    assert.equal(h.calls.filter(c => c[0] === 'trust').length,0)
    h.confirmed.value = true; await h.submitManual()
    assert.deepEqual(h.calls.filter(c => ['trust','retry'].includes(c[0])).map(c => c[0]), ['trust','retry'])
  } finally { h.cleanup() }
})
test('changed fingerprints pass the old key and target only after confirmation, and changed polling data revokes confirmation', async () => {
  const trusted=[]
  const h=harness({trustHost:async(...args)=>trusted.push(args)})
  try {
    await flush();await h.start()
    h.batch.value.items[0]={server_id:1,status:'needs_trust',fingerprint:'SHA256:new',previous_fingerprint:'SHA256:old',host:'192.0.2.10',port:10022}
    h.inputFor(h.batch.value.items[0]);h.confirmed.value=true
    h.batch.value.items[0].fingerprint='SHA256:newer'
    assert.equal(h.confirmed.value,false);await h.submitManual();assert.equal(trusted.length,0)
    h.confirmed.value=true;await h.submitManual()
    assert.equal(trusted.length,1);assert.equal(trusted[0][1],'SHA256:newer')
    assert.deepEqual({...trusted[0][2]},{previous_fingerprint:'SHA256:old',expected_host:'192.0.2.10',expected_port:10022})
  }finally{h.cleanup()}
})

test('logout clears manual secrets and ignores a late batch response', async () => {
  let resolve
  const h = harness({ agentBatch: () => new Promise(r => { resolve = r }) })
  try {
    await flush(); await h.start(); h.credentials.password = 'temporary'
    const pending = h.refresh(); h.access.authenticated = false; await flush()
    assert.equal(h.credentials.password, ''); assert.equal(h.batch.value,null)
    resolve({ data: h.snapshot }); await pending
    assert.equal(h.batch.value,null)
  } finally { h.cleanup() }
})
test('a late running poll cannot replace a stopped batch', async () => {
  let resolve
  const h = harness({ agentBatch: () => new Promise(r => { resolve = r }) })
  try {
    await flush(); await h.start()
    const pending = h.refresh(); await h.stop()
    resolve({ data: h.snapshot }); await pending
    assert.equal(h.batch.value.status, 'cancelled')
  } finally { h.cleanup() }
})
