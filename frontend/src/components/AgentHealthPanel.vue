<template>
  <section class="agent-health" aria-labelledby="agent-health-title">
    <div class="health-heading">
      <div><h3 id="agent-health-title">Agent 上报检查</h3><p>检查当前已保存节点的最近上报。正常每 5 分钟一次，无需 SSH。</p></div>
      <button type="button" class="health-button" :disabled="checking" @click="checkAgent">{{ checking ? '检测中…' : '检测 Agent 上报可用性' }}</button>
    </div>
    <div v-if="result" class="health-result" :class="result.status" role="status" aria-live="polite">
      <div class="result-heading"><strong>{{ labels[result.status] || '上报不可用' }}</strong><small>检测时间 {{ timeLabel(result.checked_at) }}</small></div>
      <p>{{ result.message }}</p>
      <dl><div><dt>最近接收</dt><dd>{{ timeLabel(result.last_received_at) }}</dd></div><div><dt>最近采样</dt><dd>{{ timeLabel(result.last_sampled_at) }}</dd></div></dl>
    </div>
    <p v-if="error" class="health-error" role="alert">{{ error }}</p>
    <div class="service-hints"><strong>在该节点服务器上检查</strong><div><span>服务状态</span><code>sudo systemctl status node-monitor-agent --no-pager</code></div><div><span>最近日志</span><code>sudo journalctl -u node-monitor-agent -n 30 --no-pager</code></div><p>检测依据平台已接收的数据，不会登录或重启节点。异常时可在运维命令行执行以上命令。</p></div>
  </section>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { api } from '../api'

const props = defineProps({ serverId: { type: Number, required: true } })
const checking = ref(false), result = ref(null), error = ref('')
const labels = { online: '上报正常', stale: '上报延迟', offline: '上报不可用', pending: '待上报', unconfigured: '未配置上报凭据' }
let controller, disposed = false
function timeLabel(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '暂无记录' }
async function checkAgent() {
  if (checking.value || disposed) return
  checking.value = true; error.value = ''; result.value = null
  controller = new AbortController()
  try {
    const { data } = await api.agentHealth(props.serverId, { signal: controller.signal })
    if (!disposed) result.value = data
  } catch (e) {
    if (!disposed) error.value = typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '检测失败，请检查平台连接后重试。'
  } finally { if (!disposed) checking.value = false }
}
onBeforeUnmount(() => { disposed = true; controller?.abort() })
</script>

<style scoped>
.agent-health{margin-top:20px;border:1px solid #dbe5f2;border-radius:12px;overflow:hidden;background:#fff}.health-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:17px 18px;background:#f8faff}.health-heading h3{margin:0;font-size:13px;color:#334155}.health-heading p{margin:6px 0 0;color:#64748b;font-size:11px;line-height:1.6}.health-button{flex-shrink:0;border:1px solid #bfdbfe;border-radius:8px;padding:9px 12px;background:#fff;color:#2563eb;font-size:12px;font-weight:600;cursor:pointer}.health-button:hover{background:#eff6ff}.health-button:disabled{opacity:.55;cursor:wait}.health-button:focus-visible{outline:3px solid #93c5fd;outline-offset:3px}.health-result{margin:14px 18px;padding:12px 14px;border-radius:8px;background:#fff7ed;color:#9a3412}.health-result.online{background:#f0fdf4;color:#166534}.health-result.offline,.health-result.unconfigured{background:#fff1f2;color:#9f1239}.result-heading{display:flex;align-items:center;justify-content:space-between;gap:10px}.result-heading strong{font-size:12px}.result-heading small{font-size:10px;opacity:.8}.health-result p{margin:8px 0;font-size:12px;line-height:1.7}.health-result dl{display:flex;gap:22px;flex-wrap:wrap;margin:8px 0 0;font-size:11px}.health-result dl>div{display:flex;gap:8px}.health-result dt{opacity:.8}.health-result dd{margin:0}.health-error{margin:14px 18px;color:#be123c;font-size:12px}.service-hints{padding:14px 18px;border-top:1px solid #edf2f7;color:#64748b}.service-hints>strong{display:block;font-size:11px;font-weight:600;margin-bottom:9px}.service-hints>div{display:flex;gap:12px;align-items:baseline;margin:7px 0;font-size:11px}.service-hints span{flex-shrink:0}.service-hints code{font:11px/1.6 ui-monospace,monospace;overflow-wrap:anywhere;color:#475569;user-select:all}.service-hints p{font-size:11px;line-height:1.6;margin:10px 0 0;color:#94a3b8}@media(max-width:700px){.health-heading,.result-heading{align-items:flex-start;flex-direction:column}.health-button{width:100%}.service-hints>div{flex-direction:column;gap:2px}}
</style>
