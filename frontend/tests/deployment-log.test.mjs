import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { effectScope, nextTick, reactive, ref, watch } from 'vue'
const flush = async () => { await nextTick(); await new Promise(setImmediate) }

test('logs follow new output, pause while reading earlier lines, and resume explicitly', async () => {
  const props = reactive({ lines: ['first'], state: 'live' })
  const context = { ref, watch, nextTick, defineProps: () => props }
  vm.createContext(context)
  const source = fs.readFileSync(new URL('../src/components/DeploymentLog.vue', import.meta.url), 'utf8')
    .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const scope = effectScope()
  scope.run(() => vm.runInContext(source + '\nthis.h = {pane, following, scrolled, resume}', context))
  const h = context.h
  try {
    h.pane.value = { scrollHeight: 1000, clientHeight: 100, scrollTop: 0 }
    await flush()
    assert.equal(h.pane.value.scrollTop, 1000)
    h.pane.value.scrollTop = 100; h.scrolled()
    assert.equal(h.following.value, false)
    props.lines = ['first', 'second']; await flush()
    assert.equal(h.pane.value.scrollTop, 100)
    await h.resume(); assert.equal(h.pane.value.scrollTop, 1000)
    h.pane.value.scrollHeight = 1300
    props.lines = ['first', 'second', 'third']; await flush()
    assert.equal(h.pane.value.scrollTop, 1300)
  } finally { scope.stop() }
})
