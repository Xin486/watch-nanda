import test from 'node:test'
import assert from 'node:assert/strict'
import { createDeploymentFeed } from '../src/utils/deploymentFeed.js'

const flush = async () => { await Promise.resolve(); await Promise.resolve() }
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
function harness(fetchSnapshot = async () => ({ data: { status: 'running', logs: ['fallback'] } })) {
  let clock = 1000, tick, stream, cancelled = false
  const states = [], snapshots = [], errors = []
  class Source {
    listeners = {}; closed = false
    constructor(url) { this.url = url; stream = this }
    addEventListener(name, fn) { this.listeners[name] = fn }
    emit(name, data) { this.listeners[name]({ data: JSON.stringify(data) }) }
    close() { this.closed = true }
  }
  const feed = createDeploymentFeed({ url: '/read-only/stream', fetchSnapshot,
    EventSourceImpl: Source, now: () => clock, every: fn => { tick = fn; return 1 }, cancel: () => { cancelled = true },
    onState: value => states.push(value), onSnapshot: value => snapshots.push(value), onError: error => errors.push(error),
    isComplete: data => data.status !== 'running',
  })
  return { feed, stream, states, snapshots, errors, tick: async ms => { clock += ms; tick(); await flush() }, cancelled: () => cancelled }
}

test('SSE snapshots and heartbeat keep the feed live without HTTP polling; completion closes it', async () => {
  let gets = 0
  const h = harness(async () => { gets++; return {} })
  h.stream.emit('snapshot', { status: 'running', logs: ['installing'] })
  await h.tick(15000); h.stream.emit('heartbeat', {}); await h.tick(15000)
  assert.equal(gets, 0); assert.equal(h.states.at(-1), 'live')
  h.stream.emit('snapshot', { status: 'success', logs: ['ready'] })
  assert.equal(h.snapshots.at(-1).status, 'success'); assert.equal(h.states.at(-1), 'complete')
  assert.equal(h.stream.closed, true); assert.equal(h.cancelled(), true)
})
test('disconnect falls back to read-only HTTP, then native reconnection restores live updates', async () => {
  const h = harness()
  h.stream.onerror(); await flush()
  assert.equal(h.snapshots.at(-1).logs[0], 'fallback'); assert.equal(h.states.at(-1), 'fallback')
  h.stream.emit('snapshot', { status: 'running', logs: ['reconnected'] })
  assert.equal(h.states.at(-1), 'live'); assert.equal(h.stream.closed, false)
  h.feed.stop()
})
test('stale HTTP result cannot replace a newer push or restore data after unsubscribe', async () => {
  const pending = deferred(), h = harness(() => pending.promise)
  const read = h.feed.refresh()
  h.stream.emit('snapshot', { status: 'running', logs: ['new'] })
  pending.resolve({ data: { status: 'running', logs: ['old'] } }); await read
  assert.deepEqual(h.snapshots.map(s => s.logs[0]), ['new'])
  h.feed.stop(); h.stream.emit('snapshot', { status: 'failed' }); h.stream.onerror()
  assert.equal(h.snapshots.length, 1)
})
test('silent connection uses the watchdog and serialized HTTP requests', async () => {
  const pending = deferred(); let gets = 0
  const h = harness(() => { gets++; return pending.promise })
  await h.tick(21000); await h.tick(5000)
  assert.equal(gets, 1)
  h.feed.stop(); pending.resolve({ data: { status: 'running' } }); await flush()
  assert.equal(h.snapshots.length, 0)
})
test('expired session or missing task stops reconnect and forwards the error once', async () => {
  for (const status of [401, 403, 404]) {
    const h = harness()
    h.stream.emit('feed-error', { status, detail: '请重新登录或核验任务' })
    h.stream.emit('snapshot', { status: 'running' }); h.stream.onerror()
    assert.equal(h.errors.length, 1); assert.equal(h.errors[0].response.status, status)
    assert.equal(h.stream.closed, true); assert.equal(h.snapshots.length, 0)
  }
})
test('fallback authentication errors stop, while network errors remain retryable', async () => {
  const expired = harness(async () => { throw { response: { status: 401 } } })
  await expired.feed.refresh(); assert.equal(expired.errors.length, 1); assert.equal(expired.stream.closed, true)
  const network = harness(async () => { throw new Error('offline') })
  await network.feed.refresh(); assert.equal(network.errors.length, 0); assert.equal(network.stream.closed, false)
  network.feed.stop()
})
