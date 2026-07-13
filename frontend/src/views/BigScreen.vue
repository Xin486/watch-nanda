<template>
  <div class="big-screen-layout">
    <header class="bs-header">
      <div class="bs-logo">
        <span class="logo-accent"></span>
        <h2>{{ companyName || '南大仙林' }} 算力集群指挥大屏</h2>
      </div>
      <div class="bs-nav">
        <router-link to="/dashboard" class="back-btn">⬅ 返回控制台</router-link>
      </div>
      <div class="bs-time">
        <span>{{ totalOnlineServers }} 在线节点 · {{ totalGPUs }} 设备</span>
        <span class="time-text">{{ currentTime }}</span>
      </div>
    </header>

    <div class="bs-main">
      <aside class="bs-left">
        <div class="bs-panel">
          <div class="panel-title">🎯 算力大盘 GPU分布</div>
          <div class="big-number">
            {{ totalGPUs }} <span class="unit">GPUs</span>
          </div>
          
          <div class="progress-item mt-4">
            <div class="prog-label"><span>高负载节点 (>80% CPU)</span><span>{{ highLoadServers.length }}</span></div>
            <div class="prog-track"><div class="prog-fill bg-orange" :style="{ width: (highLoadServers.length/servers.length)*100 + '%' }"></div></div>
          </div>
          
          <div class="progress-item">
            <div class="prog-label"><span>闲置节点 (<10% CPU)</span><span>{{ idleServers.length }}</span></div>
            <div class="prog-track"><div class="prog-fill bg-blue" :style="{ width: (idleServers.length/servers.length)*100 + '%' }"></div></div>
          </div>
        </div>

        <div class="bs-panel">
          <div class="panel-title">🌡️ 集群状态</div>
          <div class="status-list">
            <div class="status-row"><span>最高温度</span><span class="val text-orange">{{ maxGpuTemp }}°C</span></div>
            <div class="status-row"><span>总节点数</span><span class="val">{{ servers.length }}</span></div>
            <div class="status-row"><span>离线告警</span><span class="val text-red">{{ servers.filter(s=>s.status==='offline').length }}</span></div>
          </div>
        </div>
      </aside>

      <section class="bs-center">
        <div class="bs-panel full-height">
          <div class="panel-title flex-between">
            <span>🗄️ 节点分组矩阵 (自动过滤离线)</span>
            <span class="sub-text">共 {{ Object.keys(matrixData).length }} 个分组</span>
          </div>
          
          <div class="matrix-container">
            <div class="rack-col" v-for="(groupServers, groupName) in matrixData" :key="groupName">
              <div class="rack-header">
                <strong>{{ groupName }}</strong>
                <span>{{ groupServers.length }} 台</span>
              </div>
              <div class="rack-body">
                <div class="server-block" v-for="s in groupServers" :key="s.id" :class="getLoadLevel(s.cpu_percent)">
                  <div class="sb-top">
                    <span class="sb-name" :title="s.hostname">{{ s.hostname }}</span>
                    <span class="sb-gpu" v-if="s.gpu_data && s.gpu_data.length">{{ s.gpu_data.length }}G</span>
                  </div>
                  <div class="sb-bottom">{{ s.cpu_percent }}% CPU</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <aside class="bs-right">
        <div class="bs-panel chart-panel">
          <div class="panel-title">📍 区域分布</div>
          <div ref="pieChartRef" class="echart-box"></div>
        </div>
        
        <div class="bs-panel alert-panel">
          <div class="panel-title">🚨 离线告警日志</div>
          <ul class="alert-list" v-if="offlineServers.length > 0">
            <li v-for="s in offlineServers" :key="s.id">
              <span class="dot"></span> {{ s.hostname }} ({{ s.ip_address }}) 连接丢失
            </li>
          </ul>
          <div class="empty-state" v-else>✅ 当前无离线告警</div>
        </div>
      </aside>
    </div>

    <div class="bs-bottom">
      <div class="bs-panel bottom-chart-panel">
        <div class="panel-title">📈 GPU 型号分布</div>
        <div ref="barChartRef" class="echart-box-horizontal"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, shallowRef, watch } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts'

const companyName = ref(localStorage.getItem('companyName') || '南大仙林')
const servers = ref([])
const currentTime = ref(new Date().toLocaleString('zh-CN'))
let timer = null
let dataTimer = null

const pieChartRef = ref(null)
const barChartRef = ref(null)
const pieChart = shallowRef(null)
const barChart = shallowRef(null)

const fetchServers = async () => {
  try {
    const host = window.location.hostname
    const res = await axios.get(`http://${host}:7980/api/servers`)
    servers.value = res.data
  } catch (error) { console.error('数据拉取失败') }
}

