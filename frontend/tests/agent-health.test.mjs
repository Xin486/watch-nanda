import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { compile } from '@vue/compiler-dom'
import { renderToString } from '@vue/server-renderer'

const source = readFileSync(new URL('../src/components/AgentHealthPanel.vue', import.meta.url), 'utf8')
const template = source.match(/<template>([\s\S]*?)<\/template>/)[1]
const render = new Function('Vue', compile(template, { mode: 'function' }).code)(Vue)
async function panel(state = {}) {
  const context = { serverId: 139, hideRedeploy: false, checking: false, result: null, error: '', labels: { online: '上报正常', offline: '上报不可用' }, timeLabel: () => '检测时间', checkAgent() { throw new Error('rendering must not start a check') }, ...state }
  const app = Vue.createSSRApp({ setup: () => context, render })
  app.component('router-link', { props: ['to'], setup: (props, { slots }) => () => Vue.h('a', { href: props.to }, slots.default()) })
  return renderToString(app)
}
for (const [name, state] of [
  ['before checking', {}],
  ['while checking', { checking: true }],
  ['after a healthy result', { result: { status: 'online', message: '上报正常' } }],
  ['after an unavailable result', { result: { status: 'offline', message: '上报不可用' } }],
  ['after a failed health request', { error: '检测失败' }],
]) {
  test('Agent redeployment entry remains available ' + name, async () => {
    const html = await panel(state)
    assert.match(html, /href="\/nodes\/139\/agent\/redeploy"/)
    assert.match(html, /重新部署 Agent/)
  })
}
test('the redeployment page can hide its own navigation entry', async () => {
  assert.doesNotMatch(await panel({ hideRedeploy: true }), /href="\/nodes\/139\/agent\/redeploy"/)
})
