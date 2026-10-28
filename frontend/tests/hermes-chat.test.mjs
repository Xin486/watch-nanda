import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

const code = fs.readFileSync(new URL('../src/utils/hermesChat.js', import.meta.url), 'utf8')
  .replace(/^import .*$/gm, '').replace(/export /g, '')
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const snapshot = (id = 'conversation-123456', status = 'idle') => ({ id, status, messages: [], error: '', activity: '' })
function harness(request) {
  const context = { AbortController, crypto: { randomUUID: () => 'request-1234567890' }, setTimeout, clearTimeout }
  vm.createContext(context); vm.runInContext(code + '\nthis.factory=createHermesChat;this.backgroundNotice=hermesBackgroundNotice', context)
  const states = [], timers = new Map(); let serial = 0
  const client = context.factory({ request, changed: s => states.push(s),
    every: fn => { timers.set(++serial, fn); return serial }, cancel: id => timers.delete(id) })
  return { client, states, timers, backgroundNotice: context.backgroundNotice, get last() { return states.at(-1) } }
}

test('a late poll cannot overwrite a newly submitted turn', async () => {
  const poll = deferred(), sent = snapshot(undefined, 'running')
  const h = harness((path, options) => options?.method === 'POST' ? Promise.resolve(sent) : poll.promise)
  const old = h.client.restore('conversation-123456')
  await h.client.send('hello'); assert.equal(h.last.status, 'running')
  poll.resolve(snapshot()); await old
  assert.equal(h.last.status, 'running'); h.client.dispose()
})

test('logout during creation prevents a late POST and hides old messages', async () => {
  const created = deferred(), calls = []
  const h = harness((path) => { calls.push(path); return created.promise })
  const sending = h.client.send('private message'); h.client.clear()
  created.resolve(snapshot()); await sending
  assert.equal(calls.length, 1); assert.equal(h.last.id, ''); assert.equal(h.last.messages.length, 0)
})

test('uncertain submission is reconciled by GET without automatic resubmission', async () => {
  let posts = 0
  const h = harness(async (_, options) => {
    if (options?.method === 'POST') { if (++posts === 1) return snapshot(); throw Error('offline') }
    return snapshot(undefined, 'running')
  })
  await h.client.send('hello'); assert.equal(posts, 2)
  await [...h.timers.values()][0]()
  assert.equal(h.last.status, 'running'); assert.equal(posts, 2); h.client.dispose()
})

test('unauthorized snapshots erase transcript and stop polling', async () => {
  const h = harness(async () => { throw Object.assign(Error('请重新登录'), { status: 401 }) })
  await h.client.restore('conversation-123456')
  assert.equal(h.last.id, ''); assert.equal(h.timers.size, 0)
})

test('minimized running chat keeps polling, disposal rejects stale responses', async () => {
  const later = deferred()
  let count = 0
  const h = harness(async () => ++count === 1 ? snapshot(undefined, 'running') : later.promise)
  await h.client.restore('conversation-123456'); h.client.setActive(false)
  assert.equal(h.timers.size, 1)
  const updating = [...h.timers.values()][0](); h.client.dispose()
  const before = h.states.length; later.resolve(snapshot()); await updating
  assert.equal(h.states.length, before); assert.equal(h.timers.size, 0)
})

test('closing and reopening a running panel only reads the same task, never stops or resubmits it', async () => {
  const calls = []
  let finished = false
  const h = harness(async (path, options) => {
    calls.push([path, options?.method || 'GET'])
    return { ...snapshot(undefined, finished ? 'idle' : 'running'), messages: [{ role: 'assistant', text: finished ? 'done' : 'working' }] }
  })
  await h.client.restore('conversation-123456')
  h.client.setActive(false)
  await [...h.timers.values()][0]()
  assert.equal(h.last.status, 'running')
  finished = true; await [...h.timers.values()][0]()
  assert.equal(h.last.messages[0].text, 'done'); assert.equal(h.timers.size, 0)
  await h.client.setActive(true)
  assert.ok(calls.every(([path, method]) => path === '/conversation-123456' && method === 'GET'))
  h.client.dispose()
})

test('background notice distinguishes running, completed, stopped and failed tasks and clears on open/logout', () => {
  const { backgroundNotice: notice } = harness(async () => snapshot())
  const running = snapshot(undefined, 'running'), idle = snapshot(), stopped = snapshot(undefined, 'stopped')
  assert.equal(notice(idle, running, false), '任务正在后台执行')
  assert.equal(notice(running, idle, false), '任务已完成，点击查看')
  assert.equal(notice(idle, idle, false, '任务已完成，点击查看'), '任务已完成，点击查看')
  assert.equal(notice(running, stopped, false), '任务已停止，点击查看')
  assert.equal(notice(running, snapshot(undefined, 'error'), false), '任务需要关注，点击查看')
  assert.equal(notice(running, idle, true), '')
  assert.equal(notice(running, { ...idle, id: '' }, false), '')
})
