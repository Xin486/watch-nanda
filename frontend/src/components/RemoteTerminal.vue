<template>
  <section class="terminal-panel">
    <div class="terminal-heading"><div><span class="terminal-indicator" :class="{ connected }"/><h2>运维命令行</h2><span>{{ connected ? '已连接' : connecting ? '连接中…' : '未连接' }}</span></div><button v-if="connected || connecting" class="terminal-button" @click="disconnect">断开连接</button><button v-else class="terminal-button" :disabled="access.testing" @click="connect">进入命令行 ↗</button></div>
    <div ref="element" class="terminal-canvas" :class="{ empty: !started }"><div v-if="!started" class="terminal-placeholder"><span>›_</span><strong>连接 {{ node.hostname }}</strong><p>使用左侧已配置的凭据，或填写用户名和密码后进入命令行。</p></div></div>
    <footer>关闭页面会结束连接 · 闲置 15 分钟自动断开 · 命令和输出不写入项目日志</footer>
  </section>
</template>
<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
const props = defineProps({ node: { type: Object, required: true }, access: { type: Object, required: true }, autoConnect: Boolean })
const emit = defineEmits(['busy'])
const element = ref(null), started = ref(false), connected = ref(false), connecting = ref(false)
let terminal, fit, observer, socket, pendingAuth, generation = 0, disposed = false
function resize() {
  if (socket?.readyState === WebSocket.OPEN && connected.value && terminal) socket.send(JSON.stringify({ type: 'resize', cols: Math.max(20, Math.min(300, terminal.cols)), rows: Math.max(5, Math.min(100, terminal.rows)) }))
}
function disconnect() {
  generation++
  if (pendingAuth?.password) pendingAuth.password = ''
  pendingAuth = null
  if (socket) {
    socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null
    socket.close()
    socket = null
  }
  observer?.disconnect()
  observer = null
  terminal?.dispose()
  terminal = null
  fit = null
  connected.value = connecting.value = started.value = false
  emit('busy', false)
}
async function connect() {
  if (connecting.value || props.access.testing || !props.access.validInput()) return
  props.access.error = ''
  disconnect()
  const current = generation
  pendingAuth = props.access.payload()
  connecting.value = started.value = true
  emit('busy', true)
  await nextTick()
  if (disposed || current !== generation || !element.value) return
  try {
    terminal = new Terminal({ cursorBlink: true, fontSize: 13, fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', scrollback: 2000, disableStdin: true, theme: { background: '#111b2b', foreground: '#dbe5f3', cursor: '#60a5fa', selectionBackground: '#334867' } })
    fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(element.value)
    fit.fit()
    const ws = new WebSocket(`wss://${location.host}/api/ops/nodes/${props.node.id}/terminal`)
    socket = ws
    ws.onopen = () => {
      if (socket !== ws) return
      ws.send(JSON.stringify(pendingAuth))
      if (pendingAuth?.password) pendingAuth.password = ''
      pendingAuth = null
    }
    ws.onmessage = event => {
      if (socket !== ws) return
      let data
      try { data = JSON.parse(event.data) } catch { return }
      if (data.type === 'connected') {
        connected.value = true
        connecting.value = false
        terminal.options.disableStdin = false
        terminal.focus()
        resize()
        window.dispatchEvent(new CustomEvent('servers-refresh'))
      } else if (data.type === 'output') terminal.write(data.data)
      else if (data.type === 'error') {
        props.access.error = data.message
        props.access.check = data
        terminal.writeln(`\r\n[${data.message}]`)
      } else if (data.type === 'closed') terminal.writeln(`\r\n[${data.message}]`)
    }
    ws.onerror = () => { props.access.error = '终端连接失败，请检查 HTTPS、运维登录和 SSH 凭据。' }
    ws.onclose = event => {
      if (socket !== ws) return
      if (pendingAuth?.password) pendingAuth.password = ''
      pendingAuth = null
      connected.value = connecting.value = false
      emit('busy', false)
      terminal.options.disableStdin = true
      terminal.writeln('\r\n[连接已断开]')
      socket = null
      if (event.code === 4401) props.access.handleError({ response: { status: 401, data: { detail: '运维登录已过期，请重新登录。' } } })
    }
    terminal.onData(data => {
      if (ws.readyState === WebSocket.OPEN && connected.value) for (let i = 0; i < data.length; i += 4096) ws.send(JSON.stringify({ type: 'input', data: data.slice(i, i + 4096) }))
    })
    observer = new ResizeObserver(() => { fit?.fit(); resize() })
    observer.observe(element.value)
  } catch {
    disconnect()
    props.access.error = '无法初始化命令行，请重试。'
  }
}
onMounted(() => { if (props.autoConnect && props.node.has_ssh_password && props.access.useSaved) connect() })
onBeforeUnmount(() => { disposed = true; disconnect() })
</script>
