<template>
  <Teleport to="body">
    <section v-if="open" id="hermes-chat" class="hermes-panel" role="dialog" aria-label="Hermes 对话" @keydown.esc="close">
      <header class="hermes-header">
        <span class="hermes-avatar" aria-hidden="true">H<span>✦</span></span>
        <div class="hermes-heading"><h2>Hermes 助手</h2><p><span class="hermes-dot" /> DeepSeek Flash</p></div>
        <button v-if="access.authenticated" class="hermes-icon" title="新建对话" aria-label="新建对话" :disabled="busy || chat.status === 'running'" @click="reset">＋</button>
        <button class="hermes-icon" title="收起对话，任务继续执行" aria-label="收起对话，任务继续执行" @click="close">×</button>
      </header>

      <div v-if="!access.secure" class="hermes-welcome"><h3>请通过 HTTPS 访问</h3><p>安全连接后即可使用 Hermes。</p></div>
      <form v-else-if="!access.authenticated" class="hermes-login" @submit.prevent="login">
        <span class="hermes-eyebrow">YOUR WORKSPACE ASSISTANT</span>
        <h3>你好，我是 Hermes。</h3><p>登录后，在这里继续你的对话。</p>
        <label for="hermes-password">平台运维口令</label>
        <input id="hermes-password" v-model="access.password" type="password" autocomplete="current-password" required placeholder="输入运维口令" />
        <button class="hermes-primary" :disabled="access.busy">{{ access.busy ? '正在登录…' : '登录并开始对话' }}</button>
        <p class="hermes-login-hint">与平台运维页面共用登录状态。</p>
        <p v-if="access.error" class="hermes-error" role="alert">{{ access.error }}</p>
      </form>

      <template v-else>
        <div ref="transcript" class="hermes-transcript" role="log" aria-label="对话记录" aria-live="polite" @scroll="trackScroll">
          <div v-if="!chat.messages.length" class="hermes-welcome">
            <span class="hermes-eyebrow">LET’S WORK TOGETHER</span><h3>有什么可以帮你？</h3>
            <p>排查问题、整理思路，或一起完成一项工作。</p>
            <button v-for="suggestion in suggestions" :key="suggestion" class="hermes-suggestion" :disabled="busy" @click="send(suggestion)">{{ suggestion }}<span aria-hidden="true">↗</span></button>
          </div>
          <article v-for="(message, index) in chat.messages" :key="index" class="hermes-message" :class="message.role">
            <span class="hermes-message-label">{{ message.role === 'user' ? '你' : 'Hermes' }}</span>
            <div v-if="message.text" class="hermes-message-text">{{ message.text }}</div>
            <span v-else-if="chat.status === 'running'" class="hermes-thinking">正在思考<span>…</span></span>
            <span v-else class="hermes-empty-reply">未生成回复</span>
          </article>
        </div>
        <div v-if="chat.error" class="hermes-error hermes-notice" role="alert">{{ chat.error }}</div>
        <div v-if="chat.status === 'running'" class="hermes-progress" role="status"><span>{{ chat.activity || '正在执行…' }} · 收起后继续</span><button :disabled="busy" @click="stop">停止任务</button></div>
        <form class="hermes-composer" @submit.prevent="send()">
          <textarea ref="composer" v-model="draft" rows="2" maxlength="8000" placeholder="向 Hermes 发送消息…" aria-label="聊天消息" :disabled="busy || chat.status === 'running'" @keydown.enter="onEnter" />
          <div class="hermes-composer-bottom"><span>Enter 发送 · Shift + Enter 换行</span><button class="hermes-send" type="submit" :disabled="busy || chat.status === 'running' || !draft.trim()" aria-label="发送消息">↑</button></div>
        </form>
        <footer class="hermes-footer"><span>AI 回复请核实 · 工具以 test 权限运行</span><button @click="logout">退出登录</button></footer>
      </template>
    </section>

    <button v-if="!open && backgroundNotice" class="hermes-background-notice" @click="toggle"><span :class="{ running: chat.status === 'running' }" aria-hidden="true" />{{ backgroundNotice }}</button>
    <button ref="launcher" class="hermes-launcher" :class="{ 'is-open': open }" :aria-expanded="open" aria-controls="hermes-chat" :aria-label="open ? '收起 Hermes 对话，任务继续执行' : (backgroundNotice || '打开 Hermes 对话')" :title="backgroundNotice || 'Hermes 助手'" @click="toggle">
      <svg v-if="!open" viewBox="0 0 28 28" fill="none" aria-hidden="true"><path d="M6 5.5h16a2.5 2.5 0 0 1 2.5 2.5v10a2.5 2.5 0 0 1-2.5 2.5h-8l-6 4v-4H6A2.5 2.5 0 0 1 3.5 18V8A2.5 2.5 0 0 1 6 5.5Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M10 10v6m8-6v6m-8-3h8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
      <span v-else aria-hidden="true">⌄</span><span v-if="chat.status === 'running'" class="hermes-busy-dot" />
    </button>
  </Teleport>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { useOperatorSession } from '../composables/useOperatorSession'
