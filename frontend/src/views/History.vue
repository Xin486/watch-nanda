<template>
  <div class="history-page">
    <div class="panel">
      <h2>服务器历史负载</h2>
      <p class="desc">点击服务器右侧的“查看折线图”分析其资源波动趋势。</p>
      
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>主机名</th>
            <th>IP 地址</th>
            <th>分组</th>
            <th>在线状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="server in servers" :key="server.id">
            <td>{{ server.id }}</td>
            <td><strong>{{ server.hostname }}</strong></td>
            <td>{{ server.ip_address }}</td>
            <td><span class="group-tag">{{ server.group_name }}</span></td>
            <td>
              <span :class="['node-status', server.status]">
                ● {{ statusLabel(server.status) }}
              </span>
            </td>
            <td class="actions">
              <button class="btn-view" @click="openChart(server)">查看折线图</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <dialog ref="dialogRef" class="history-dialog" aria-labelledby="history-title" @cancel.prevent="closeChart" @click="onBackdrop">
      <header class="trend-header">
        <div class="trend-heading"><span class="trend-icon" aria-hidden="true">↗</span><div>
          <h3 id="history-title">{{ currentServer?.hostname }} <span>负载趋势</span></h3>
          <p>{{ currentServer?.ip_address }} <span>·</span> {{ currentServer?.group_name || '未分组' }}</p>
        </div></div>
        <div class="trend-header-actions"><ThemeToggle/><button class="trend-close" aria-label="关闭折线图" @click="closeChart">×</button></div>
      </header>
      <div class="trend-toolbar">
        <div class="time-filters" aria-label="历史时间范围">
          <button v-for="range in ranges" :key="range.days" :class="{active:currentRange === range.days}"
            :aria-pressed="currentRange === range.days" @click="changeRange(range.days)">{{ range.label }}</button>
        </div>
        <span class="aggregation-tag">{{ currentRange > 7 ? '按日平均' : '10 分钟快照' }} · 北京时间</span>
      </div>
      <div class="trend-summary">
        <div v-for="metric in METRICS" :key="metric.key" class="metric-summary" :style="{'--metric-color':metric.color}">
          <span class="metric-name"><i></i>{{ metric.name }}</span>
          <div class="metric-value">{{ isLoading ? '—' : percentage(history?.summary[metric.key]?.average) }}<small>平均</small></div>
          <span class="metric-peak">采样峰值 <strong>{{ isLoading ? '—' : percentage(history?.summary[metric.key]?.peak) }}</strong></span>
        </div>
      </div>
      <div class="chart-toolbar">
        <span>{{ history && !isLoading ? `${history.samples.toLocaleString()} 条快照 · ${history.points.length} 个展示点` : '资源使用率' }}</span>
        <label><input type="checkbox" v-model="fullRange" :disabled="isLoading || !history" @change="renderChart">显示完整时间范围</label>
      </div>
      <div class="chart-stage" :aria-busy="isLoading">
        <div ref="chartRef" class="chart-container" aria-label="CPU、内存及平均显存占用历史折线图"></div>
        <div v-if="isLoading" class="chart-state loading-state" role="status"><span class="trend-spinner"></span><strong>正在加载负载数据</strong><small>长期范围将按日聚合</small></div>
        <div v-else-if="historyError" class="chart-state history-message" role="alert"><span class="state-icon">!</span><strong>{{ historyError }}</strong><small>可直接重试，或选择其他时间范围</small><button @click="retryHistory">重新加载</button></div>
        <div v-else-if="history?.empty" class="chart-state history-message"><span class="state-icon">↗</span><strong>此时间范围暂无有效采集数据</strong><small>离线、过期或未采集的记录不会按 0% 绘制</small><button @click="retryHistory">刷新数据</button></div>
      </div>
      <footer class="trend-footer"><span>拖动下方滑块缩放 · 点击图例显示或隐藏曲线</span><span>缺失采集显示断点，显存占用不代表 GPU 计算利用率</span></footer>
      <p class="trend-period" v-if="history && !isLoading">查询范围：{{ beijingTime(history.start) }} — {{ beijingTime(history.end) }}<span v-if="!fullRange"> · 图表聚焦已有记录时段</span></p>
    </dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import { statusLabel } from '../utils/serverStatus'
import { METRICS, percentage, beijingTime, prepareHistory, chartOption } from '../utils/historyChart'
import { api } from '../api'
import { useServers } from '../composables/useServers'
import { useTheme } from '../composables/useTheme'
import { readThemeColors } from '../utils/theme'
import ThemeToggle from '../components/ThemeToggle.vue'

const { servers, subscribe } = useServers()
const { theme } = useTheme()
const route = useRoute()
const ranges = [{days:1,label:'1 天'},{days:7,label:'1 周'},{days:90,label:'1 季度'},{days:180,label:'半年'},{days:365,label:'1 年'}]
const dialogRef = ref(null)
const chartRef = ref(null)
const myChart = shallowRef(null)
const currentServer = ref(null)
const currentRange = ref(1)
const isLoading = ref(false)
const historyError = ref('')
const history = shallowRef(null)
const fullRange = ref(false)
let requestId = 0
let requestController = null
let observer = null
let resizeFrame = 0
let opener = null

