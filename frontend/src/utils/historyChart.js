export const METRICS = [
  { key: 'cpu', name: 'CPU 使用率', color: '#3b82f6' },
  { key: 'ram', name: '内存使用率', color: '#14b8a6' },
  { key: 'gpu', name: '平均显存占用', color: '#8b5cf6' },
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

export function chartOption(history, width, fullRange = false) {
  const { start, end, points, seriesPoints, daily } = history
  const min = fullRange || !points.length ? start : Math.max(start, points[0].time - (daily ? 43200000 : 600000))
  const span = end - min
  const labelMode = span > 120 * 86400000 ? 'month' : span > 2 * 86400000 ? 'day' : 'clock'
  return {
    animation: false,
    textStyle: { fontFamily:'Inter, "Microsoft YaHei", sans-serif' },
    color: METRICS.map(m => m.color),
    legend: { top: 8, left: 'center', itemWidth: 16, itemHeight: 8, itemGap: width < 500 ? 10 : 24,
      textStyle: { color:'#526079', fontSize:width < 500 ? 10 : 12 } },
    grid: { top: 62, left: 48, right: 24, bottom: 86 },
    tooltip: { trigger:'axis', confine:true, renderMode:'richText', backgroundColor:'#fff', borderColor:'#e2e8f0',
      textStyle:{ color:'#334155', fontSize:12 }, axisPointer:{ type:'line', lineStyle:{ color:'#94a3b8', type:'dashed' } },
      formatter: params => !params?.length ? '' : `${beijingTime(params[0].value[0])}${daily ? ' · 日均' : ''}\n` +
        params.map(p => `${p.seriesName}    ${percentage(p.value[1])}`).join('\n') },
    xAxis: { type:'time', min, max:end, splitNumber:Math.max(2, Math.floor(width/140)),
      axisLine:{ lineStyle:{color:'#e2e8f0'} }, axisTick:{ show:false }, splitLine:{show:false},
      axisLabel:{ color:'#8491a5', fontSize:11, margin:14, hideOverlap:true, showMinLabel:false, showMaxLabel:false,
        formatter: value => beijingTime(value,labelMode) } },
    yAxis: { type:'value', min:0, max:100, interval:25, axisLabel:{ formatter:'{value}%',color:'#8491a5',fontSize:11 },
      splitLine:{ lineStyle:{ color:'#edf1f7',type:'dashed' } } },
    dataZoom: [{ type:'slider', bottom:10, height:22, borderColor:'transparent', backgroundColor:'#f3f6fb',
      fillerColor:'rgba(59,130,246,.10)', handleStyle:{ color:'#fff',borderColor:'#93b8f9' },
      showDetail:false, brushSelect:false, textStyle:{color:'#64748b'}, filterMode:'none' },
      {type:'inside', filterMode:'none', zoomOnMouseWheel:'ctrl', moveOnMouseWheel:false}],
    series: METRICS.map(({key,name,color}) => ({ name, type:'line', smooth:false, connectNulls:false,
      showSymbol:points.length <= 10, symbol:'circle', symbolSize:5, lineStyle:{width:2},
      itemStyle:{color}, emphasis:{focus:'series'}, areaStyle:{color,opacity:0.035},
      data:seriesPoints.map(p => [p.time,p[key]]) })),
  }
}
