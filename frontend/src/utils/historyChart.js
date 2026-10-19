export const METRICS = [
  { key: 'cpu', name: 'CPU 使用率', color: 'var(--metric-cpu)' },
  { key: 'ram', name: '内存使用率', color: 'var(--metric-memory)' },
  { key: 'gpu', name: '平均显存占用', color: 'var(--metric-gpu)' },
]
const finitePercent = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null
export const percentage = value => finitePercent(value) == null ? '—' : `${Number(value.toFixed(1))}%`
export function beijingTime(time, mode = 'full') {
  const d = new Date(Number(time) + 8 * 3600000)
  if (!Number.isFinite(d.getTime())) return '—'
  const pad = n => String(n).padStart(2, '0')
  const date = `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}`
  const clock = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
  return mode === 'month' ? date.slice(0, 7) : mode === 'day' ? date.slice(5) : mode === 'clock' ? clock : `${date} ${clock}`
}

export function prepareHistory(payload, days, now = Date.now()) {
  if (!payload || !Array.isArray(payload.data)) throw new Error('Invalid history response')
  const end = Date.parse(payload.end) || now
  const start = Date.parse(payload.start) || end - days * 86400000
  const daily = payload.aggregation === 'day' || (!payload.aggregation && days > 7)
  const records = new Map()
  for (const row of payload.data) {
    if (!row || typeof row !== 'object') continue
    const time = Date.parse(row.time)
    if (!Number.isFinite(time) || time < start || time > end) continue
    records.set(time, { time, ...Object.fromEntries(METRICS.map(({key}) => [key, finitePercent(row[key])])) })
  }
  const points = [...records.values()].sort((a,b) => a.time - b.time)
  const seriesPoints = []
  for (const point of points) {
    const prev = seriesPoints.at(-1)
    // A stopped collector leaves no rows. Preserve that gap instead of drawing
    // a misleading straight line across hours/days without measurements.
    if (prev && point.time - prev.time > (daily ? 1.5 * 86400000 : 30 * 60000)) {
      seriesPoints.push({ time: prev.time + (point.time-prev.time)/2, cpu:null, ram:null, gpu:null })
    }
    seriesPoints.push(point)
  }
  const summary = Object.fromEntries(METRICS.map(({key}) => {
    const values = points.map(p => p[key]).filter(v => v != null)
    const supplied = payload.summary?.[key]
    return [key, supplied ? { average:finitePercent(supplied.average), peak:finitePercent(supplied.peak) } : {
      average: values.length ? values.reduce((a,b) => a+b,0)/values.length : null,
      peak: values.length ? Math.max(...values) : null,
    }]
  }))
  return { start, end, daily, points, seriesPoints, summary, samples: payload.samples ?? points.length,
    empty: !points.some(p => METRICS.some(({key}) => p[key] != null)) }
}

export function chartOption(history, width, fullRange = false, palette = {
  text:'#20334d', muted:'#526985', border:'#d8e1ee', grid:'#e5ebf4', surface:'#ffffff', soft:'#f5f8fc',
  accent:'#245dcc', accentSoft:'#e8f0ff', track:'#e4ebf5', cpu:'#4e83dc', memory:'#23a18b', gpu:'#9870d2',
}) {
  const { start, end, points, seriesPoints, daily } = history
  const min = fullRange || !points.length ? start : Math.max(start, points[0].time - (daily ? 43200000 : 600000))
  const span = end - min
  const labelMode = span > 120 * 86400000 ? 'month' : span > 2 * 86400000 ? 'day' : 'clock'
  return {
    animation: false,
    textStyle: { fontFamily:'Inter, "Microsoft YaHei", sans-serif', color:palette.text },
    color: [palette.cpu, palette.memory, palette.gpu],
    legend: { top: 8, left: 'center', itemWidth: 16, itemHeight: 8, itemGap: width < 500 ? 10 : 24,
      textStyle: { color:palette.muted, fontSize:width < 500 ? 10 : 12 }, inactiveColor:palette.muted },
    grid: { top: 62, left: 48, right: 24, bottom: 86 },
    tooltip: { trigger:'axis', confine:true, renderMode:'richText', backgroundColor:palette.surface, borderColor:palette.border,
      textStyle:{ color:palette.text, fontSize:12 }, axisPointer:{ type:'line', lineStyle:{ color:palette.muted, type:'dashed' } },
      formatter: params => !params?.length ? '' : `${beijingTime(params[0].value[0])}${daily ? ' · 日均' : ''}\n` +
        params.map(p => `${p.seriesName}    ${percentage(p.value[1])}`).join('\n') },
    xAxis: { type:'time', min, max:end, splitNumber:Math.max(2, Math.floor(width/140)),
      axisLine:{ lineStyle:{color:palette.border} }, axisTick:{ show:false }, splitLine:{show:false},
      axisLabel:{ color:palette.muted, fontSize:11, margin:14, hideOverlap:true, showMinLabel:false, showMaxLabel:false,
        formatter: value => beijingTime(value,labelMode) } },
    yAxis: { type:'value', min:0, max:100, interval:25, axisLabel:{ formatter:'{value}%',color:palette.muted,fontSize:11 },
      splitLine:{ lineStyle:{ color:palette.grid,type:'dashed' } } },
    dataZoom: [{ type:'slider', bottom:10, height:22, borderColor:'transparent', backgroundColor:palette.soft,
      fillerColor:palette.accentSoft, handleStyle:{ color:palette.surface,borderColor:palette.accent },
      dataBackground:{ lineStyle:{color:palette.muted}, areaStyle:{color:palette.track} },
      selectedDataBackground:{lineStyle:{color:palette.accent},areaStyle:{color:palette.accentSoft}},
      showDetail:false, brushSelect:false, textStyle:{color:palette.muted}, filterMode:'none' },
      {type:'inside', filterMode:'none', zoomOnMouseWheel:'ctrl', moveOnMouseWheel:false}],
    series: METRICS.map(({key,name}, i) => ({ name, type:'line', smooth:false, connectNulls:false,
      showSymbol:points.length <= 10, symbol:'circle', symbolSize:5, lineStyle:{width:2},
      itemStyle:{color:[palette.cpu,palette.memory,palette.gpu][i]}, emphasis:{focus:'series'}, areaStyle:{color:[palette.cpu,palette.memory,palette.gpu][i],opacity:0.035},
      data:seriesPoints.map(p => [p.time,p[key]]) })),
  }
}
