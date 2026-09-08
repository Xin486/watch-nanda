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
              <span :class="server.status === 'online' ? 'text-green' : 'text-red'">
                ● {{ server.status === 'online' ? '在线' : '离线' }}
              </span>
            </td>
            <td class="actions">
              <button class="btn-view" @click="openChart(server)">📈 查看折线图</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 折线图弹窗 -->
    <div class="modal-overlay" v-if="showChartModal" @click.self="closeChart">
      <div class="modal-content chart-modal">
        <div class="modal-header">
          <div class="header-left">
            <h3>{{ currentServer?.hostname }} 的负载趋势</h3>
            <div class="time-filters">
              <button :class="{ active: currentRange === 1 }" @click="changeRange(1)">1 天</button>
              <button :class="{ active: currentRange === 7 }" @click="changeRange(7)">1 周</button>
              <button :class="{ active: currentRange === 90 }" @click="changeRange(90)">1 季度</button>
              <button :class="{ active: currentRange === 180 }" @click="changeRange(180)">半 年</button>
              <button :class="{ active: currentRange === 365 }" @click="changeRange(365)">1 年</button>
            </div>
          </div>
          <button class="close-btn" @click="closeChart">✖</button>
        </div>
        
        <div v-if="isLoading" class="loading-state">数据拉取与计算中...</div>
        
        <div ref="chartRef" class="chart-container" v-show="!isLoading"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
// 历史负载页：节点列表 + CPU/内存/显存历史折线图
import { ref, onMounted, nextTick, shallowRef } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api'
import { useServers } from '../composables/useServers'

const { servers, subscribe } = useServers()
const showChartModal = ref(false)
const currentServer = ref(null)
const isLoading = ref(false)
const currentRange = ref(1)

const chartRef = ref(null)
const myChart = shallowRef(null)

// 服务器列表通过 SSE 实时推送

const openChart = async (server) => {
  currentServer.value = server
  showChartModal.value = true
  currentRange.value = 1 
  
  await nextTick()
  if (!myChart.value) { myChart.value = echarts.init(chartRef.value) }
  
  fetchHistoryData(server.id, 1)
}

const changeRange = (days) => {
  currentRange.value = days
  fetchHistoryData(currentServer.value.id, days)
}

const fetchHistoryData = async (serverId, days) => {
  isLoading.value = true
  try {
    const res = await api.getHistory(serverId, days)
    const dataPoints = res.data.data
    
    // 计算图表时间轴的起止（毫秒）
    const endTime = new Date().getTime()
    const startTime = endTime - (days * 24 * 60 * 60 * 1000)
    
    // 将数据映射为 ECharts 真实时间轴所需的 [时间戳, 值] 二维数组格式
    const cpus = dataPoints.map(item => [new Date(item.time).getTime(), item.cpu])
    const rams = dataPoints.map(item => [new Date(item.time).getTime(), item.ram])
    const gpus = dataPoints.map(item => [new Date(item.time).getTime(), item.gpu])
    
    renderChart(cpus, rams, gpus, startTime, endTime)
  } catch (error) {
    console.error('拉取历史数据失败', error)
  } finally {
    isLoading.value = false
  }
}