// === 数据计算逻辑 ===
const onlineServers = computed(() => servers.value.filter(s => s.status === 'online'))
const offlineServers = computed(() => servers.value.filter(s => s.status === 'offline'))
const totalOnlineServers = computed(() => onlineServers.value.length)

// 中间矩阵数据：按 group_name 分组，并且过滤掉离线机器
const matrixData = computed(() => {
  const groups = {}
  onlineServers.value.forEach(s => {
    const g = s.group_name || '未分组'
    if (!groups[g]) groups[g] = []
    groups[g].push(s)
  })
  return groups
})

// 提取所有在线机器的 GPU 数据
const allGpus = computed(() => {
  return onlineServers.value.flatMap(s => s.gpu_data || [])
})
const totalGPUs = computed(() => allGpus.value.length)

const maxGpuTemp = computed(() => {
  if (!allGpus.value.length) return '--'
  return Math.max(...allGpus.value.map(g => parseInt(g.temp) || 0))
})

const highLoadServers = computed(() => onlineServers.value.filter(s => s.cpu_percent >= 80))
const idleServers = computed(() => onlineServers.value.filter(s => s.cpu_percent <= 10))

const getLoadLevel = (cpu) => {
  if (cpu >= 80) return 'level-danger'
  if (cpu >= 40) return 'level-warning'
  return 'level-normal'
}

// === ECharts 图表渲染 ===
const renderCharts = () => {
  // 1. 区域分布饼图
  if (!pieChart.value && pieChartRef.value) pieChart.value = echarts.init(pieChartRef.value)
  if (pieChart.value) {
    const groupCounts = Object.entries(matrixData.value).map(([name, list]) => ({ name, value: list.length }))
    pieChart.value.setOption({
      tooltip: { trigger: 'item' },
      series: [{
        type: 'pie', radius: ['40%', '70%'], avoidLabelOverlap: false,
        itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
        label: { show: false, position: 'center' },
        emphasis: { label: { show: true, fontSize: 16, fontWeight: 'bold' } },
        labelLine: { show: false },
        data: groupCounts
      }]
    })
  }

  // 2. GPU 型号柱状图
  if (!barChart.value && barChartRef.value) barChart.value = echarts.init(barChartRef.value)
  if (barChart.value) {
    const modelCounts = {}
    allGpus.value.forEach(g => {
      // 简化型号名称展示，例如从 "NVIDIA GeForce RTX 4090" 提取 "RTX 4090"
      let name = g.model || 'Unknown'
      name = name.replace('NVIDIA ', '').replace('GeForce ', '')
      modelCounts[name] = (modelCounts[name] || 0) + 1
    })
    const sortedData = Object.entries(modelCounts).sort((a, b) => a[1] - b[1]) // 升序排列供横向柱状图展示

    barChart.value.setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: '2%', right: '4%', bottom: '0%', top: '5%', containLabel: true },
      xAxis: { type: 'value', show: false },
      yAxis: { type: 'category', data: sortedData.map(i => i[0]), axisLine: {show: false}, axisTick: {show: false} },
      series: [{
        type: 'bar',
        data: sortedData.map(i => i[1]),
        label: { show: true, position: 'right' },
        itemStyle: { color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{offset: 0, color: '#f97316'}, {offset: 1, color: '#fb923c'}]) }
      }]
    })
  }
}

watch(servers, () => { nextTick(() => renderCharts()) }, { deep: true })

onMounted(() => {
  fetchServers()
  dataTimer = setInterval(fetchServers, 60000)
  timer = setInterval(() => { currentTime.value = new Date().toLocaleString('zh-CN') }, 1000)
  window.addEventListener('resize', () => { pieChart.value?.resize(); barChart.value?.resize() })
})

onUnmounted(() => {
  clearInterval(timer); clearInterval(dataTimer)
  pieChart.value?.dispose(); barChart.value?.dispose()
})
</script>