import { createHermesChat, hermesBackgroundNotice } from '../utils/hermesChat'

const key = 'watch-nanda-hermes-conversation'
const access = useOperatorSession()
const open = ref(false), busy = ref(false), draft = ref('')
const backgroundNotice = ref('')
const composer = ref(null), launcher = ref(null), transcript = ref(null)
const chat = ref({ id: '', messages: [], status: 'idle', error: '', activity: '' })
const suggestions = ['你能帮我做哪些事情？', '服务器负载过高，该如何排查？']
let follow = true, mounted = true, restored = false
function storage(value) { try { if (value) sessionStorage.setItem(key, value); else sessionStorage.removeItem(key) } catch {} }
const client = createHermesChat({ changed(value) {
  if (!mounted) return
  backgroundNotice.value = hermesBackgroundNotice(chat.value, value, open.value, backgroundNotice.value)
  chat.value = value; storage(value.id)
  if (value.error.includes('登录') && !value.id) access.authenticated = false
  if (follow) nextTick(() => { if (transcript.value) transcript.value.scrollTop = transcript.value.scrollHeight })
} })
client.setActive(false)
function trackScroll() { const el = transcript.value; follow = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 60 }
async function restore() {
  if (restored) return
  restored = true
  try { await client.restore(sessionStorage.getItem(key)) } catch {}
}
async function toggle() {
  if (open.value) return close()
  open.value = true
  backgroundNotice.value = ''
  try { await api.opsSession(); if (mounted) access.authenticated = true } catch (e) { if (mounted) access.handleError(e) }
  if (!mounted || !open.value) return
  if (access.authenticated) { await restore(); client.setActive(true) }
  nextTick(() => composer.value?.focus())
}
function close() {
  open.value = false; access.password = ''
  backgroundNotice.value = hermesBackgroundNotice(chat.value, chat.value, false, backgroundNotice.value)
  client.setActive(false); nextTick(() => launcher.value?.focus())
}
async function login() { await access.login(); if (access.authenticated && mounted) { await restore(); client.setActive(true); nextTick(() => composer.value?.focus()) } }
async function send(text = draft.value) {
  if (busy.value || !text.trim() || chat.value.status === 'running') return
  busy.value = true; follow = true
  try { await client.send(text); if (chat.value.messages.at(-2)?.text === text.trim()) draft.value = '' }
  finally { busy.value = false }
}
async function stop() { busy.value = true; try { await client.stop() } finally { busy.value = false } }
async function reset() { busy.value = true; try { await client.reset(); follow = true } finally { busy.value = false } }
async function logout() { client.clear(); draft.value = ''; restored = true; await access.logout() }
function onEnter(event) { if (!event.shiftKey && !event.isComposing) { event.preventDefault(); send() } }
watch(() => access.authenticated, value => { if (!value) { client.clear(); draft.value = ''; restored = false } })
onBeforeUnmount(() => { mounted = false; client.dispose(); access.password = '' })
</script>

