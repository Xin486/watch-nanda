import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue'

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const changedKey = { ok:false, code:'host_key_changed', fingerprint:'SHA256:new-key', previous_fingerprint:'SHA256:old-key', host:'192.0.2.7', port:10022 }
function accessHarness({ protocol = 'https:', action, saved = true, selectable = true, api = {} } = {}) {
  const route = reactive({ params: { id: '7' }, query: action ? { action } : {} })
  const servers = ref([{ id: 7, hostname: 'test-node', ssh_user: 'test', has_ssh_password: saved }])
  const mounts = [], unmounts = [], redirects = [], calls = []
  const context = {
    computed, reactive, ref, watch, location: { protocol },
    onMounted: fn => mounts.push(fn), onBeforeUnmount: fn => unmounts.push(fn),
    useRoute: () => route, useRouter: () => ({ replace: target => redirects.push(target) }),
    useServers: () => ({ servers, subscribe() {}, patchServer(patch) { servers.value = servers.value.map(s => s.id === patch.id ? { ...s, ...patch } : s) } }),
    window: { dispatchEvent() {} }, CustomEvent: class {},
    api: {
      opsSession: async () => ({}), opsLogin: async () => ({}), opsLogout: async () => ({}),
      sshTest: async (id, payload) => { calls.push({ id, payload }); return { data: { ok: true, message: 'ok' } } },
      trustHost: async () => ({}), ...api,
    },
  }
  vm.createContext(context)
  const code = fs.readFileSync(new URL('../src/composables/useNodeAccess.js', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace('export function ', 'function ')
  vm.runInContext(code + '\nthis.create = useNodeAccess', context)
  const scope = effectScope()
  const access = scope.run(() => context.create({ selectable }))
  return { access, route, servers, calls, redirects, mount: () => mounts[0](), cleanup: () => { unmounts.forEach(fn => fn()); scope.stop() } }
}

test('changed host key prompts without saving; explicit confirmation saves both fingerprints and retries SSH', async () => {
  const trusts=[], checks=[]
  const h=accessHarness({api:{sshTest:async()=>{checks.push(1);return {data:checks.length===1?changedKey:{ok:true}}},trustHost:async(...args)=>{trusts.push(args)}}})
  try {
    await h.mount();await h.access.testConnection()
    assert.equal(h.access.trustDialogOpen,true);assert.equal(trusts.length,0)
    await h.access.confirmHostChange()
    assert.equal(trusts.length,1);assert.equal(trusts[0][0],7);assert.equal(trusts[0][1],changedKey.fingerprint)
    assert.deepEqual({...trusts[0][2]},{previous_fingerprint:changedKey.previous_fingerprint,expected_host:changedKey.host,expected_port:10022})
    assert.equal(checks.length,2);assert.equal(h.access.check.ok,true);assert.equal(h.access.trustDialogOpen,false)
  }finally{h.cleanup()}
})

test('cancel changed host key leaves trust unchanged, while confirmed terminal errors resume the terminal', async () => {
  let trusted=0,resumed=0
  const h=accessHarness({api:{trustHost:async()=>{trusted++}}})
  try {
    await h.mount();h.access.connectionResult(changedKey,async()=>{resumed++})
    h.access.cancelHostTrust();await h.access.confirmHostChange()
    assert.equal(trusted,0);assert.equal(resumed,0)
    h.access.connectionResult(changedKey,async()=>{resumed++})
    await h.access.confirmHostChange()
    assert.equal(trusted,1);assert.equal(resumed,1);assert.equal(h.calls.length,0)
  }finally{h.cleanup()}
})

test('logout or a changed node address invalidates host confirmation and late trust responses cannot reconnect', async () => {
  const pending=deferred();let resumed=0
  const h=accessHarness({api:{trustHost:()=>pending.promise}})
  try {
    await h.mount();h.access.connectionResult(changedKey,async()=>{resumed++})
    const trusting=h.access.confirmHostChange();await h.access.logout();pending.resolve({});await trusting
    assert.equal(resumed,0);assert.equal(h.access.trustDialogOpen,false)
    h.access.authenticated=true;h.access.connectionResult(changedKey)
    h.servers.value=[{...h.servers.value[0],ip_address:'192.0.2.20'}];await nextTick()
    assert.equal(h.access.trustDialogOpen,false);assert.equal(h.access.check,null)
  }finally{h.cleanup()}
})

test('a rejected host-key update keeps the confirmation visible and does not reconnect', async () => {
  const h=accessHarness({api:{trustHost:async()=>{throw {response:{status:409,data:{detail:'指纹再次变化'}}}}}})
  try {
    await h.mount();h.access.connectionResult(changedKey);await h.access.confirmHostChange()
    assert.equal(h.access.error,'指纹再次变化');assert.equal(h.access.trustDialogOpen,true);assert.equal(h.calls.length,0);assert.equal(h.access.busy,false)
  }finally{h.cleanup()}
})

test('existing-node SSH action checks saved credentials once without starting a deployment', async () => {
  const h = accessHarness({ action: 'check' })
  try {
    await h.mount()
    await nextTick()
    assert.equal(h.access.authenticated, true)
    assert.equal(h.calls.length, 1)
    assert.equal(h.calls[0].id, 7)
    assert.equal(h.calls[0].payload.use_saved, true)
    h.servers.value = [{ ...h.servers.value[0], cpu_percent: 12 }]
    await nextTick()
    assert.equal(h.calls.length, 1)
  } finally { h.cleanup() }
})

test('manual credentials request verified synchronization and clear on logout', async () => {
  const h = accessHarness({ action: 'check', saved: false })
  try {
    await h.mount()
    assert.equal(h.calls.length, 0)
    assert.equal(await h.access.testConnection(), false)
    h.access.credentials.username = 'operator'
    h.access.credentials.password = 'temporary-test-only'
    await nextTick()
    assert.equal(await h.access.testConnection(), true)
    assert.equal(h.calls[0].payload.use_saved, false)
    assert.equal(h.calls[0].payload.save_credentials, true)
    assert.equal(h.calls[0].payload.username, 'operator')
    await h.access.logout()
    assert.equal(h.access.authenticated, false)
    assert.equal(h.access.credentials.password, '')
  } finally { h.cleanup() }
})

test('verified credential sync patches the shared node, selects saved credentials and clears plaintext', async () => {
  const h=accessHarness({saved:false,api:{sshTest:async()=>({data:{ok:true,credentials_saved:true,credentials_message:'synced',server:{id:7,ssh_user:'manager',has_ssh_password:true,status:'pending',collection_status:'pending',data_age_seconds:null}}})}})
  try {
    await h.mount()
    h.access.credentials.username='manager';h.access.credentials.password='temporary-test-only'
    assert.equal(await h.access.testConnection(),true)
    await nextTick()
    assert.equal(h.access.useSaved,true);assert.equal(h.access.credentials.password,'')
    assert.equal(h.access.node.ssh_user,'manager');assert.equal(h.access.node.status,'pending')
    assert.equal(h.access.check.credentials_saved,true)
    assert.equal(h.access.payload().use_saved,true)
  } finally {h.cleanup()}
})

test('late SSH result cannot restore credentials or results after logout', async () => {
  const request = deferred()
  const h = accessHarness({ api: { sshTest: () => request.promise } })
  try {
    await h.mount()
    const pending = h.access.testConnection()
    await h.access.logout()
    request.resolve({ data: { ok: true, message: 'late result' } })
    assert.equal(await pending, false)
    assert.equal(h.access.check, null)
    assert.equal(h.access.testing, false)
  } finally { h.cleanup() }
})

test('late session response cannot sign the page back in after logout', async () => {
  const request = deferred()
  const h = accessHarness({ api: { opsSession: () => request.promise } })
  try {
    const mounting = h.mount()
    await h.access.logout()
    request.resolve({})
    await mounting
    assert.equal(h.access.authenticated, false)
  } finally { h.cleanup() }
})

test('changing an operations node clears manual secrets and targets the operations route', async () => {
  const h = accessHarness({ saved: false })
  try {
    await h.mount()
    h.access.credentials.password = 'temporary-test-only'
    h.servers.value.push({ id: 8, hostname: 'other', ssh_user: 'manager', has_ssh_password: true })
    h.access.selectedId = '8'
    await nextTick()
    assert.equal(h.access.credentials.password, '')
    assert.equal(h.access.credentials.username, 'manager')
    assert.equal(h.redirects.at(-1), '/nodes/8/operations')
  } finally { h.cleanup() }
})

test('new-server deployment access keeps its target and HTTP cannot authenticate or test SSH', async () => {
  let logins = 0, sessions = 0
  const h = accessHarness({ protocol: 'http:', selectable: false, api: { opsLogin: async () => logins++, opsSession: async () => sessions++ } })
  try {
    await h.mount()
    await h.access.login()
    assert.equal(await h.access.testConnection(), false)
    h.access.selectedId = '8'
    await nextTick()
    assert.equal(h.redirects.length, 0)
    assert.equal(logins, 0)
    assert.equal(sessions, 0)
    assert.equal(h.calls.length, 0)
  } finally { h.cleanup() }
})

test('new Agent node opens deployment, while adding an SSH node keeps the current page', () => {
  const targets = []
  const context = {
    computed, ref, watch: () => {}, onMounted: () => {}, onUnmounted: () => {},
    useRoute: () => ({ path: '/dashboard' }), useRouter: () => ({ push: path => targets.push(path) }),
    useServers: () => ({ servers: ref([]), subscribe() {} }),
    localStorage: { getItem: () => null }, window: { dispatchEvent() {} }, CustomEvent: class {},
    setTimeout: () => 1, clearTimeout() {},
  }
  vm.createContext(context)
  const source = fs.readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  vm.runInContext(source + '\nthis.created = onServerCreated', context)
  context.created({ server_id: 71, collection_mode: 'agent' })
  context.created({ server_id: 72, collection_mode: 'ssh' })
  assert.deepEqual(targets, ['/servers/new/71/agent'])
})

test('new-node deployment checks SSH before submitting one job and resumes its logs', async () => {
  const calls = [], timers = [], unmounts = []
  const access = reactive({ authenticated: true, node: { id: 71, ip_address: '192.168.1.71' },
    credentials: { password: 'temporary-test-only' }, busy: false,
    validInput: () => true, testConnection: async () => { calls.push('ssh'); return true },
    payload: () => ({ use_saved: true }), handleError: e => { throw e },
  })
  const context = {
    computed, ref, watch, onBeforeUnmount: fn => unmounts.push(fn),
    defineProps: () => ({ redeploy: false }),
    useNodeAccess: () => access,
    setInterval: fn => { timers.push(fn); return fn }, clearInterval() {},
    window: { dispatchEvent() {} }, CustomEvent: class {},
    api: {
      deploymentJobs: async id => { assert.equal(id, 71); return { data: [] } },
      deployAgent: async (id, payload) => { calls.push('deploy'); assert.equal(id, 71); assert.equal(payload.use_saved, true); return { data: { id: 'job-71', status: 'running', logs: [] } } },
      deploymentJob: async id => { assert.equal(id, 'job-71'); return { data: { id, status: 'success', logs: ['Agent ready'] } } },
    },
  }
  vm.createContext(context)
  const source = fs.readFileSync(new URL('../src/views/AgentDeploy.vue', import.meta.url), 'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const scope = effectScope()
  scope.run(() => vm.runInContext(source + '\nthis.exposed = {deploy,job,pollJob}', context))
  try {
    await nextTick()
    await context.exposed.deploy()
    assert.deepEqual(calls, ['ssh', 'deploy'])
    assert.equal(access.credentials.password, '')
    assert.equal(context.exposed.job.value.status, 'running')
    assert.equal(timers.length, 1)
    await context.exposed.pollJob()
    assert.equal(context.exposed.job.value.status, 'success')
    assert.equal(context.exposed.job.value.logs[0], 'Agent ready')
    await context.exposed.deploy()
    assert.equal(calls.length, 2)
  } finally { unmounts.forEach(fn => fn()); scope.stop() }
})

test('redeployment is explicit, uses the existing Agent ID and ignores old deployment success', async () => {
  const calls = [], unmounts = []
  const access = reactive({ authenticated: true, node: { id: 7, collection_mode: 'agent', ip_address: '192.168.1.7' },
    credentials: { password: 'test-only' }, busy: false, validInput: () => true,
    testConnection: async () => { calls.push('ssh'); return true }, payload: () => ({ use_saved: true }), handleError: e => { throw e },
  })
  const context = {
    computed, ref, watch, defineProps: () => ({ redeploy: true }), onBeforeUnmount: fn => unmounts.push(fn),
    useNodeAccess: options => { assert.equal(options.selectable, false); return access },
    setInterval: () => 1, clearInterval() {}, window: { dispatchEvent() {} }, CustomEvent: class {},
    api: {
      deploymentJobs: async () => ({ data: [{ id: 'old-deploy', kind: 'deploy', status: 'success' }] }),
      deployAgent: () => { throw Error('New-server API must not be used') },
      redeployAgent: async (id, payload) => { calls.push('redeploy'); assert.equal(id, 7); assert.equal(payload.use_saved, true); return { data: { id: 'repair-7', status: 'running', kind: 'redeploy' } } },
      deploymentJob: async () => ({ data: { id: 'repair-7', status: 'success', kind: 'redeploy' } }),
    },
  }
  vm.createContext(context)
  const source = fs.readFileSync(new URL('../src/views/AgentDeploy.vue', import.meta.url), 'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const scope = effectScope()
  scope.run(() => vm.runInContext(source + '\nthis.exposed = {deploy,job,pollJob}', context))
  try {
    await nextTick()
    assert.deepEqual(calls, [])
    assert.equal(context.exposed.job.value, null)
    await context.exposed.deploy()
    assert.deepEqual(calls, ['ssh', 'redeploy'])
    assert.equal(access.credentials.password, '')
    await context.exposed.pollJob()
    assert.equal(context.exposed.job.value.status, 'success')
    access.node.collection_mode = 'ssh'
    await nextTick()
    await context.exposed.deploy()
    assert.equal(calls.length, 2)
  } finally { unmounts.forEach(fn => fn()); scope.stop() }
})
