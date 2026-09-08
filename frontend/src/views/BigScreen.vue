<template>
  <div class="bs-wrap">
    <!-- 背景装饰层 -->
    <div class="bg-grid"></div>
    <div class="bg-glow glow-a"></div>
    <div class="bg-glow glow-b"></div>
    <div class="bg-glow glow-c"></div>
    <div class="scanline"></div>

    <div class="bs-layout">
      <!-- ============ 顶栏 ============ -->
      <header class="bs-header">
        <router-link to="/dashboard" class="back-btn">◀ 返回控制台</router-link>

        <div class="header-center">
          <span class="title-wing wing-left"></span>
          <div class="title-box">
            <h1 class="title">{{ companyName || '南大仙林' }}</h1>
            <p class="subtitle">AI COMPUTE CLUSTER · COMMAND CENTER</p>
          </div>
          <span class="title-wing wing-right"></span>
        </div>

        <div class="header-right">
          <div class="weather" v-if="weather" :title="`${weather.city || '当前位置'} · ${weather.desc} ${weather.high}°/${weather.low}°`">
            <span class="weather-icon">{{ weather.icon }}</span>
            <div class="weather-info">
              <span class="weather-temp">{{ weather.temp }}<em>°C</em></span>
              <span class="weather-desc">{{ weather.city || '当前位置' }} · {{ weather.desc }}</span>
            </div>
          </div>
          <div class="market" v-if="marketData && marketData.klines.length" @click="openMarketModal" title="半导体 K 线，点击查看大图">
            <div class="market-info">
              <span class="market-name">{{ marketData.name }}</span>
              <span class="market-price" :class="marketData.latest.pct >= 0 ? 'market-up' : 'market-down'">
                {{ marketData.latest.price }}
                <em>{{ (marketData.latest.pct >= 0 ? '+' : '') + marketData.latest.pct }}%</em>
              </span>
            </div>
            <div ref="miniKlineRef" class="market-mini"></div>
          </div>
          <span class="live-dot"></span>
          <div class="datetime">
            <span class="date">{{ currentDate }}</span>
            <span class="time">{{ currentTime }}</span>
          </div>
        </div>
      </header>

      <!-- ============ 主体 ============ -->
      <main class="bs-main">
        <!-- 左列 -->
        <aside class="bs-left">
          <div class="tech-panel">
            <span class="corner tl"></span><span class="corner tr"></span>
            <span class="corner bl"></span><span class="corner br"></span>
            <div class="panel-head"><i class="ph-ico">🎯</i><h3>算力总览</h3></div>

            <div class="gpu-hero">
              <div class="gpu-num">{{ gpuCountAnimated }}</div>
              <div class="gpu-unit">GPUs</div>
            </div>

            <div class="rate-row">
              <div class="rate-ring" :style="onlineRateStyle">
                <div class="rate-inner">
                  <span class="rate-val">{{ onlineRate }}%</span>
                  <span class="rate-label">在线率</span>
                </div>
              </div>
              <div class="rate-detail">
                <div class="rate-line"><span>在线节点</span><b class="c-green">{{ onlineCountAnimated }}</b></div>
                <div class="rate-line"><span>离线节点</span><b class="c-red">{{ offlineCount }}</b></div>
                <div class="rate-line"><span>总节点数</span><b>{{ servers.length }}</b></div>
              </div>
            </div>

            <div class="load-section">
              <div class="load-item">
                <div class="load-label"><span>高负载节点 &gt;80%</span><span class="c-orange">{{ highLoadServers.length }}</span></div>
                <div class="load-bar"><i class="load-fill fill-orange" :style="{ width: pct(highLoadServers.length) }"></i></div>
              </div>
              <div class="load-item">
                <div class="load-label"><span>闲置节点 &lt;10%</span><span class="c-cyan">{{ idleServers.length }}</span></div>
                <div class="load-bar"><i class="load-fill fill-cyan" :style="{ width: pct(idleServers.length) }"></i></div>
              </div>
            </div>
          </div>

          <div class="tech-panel">
            <span class="corner tl"></span><span class="corner tr"></span>
            <span class="corner bl"></span><span class="corner br"></span>
            <div class="panel-head"><i class="ph-ico">🌡️</i><h3>集群状态</h3></div>
            <ul class="status-list">
              <li>
                <span class="k"><i class="dot-ico i-orange"></i>最高温度</span>
                <span class="v c-orange">{{ maxGpuTempText }}</span>
              </li>
              <li>
                <span class="k"><i class="dot-ico i-cyan"></i>GPU 设备</span>
                <span class="v">{{ totalGPUs }} 张</span>
              </li>
              <li>
                <span class="k"><i class="dot-ico i-red"></i>离线告警</span>
                <span class="v c-red">{{ offlineCount }} 台</span>
              </li>
              <li>
                <span class="k"><i class="dot-ico i-violet"></i>在线分组</span>
                <span class="v">{{ matrixGroupCount }} 组</span>
              </li>
            </ul>
          </div>
        </aside>

        <!-- 中列 -->
        <section class="bs-center">
          <div class="tech-panel center-panel">
            <span class="corner tl"></span><span class="corner tr"></span>
            <span class="corner bl"></span><span class="corner br"></span>
            <div class="panel-head head-between">
              <span class="head-left"><i class="ph-ico">🗄️</i><h3>节点分组矩阵</h3></span>
              <span class="head-sub">共 {{ matrixGroupCount }} 个分组 · {{ totalOnlineServers }} 在线 · 自动过滤离线</span>
            </div>

            <div class="matrix-container">
              <div class="group-section" v-for="(groupServers, groupName) in matrixData" :key="groupName">
                <div class="group-header">
                  <span class="gh-line"></span>
                  <strong>{{ groupName }}</strong>
                  <span class="gh-count">{{ groupServers.length }} 台</span>
                </div>
                <div class="node-grid">
                  <div class="node-tile" v-for="s in groupServers" :key="s.id" :class="getLoadLevel(gpuPowerPercentOf(s))" :title="`${s.hostname} · GPU功率 ${Math.round(gpuPowerOf(s))}W`">
                    <div class="nt-top">
                      <span class="nt-name">{{ s.hostname }}</span>
                      <span class="nt-gpu" v-if="s.gpu_data && s.gpu_data.length">{{ s.gpu_data.length }}G</span>
                    </div>
                    <div class="nt-meta">
                      <span class="nt-val">{{ Math.round(gpuPowerOf(s)) }}W</span>
                      <div class="nt-bar"><i :style="{ width: Math.min(gpuPowerPercentOf(s), 100) + '%' }"></i></div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- 右列 -->
        <aside class="bs-right">
          <div class="tech-panel rank-panel">
            <span class="corner tl"></span><span class="corner tr"></span>
            <span class="corner bl"></span><span class="corner br"></span>
            <div class="panel-head head-between">
              <span class="head-left"><i class="ph-ico">⚡</i><h3>区域功耗排行</h3></span>
              <span class="head-sub">GPU 功率 · {{ rankedServers.length }} 台在线</span>
            </div>
            <ul class="rank-list" v-if="rankedServers.length">
              <li v-for="(s, i) in rankedServers" :key="s.id">
                <span class="rank-no" :class="'rank-' + (i + 1)">{{ i + 1 }}</span>
                <div class="rank-body">
                  <div class="rank-top">
                    <span class="rank-name" :title="s.hostname">{{ s.hostname }}</span>
                    <span class="rank-group">{{ s.group_name || '未分组' }}</span>
                  </div>
                  <div class="rank-bar"><i :class="rankBarClass(gpuPowerPercentOf(s))" :style="{ width: Math.min(gpuPowerPercentOf(s), 100) + '%' }"></i></div>
                </div>
                <span class="rank-val" :class="rankValClass(gpuPowerPercentOf(s))">{{ Math.round(gpuPowerOf(s)) }}W</span>
              </li>
            </ul>
            <div class="empty-state" v-else>暂无在线节点数据</div>
          </div>

          <div class="tech-panel alert-panel">
            <span class="corner tl"></span><span class="corner tr"></span>
            <span class="corner bl"></span><span class="corner br"></span>
            <div class="panel-head head-between">
              <span class="head-left"><i class="ph-ico">🚨</i><h3>离线告警日志</h3></span>
              <span class="head-sub" :class="{ 'sub-danger': offlineServers.length > 0 }">
                {{ offlineServers.length > 0 ? offlineServers.length + ' 条告警' : '运行正常' }}
              </span>
            </div>
            <ul class="alert-list" v-if="offlineServers.length > 0">
              <li v-for="s in offlineServers" :key="s.id">
                <span class="alert-dot"></span>
                <div class="alert-body">
                  <span class="alert-name">{{ s.hostname }}</span>
                  <span class="alert-ip">{{ s.ip_address }} · 连接丢失</span>
                </div>
                <span class="alert-tag">LOST</span>
              </li>
            </ul>
            <div class="empty-state" v-else>✅ 当前无离线告警，集群运行稳定</div>
          </div>
        </aside>
      </main>

      <!-- ============ 底部 ============ -->
      <footer class="bs-bottom">
        <div class="tech-panel bottom-panel">
          <span class="corner tl"></span><span class="corner tr"></span>
          <span class="corner bl"></span><span class="corner br"></span>
          <div class="panel-head head-between">
            <span class="head-left"><i class="ph-ico">📈</i><h3>GPU 型号分布</h3></span>
            <span class="head-sub">共 {{ totalGPUs }} 张 GPU · {{ gpuModelStats.length }} 种型号</span>
          </div>
          <div ref="barChartRef" class="echart-box-horizontal"></div>
        </div>
      </footer>

      <!-- ============ 行情 K 线弹窗 ============ -->
      <div class="market-modal-overlay" v-if="showMarketModal" @click.self="closeMarketModal">
        <div class="market-modal tech-panel">
          <span class="corner tl"></span><span class="corner tr"></span>
          <span class="corner bl"></span><span class="corner br"></span>
          <div class="panel-head head-between">
            <span class="head-left">
              <i class="ph-ico">📈</i>
              <h3>{{ marketData.name }} · 日K线</h3>
              <span class="market-price" :class="marketData.latest.pct >= 0 ? 'market-up' : 'market-down'">
                {{ marketData.latest.price }} <em>{{ (marketData.latest.pct >= 0 ? '+' : '') + marketData.latest.pct }}%</em>
              </span>
            </span>
            <span class="market-modal-close" @click="closeMarketModal">✖</span>
          </div>
          <div ref="bigKlineRef" class="market-big"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// 算力大屏：科技风全景监控（分组矩阵 / 区域占用排行 / 告警日志 / GPU 型号分布 / 天气）
