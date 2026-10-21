import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import * as Vue from 'vue'
import { compile } from '@vue/compiler-dom'
import { parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'

function harness(detail = false) {
  const calls = [], events = []
  const filename = detail ? 'NodeDetailModal.vue' : 'AddServerModal.vue'
  const { descriptor } = parse(readFileSync(new URL('../src/components/'+filename, import.meta.url), 'utf8'))
  const context = {
    ref: Vue.ref, computed: Vue.computed, onMounted() {}, onBeforeUnmount() {},
    defineProps: () => ({ serverId: 42, groups: [] }), defineEmits: () => (...args) => events.push(args),
    useRouter: () => ({ push() {} }), useServers: () => ({ servers: Vue.ref([{ id: 42, collection_mode: 'agent' }]) }),
    ageLabel: () => '', statusLabel: () => '', location: { protocol: 'https:' }, window: { location: { protocol: 'https:' } },
    api: { addServer: async payload => { calls.push({ ...payload }); return { data: { success: true, server_id: 42 } } } },
  }
  vm.createContext(context)
  const names = detail ? 'node,tab,form,saving,error,groups,metrics,close,backdrop,goOps,save,ageLabel,statusLabel'
    : 'dialog,saving,errorMessage,form,closeModal,trapFocus,onBackdropClick,submit'
  vm.runInContext(descriptor.scriptSetup.content.replace(/^import .*$/gm, '')+'\nthis.bindings = {'+names+'}', context)
  const bindings = context.bindings
  Object.assign(bindings.form.value, { hostname: 'test-node', ip_address: '114.212.173.212', group_name: '', ssh_user: 'test', ssh_port: 10064, collection_mode: 'agent', ssh_password: 'test-only-password' })
  if (detail) bindings.tab.value = 'settings'
  const render = new Function('Vue', compile(descriptor.template.content, { mode: 'function', prefixIdentifiers: true }).code)(Vue)
  return { ...bindings, calls, events, async html() {
    const app = Vue.createSSRApp({ setup: () => ({ ...bindings, ...(!detail ? { groups: [] } : {}), serverId: 42 }), render })
    for (const name of ['NodeAlertToggle', 'AgentHealthPanel']) app.component(name, { render: () => null })
    app.component('router-link', { render: () => null })
    return renderToString(app)
  } }
}

for (const mode of ['agent', 'ssh']) {
  test('adding a '+mode+' node exposes an editable nondefault SSH port', async () => {
    const h = harness();h.form.value.collection_mode = mode
    const html = await h.html()
    const input = html.match(/<input[^>]*id="add-ssh-port"[^>]*>/)?.[0]
    assert.ok(input)
    assert.match(input, /value="10064"/)
    assert.match(input, /min="1"/);assert.match(input, /max="65535"/)
    assert.doesNotMatch(input, /disabled/)
    assert.equal(html.includes('id="add-ssh-password"'), mode === 'ssh')
  })
  test('editing a '+mode+' node also exposes its SSH connection port', async () => {
    const h = harness(true);h.form.value.collection_mode = mode
    const html = await h.html()
    assert.match(html, /SSH 端口<input[^>]*value="10064"[^>]*>/)
    assert.equal(html.includes('type="password"'), mode === 'ssh')
  })
}

test('Agent creation submits the chosen numeric port without a password, preserving it across mode changes', async () => {
  const h = harness()
  h.form.value.ssh_port = '10064'
  h.form.value.collection_mode = 'ssh';h.form.value.collection_mode = 'agent'
  await h.submit()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].ssh_port, 10064)
  assert.equal(h.calls[0].ssh_user, 'test')
  assert.equal(h.calls[0].collection_mode, 'agent')
  assert.equal(Object.hasOwn(h.calls[0], 'ssh_password'), false)
  assert.equal(h.events[0][1].server_id, 42)
  h.form.value.collection_mode = 'ssh';h.form.value.ssh_password = 'test-only-password'
  await h.submit()
  assert.equal(h.calls[1].ssh_port, 10064)
  assert.equal(h.calls[1].ssh_password, 'test-only-password')
})

test('both creation modes reject invalid ports before sending a request', async () => {
  for (const mode of ['agent', 'ssh']) {
    const h = harness();h.form.value.collection_mode = mode
    for (const port of ['', 0, -1, 1.5, 65536, 'invalid']) {
      h.form.value.ssh_port = port;await h.submit()
      assert.equal(h.calls.length, 0)
      assert.match(h.errorMessage.value, /1–65535/)
    }
    for (const port of [1, 22, 65535]) {
      h.form.value.ssh_port = port;await h.submit()
      assert.equal(h.calls.at(-1).ssh_port, port)
    }
  }
})
