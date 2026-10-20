import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { computed, ref } from 'vue'
import { cleanupInventory, collectionLabel, connectionAddress, filterCleanupNodes, serverIdentity } from '../src/utils/cleanupNodes.js'
import { eligibleAgents, selectedAgents } from '../src/utils/batchSelection.js'

const nodes = [
  { id: 131, hostname: '徐科-10064', ip_address: '114.212.173.212', ssh_port: 10064, collection_mode: 'ssh', group_name: '创业大厦' },
  { id: 5, hostname: '另一个节点', ip_address: '192.168.1.5', ssh_port: 22, collection_mode: 'agent' },
  { id: 33, hostname: '徐科-10064', ip_address: '114.212.173.212', ssh_port: 10064, collection_mode: 'agent', group_name: '创业大厦' },
]

test('same-name records stay distinct by ID, even at the same address; duplicates sort together first', () => {
  const inventory = cleanupInventory(nodes)
  assert.equal(inventory.duplicateGroups, 1)
  assert.equal(inventory.duplicateNodes, 2)
  assert.deepEqual(filterCleanupNodes(inventory.rows).map(n => n.id), [33, 131, 5])
  assert.deepEqual(filterCleanupNodes(inventory.rows, '', true).map(n => n.id), [33, 131])
  assert.equal(nodes[0].duplicateCount, undefined)
  assert.deepEqual(filterCleanupNodes(inventory.rows, '徐科').map(n => n.id), [33, 131])
  assert.deepEqual(filterCleanupNodes(inventory.rows, '10064', true).map(n => n.id), [33, 131])
  assert.deepEqual(filterCleanupNodes(inventory.rows, '131').map(n => n.id), [131])
  assert.deepEqual(filterCleanupNodes(inventory.rows, 'ssh', true).map(n => n.id), [131])
  assert.deepEqual(filterCleanupNodes(inventory.rows, '创业大厦').map(n => n.id), [33, 131])
  assert.deepEqual(filterCleanupNodes(inventory.rows, 'missing'), [])
})

test('name matching normalizes padding and width but never merges records or groups unnamed nodes', () => {
  const inventory = cleanupInventory([
    { id: 1, hostname: ' Node-A ' }, { id: 2, hostname: 'Ｎｏｄｅ-A' },
    { id: 3, hostname: '' }, { id: 4, hostname: null },
  ])
  assert.equal(inventory.duplicateGroups, 1)
  assert.deepEqual(filterCleanupNodes(inventory.rows, 'node-a', true).map(n => n.id), [1, 2])
  assert.equal(connectionAddress({ ip_address: '::1', ssh_port: 2222 }), '[::1]:2222')
  assert.equal(connectionAddress({ ip_address: '192.168.1.5' }), '192.168.1.5:22')
})

function harness(confirm = false, apiOverrides = {}) {
  const calls = [], confirmations = [], messages = [], servers = ref(nodes)
  const context = {
    ref, computed, cleanupInventory, collectionLabel, connectionAddress, filterCleanupNodes, serverIdentity,
    eligibleAgents, selectedAgents, statusLabel: value => value,
    useServers: () => ({ servers, subscribe() {} }), useRouter: () => ({ push() {} }), onMounted() {},
    window: { confirm(message) { confirmations.push(message); return confirm }, dispatchEvent() {} },
    alert: message => messages.push(message), CustomEvent: class {},
    api: { deleteServer: async id => { calls.push(['delete', id]); return { data: { success: true } } },
      clearHistory: async id => calls.push(['history', id]), ...apiOverrides },
  }
  vm.createContext(context)
  const source = readFileSync(new URL('../src/views/Cleanup.vue', import.meta.url), 'utf8')
  const script = source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  vm.runInContext(script + '\nthis.h = { deleteServer, cleanHistory, visibleServers, search, duplicatesOnly, pendingId }', context)
  return { ...context.h, calls, confirmations, messages }
}

test('filtering does not delete anything; cancelling names the exact record and performs no mutation', async () => {
  const h = harness()
  h.search.value = '徐科'; h.duplicatesOnly.value = true
  assert.deepEqual(Array.from(h.visibleServers.value, n => n.id), [33, 131])
  await h.deleteServer(nodes[0]); await h.cleanHistory(nodes[2])
  assert.deepEqual(h.calls, [])
  assert.match(h.confirmations[0], /节点 ID：131/)
  assert.match(h.confirmations[0], /114\.212\.173\.212:10064/)
  assert.match(h.confirmations[0], /采集方式：SSH/)
  assert.match(h.confirmations[1], /节点 ID：33/)
  assert.match(h.confirmations[1], /采集方式：Agent/)
})

test('confirmed deletion targets only the chosen ID and rejects concurrent actions', async () => {
  let finish
  const h = harness(true, { deleteServer: id => { h.calls.push(['delete', id]); return new Promise(resolve => { finish = resolve }) } })
  const pending = h.deleteServer(nodes[0])
  await h.deleteServer(nodes[2]); await h.cleanHistory(nodes[2])
  assert.deepEqual(h.calls, [['delete', 131]])
  assert.equal(h.confirmations.length, 1)
  finish({ data: { success: true } }); await pending
  assert.equal(h.pendingId.value, null)
})

test('failed deletion is not reported as success and unlocks actions', async () => {
  const h = harness(true, { deleteServer: async () => ({ data: { success: false, message: '找不到该服务器' } }) })
  await h.deleteServer(nodes[0])
  assert.deepEqual(h.messages, ['找不到该服务器'])
  assert.equal(h.pendingId.value, null)
})