import { ref, computed, onMounted, onUnmounted, nextTick, shallowRef, watch } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api'
import { useServers } from '../composables/useServers'

const companyName = ref(localStorage.getItem('companyName') || '南大仙林')
const { servers, subscribe } = useServers()
const currentTime = ref('--:--:--')
const currentDate = ref('')
let timer = null

const barChartRef = ref(null)
const barChart = shallowRef(null)

// 天气
const weather = ref(null)

// 半导体行情 K 线
const marketData = ref(null)
const showMarketModal = ref(false)
const miniKlineRef = ref(null)
const bigKlineRef = ref(null)
const miniKline = shallowRef(null)
const bigKline = shallowRef(null)
const MARKET_SYMBOL = '90.BK1036'   // 东方财富「半导体」行业板块指数
let marketTimer = null

// ---------- 服务器数据通过 SSE 实时推送，无需手动拉取 ----------

// ---------- 天气（Open-Meteo，自动定位） ----------
const WEATHER_CODES = {
  0: { icon: '☀️', desc: '晴' },
  1: { icon: '🌤️', desc: '大致晴朗' },
  2: { icon: '⛅', desc: '多云' },
  3: { icon: '☁️', desc: '阴' },
  45: { icon: '🌫️', desc: '雾' },
  48: { icon: '🌫️', desc: '雾凇' },
  51: { icon: '🌦️', desc: '小毛毛雨' },
  53: { icon: '🌦️', desc: '毛毛雨' },
  55: { icon: '🌧️', desc: '大毛毛雨' },
  61: { icon: '🌧️', desc: '小雨' },
  63: { icon: '🌧️', desc: '中雨' },
  65: { icon: '🌧️', desc: '大雨' },
  66: { icon: '🌧️', desc: '冻雨' },
  67: { icon: '🌧️', desc: '冻雨' },
  71: { icon: '🌨️', desc: '小雪' },
  73: { icon: '🌨️', desc: '中雪' },
  75: { icon: '❄️', desc: '大雪' },
  77: { icon: '❄️', desc: '雪粒' },
  80: { icon: '🌦️', desc: '阵雨' },
  81: { icon: '🌧️', desc: '强阵雨' },
  82: { icon: '⛈️', desc: '强阵雨' },
  85: { icon: '🌨️', desc: '阵雪' },
  86: { icon: '🌨️', desc: '强阵雪' },
  95: { icon: '⛈️', desc: '雷雨' },
  96: { icon: '⛈️', desc: '雷雨冰雹' },
  99: { icon: '⛈️', desc: '强雷雨冰雹' }
}
const decodeWeather = (code) => WEATHER_CODES[code] || { icon: '🌡️', desc: '未知' }

