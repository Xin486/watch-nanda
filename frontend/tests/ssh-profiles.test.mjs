import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { effectScope, nextTick, reactive, ref, watch } from 'vue'

const flush = async () => { await nextTick(); await Promise.resolve(); await nextTick() }
function harness(overrides = {}) {
  const calls = [], unmounts = []
  const profile = { id:'first',name:'cluster',username:'root',priority:10,enabled:true,has_password:true }
  const access = reactive({ authenticated:true,error:'',handleError(e) { this.error=e.response?.data?.detail||'failed' } })
  const context = {
    reactive, ref, watch, useOperatorSession:() => access, onBeforeUnmount:fn => unmounts.push(fn), window:{confirm:() => true},
    api:{
      sshProfiles:async () => ({data:[profile]}),
      createSshProfile:async body => calls.push(['create',{...body}]),
      updateSshProfile:async (id,body) => calls.push(['update',id,{...body}]),
      deleteSshProfile:async id => calls.push(['delete',id]), ...overrides,
    },
  }
  vm.createContext(context)
  const script=readFileSync(new URL('../src/views/SshCredentials.vue',import.meta.url),'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm,'')
  const scope=effectScope()
  scope.run(() => vm.runInContext(script+'\nthis.h={save,edit,remove,form,profiles,loaded}',context))
  return {...context.h,access,profile,calls,cleanup(){unmounts.forEach(fn => fn());scope.stop()}}
}
test('common credential passwords are sent only when entered and clear after save',async () => {
  const h=harness()
  try {
    await flush(); assert.equal(h.loaded.value,true)
    Object.assign(h.form,{name:'new',username:'admin',password:'temporary',priority:2,enabled:true})
    await h.save()
    assert.equal(h.calls[0][0],'create');assert.equal(h.calls[0][1].password,'temporary');assert.equal(h.form.password,'')
    h.edit(h.profile);h.form.name='renamed';await h.save()
    assert.equal(h.calls[1][0],'update');assert.equal(h.calls[1][1],'first');assert.equal('password' in h.calls[1][2],false)
  } finally {h.cleanup()}
})
test('logout clears a typed password and prevents a late profile list from reappearing',async () => {
  let resolve
  const h=harness({sshProfiles:() => new Promise(r => {resolve=r})})
  try {
    h.form.password='temporary';h.access.authenticated=false;await flush()
    assert.equal(h.form.password,'')
    resolve({data:[h.profile]});await flush()
    assert.equal(h.profiles.value.length,0);assert.equal(h.loaded.value,false)
  } finally {h.cleanup()}
})