<style scoped>
/* 全局布局重置：模仿 IDC 大屏专属样式 (浅色现代风) */
.big-screen-layout { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #f4f6f8; z-index: 9999; display: flex; flex-direction: column; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }

/* 顶栏 */
.bs-header { height: 60px; background: #fff; display: flex; justify-content: space-between; align-items: center; padding: 0 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); }
.bs-logo { display: flex; align-items: center; gap: 10px; }
.logo-accent { width: 16px; height: 16px; background: linear-gradient(135deg, #f97316, #ea580c); border-radius: 4px; }
.bs-logo h2 { margin: 0; font-size: 18px; color: #1e293b; font-weight: 800; letter-spacing: 1px; }
.back-btn { text-decoration: none; background: #f1f5f9; color: #475569; padding: 6px 16px; border-radius: 20px; font-size: 13px; font-weight: bold; transition: 0.2s; border: 1px solid #e2e8f0; }
.back-btn:hover { background: #e2e8f0; color: #0f172a; }
.bs-time { display: flex; align-items: center; gap: 15px; font-size: 13px; color: #64748b; font-weight: bold; }
.time-text { color: #0f172a; font-family: monospace; font-size: 15px; }

/* 主体网格 */
.bs-main { flex: 1; display: grid; grid-template-columns: 280px 1fr 300px; gap: 16px; padding: 16px; overflow: hidden; }
.bs-panel { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.03); display: flex; flex-direction: column; margin-bottom: 16px; }
.bs-panel:last-child { margin-bottom: 0; }
.full-height { height: 100%; margin-bottom: 0; }
.panel-title { font-size: 14px; font-weight: bold; color: #334155; margin-bottom: 15px; display: flex; align-items: center; }
.flex-between { justify-content: space-between; }
.sub-text { font-size: 12px; color: #94a3b8; font-weight: normal; }

/* 左侧统计 */
.big-number { font-size: 36px; font-weight: 900; color: #0f172a; border-bottom: 1px solid #f1f5f9; padding-bottom: 15px; }
.unit { font-size: 14px; color: #64748b; font-weight: bold; }
.progress-item { margin-top: 15px; }
.prog-label { display: flex; justify-content: space-between; font-size: 12px; color: #475569; margin-bottom: 6px; }
.prog-track { height: 6px; background: #f1f5f9; border-radius: 3px; overflow: hidden; }
.prog-fill { height: 100%; transition: width 0.5s; }
.bg-orange { background: #f97316; }
.bg-blue { background: #3b82f6; }
.status-list { display: flex; flex-direction: column; gap: 12px; }
.status-row { display: flex; justify-content: space-between; font-size: 13px; color: #475569; padding: 8px 0; border-bottom: 1px dashed #f1f5f9; }
.status-row .val { font-weight: bold; color: #0f172a; }
.text-orange { color: #ea580c !important; }
.text-red { color: #ef4444 !important; }

/* 👑 中间机柜矩阵 (核心视图重构) */
.matrix-container { flex: 1; display: flex; gap: 12px; overflow-x: auto; padding-bottom: 10px; align-items: flex-start; }
/* 自定义滚动条 */
.matrix-container::-webkit-scrollbar { height: 8px; }
.matrix-container::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
.rack-col { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; min-width: 160px; max-width: 180px; flex-shrink: 0; display: flex; flex-direction: column; }
.rack-header { padding: 10px; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center; font-size: 13px; color: #1e293b; background: #f1f5f9; border-radius: 6px 6px 0 0; }
.rack-header span { font-size: 11px; color: #64748b; background: #fff; padding: 2px 6px; border-radius: 10px; border: 1px solid #e2e8f0; }
.rack-body { padding: 8px; display: flex; flex-direction: column; gap: 6px; overflow-y: auto; max-height: calc(100vh - 180px); }

/* 服务器小区块 */
.server-block { border: 1px solid transparent; border-radius: 4px; padding: 8px; display: flex; flex-direction: column; gap: 4px; transition: 0.2s; }
.sb-top { display: flex; justify-content: space-between; align-items: center; }
.sb-name { font-size: 12px; font-weight: bold; color: #1e293b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100px; }
.sb-gpu { font-size: 10px; background: rgba(255,255,255,0.8); padding: 1px 4px; border-radius: 3px; font-weight: bold; color: #0f172a; }
.sb-bottom { font-size: 11px; font-family: monospace; }
/* 颜色梯队映射 */
.level-normal { background: #ecfdf5; border-color: #a7f3d0; } .level-normal .sb-bottom { color: #059669; }
.level-warning { background: #fffbeb; border-color: #fde68a; } .level-warning .sb-bottom { color: #d97706; }
.level-danger { background: #fef2f2; border-color: #fecaca; } .level-danger .sb-bottom { color: #dc2626; }

/* 右侧与底部图表 */
.chart-panel { height: 260px; }
.alert-panel { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
.echart-box { width: 100%; height: 100%; }
.alert-list { list-style: none; padding: 0; margin: 0; overflow-y: auto; font-size: 12px; color: #ef4444; }
.alert-list li { margin-bottom: 8px; padding: 8px; background: #fef2f2; border-radius: 4px; display: flex; align-items: center; gap: 6px; }
.dot { width: 6px; height: 6px; background: #ef4444; border-radius: 50%; }
.empty-state { text-align: center; color: #10b981; font-weight: bold; margin-top: 20px; font-size: 13px; }

.bs-bottom { padding: 0 16px 16px 16px; height: 240px; }
.bottom-chart-panel { height: 100%; margin: 0; }
.echart-box-horizontal { width: 100%; height: 100%; }
</style>