const locateUser = async () => {
  let coords = null
  // 1. 优先浏览器精确定位（坐标最准）
  try {
    if (navigator.geolocation) {
      const pos = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, { timeout: 6000, maximumAge: 600000 })
      })
      coords = { lat: Number(pos.coords.latitude.toFixed(4)), lon: Number(pos.coords.longitude.toFixed(4)) }
    }
  } catch (e) {}
  // 2. IP 定位（拿城市名，兼作坐标兜底）
  const providers = ['https://ipwho.is/', 'https://ipapi.co/json/']
  for (const url of providers) {
    try {
      const r = await fetch(url)
      const d = await r.json()
      if (d.latitude && d.longitude) {
        const city = d.city || d.region || null
        if (!coords) coords = { lat: Number(d.latitude.toFixed(4)), lon: Number(d.longitude.toFixed(4)) }
        return { ...coords, city }
      }
    } catch (e) {}
  }
  return coords ? { ...coords, city: null } : null
}

const fetchWeather = async () => {
  try {
    const pos = await locateUser()
    if (!pos) return
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${pos.lat}&longitude=${pos.lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=1`
    const res = await fetch(url)
    const data = await res.json()
    const w = decodeWeather(data.current.weather_code)
    weather.value = {
      icon: w.icon,
      desc: w.desc,
      temp: Math.round(data.current.temperature_2m),
      high: Math.round(data.daily.temperature_2m_max[0]),
      low: Math.round(data.daily.temperature_2m_min[0]),
      city: pos.city
    }
  } catch (e) { console.error('天气获取失败', e) }
}

// ---------- 行情 K 线（后端代理东方财富） ----------
const fetchMarket = async () => {
  try {
    const res = await api.getMarketKline({ symbol: MARKET_SYMBOL, klt: 101, lmt: 120 })
    if (res.data && res.data.klines && res.data.klines.length) marketData.value = res.data
  } catch (error) { console.error('行情数据拉取失败', error) }
}

// ---------- 派生数据 ----------
const onlineServers = computed(() => servers.value.filter(s => s.status === 'online'))
const offlineServers = computed(() => servers.value.filter(s => s.status === 'offline'))
const totalOnlineServers = computed(() => onlineServers.value.length)
const offlineCount = computed(() => offlineServers.value.length)

const matrixData = computed(() => {
  const groups = {}
  onlineServers.value.forEach(s => {
    const g = s.group_name || '未分组'
    if (!groups[g]) groups[g] = []
    groups[g].push(s)
  })
  return groups
})
const matrixGroupCount = computed(() => Object.keys(matrixData.value).length)

// 区域功耗排行：在线节点按 GPU 总功率降序（无 GPU 的节点功率为 0 排最后）
const gpuPowerOf = (s) => (s.gpu_data || []).reduce((sum, g) => sum + (g.power_draw || 0), 0)
const gpuPowerLimitOf = (s) => (s.gpu_data || []).reduce((sum, g) => sum + (g.power_limit || 0), 0)
const gpuPowerPercentOf = (s) => {
  const limit = gpuPowerLimitOf(s)
  if (limit > 0) return Math.round((gpuPowerOf(s) / limit) * 100)
  // 部分新驱动/卡型拿不到功耗上限（如 4090），按 400W 参考值估算进度，仅作视觉参考
  return Math.min(Math.round((gpuPowerOf(s) / 400) * 100), 100)
}

const rankedServers = computed(() => {
  return onlineServers.value
    .slice()
    .sort((a, b) => gpuPowerOf(b) - gpuPowerOf(a))
})

const rankBarClass = (pct) => {
  if (pct >= 80) return 'bar-danger'
  if (pct >= 40) return 'bar-warning'
  return 'bar-normal'
}
const rankValClass = (pct) => {
  if (pct >= 80) return 'val-danger'
  if (pct >= 40) return 'val-warning'
  return 'val-normal'
}

const allGpus = computed(() => onlineServers.value.flatMap(s => s.gpu_data || []))
const totalGPUs = computed(() => allGpus.value.length)

const cleanModelName = (m) => {
  let name = (m || 'Unknown').trim()
  name = name.replace(/^NVIDIA\s+/i, '').replace(/^GeForce\s+/i, '').replace(/^Tesla\s+/i, '')
  return name || 'Unknown'
}
const gpuModelStats = computed(() => {
  const counts = {}
  allGpus.value.forEach(g => {
    const name = cleanModelName(g.model)
    counts[name] = (counts[name] || 0) + 1
  })
  return Object.entries(counts)
    .map(([name, count]) => ({ name, count, pct: totalGPUs.value ? Math.round((count / totalGPUs.value) * 100) : 0 }))
    .sort((a, b) => b.count - a.count)
})

const maxGpuTemp = computed(() => {
  if (!allGpus.value.length) return '--'
  return Math.max(...allGpus.value.map(g => parseInt(g.temp) || 0))
})
const maxGpuTempText = computed(() => maxGpuTemp.value === '--' ? '--' : maxGpuTemp.value + '°C')

const highLoadServers = computed(() => onlineServers.value.filter(s => s.cpu_percent >= 80))
const idleServers = computed(() => onlineServers.value.filter(s => s.cpu_percent <= 10))

const onlineRate = computed(() => {
  if (!servers.value.length) return 0
  return Math.round((totalOnlineServers.value / servers.value.length) * 100)
})
const onlineRateStyle = computed(() => ({
  background: `conic-gradient(#22d3ee ${onlineRate.value * 3.6}deg, rgba(148, 163, 184, 0.12) 0deg)`
}))

const pct = (n) => {
  if (!servers.value.length) return '0%'
  return Math.round((n / servers.value.length) * 100) + '%'
}

const getLoadLevel = (pct) => {
  if (pct >= 80) return 'level-danger'
  if (pct >= 40) return 'level-warning'
  return 'level-normal'
}

// ---------- 数字滚动动画 ----------
function useAnimatedNumber(source) {
  const display = ref(0)
  let raf = null
  watch(source, (val) => {
    const target = typeof val === 'number' ? val : 0
    const start = display.value
    const change = target - start
    if (change === 0) return
    const t0 = performance.now()
    const dur = 700
    cancelAnimationFrame(raf)
    const step = (now) => {
      const p = Math.min((now - t0) / dur, 1)
      const ease = 1 - Math.pow(1 - p, 3)
      display.value = Math.round(start + change * ease)
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
  }, { immediate: true })
  return display
}
const gpuCountAnimated = useAnimatedNumber(totalGPUs)
const onlineCountAnimated = useAnimatedNumber(totalOnlineServers)

// ---------- 图表渲染 ----------
const renderCharts = () => {
  // GPU 型号分布柱状图（Top N + 其他）
  if (!barChart.value && barChartRef.value) barChart.value = echarts.init(barChartRef.value)
  if (barChart.value) {
    const stats = gpuModelStats.value
    const TOP = 8
    let items = stats.slice(0, TOP)
    if (stats.length > TOP) {
      const restCount = stats.slice(TOP).reduce((s, e) => s + e.count, 0)
      items = [...items, { name: '其他型号', count: restCount, pct: totalGPUs.value ? Math.round((restCount / totalGPUs.value) * 100) : 0 }]
    }
    const labels = items.map(i => i.name)
    const values = items.map(i => i.count)
    const barGradient = new echarts.graphic.LinearGradient(0, 0, 1, 0, [
      { offset: 0, color: '#0ea5e9' },
      { offset: 0.6, color: '#22d3ee' },
      { offset: 1, color: '#67e8f9' }
    ])

    barChart.value.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(8, 18, 38, 0.92)',
        borderColor: 'rgba(34, 211, 238, 0.35)',
        textStyle: { color: '#dbeafe' },
        formatter: (ps) => {
          const it = items[ps[0].dataIndex]
          return `<div style="font-weight:700;margin-bottom:4px;color:#e0f2fe">${it.name}</div><span>${it.count} 张 · 占比 ${it.pct}%</span>`
        }
      },
      grid: { left: '2%', right: '9%', bottom: '0%', top: '4%', containLabel: true },
      xAxis: { type: 'value', show: false },
      yAxis: {
        type: 'category',
        inverse: true,
        data: labels,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#a9c8e8', fontSize: 12, margin: 12 }
      },
      series: [{
        type: 'bar',
        data: values,
        barWidth: 13,
        showBackground: true,
        backgroundStyle: { color: 'rgba(148, 163, 184, 0.08)', borderRadius: 7 },
        label: {
          show: true, position: 'right', color: '#7dd3fc', fontWeight: 'bold', fontSize: 12,
          formatter: (p) => `${p.value} 张`
        },
        itemStyle: {
          borderRadius: 7,
          color: barGradient,
          shadowBlur: 12,
          shadowColor: 'rgba(34, 211, 238, 0.35)'
        }
      }]
    })
  }
}