<style scoped>
.hermes-launcher { position:fixed; right:24px; bottom:max(22px,env(safe-area-inset-bottom)); z-index:850; display:grid; place-items:center; width:58px; height:58px; border:1px solid #ffffff38; border-radius:20px; color:#fff; background:linear-gradient(145deg,#317cda,#4251bd); box-shadow:0 8px 26px #152c7150,inset 0 1px #ffffff35; cursor:pointer; transition:transform .18s,box-shadow .18s; }
.hermes-launcher:hover { transform:translateY(-3px); box-shadow:0 12px 30px #152c7160; }
.hermes-launcher svg { width:29px; height:29px; }
.hermes-launcher>span:first-child { font-size:32px; line-height:1; margin-top:-8px; }
.hermes-busy-dot { position:absolute; top:3px; right:3px; width:10px; height:10px; border:2px solid var(--surface); border-radius:50%; background:#6de0bc; }
.hermes-background-notice { position:fixed; right:94px; bottom:max(33px,calc(env(safe-area-inset-bottom) + 11px)); z-index:850; display:flex; align-items:center; gap:8px; max-width:calc(100vw - 118px); padding:9px 13px; border:1px solid var(--border); border-radius:11px; background:var(--surface); color:var(--text-secondary); box-shadow:var(--shadow-hover); font:12px/1.5 system-ui,-apple-system,"Microsoft YaHei",sans-serif; cursor:pointer; }
.hermes-background-notice>span { width:6px; height:6px; flex-shrink:0; border-radius:50%; background:var(--accent); }
.hermes-background-notice>span.running { background:var(--success); }
.hermes-background-notice:focus-visible { outline:3px solid var(--focus); outline-offset:3px; }
.hermes-panel { position:fixed; right:24px; bottom:92px; z-index:850; display:flex; flex-direction:column; width:min(420px,calc(100vw - 32px)); height:min(640px,calc(100dvh - 116px)); min-height:240px; border:1px solid var(--border); border-radius:20px; color:var(--text); background:var(--surface); box-shadow:var(--shadow-modal); overflow:hidden; font:14px/1.6 system-ui,-apple-system,"Microsoft YaHei",sans-serif; }
.hermes-panel * { box-sizing:border-box; }
.hermes-panel button,.hermes-panel input,.hermes-panel textarea { font:inherit; }
.hermes-panel button { cursor:pointer; }
.hermes-panel button:disabled { cursor:not-allowed; opacity:.5; }
.hermes-panel button:focus-visible,.hermes-launcher:focus-visible { outline:3px solid var(--focus); outline-offset:3px; }
.hermes-header { display:flex; align-items:center; gap:12px; padding:18px 20px; border-bottom:1px solid var(--border-soft); flex-shrink:0; }
.hermes-avatar { display:grid; place-items:center; position:relative; width:40px; height:40px; border-radius:13px; font-size:21px; font-weight:700; color:var(--accent); background:var(--accent-soft); border:1px solid var(--accent-border); }
.hermes-avatar span { position:absolute; right:-3px; top:-7px; font-size:17px; }
.hermes-heading { flex:1; }
.hermes-heading h2 { margin:0; font-size:15px; font-weight:650; }
.hermes-heading p { display:flex; align-items:center; gap:6px; margin:1px 0 0; color:var(--text-muted); font-size:11px; }
.hermes-dot { width:5px; height:5px; border-radius:50%; background:var(--success); }
.hermes-icon { display:grid; place-items:center; width:28px; height:28px; padding:0; background:transparent; border:0; border-radius:7px; color:var(--text-muted); font-size:24px!important; }
.hermes-icon:hover { background:var(--surface-muted); color:var(--text); }
.hermes-transcript { flex:1; min-height:0; overflow:auto; overscroll-behavior:contain; padding:18px 20px; }
.hermes-welcome { padding:20px 3px; }
.hermes-eyebrow { font-size:9px; letter-spacing:1.6px; color:var(--accent); font-weight:700; }
.hermes-welcome h3,.hermes-login h3 { font-size:24px; line-height:1.4; letter-spacing:-.6px; font-weight:650; margin:10px 0; }
.hermes-welcome p,.hermes-login p { color:var(--text-muted); font-size:13px; margin:0 0 22px; }
.hermes-suggestion { display:flex; width:100%; text-align:left; justify-content:space-between; gap:12px; margin-top:10px; padding:12px 14px; border:1px solid var(--border); border-radius:10px; color:var(--text-secondary); background:var(--surface-soft); font-size:12px!important; }
.hermes-suggestion:hover { color:var(--accent); border-color:var(--accent-border); background:var(--accent-soft); }
.hermes-message { margin-bottom:20px; max-width:100%; }
.hermes-message-label { display:block; font-size:10px; color:var(--text-muted); margin-bottom:6px; }
.hermes-message-text { white-space:pre-wrap; overflow-wrap:anywhere; font-size:13px; line-height:1.8; }
.hermes-message.user { margin-left:32px; }
.hermes-message.user .hermes-message-label { text-align:right; }
.hermes-message.user .hermes-message-text { padding:11px 14px; border:1px solid var(--accent-border); background:var(--accent-soft); border-radius:13px 13px 3px 13px; }
.hermes-thinking,.hermes-empty-reply { color:var(--text-muted); font-size:12px; }
.hermes-composer { margin:0 16px; padding:10px 12px 8px; flex-shrink:0; border:1px solid var(--border); border-radius:13px; background:var(--surface-soft); }
.hermes-composer:focus-within { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent-soft); }
.hermes-composer textarea { width:100%; max-height:110px; padding:0; border:0; resize:vertical; outline:none; background:transparent; color:var(--text); font-size:13px; }
.hermes-composer textarea::placeholder { color:var(--text-muted); }
.hermes-composer-bottom { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.hermes-composer-bottom>span { color:var(--text-muted); font-size:9px; }
.hermes-send { width:29px; height:29px; border:0; border-radius:9px; color:var(--on-primary); background:var(--primary-bg); font-size:21px!important; }
.hermes-footer { display:flex; justify-content:space-between; gap:8px; padding:10px 18px 12px; flex-shrink:0; color:var(--text-muted); font-size:9px; }
.hermes-footer button { border:0; background:none; padding:0; color:var(--text-muted); font-size:10px; }
.hermes-login { padding:32px 26px; overflow:auto; }
.hermes-login label { display:block; margin-bottom:7px; color:var(--text-secondary); font-size:12px; }
.hermes-login input { width:100%; padding:11px 12px; margin-bottom:14px; border:1px solid var(--border); border-radius:9px; color:var(--text); background:var(--surface-soft); }
.hermes-primary { width:100%; padding:10px; border:0; border-radius:9px; background:var(--primary-bg); color:var(--on-primary); }
.hermes-login-hint { margin:12px 0!important; font-size:11px!important; text-align:center; }
.hermes-error { color:var(--danger)!important; font-size:12px!important; }
.hermes-notice { padding:8px 18px; max-height:80px; overflow:auto; }
.hermes-progress { display:flex; justify-content:space-between; gap:8px; padding:7px 18px; color:var(--text-muted); font-size:10px; }
.hermes-progress button { border:0; color:var(--accent); background:none; padding:0; font-size:10px; }
@media(max-width:600px) { .hermes-launcher { right:16px; bottom:max(16px,env(safe-area-inset-bottom)); width:52px; height:52px; border-radius:17px; } .hermes-background-notice { right:78px; bottom:max(24px,calc(env(safe-area-inset-bottom) + 8px)); max-width:calc(100vw - 94px); font-size:11px; } .hermes-panel { right:8px; bottom:80px; width:calc(100vw - 16px); height:calc(100dvh - 96px); border-radius:16px; } .hermes-header { padding:13px 16px; } }
@media(prefers-reduced-motion:reduce) { .hermes-launcher { transition:none; } }
</style>
