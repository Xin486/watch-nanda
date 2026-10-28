<template>
  <div class="deployment-live-log">
    <div class="log-toolbar">
      <strong>{{ title }}</strong>
      <span role="status">{{ labels[state] || '等待进度' }}<template v-if="updatedAt"> · {{ new Date(updatedAt).toLocaleTimeString('zh-CN', { hour12: false }) }}</template></span>
      <button v-if="!following" type="button" @click="resume">跟随最新日志 ↓</button>
    </div>
    <pre ref="pane" class="deployment-log" tabindex="0" aria-label="部署日志" @scroll="scrolled">{{ lines?.join('\n') || '等待新的部署日志…' }}</pre>
  </div>
</template>
<script setup>
import { nextTick, ref, watch } from 'vue'
const props = defineProps({ lines: Array, title: { type: String, default: '部署日志' }, state: String, updatedAt: Number })
const pane = ref(null), following = ref(true)
const labels = { connecting: '正在连接实时进度', live: '实时更新', reconnecting: '正在重连，自动查询进度', fallback: '查询兜底，等待实时连接', complete: '任务已结束', error: '进度连接已停止' }
function scrolled() {
  const el = pane.value
  if (el) following.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}
async function resume() { following.value = true; await nextTick(); if (pane.value) pane.value.scrollTop = pane.value.scrollHeight }
watch(() => props.lines, async () => { if (following.value) await resume() }, { immediate: true })
</script>
<style scoped>
.deployment-live-log { min-width: 0; margin-top: 12px; }
.log-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-bottom: 8px; font-size: 12px; }
.log-toolbar span { color: var(--text-secondary); }
.log-toolbar button { margin-left: auto; border: 1px solid var(--border); border-radius: 6px; padding: 5px 9px; background: var(--surface); color: var(--text); cursor: pointer; }
.deployment-log { box-sizing: border-box; width: 100%; max-height: 340px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: 14px; border-radius: 10px; background: #101827; color: #dbe8fc; font: 12px/1.7 Consolas, monospace; }
</style>