// ---------- 行情 K 线渲染 ----------
// 计算 N 日均线（不足 N 根的位置返回 null）
const calcMA = (klines, n) => klines.map((k, i) => {
  if (i < n - 1) return null
  let sum = 0
  for (let j = 0; j < n; j++) sum += klines[i - j].close
  return +(sum / n).toFixed(2)
})

// 从后端 K 线数据构造 ECharts 所需的数据序列
const marketTheme = (klines) => ({
  times: klines.map(k => k.time),
  candles: klines.map(k => [k.open, k.close, k.low, k.high]),
  ma5: calcMA(klines, 5),
  ma10: calcMA(klines, 10),
  ma20: calcMA(klines, 20),
})

// 顶栏迷你 K 线（无坐标轴，仅看走势）
const renderMiniKline = () => {
  if (!miniKline.value && miniKlineRef.value) miniKline.value = echarts.init(miniKlineRef.value)
  if (!miniKline.value || !marketData.value) return
  const { times, candles } = marketTheme(marketData.value.klines)
  miniKline.value.setOption({
    animation: false,
    grid: { left: 0, right: 0, top: 4, bottom: 0 },
    xAxis: { type: 'category', data: times, show: false },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{
      type: 'candlestick',
      data: candles,
      // A股惯例：红涨绿跌
      itemStyle: { color: '#f87171', color0: '#34d399', borderColor: '#f87171', borderColor0: '#34d399' }
    }]
  })
}

