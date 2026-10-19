import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { freshServer } from '../src/utils/serverStatus.js'
import { prepareHistory, chartOption } from '../src/utils/historyChart.js'

const deferred = () => {
  let resolve, reject
  const promise = new Promise((a,b) => { resolve=a; reject=b })
  return { promise, resolve, reject }
}
const tick = () => new Promise(resolve => setImmediate(resolve))
function setup(file, names, overrides={}) {
  let code=fs.readFileSync(new URL('../src/'+file,import.meta.url),'utf8')
  if (file.endsWith('.vue')) code=code.match(/<script setup>([\s\S]*?)<\/script>/)[1]
  code=code.replace(/^import .*$/gm,'').replace(/export function /g,'function ')
  const unmounts=[], intervals=[], listeners=new Map(), store=new Map()
  const context={
    ref:value=>({value}), shallowRef:value=>({value}), computed:get=>({get value(){return get()}}),
    onMounted:()=>{}, onUnmounted:cb=>unmounts.push(cb), onBeforeUnmount:cb=>unmounts.push(cb), nextTick:async cb=>cb?.(),
    watch:()=>{}, useRoute:()=>({query:{}}), useRouter:()=>({push:()=>{}}),
    defineProps:()=>({serverId:1}), defineEmits:()=>()=>{}, location:{protocol:'https:'},
    setInterval:(cb,ms)=>{ const task={cb,ms}; intervals.push(task); return task }, clearInterval:()=>{},
    window:{location:{hostname:'test'}, addEventListener:(name,cb)=>listeners.set(name,cb),removeEventListener:name=>listeners.delete(name),dispatchEvent:()=>{}},
    localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v)},
    document:{}, console, alert:()=>{}, CustomEvent:class{}, AbortController,
    performance:{now:()=>1000}, freshServer, secureOrigin:()=> 'https://test', ...overrides,
  }
  vm.createContext(context)
  vm.runInContext(code+'\nthis.exposed={'+names.join(',')+'}',context)
  return {...context.exposed,unmounts,intervals,listeners,store}
}

test('freshness expires without network messages and preserves pending/offline distinctions',()=>{
  const s={collection_status:'online',data_age_seconds:175,stale_after_seconds:180}
  assert.equal(freshServer(s,5).status,'online')
  assert.equal(freshServer(s,6).status,'stale')
  assert.equal(freshServer({...s,data_age_seconds:null},1000).status,'pending')
  assert.equal(freshServer({...s,collection_status:'offline'},0).status,'offline')
})

test('agent freshness uses slower thresholds and transitions to offline during disconnection',()=>{
  const s={collection_status:'online',data_age_seconds:720,stale_after_seconds:720,offline_after_seconds:960}
  assert.equal(freshServer(s,0).status,'online')
  assert.equal(freshServer(s,1).status,'stale')
  assert.equal(freshServer(s,240).status,'stale')
  assert.equal(freshServer(s,241).status,'offline')
})

test('SSE remains authoritative over late REST; silent stream starts fallback; cleanup closes connection',async()=>{
  const requests=[],sources=[]; let now=1000
  const h=setup('composables/useServers.js',['useServers'],{
    axios:{get:()=>{const d=deferred();requests.push(d);return d.promise}},
    performance:{now:()=>now},
    EventSource:class{constructor(){sources.push(this)} close(){this.closed=true}},
  })
  const a=h.useServers(), b=h.useServers();a.subscribe();a.subscribe();b.subscribe()
  assert.equal(sources.length,1)
  sources[0].onmessage({data:JSON.stringify([{id:1,collection_status:'online',data_age_seconds:0}])})
  requests[0].resolve({data:[{id:99}]});await tick()
  assert.equal(a.servers.value[0].id,1)
  now+=26000;h.intervals.find(t=>t.ms===15000).cb();assert.equal(requests.length,2)
  now+=180000;h.intervals.find(t=>t.ms===1000).cb();assert.equal(a.servers.value[0].status,'stale')
  h.unmounts[0]();assert.equal(sources[0].closed,undefined)
  h.unmounts[1]();assert.equal(sources[0].closed,true)
  assert.equal(h.listeners.has('servers-refresh'),false)
})

