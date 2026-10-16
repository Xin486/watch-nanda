<template>
  <span class="node-bell-wrap" @click.stop>
    <button type="button" class="node-bell" :class="{ enabled }" :disabled="busy" :aria-pressed="enabled" :aria-label="`${node.hostname}邮件提醒${enabled ? '已开启，点击关闭' : '已关闭，点击开启'}`" :title="enabled ? '邮件提醒开启（受全局邮件设置控制）' : '邮件提醒关闭'" @click.stop="toggle">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" stroke-linecap="round" stroke-linejoin="round"/><path v-if="!enabled" d="M3 3l18 18" stroke-linecap="round"/></svg>
    </button>
    <span v-if="error" class="bell-error" role="alert">{{ error }}</span>
  </span>
</template>
<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'
const props = defineProps({ node: { type: Object, required: true } })
const busy = ref(false), error = ref('')
const enabled = computed(() => props.node.email_alert_enabled !== false)
async function toggle() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const { data } = await api.setNodeNotifications(props.node.id, !enabled.value)
    props.node.email_alert_enabled = data.email_alert_enabled
    window.dispatchEvent(new CustomEvent('servers-refresh'))
  } catch { error.value = '提醒设置保存失败，请重试' }
  finally { busy.value = false }
}
</script>
<style scoped>
.node-bell-wrap{position:relative;display:inline-flex}.node-bell{display:grid;place-items:center;width:33px;height:33px;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;color:#94a3b8;cursor:pointer}.node-bell.enabled{color:#2563eb;background:#eff6ff;border-color:#dbeafe}.node-bell:hover{border-color:#93c5fd}.node-bell svg{width:17px;height:17px}.node-bell:focus-visible{outline:3px solid #93c5fd;outline-offset:2px}.bell-error{position:absolute;right:0;top:38px;width:180px;padding:9px;border-radius:8px;background:#fff1f2;color:#be123c;box-shadow:0 4px 20px #0f172a20;z-index:5;font-size:12px}
</style>