// 弹窗大图：K 线 + MA5/10/20 + 缩放
const renderBigKline = () => {
  if (!bigKline.value && bigKlineRef.value) bigKline.value = echarts.init(bigKlineRef.value)
  if (!bigKline.value || !marketData.value) return
  const { times, candles, ma5, ma10, ma20 } = marketTheme(marketData.value.klines)
  bigKline.value.setOption({
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(8, 18, 38, 0.92)',
      borderColor: 'rgba(34, 211, 238, 0.35)',
      textStyle: { color: '#dbeafe' }
    },
    legend: { data: ['K线', 'MA5', 'MA10', 'MA20'], top: 0, textStyle: { color: '#a9c8e8' } },
    grid: { left: '3%', right: '2%', top: '12%', bottom: '14%' },
    xAxis: {
      type: 'category',
      data: times,
      axisLine: { lineStyle: { color: 'rgba(148,163,184,.3)' } },
      axisLabel: { color: '#a9c8e8' }
    },
    yAxis: {
      scale: true,
      splitLine: { lineStyle: { color: 'rgba(148,163,184,.15)' } },
      axisLabel: { color: '#a9c8e8' }
    },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', start: 0, end: 100, height: 16, bottom: 2,
        borderColor: 'rgba(34,211,238,.3)', backgroundColor: 'rgba(8,16,34,.6)',
        fillerColor: 'rgba(34,211,238,.15)', textStyle: { color: '#a9c8e8' } }
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: candles,
        itemStyle: { color: '#f87171', color0: '#34d399', borderColor: '#f87171', borderColor0: '#34d399' }
      },
      { name: 'MA5', type: 'line', data: ma5, smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#fbbf24' } },
      { name: 'MA10', type: 'line', data: ma10, smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#22d3ee' } },
      { name: 'MA20', type: 'line', data: ma20, smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#8b5cf6' } }
    ]
  })
}

const openMarketModal = () => {
  showMarketModal.value = true
  nextTick(() => renderBigKline())
}

const closeMarketModal = () => {
  showMarketModal.value = false
  if (bigKline.value) { bigKline.value.dispose(); bigKline.value = null }
}

watch(marketData, () => {
  nextTick(() => {
    renderMiniKline()
    if (showMarketModal.value) renderBigKline()
  })
})

watch(servers, () => { nextTick(() => renderCharts()) }, { deep: true })