test('credential sync updates a shared node immediately without resetting other nodes freshness',async()=>{
  const requests=[],sources=[];let now=1000
  const h=setup('composables/useServers.js',['useServers'],{
    axios:{get:()=>{const d=deferred();requests.push(d);return d.promise}},performance:{now:()=>now},
    EventSource:class{constructor(){sources.push(this)}close(){}},
  })
  const state=h.useServers();state.subscribe()
  sources[0].onmessage({data:JSON.stringify([{id:1,collection_status:'offline',data_age_seconds:20},{id:2,collection_status:'online',data_age_seconds:10}])})
  now=6000;h.intervals.find(t=>t.ms===1000).cb()
  h.listeners.get('servers-refresh')()
  state.patchServer({id:1,ssh_user:'manager',has_ssh_password:true,collection_status:'pending',data_age_seconds:null})
  assert.equal(state.servers.value[0].status,'pending')
  assert.equal(state.servers.value[0].has_ssh_password,true)
  assert.equal(state.servers.value[1].data_age_seconds,15)
  requests.at(-1).resolve({data:[{id:1,collection_status:'offline',data_age_seconds:20}]});await tick()
  assert.equal(state.servers.value[0].status,'pending');assert.equal(state.servers.value.length,2)
  h.unmounts[0]()
})

test('settings late synchronization preserves edits and password omission is safe',async()=>{
  const a=deferred(), e=deferred(), saved=[]
  const h=setup('views/Settings.vue',['loadConfigs','alertForm','emailForm','alertDirty','emailDirty','saveEmailSettings','emailLoaded','hasPassword'],{
    api:{getAlertSettings:()=>a.promise,getEmailSettings:()=>e.promise,saveEmailSettings:async body=>saved.push(body)},
  })
  const loading=h.loadConfigs()
  h.emailForm.value.smtp_server='user-input.test';h.emailDirty.value=true
  h.alertForm.value.offline_threshold_minutes=17;h.alertDirty.value=true
  a.resolve({data:{offline_threshold_minutes:5}})
  e.resolve({data:{smtp_server:'server-value.test',has_password:true}})
  await loading
  assert.equal(h.emailForm.value.smtp_server,'user-input.test')
  assert.equal(h.alertForm.value.offline_threshold_minutes,17)
  assert.equal(h.hasPassword.value,true)
  await h.saveEmailSettings()
  assert.equal(saved[0].smtp_server,'user-input.test')
  assert.equal(Object.hasOwn(saved[0],'smtp_password'),false)
})

test('settings cannot save before a successful initial load',async()=>{
  let writes=0
  const h=setup('views/Settings.vue',['saveEmailSettings','saveAlertSettings'],{api:{saveEmailSettings:()=>writes++,saveAlertSettings:()=>writes++}})
  await h.saveEmailSettings();await h.saveAlertSettings();assert.equal(writes,0)
})

test('history ignores out-of-order responses and cancels on close',async()=>{
  const requests=[],rendered=[]
  const chart={setOption:o=>rendered.push(o),resize:()=>{},dispose:()=>{},clear:()=>{}}
  const h=setup('views/History.vue',['fetchHistoryData','dialogRef','chartRef','myChart','closeChart','isLoading'],{
    prepareHistory, chartOption,
    useServers:()=>({servers:{value:[]},subscribe:()=>{}}),
    api:{getHistory:(id,days,options)=>{const d=deferred();requests.push({...d,options});return d.promise}},
  })
  h.dialogRef.value={open:true,close(){this.open=false}};h.chartRef.value={clientWidth:900};h.myChart.value=chart
  const old=h.fetchHistoryData(1,1), latest=h.fetchHistoryData(1,7)
  assert.equal(requests[0].options.signal.aborted,true)
  const stamp=new Date(Date.now()-3600000).toISOString()
  requests[1].resolve({data:{data:[{time:stamp,cpu:70,ram:20,gpu:30}]}});await latest
  requests[0].resolve({data:{data:[{time:stamp,cpu:10,ram:20,gpu:30}]}});await old
  assert.equal(rendered.length,1);assert.equal(rendered[0].series[0].data[0][1],70)
  const pending=h.fetchHistoryData(1,90);h.closeChart()
  assert.equal(requests[2].options.signal.aborted,true)
  assert.equal(h.isLoading.value,false)
  requests[2].resolve({data:{data:[]}});await pending;assert.equal(rendered.length,1)
})

test('node metrics update while unsaved edits remain intact; save can be repeated',async()=>{
  const servers={value:[{id:1,hostname:'before',ip_address:'127.0.0.1',cpu_percent:10,status:'online'}]};let writes=0
  const h=setup('components/NodeDetailModal.vue',['node','form','save','saving'],{
    useServers:()=>({servers,subscribe:()=>{}}),useRoute:()=>({query:{}}),
    api:{updateServer:async()=>writes++},
  })
  h.form.value={...servers.value[0],hostname:'user-edit',ssh_password:''}
  servers.value=[{...servers.value[0],cpu_percent:80}]
  assert.equal(h.node.value.cpu_percent,80);assert.equal(h.form.value.hostname,'user-edit')
  await h.save();assert.equal(h.saving.value,false)
  await h.save();assert.equal(writes,2)
})
