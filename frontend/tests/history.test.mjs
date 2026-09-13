import test from 'node:test'
import assert from 'node:assert/strict'
import {prepareHistory, chartOption, beijingTime} from '../src/utils/historyChart.js'
const end=Date.parse('2026-09-13T12:00:00Z')
const row=(minutes,cpu=20)=>({time:new Date(end-minutes*60000).toISOString(),cpu,ram:30,gpu:null})
test('sort, deduplicate, reject invalid dates/values and preserve genuine zero',()=>{
 const h=prepareHistory({data:[row(10,0),row(20,10),row(20,40),{time:'invalid',cpu:90},row(30,NaN)]},1,end)
 assert.deepEqual(h.points.map(p=>p.cpu),[null,40,0]);assert.equal(h.summary.cpu.average,20)
})
test('missing snapshots insert a gap, daily missing days also do',()=>{
 const h=prepareHistory({data:[row(80),row(10)]},1,end)
 assert.equal(h.seriesPoints.length,3);assert.equal(h.seriesPoints[1].cpu,null)
 const d=prepareHistory({data:[row(4*1440),row(1440)]},90,end)
 assert.equal(d.seriesPoints.length,3)
})
test('backend weighted statistics preserved, zero is not empty',()=>{
 const h=prepareHistory({data:[row(10,0)],summary:{cpu:{average:12,peak:80}}},1,end)
 assert.equal(h.empty,false);assert.equal(h.summary.cpu.average,12)
})
test('long axis labels suppress overlaps and use fixed dates',()=>{
 const h=prepareHistory({data:[row(10)]},365,end)
 const o=chartOption(h,340,true)
 assert.equal(o.xAxis.axisLabel.hideOverlap,true);assert.equal(o.xAxis.splitNumber,2)
 assert.equal(o.xAxis.axisLabel.formatter(end),'2026-09')
 assert.ok(o.series.every(s=>s.connectNulls===false && s.smooth===false))
})
test('single-point data uses visible symbols; timezone is explicit',()=>{
 const h=prepareHistory({data:[row(10)]},1,end)
 assert.equal(chartOption(h,900).series[0].showSymbol,true)
 assert.equal(beijingTime(Date.parse('2026-09-12T16:00:00Z')),'2026-09-13 00:00')
})
test('invalid response fails explicitly rather than a blank chart',()=>{
 assert.throws(()=>prepareHistory({data:null},1,end));assert.equal(prepareHistory({data:[]},1,end).empty,true)
})