function renderChart(preserveView = false) {
  if (!dialogRef.value?.open || !chartRef.value || !history.value) return
  if (!myChart.value) myChart.value = echarts.init(chartRef.value, null, {renderer:'svg'})
  myChart.value.resize()
  const option = chartOption(history.value, chartRef.value.clientWidth, fullRange.value, readThemeColors())
  if (preserveView === true) {
    const current = myChart.value.getOption() || {}
    option.legend.selected = current.legend?.[0]?.selected || {}
    option.dataZoom.forEach((zoom, i) => {
      const saved = current.dataZoom?.[i]
      if (saved) { zoom.start = saved.start; zoom.end = saved.end }
    })
  }
  myChart.value.setOption(option, {notMerge:true})
}
watch(theme, () => renderChart(true), { flush: 'post' })

async function fetchHistoryData(serverId, days) {
  const id = ++requestId
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  isLoading.value = true
  historyError.value = ''
  history.value = null
  myChart.value?.clear()
  try {
    const response = await api.getHistory(serverId, days, {signal:controller.signal, timeout:45000})
    if (id !== requestId || !dialogRef.value?.open) return
    history.value = prepareHistory(response.data, days)
    // The chart container stays visible and has a fixed height while loading.
    // Initialize only after Vue has mounted the open dialog and measured it.
    await nextTick()
    if (id !== requestId || !dialogRef.value?.open) return
    renderChart()
  } catch (error) {
    if (id !== requestId || controller.signal.aborted) return
    history.value = null
    historyError.value = error.response?.status === 404 ? '该服务器已不存在，请刷新节点列表' :
      error.code === 'ECONNABORTED' ? '查询超时，请稍后重试' : '历史数据加载失败'
    myChart.value?.clear()
  } finally {
    if (id === requestId) isLoading.value = false
  }
}

async function openChart(server) {
  opener = document.activeElement
  currentServer.value = server
  currentRange.value = 1
  fullRange.value = false
  dialogRef.value.showModal()
  // Start the request immediately: a close during nextTick cannot start a stale request.
  fetchHistoryData(server.id, 1)
}
function changeRange(days) {
  if (days === currentRange.value && !historyError.value) return
  currentRange.value = days
  fetchHistoryData(currentServer.value.id, days)
}
function retryHistory() { fetchHistoryData(currentServer.value.id, currentRange.value) }
function closeChart() {
  ++requestId
  requestController?.abort()
  requestController = null
  isLoading.value = false
  history.value = null
  historyError.value = ''
  myChart.value?.dispose()
  myChart.value = null
  dialogRef.value?.close()
  opener?.focus?.()
}
function onBackdrop(event) {
  if (event.target !== dialogRef.value) return
  const r = dialogRef.value.getBoundingClientRect()
  if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeChart()
}
function resizeChart() {
  cancelAnimationFrame(resizeFrame)
  resizeFrame = requestAnimationFrame(() => {
    if (!myChart.value || !dialogRef.value?.open) return
    myChart.value.resize()
    myChart.value.setOption({xAxis:{splitNumber:Math.max(2, Math.floor(chartRef.value.clientWidth/140))}})
  })
}
onMounted(() => { subscribe(); observer = new ResizeObserver(resizeChart); observer.observe(chartRef.value) })
watch(() => servers.value.find(s => String(s.id) === String(route.query.server_id))?.id, async id => {
  if (id) { await nextTick(); openChart(servers.value.find(s => s.id === id)) }
}, { immediate: true })
onUnmounted(() => { observer?.disconnect(); cancelAnimationFrame(resizeFrame); closeChart() })
</script>