const renderChart = (cpus, rams, gpus, startTime, endTime) => {
  const option = {
    tooltip: {
      trigger: 'axis',
      // 自定义悬浮提示格式（时间 + 各系列值）
      formatter: function (params) {
        if (!params || !params.length) return '';
        let date = new Date(params[0].value[0]);
        let timeStr = date.toLocaleString('zh-CN', { hour12: false });
        let res = `<div style="margin-bottom:5px;font-weight:bold;color:#1e293b">${timeStr}</div>`;
        params.forEach(p => {
          res += `<div style="display:flex;justify-content:space-between;gap:20px;">
                    <span>${p.marker} ${p.seriesName}</span>
                    <span style="font-weight:bold">${p.value[1]}</span>
                  </div>`;
        });
        return res;
      }
    },
    // 图例放顶部，避免与 X 轴重叠
    legend: {
      data: ['CPU使用率 (%)', '内存使用率 (%)', '平均显存占用 (%)'],
      top: '0%',
      textStyle: { color: '#475569' }
    },
    // 画布边距：给图例和时间轴留出空间
    grid: { top: '15%', left: '2%', right: '4%', bottom: '8%', containLabel: true },
    xAxis: {
      type: 'time',
      min: startTime, // 锁定 X 轴起点（days 天前）
      max: endTime,   // 锁定 X 轴终点（当前时间）
      splitLine: { show: false }, // 隐藏垂直网格线
      axisLabel: {
        color: '#64748b',
        // 时间格式化：跨度大显示到月/日，跨度小显示到时分
        formatter: {
            year: '{yyyy}年',
            month: '{MM}-{dd}',
            day: '{MM}-{dd}',
            hour: '{HH}:{mm}',
            minute: '{HH}:{mm}'
        }
      }
    },
    yAxis: {
      type: 'value',
      max: 100,
      splitLine: { lineStyle: { type: 'dashed', color: '#e2e8f0' } }
    },
    series: [
      // showSymbol: false —— 隐藏数据点，悬浮时才显示
      { name: 'CPU使用率 (%)', type: 'line', showSymbol: false, smooth: true, itemStyle: { color: '#10b981' }, areaStyle: { color: 'rgba(16, 185, 129, 0.1)' }, data: cpus },
      { name: '内存使用率 (%)', type: 'line', showSymbol: false, smooth: true, itemStyle: { color: '#f59e0b' }, areaStyle: { color: 'rgba(245, 158, 11, 0.1)' }, data: rams },
      { name: '平均显存占用 (%)', type: 'line', showSymbol: false, smooth: true, itemStyle: { color: '#ef4444' }, areaStyle: { color: 'rgba(239, 68, 68, 0.1)' }, data: gpus }
    ]
  }
  myChart.value.setOption(option)
}

const closeChart = () => {
  showChartModal.value = false
  if (myChart.value) {
    myChart.value.dispose()
    myChart.value = null
  }
}

onMounted(() => subscribe())
</script>

<style scoped>
/* 列表部分样式保持不变 */
.history-page { padding: 20px; }
.panel { background: #fff; padding: 25px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
h2 { margin-top: 0; color: #1e293b; }
.desc { color: #64748b; margin-bottom: 20px; font-size: 14px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.data-table th, .data-table td { padding: 12px 15px; border-bottom: 1px solid #e2e8f0; text-align: left; }
.data-table th { background-color: #f8fafc; color: #475569; font-weight: bold; }
.data-table tr:hover { background-color: #f1f5f9; }
.group-tag { background: #e0f2fe; color: #0369a1; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
.text-green { color: #10b981; font-weight: bold; }
.text-red { color: #ef4444; font-weight: bold; }
.actions { display: flex; gap: 8px; }
.btn-view { background: #f0fdf4; border: 1px solid #86efac; color: #15803d; padding: 6px 12px; border-radius: 4px; cursor: pointer; transition: 0.2s; font-weight: bold; }
.btn-view:hover { background: #16a34a; color: #fff; }

/* 弹窗及按钮组样式 */
.modal-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0, 0, 0, 0.6); display: flex; justify-content: center; align-items: center; z-index: 1000; }
.chart-modal { width: 900px; max-width: 95vw; padding: 25px; border-radius: 12px;}
.modal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #e2e8f0; padding-bottom: 15px; margin-bottom: 25px; }
.header-left { display: flex; align-items: center; gap: 20px; }
.header-left h3 { margin: 0; color: #1e293b; font-size: 18px; }

/* 🪐 时间范围选择器按钮 */
.time-filters { display: flex; background: #f1f5f9; border-radius: 6px; padding: 4px; }
.time-filters button { border: none; background: transparent; padding: 6px 16px; font-size: 13px; color: #64748b; cursor: pointer; border-radius: 4px; transition: all 0.2s; }
.time-filters button:hover { color: #1e293b; }
.time-filters button.active { background: #fff; color: #3b82f6; box-shadow: 0 1px 3px rgba(0,0,0,0.1); font-weight: bold; }

.close-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: #94a3b8; }
.close-btn:hover { color: #ef4444; }

.loading-state { height: 400px; display: flex; align-items: center; justify-content: center; color: #64748b; font-weight: bold; }
.chart-container { width: 100%; height: 400px; }
</style>