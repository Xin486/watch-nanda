import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { ref } from 'vue'

function harness(api = {}) {
  const mounts = [], unmounts = [], writes = []
  const context = {
    ref, onMounted: fn => mounts.push(fn), onBeforeUnmount: fn => unmounts.push(fn),
    usePlatformSettings: () => ({ platform: ref({ branding: {} }), refreshPlatform: async () => {} }),
    localStorage: { setItem: (...args) => writes.push(args) }, window: { dispatchEvent() {} }, Event: class {},
    api: { opsSession: async () => ({}), agentSettings: async () => ({ data: { report_url: '', routes: [] } }),
      hermesSettings: async () => ({ data: { enabled: false, api_base: 'https://provider.example/v1', model: 'model', has_api_key: true } }), ...api },
  }
  vm.createContext(context)
  const source = fs.readFileSync(new URL('../src/components/PlatformSettings.vue', import.meta.url), 'utf8')
    .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  vm.runInContext(source + '\nthis.form = {load,saveHermes,ready,busy,apiKey,hermes,notice,failed}', context)
  return { ...context.form, mount: () => mounts[0](), cleanup: () => unmounts.forEach(fn => fn()), writes }
}

test('settings cannot save before loading, and blank keys are omitted while successful keys are cleared', async () => {
  const saved = []
  const h = harness({ saveHermesSettings: async payload => { saved.push(payload); return { data: { ...payload, has_api_key: true } } } })
  try {
    await h.saveHermes(); assert.equal(saved.length, 0)
    await h.mount(); assert.equal(h.ready.value, true)
    await h.saveHermes(); assert.equal('api_key' in saved[0], false)
    h.apiKey.value = 'temporary-test-key'; await h.saveHermes()
    assert.equal(saved[1].api_key, 'temporary-test-key'); assert.equal(h.apiKey.value, '')
    assert.equal(h.writes.length, 0)
  } finally { h.cleanup() }
})

test('failed saves retain unsaved input for retry and unmount clears private fields', async () => {
  const h = harness({ saveHermesSettings: async () => { throw { response: { data: { detail: '服务尚未就绪' } } } } })
  await h.mount(); h.apiKey.value = 'temporary-test-key'
  await h.saveHermes()
  assert.equal(h.apiKey.value, 'temporary-test-key'); assert.equal(h.failed.value, true)
  assert.equal(h.notice.value, '服务尚未就绪'); assert.equal(h.busy.value, false)
  h.cleanup(); assert.equal(h.apiKey.value, '')
})
