import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { ref, reactive, watch, nextTick } from 'vue'

const shortcutCode = fs.readFileSync(new URL('../src/utils/hermesNodeShortcut.js', import.meta.url), 'utf8').replace(/export /g, '')
const componentCode = fs.readFileSync(new URL('../src/components/HermesChat.vue', import.meta.url), 'utf8')
  .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }

function harness({ authenticated = true, clipboard = true, session = async () => {} } = {}) {
  const writes = [], calls = [], listeners = new Map(), cleanups = []
  const access = reactive({ secure: true, authenticated, password: '',
    handleError() { this.authenticated = false },
    async login() { this.authenticated = true }, async logout() { this.authenticated = false },
  })
  const context = {
    ref, watch: (...args) => cleanups.push(watch(...args)), nextTick,
    onMounted: fn => fn(), onBeforeUnmount: fn => cleanups.push(fn),
    useOperatorSession: () => access, hermesBackgroundNotice: () => '',
    api: { opsSession: session }, sessionStorage: { getItem() {}, setItem() {}, removeItem() {} },
    navigator: { clipboard: { async writeText(text) { writes.push(text); if (!clipboard) throw Error('denied') } } },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail } },
    window: {
      addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name),
      dispatchEvent: event => listeners.get(event.type)?.(event),
    },
    createHermesChat: ({ changed }) => ({
      setActive: value => calls.push(['active', value]), restore: id => calls.push(['restore', id]),
      send: text => calls.push(['send', text]), stop: () => calls.push(['stop']),
      clear() { changed({ id: '', status: 'idle', messages: [], error: '', activity: '' }) }, dispose() {},
    }),
  }
  vm.createContext(context)
  vm.runInContext(shortcutCode + '\n' + componentCode + '\nthis.h={open,draft,chat,shortcutNotice,showPanel,login,logout,close,openHermesForNode}', context)
  return { ...context.h, access, calls, writes, listeners,
    select: name => context.window.dispatchEvent(new context.CustomEvent('hermes-node-shortcut', { detail: name })),
    dispose: () => cleanups.forEach(fn => fn()),
  }
}

test('node shortcut opens chat, copies only the selected name and preserves the draft without sending', async () => {
  const h = harness()
  h.draft.value = '请检查'
  await h.select('陆-33')
  assert.equal(h.open.value, true)
  assert.deepEqual(h.writes, ['陆-33'])
  assert.equal(h.draft.value, '请检查 陆-33 ')
  await h.select('节点-B')
  assert.equal(h.open.value, true, 'selecting while open must not toggle the panel closed')
  assert.equal(h.draft.value, '请检查 陆-33 节点-B ')
  assert.ok(h.calls.every(([method]) => !['send', 'stop'].includes(method)))
  h.dispose()
  assert.equal(h.listeners.size, 0)
})

test('expired login shows authentication and retains the inserted name through login', async () => {
  const h = harness({ session: async () => { throw Error('expired') } })
  h.draft.value = '旧登录的草稿'
  await h.select('陆-33')
  assert.equal(h.access.authenticated, false)
  assert.equal(h.draft.value, '陆-33 ')
  await h.login()
  assert.equal(h.access.authenticated, true)
  assert.equal(h.draft.value, '陆-33 ')
  assert.ok(h.calls.every(([method]) => method !== 'send'))
  h.dispose()
})

test('clipboard denial still fills a running chat without stopping its task; full draft is preserved', async () => {
  const h = harness({ clipboard: false })
  h.chat.value.status = 'running'
  await h.select('陆-33')
  assert.equal(h.draft.value, '陆-33 ')
  assert.match(h.shortcutNotice.value, /未允许复制/)
  assert.equal(h.chat.value.status, 'running')
  h.draft.value = '文'.repeat(8000)
  await h.select('节点-B')
  assert.equal(h.draft.value, '文'.repeat(8000))
  assert.match(h.shortcutNotice.value, /输入框已满/)
  assert.ok(h.calls.every(([method]) => !['send', 'stop'].includes(method)))
  h.dispose()
})

test('rapid selections keep their order; logout cancels pending insertion and late authentication', async () => {
  const pending = deferred(), h = harness({ session: () => pending.promise })
  const first = h.select('节点-A'), second = h.select('节点-B')
  pending.resolve(); await Promise.all([first, second])
  assert.equal(h.draft.value, '节点-A 节点-B ')
  h.dispose()

  const late = deferred(), other = harness({ session: () => late.promise })
  const selecting = other.select('私有节点')
  await other.logout()
  late.resolve(); await selecting
  assert.equal(other.access.authenticated, false)
  assert.equal(other.draft.value, '')
  assert.equal(other.shortcutNotice.value, '')
  other.dispose()
})