// ---------- 时钟 ----------
const week = ['日', '一', '二', '三', '四', '五', '六']
const tickClock = () => {
  const d = new Date()
  currentTime.value = d.toLocaleTimeString('zh-CN', { hour12: false })
  currentDate.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 星期${week[d.getDay()]}`
}

onMounted(() => {
  subscribe()      // SSE 实时推送服务器数据
  fetchWeather()
  fetchMarket()
  tickClock()
  marketTimer = setInterval(fetchMarket, 60000)  // 行情 K 线：60 秒刷新
  timer = setInterval(tickClock, 1000)
  window.addEventListener('resize', () => { barChart.value?.resize(); miniKline.value?.resize(); bigKline.value?.resize() })
})

onUnmounted(() => {
  clearInterval(timer); clearInterval(marketTimer)
  barChart.value?.dispose(); miniKline.value?.dispose(); bigKline.value?.dispose()
})
</script>

<style scoped>
/* ==================== 全局 ==================== */
.bs-wrap {
  position: fixed; inset: 0; overflow: hidden;
  background: radial-gradient(1200px 620px at 50% -12%, #0d1b33 0%, #060a18 55%, #03050c 100%);
  color: #dbeafe;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Roboto, sans-serif;
}
.bs-layout { position: relative; z-index: 2; height: 100%; display: flex; flex-direction: column; }

/* ---- 背景装饰 ---- */
.bg-grid {
  position: absolute; inset: 0; z-index: 0; pointer-events: none;
  background-image:
    linear-gradient(rgba(34, 211, 238, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(34, 211, 238, 0.05) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(120% 90% at 50% 40%, #000 40%, transparent 100%);
  -webkit-mask-image: radial-gradient(120% 90% at 50% 40%, #000 40%, transparent 100%);
}
.bg-glow { position: absolute; border-radius: 50%; filter: blur(90px); opacity: .5; z-index: 0; pointer-events: none; }
.glow-a { width: 440px; height: 440px; background: radial-gradient(circle, rgba(34, 211, 238, .28), transparent 70%); top: -90px; left: -70px; animation: float 12s ease-in-out infinite; }
.glow-b { width: 520px; height: 520px; background: radial-gradient(circle, rgba(99, 102, 241, .26), transparent 70%); bottom: -120px; right: -80px; animation: float 15s ease-in-out infinite reverse; }
.glow-c { width: 320px; height: 320px; background: radial-gradient(circle, rgba(245, 158, 11, .16), transparent 70%); top: 40%; left: 46%; animation: float 18s ease-in-out infinite; }
.scanline {
  position: absolute; left: 0; right: 0; top: 0; height: 140px; z-index: 1; pointer-events: none;
  background: linear-gradient(180deg, transparent, rgba(34, 211, 238, .05), transparent);
  animation: scan 9s linear infinite;
}
@keyframes scan { 0% { transform: translateY(-140px); } 100% { transform: translateY(100vh); } }
@keyframes float { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(30px, 22px); } }

/* ==================== 顶栏 ==================== */
.bs-header {
  height: 76px; display: flex; align-items: center; justify-content: space-between; padding: 0 22px; flex-shrink: 0;
  background: linear-gradient(180deg, rgba(13, 25, 48, .92), rgba(8, 14, 30, .5));
  border-bottom: 1px solid rgba(56, 189, 248, .2);
  box-shadow: 0 4px 30px rgba(0, 0, 0, .5);
  position: relative;
}
.bs-header::after {
  content: ''; position: absolute; left: 0; bottom: -1px; height: 2px; width: 100%;
  background: linear-gradient(90deg, transparent, rgba(34, 211, 238, .7), transparent);
}
.back-btn {
  text-decoration: none; color: #7dd3fc; font-size: 13px; font-weight: 600; letter-spacing: 1px;
  padding: 8px 16px; border: 1px solid rgba(34, 211, 238, .35); border-radius: 6px;
  background: rgba(34, 211, 238, .06); transition: .25s;
}
.back-btn:hover { background: rgba(34, 211, 238, .16); color: #e0f2fe; box-shadow: 0 0 16px rgba(34, 211, 238, .35); }

.header-center { display: flex; align-items: center; gap: 18px; }
.title-wing { display: inline-block; width: 90px; height: 2px; background: linear-gradient(90deg, transparent, #22d3ee); position: relative; }
.title-wing::after { content: ''; position: absolute; right: 0; top: -3px; width: 8px; height: 8px; background: #22d3ee; border-radius: 50%; box-shadow: 0 0 10px #22d3ee; }
.wing-right { transform: scaleX(-1); }
.title-box { text-align: center; }
.title {
  margin: 0; font-size: 26px; font-weight: 800; letter-spacing: 3px;
  background: linear-gradient(90deg, #7dd3fc, #ffffff, #7dd3fc);
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  text-shadow: 0 0 30px rgba(34, 211, 238, .4);
}
.subtitle { margin: 2px 0 0; font-size: 11px; letter-spacing: 4px; color: #4b8bb8; font-weight: 600; }

.header-right { display: flex; align-items: center; gap: 14px; }
.weather { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border: 1px solid rgba(34, 211, 238, .25); border-radius: 6px; background: rgba(34, 211, 238, .06); }
.weather-icon { font-size: 22px; line-height: 1; }
.weather-info { display: flex; flex-direction: column; align-items: flex-start; line-height: 1.15; }
.weather-temp { font-size: 18px; font-weight: 800; color: #e0f2fe; font-family: "SFMono-Regular", Consolas, monospace; text-shadow: 0 0 10px rgba(34, 211, 238, .4); }
.weather-temp em { font-style: normal; font-size: 11px; color: #7dd3fc; margin-left: 1px; }
.weather-desc { font-size: 10px; color: #7dd3fc; letter-spacing: 1px; }

/* ---- 顶栏：半导体行情 K 线卡片 ---- */
.market { display: flex; align-items: center; gap: 10px; padding: 5px 10px; border: 1px solid rgba(34, 211, 238, .25); border-radius: 6px; background: rgba(34, 211, 238, .06); cursor: pointer; transition: .25s; }
.market:hover { background: rgba(34, 211, 238, .14); box-shadow: 0 0 14px rgba(34, 211, 238, .3); }
.market-info { display: flex; flex-direction: column; align-items: flex-start; line-height: 1.15; }
.market-name { font-size: 10px; color: #7dd3fc; letter-spacing: 1px; }
.market-price { font-size: 15px; font-weight: 800; font-family: "SFMono-Regular", Consolas, monospace; }
.market-price em { font-style: normal; font-size: 10px; margin-left: 4px; }
.market-up { color: #f87171; }    /* A股惯例：红涨 */
.market-down { color: #34d399; }  /* 绿跌 */
.market-mini { width: 110px; height: 34px; }

/* ---- 行情 K 线弹窗 ---- */
.market-modal-overlay { position: fixed; inset: 0; z-index: 1000; background: rgba(3, 5, 12, .75); display: flex; align-items: center; justify-content: center; }
.market-modal { width: 860px; max-width: 92vw; padding: 16px 18px; }
.market-modal-close { cursor: pointer; color: #94a3b8; font-size: 16px; }
.market-modal-close:hover { color: #f87171; }
.market-big { width: 100%; height: 420px; }
.live-dot { width: 9px; height: 9px; border-radius: 50%; background: #34d399; box-shadow: 0 0 12px #34d399; animation: pulse 1.6s ease-in-out infinite; }
.datetime { display: flex; flex-direction: column; align-items: flex-end; }
.date { font-size: 11px; color: #7dd3fc; letter-spacing: 1px; }
.time { font-size: 22px; font-weight: 700; font-family: "SFMono-Regular", Consolas, monospace; color: #e0f2fe; text-shadow: 0 0 14px rgba(34, 211, 238, .5); }
@keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .4; transform: scale(.8); } }

/* ==================== 主体布局 ==================== */
.bs-main { flex: 1; display: grid; grid-template-columns: 300px 1fr 320px; gap: 14px; padding: 14px 16px; min-height: 0; overflow: hidden; }
.bs-left, .bs-right { display: flex; flex-direction: column; gap: 14px; min-height: 0; overflow: hidden; }
.bs-center { min-width: 0; min-height: 0; display: flex; }
.bs-bottom { height: 216px; padding: 0 16px 14px; flex-shrink: 0; }

/* ==================== 科技面板 ==================== */
.tech-panel {
  position: relative;
  background: linear-gradient(160deg, rgba(21, 40, 74, .72), rgba(8, 16, 34, .85));
  border: 1px solid rgba(56, 189, 248, .18);
  border-radius: 6px; padding: 14px 16px;
  backdrop-filter: blur(6px);
  box-shadow: inset 0 0 32px rgba(34, 211, 238, .05), 0 6px 22px rgba(0, 0, 0, .45);
}
.corner { position: absolute; width: 14px; height: 14px; border: 2px solid #22d3ee; opacity: .9; filter: drop-shadow(0 0 4px rgba(34, 211, 238, .6)); }
.corner.tl { top: -2px; left: -2px; border-right: none; border-bottom: none; }
.corner.tr { top: -2px; right: -2px; border-left: none; border-bottom: none; }
.corner.bl { bottom: -2px; left: -2px; border-right: none; border-top: none; }
.corner.br { bottom: -2px; right: -2px; border-left: none; border-top: none; }

.panel-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; padding-bottom: 9px; border-bottom: 1px solid rgba(56, 189, 248, .12); position: relative; }
.panel-head::after { content: ''; position: absolute; left: 0; bottom: -1px; width: 46px; height: 2px; background: linear-gradient(90deg, #22d3ee, transparent); }
.panel-head h3 { margin: 0; font-size: 14px; color: #c7e7ff; letter-spacing: 1px; font-weight: 600; }
.ph-ico { font-style: normal; font-size: 15px; }
.head-between { justify-content: space-between; }
.head-left { display: flex; align-items: center; gap: 8px; }
.head-sub { font-size: 11px; color: #4b8bb8; letter-spacing: 1px; }
.head-sub.sub-danger { color: #f87171; }

/* ==================== 左列：算力总览 ==================== */
.gpu-hero { display: flex; align-items: baseline; justify-content: center; gap: 8px; padding: 8px 0 16px; }
.gpu-num {
  font-size: 58px; font-weight: 900; line-height: 1; font-family: "SFMono-Regular", Consolas, monospace;
  background: linear-gradient(180deg, #e0f2fe, #22d3ee);
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  filter: drop-shadow(0 0 18px rgba(34, 211, 238, .5));
  animation: glow-num 3s ease-in-out infinite;
}
.gpu-unit { font-size: 15px; color: #7dd3fc; font-weight: 700; letter-spacing: 1px; }
@keyframes glow-num { 0%, 100% { filter: drop-shadow(0 0 12px rgba(34, 211, 238, .4)); } 50% { filter: drop-shadow(0 0 26px rgba(34, 211, 238, .75)); } }

.rate-row { display: flex; align-items: center; gap: 18px; padding: 8px 4px; }
.rate-ring { width: 92px; height: 92px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0; filter: drop-shadow(0 0 10px rgba(34, 211, 238, .35)); }
.rate-inner { width: 72px; height: 72px; border-radius: 50%; background: #0a1428; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.rate-val { font-size: 20px; font-weight: 800; color: #7dd3fc; }
.rate-label { font-size: 10px; color: #4b8bb8; letter-spacing: 1px; }
.rate-detail { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.rate-line { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #8fb3d0; }
.rate-line b { font-size: 16px; font-family: "SFMono-Regular", Consolas, monospace; }
.c-green { color: #34d399; }
.c-red { color: #f87171; }
.c-orange { color: #fbbf24; }
.c-cyan { color: #22d3ee; }

.load-section { margin-top: 10px; display: flex; flex-direction: column; gap: 14px; }
.load-label { display: flex; justify-content: space-between; font-size: 12px; color: #8fb3d0; margin-bottom: 6px; }
.load-bar { height: 6px; background: rgba(148, 163, 184, .12); border-radius: 3px; overflow: hidden; }
.load-fill { display: block; height: 100%; border-radius: 3px; transition: width .6s; position: relative; }
.load-fill::after { content: ''; position: absolute; inset: 0; background: linear-gradient(90deg, transparent, rgba(255, 255, 255, .5), transparent); animation: shimmer 2.4s infinite; }
.fill-orange { background: linear-gradient(90deg, #f97316, #fbbf24); box-shadow: 0 0 10px rgba(251, 191, 36, .5); }
.fill-cyan { background: linear-gradient(90deg, #0ea5e9, #22d3ee); box-shadow: 0 0 10px rgba(34, 211, 238, .5); }
@keyframes shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }

/* ==================== 左列：集群状态 ==================== */
.status-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.status-list li { display: flex; justify-content: space-between; align-items: center; padding: 11px 6px; border-bottom: 1px dashed rgba(56, 189, 248, .1); }
.status-list li:last-child { border-bottom: none; }
.status-list .k { font-size: 13px; color: #8fb3d0; display: flex; align-items: center; gap: 8px; }
.status-list .v { font-size: 16px; font-weight: 800; font-family: "SFMono-Regular", Consolas, monospace; color: #e0f2fe; }
.dot-ico { width: 8px; height: 8px; border-radius: 50%; display: inline-block; box-shadow: 0 0 8px currentColor; }
.i-orange { background: #fbbf24; color: #fbbf24; }
.i-cyan { background: #22d3ee; color: #22d3ee; }
.i-red { background: #f87171; color: #f87171; }
.i-violet { background: #8b5cf6; color: #8b5cf6; }

/* ==================== 中列：节点分组矩阵 ==================== */
.center-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.matrix-container {
  flex: 1; min-height: 0;
  overflow-y: auto; overflow-x: hidden;
  padding: 2px 4px 10px 2px;
}
.matrix-container::-webkit-scrollbar { width: 8px; }
.matrix-container::-webkit-scrollbar-track { background: rgba(148, 163, 184, .06); border-radius: 4px; }
.matrix-container::-webkit-scrollbar-thumb { background: rgba(34, 211, 238, .35); border-radius: 4px; }

.group-section { margin-bottom: 14px; }
.group-section:last-child { margin-bottom: 0; }
.group-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.gh-line { width: 3px; height: 14px; border-radius: 2px; background: linear-gradient(180deg, #22d3ee, transparent); box-shadow: 0 0 8px #22d3ee; flex-shrink: 0; }
.group-header strong { font-size: 14px; color: #c7e7ff; letter-spacing: 1px; font-weight: 700; }
.gh-count { font-size: 11px; color: #4b8bb8; background: rgba(34, 211, 238, .1); padding: 2px 8px; border-radius: 10px; border: 1px solid rgba(34, 211, 238, .2); }

.node-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }

.node-tile { border: 1px solid transparent; border-radius: 4px; padding: 7px 9px; display: flex; flex-direction: column; gap: 5px; transition: .2s; position: relative; overflow: hidden; }
.node-tile::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; }
.node-tile:hover { transform: translateY(-1px); filter: brightness(1.15); }
.nt-top { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
.nt-name { flex: 1; min-width: 0; font-size: 12px; font-weight: 700; color: #dbeafe; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nt-gpu { flex-shrink: 0; font-size: 10px; background: rgba(34, 211, 238, .15); padding: 1px 5px; border-radius: 3px; font-weight: 700; color: #7dd3fc; border: 1px solid rgba(34, 211, 238, .3); }
.nt-meta { display: flex; align-items: center; gap: 6px; }
.nt-val { min-width: 46px; font-size: 11px; font-weight: 700; font-family: "SFMono-Regular", Consolas, monospace; }
.nt-bar { flex: 1; height: 3px; background: rgba(148, 163, 184, .15); border-radius: 2px; overflow: hidden; }
.nt-bar i { display: block; height: 100%; border-radius: 2px; }

.level-normal { background: rgba(16, 185, 129, .1); border-color: rgba(16, 185, 129, .32); }
.level-normal::before { background: #34d399; box-shadow: 0 0 8px #34d399; }
.level-normal .nt-val { color: #6ee7b7; }
.level-normal .nt-bar i { background: #34d399; }
.level-warning { background: rgba(245, 158, 11, .1); border-color: rgba(245, 158, 11, .32); }
.level-warning::before { background: #fbbf24; box-shadow: 0 0 8px #fbbf24; }
.level-warning .nt-val { color: #fcd34d; }
.level-warning .nt-bar i { background: #fbbf24; }
.level-danger { background: rgba(239, 68, 68, .13); border-color: rgba(239, 68, 68, .4); }
.level-danger::before { background: #f87171; box-shadow: 0 0 8px #f87171; }
.level-danger .nt-val { color: #fca5a5; }
.level-danger .nt-bar i { background: #f87171; }

/* ==================== 右列：区域占用排行 ==================== */
.rank-panel { height: 300px; flex-shrink: 0; display: flex; flex-direction: column; }
.rank-list { list-style: none; margin: 0; padding: 0; flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; padding-right: 2px; }
.rank-list::-webkit-scrollbar { width: 6px; }
.rank-list::-webkit-scrollbar-thumb { background: rgba(34, 211, 238, .35); border-radius: 3px; }
.rank-list li { display: flex; align-items: center; gap: 10px; padding: 8px 10px; background: rgba(34, 211, 238, .05); border: 1px solid rgba(56, 189, 248, .14); border-radius: 5px; transition: .2s; }
.rank-list li:hover { background: rgba(34, 211, 238, .1); border-color: rgba(34, 211, 238, .3); }
.rank-no { width: 22px; height: 22px; border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 800; font-family: "SFMono-Regular", Consolas, monospace; background: rgba(148, 163, 184, .15); color: #8fb3d0; flex-shrink: 0; }
.rank-no.rank-1 { background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #0a1428; box-shadow: 0 0 10px rgba(251, 191, 36, .5); }
.rank-no.rank-2 { background: linear-gradient(135deg, #e2e8f0, #94a3b8); color: #0a1428; box-shadow: 0 0 8px rgba(226, 232, 240, .4); }
.rank-no.rank-3 { background: linear-gradient(135deg, #f59e0b, #b45309); color: #0a1428; box-shadow: 0 0 8px rgba(245, 158, 11, .4); }
.rank-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.rank-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rank-name { font-size: 13px; font-weight: 700; color: #dbeafe; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rank-group { flex-shrink: 0; font-size: 10px; color: #7dd3fc; background: rgba(34, 211, 238, .12); padding: 1px 6px; border-radius: 8px; border: 1px solid rgba(34, 211, 238, .25); }
.rank-bar { height: 5px; background: rgba(148, 163, 184, .15); border-radius: 3px; overflow: hidden; }
.rank-bar i { display: block; height: 100%; border-radius: 3px; transition: width .6s; }
.bar-normal { background: linear-gradient(90deg, #10b981, #34d399); box-shadow: 0 0 8px rgba(52, 211, 153, .4); }
.bar-warning { background: linear-gradient(90deg, #f59e0b, #fbbf24); box-shadow: 0 0 8px rgba(251, 191, 36, .4); }
.bar-danger { background: linear-gradient(90deg, #ef4444, #f87171); box-shadow: 0 0 8px rgba(248, 113, 113, .5); }
.rank-val { min-width: 44px; text-align: right; font-size: 15px; font-weight: 800; font-family: "SFMono-Regular", Consolas, monospace; flex-shrink: 0; }
.val-normal { color: #34d399; }
.val-warning { color: #fbbf24; }
.val-danger { color: #f87171; }

/* ==================== 右列：告警日志 ==================== */
.alert-panel { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.alert-list { list-style: none; margin: 0; padding: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.alert-list::-webkit-scrollbar { width: 6px; }
.alert-list::-webkit-scrollbar-thumb { background: rgba(239, 68, 68, .35); border-radius: 3px; }
.alert-list li { display: flex; align-items: center; gap: 10px; padding: 10px 12px; background: rgba(239, 68, 68, .08); border: 1px solid rgba(239, 68, 68, .25); border-radius: 5px; }
.alert-dot { width: 8px; height: 8px; border-radius: 50%; background: #f87171; box-shadow: 0 0 10px #f87171; animation: pulse 1.2s ease-in-out infinite; flex-shrink: 0; }
.alert-body { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.alert-name { font-size: 13px; font-weight: 700; color: #fca5a5; }
.alert-ip { font-size: 11px; color: #b0898f; font-family: "SFMono-Regular", Consolas, monospace; }
.alert-tag { font-size: 10px; font-weight: 800; color: #f87171; border: 1px solid rgba(239, 68, 68, .4); padding: 2px 6px; border-radius: 3px; letter-spacing: 1px; }
.empty-state { text-align: center; color: #34d399; font-weight: 600; margin-top: 26px; font-size: 13px; }

/* ==================== 底部：柱状图 ==================== */
.bottom-panel { height: 100%; display: flex; flex-direction: column; }
.echart-box-horizontal { flex: 1; width: 100%; }
</style>