<style scoped>
.history-page { padding: 30px 32px; color: var(--text); }
.panel { background:var(--surface); padding: 25px; border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow); }
h2 { margin-top: 0; color: var(--text); }
.desc { color: var(--text-muted); margin-bottom: 20px; font-size: 14px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.data-table th, .data-table td { padding: 12px 15px; border-bottom: 1px solid var(--border); text-align: left; }
.data-table th { background-color: var(--surface-soft); color: var(--text-secondary); font-weight: bold; }
.data-table tr:hover { background-color: var(--surface-muted); }
.group-tag { background: var(--accent-soft); color: var(--accent); padding: 4px 8px; border-radius: 4px; font-size: 12px; }
.text-green { color: var(--success); font-weight: bold; }
.text-red { color: var(--danger); font-weight: bold; }
.actions { display: flex; gap: 8px; }
.btn-view { background: var(--accent-soft); border: 1px solid var(--accent-border); color: var(--accent); padding: 6px 12px; border-radius: 7px; cursor: pointer; transition: 0.2s; font-weight: 500; }
.btn-view:hover { background: var(--primary-bg); color:var(--on-primary); }


.panel { overflow-x:auto; }
.history-dialog { width:1100px; max-width:calc(100vw - 32px); max-height:92vh; overflow:auto; box-sizing:border-box; padding:28px 32px 20px; border:1px solid var(--border); border-radius:20px; background:var(--surface); color:var(--text); box-shadow:0 24px 90px #0f172a40; }
.history-dialog::backdrop { background:var(--backdrop); backdrop-filter:blur(3px); }
.trend-header { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.trend-header-actions { display:flex; align-items:center; gap:10px; }
.trend-heading { display:flex; align-items:center; gap:14px; min-width:0; }
.trend-icon { display:grid; place-items:center; width:44px; height:44px; background:var(--accent-soft); color:var(--accent); border-radius:13px; font-size:28px; flex-shrink:0; }
.trend-heading h3 { font-size:20px; margin:0; overflow-wrap:anywhere; }
.trend-heading h3 span { font-weight:400; color:var(--text-muted); margin-left:8px; }
.trend-heading p { font-size:12px; color:var(--text-muted); margin:7px 0 0; }.trend-heading p span { margin:0 8px; }
.trend-close { width:34px; height:34px; flex-shrink:0; border:0; border-radius:9px; color:var(--text-muted); background:var(--surface-soft); font-size:25px; cursor:pointer; }
.trend-close:hover { background:var(--danger-bg); color:var(--danger); }
.trend-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:24px 0 20px; }
.time-filters { display:flex; border-radius:10px; padding:4px; background:var(--page-bg); gap:3px; }
.time-filters button { white-space:nowrap; border:0; border-radius:7px; padding:8px 18px; background:transparent; color:var(--text-muted); font-size:13px; cursor:pointer; }
.time-filters button.active { background:var(--surface); color:var(--accent); box-shadow:0 2px 5px #0f172a0d; font-weight:600; }
.aggregation-tag { font-size:12px; color:var(--text-muted); white-space:nowrap; }
.trend-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }
.metric-summary { background:var(--surface-soft); border:1px solid var(--border-soft); padding:15px 20px; border-radius:12px; }
.metric-name { color:var(--text-muted); font-size:12px; display:flex; align-items:center; gap:7px; }.metric-name i { width:7px; height:7px; border-radius:50%; background:var(--metric-color); }
.metric-value { font-size:28px; font-weight:650; color:var(--metric-color); margin:9px 0 5px; font-variant-numeric:tabular-nums; }.metric-value small { margin-left:9px; font-size:11px; color:var(--text-muted); font-weight:400; }
.metric-peak { font-size:11px; color:var(--text-muted); }.metric-peak strong { font-weight:500; margin-left:6px; color:var(--text-muted); }
.chart-toolbar { display:flex; justify-content:space-between; align-items:center; gap:12px; font-size:12px; color:var(--text-muted); margin:20px 0 8px; }
.chart-toolbar label { display:flex; align-items:center; gap:6px; cursor:pointer; }.chart-toolbar input { accent-color:var(--accent); }
.chart-stage { position:relative; }.chart-container { width:100%; height:350px; }
.chart-state { position:absolute; inset:0; background:var(--surface); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:13px; text-align:center; padding:16px; }
.chart-state strong { font-size:15px; color:var(--text-secondary); }.chart-state small { font-size:12px; color:var(--text-muted); }.chart-state button { border:1px solid var(--accent-border); border-radius:8px; background:var(--accent-soft); color:var(--accent); padding:8px 18px; cursor:pointer; }
.state-icon { display:grid; place-items:center; width:40px; height:40px; border-radius:50%; background:var(--surface-muted); color:var(--text-muted); font-size:24px; }
.trend-spinner { width:24px; height:24px; border:3px solid var(--accent-border); border-top-color:var(--accent); border-radius:50%; animation:spin .8s linear infinite; }@keyframes spin { to {transform:rotate(360deg)} }
.trend-footer { border-top:1px solid var(--border-soft); margin-top:10px; padding-top:14px; display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; font-size:11px; color:var(--text-muted); }
.trend-period { font-size:11px; color:var(--text-muted); margin:10px 0 0; line-height:1.7; }
button:focus-visible { outline:2px solid var(--accent-border); outline-offset:3px; }
@media(max-width:640px) {
 .history-page { padding:20px 16px; }
 .history-dialog { padding:20px 16px 16px; border-radius:15px; max-width:calc(100vw - 16px); }
 .trend-heading h3 {font-size:17px}.trend-heading h3 span {display:block;margin:3px 0 0;font-size:13px}.trend-icon {width:36px;height:36px}
 .trend-toolbar {flex-direction:column;align-items:stretch;margin:18px 0 14px}.time-filters button {padding:8px 0;flex:1}.time-filters {width:100%;box-sizing:border-box}.aggregation-tag {text-align:right}
 .trend-summary {gap:7px}.metric-summary {padding:12px 8px}.metric-name {font-size:10px;gap:4px}.metric-value {font-size:21px}.metric-value small {display:block;margin:3px 0 0;font-size:10px}.metric-peak {font-size:10px}.metric-peak strong {display:block;margin:4px 0 0}
 .chart-toolbar {font-size:10px;gap:4px}.chart-container {height:320px}.trend-footer {line-height:1.8}
}
</style>
