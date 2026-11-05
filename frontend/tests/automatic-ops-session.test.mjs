import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { ref, reactive } from 'vue'

test('operator pages connect automatically and retries never submit an operator password', async () => {
  const mounts = [], unmounts = [], calls = []
  const context = { ref, reactive, location: { protocol: 'https:' },
    onMounted: fn => mounts.push(fn), onBeforeUnmount: fn => unmounts.push(fn),
    api: { opsSession: async () => calls.push('session'), opsLogin: () => { throw Error('no password login') } },
  }
  vm.createContext(context)
  const source = fs.readFileSync(new URL('../src/composables/useOperatorSession.js', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '').replace('export function ', 'function ')
  vm.runInContext(source + '\nthis.access=useOperatorSession()', context)
  await mounts[0]()
  assert.equal(context.access.authenticated, true)
  assert.deepEqual(calls, ['session'])
  context.access.authenticated = false
  await context.access.login()
  assert.equal(context.access.authenticated, true)
  assert.deepEqual(calls, ['session', 'session'])
  unmounts.forEach(fn => fn())
})
