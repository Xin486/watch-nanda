<template>
  <main class="agent-management">
    <router-link to="/cleanup">← 返回数据清理与节点管理</router-link><h1>一键批量重新部署 Agent</h1>
    <p>按队列逐台处理已有 Agent 节点，保留节点编号、分组和历史数据。关闭页面后队列继续；停止队列会取消未执行节点，当前节点会完成部署或回滚。</p>
    <OperatorGate :access="access">
      <div class="management-toolbar"><router-link to="/settings/ssh-credentials">管理常用 SSH 用户名和密码 ↗</router-link><button :disabled="busy" @click="loadRecent">刷新最近批次</button></div>
      <div v-if="recent.length" class="management-card"><h2>最近批次</h2><div class="management-inline"><button v-for="entry in recent.slice(0, 8)" :key="entry.id" :disabled="busy" @click="openBatch(entry.id)">{{ formatTime(entry.created_at) }} · {{ entry.total }} 台 · {{ batchLabels[entry.status] }}</button></div></div>
      <section v-if="!batch" class="management-card">
        <h2>确认待重部署节点 · {{ selected.length }} 台</h2>
        <p>优先尝试常用凭据，再尝试节点已保存的凭据。缺失或认证失败时，该节点等待手动输入，队列继续。只有点击下方按钮才开始执行。</p>
        <p v-if="ids.length > selected.length">部分所选节点已删除或不再使用 Agent，已从待部署列表移除。</p>
        <div class="management-table-wrap"><table class="management-table"><thead><tr><th>节点</th><th>地址</th><th>分组</th></tr></thead><tbody><tr v-for="node in selected" :key="node.id"><td>{{ node.hostname }} · #{{ node.id }}</td><td>{{ node.ip_address }}:{{ node.ssh_port || 22 }}</td><td>{{ node.group_name || '未分组' }}</td></tr></tbody></table></div>
        <div class="management-toolbar"><button class="management-primary" :disabled="busy || !selected.length" @click="start">{{ busy ? '正在提交…' : `开始批量重部署（${selected.length} 台）` }}</button></div>
      </section>
      <template v-else>
        <section class="management-card"><div class="management-toolbar"><h2>{{ batchLabels[batch.status] }} · {{ batch.total }} 台</h2><div class="management-inline"><button :disabled="busy" @click="refresh">刷新进度</button><button v-if="!batch.cancelled && batch.counts.needs_credentials" :disabled="busy" @click="retryWaiting">重试所有等待凭据节点</button><button v-if="['running', 'waiting_input'].includes(batch.status)" :disabled="busy" @click="stop">停止后续队列</button></div></div><div class="management-counts"><span v-for="(count, state) in batch.counts" :key="state">{{ labels[state] }} {{ count }}</span></div>
          <p v-if="batch.status === 'waiting_input'">可执行节点已处理完毕，请为下方需要处理的节点补充凭据或确认指纹。可以先在设置中补充常用凭据，再重试这些节点。</p>
          <div class="management-table-wrap"><table class="management-table"><thead><tr><th>节点</th><th>进度</th><th>检查结果</th><th>操作</th></tr></thead><tbody>
            <tr v-for="item in batch.items" :key="item.server_id"><td><strong>{{ item.hostname }}</strong><p>#{{ item.server_id }} · {{ item.address }}</p></td><td>{{ labels[item.status] }}</td><td class="management-row-message">{{ item.message }}<div v-if="item.job_id"><button :disabled="busy" @click="loadLogs(item)">查看实时日志</button></div></td><td><template v-if="!batch.cancelled && ['needs_credentials', 'needs_trust', 'failed'].includes(item.status)"><button :disabled="busy" @click="inputFor(item)">{{ item.status === 'needs_trust' ? '核对指纹' : '输入用户名和密码' }}</button><button v-if="item.status !== 'needs_trust'" :disabled="busy" @click="retry(item)">重试常用/已存凭据</button></template><router-link v-if="item.status === 'success'" :to="`/nodes/${item.server_id}/agent/redeploy`">检查上报 ↗</router-link></td></tr>
          </tbody></table></div>
          <div class="management-toolbar"><span role="status">已处理 {{ processed }} / {{ batch.total }} 台（含等待人工处理）</span><button v-if="selectedLogNode" :disabled="busy" @click="followRunning">跟随当前部署节点</button></div>
          <progress :value="processed" :max="batch.total" aria-label="批量部署处理进度" style="width:100%"></progress>
          <DeploymentLog :key="`${batch.id}:${liveLog?.server_id || ''}:${liveLog?.job_id || ''}`" :lines="liveLog?.logs" :title="liveLog ? `${liveLog.hostname} · #${liveLog.server_id} 部署日志` : '当前部署节点日志'" :state="feedState" :updated-at="updatedAt" />
        </section>
        <form v-if="manualItem" class="management-card" @submit.prevent="submitManual">
          <h2>处理 {{ manualItem.hostname }} · #{{ manualItem.server_id }}</h2><p>{{ manualItem.message }}</p>
          <template v-if="manualItem.status === 'needs_trust'"><p>请通过可信渠道核对 {{ manualItem.address }} 的主机指纹：</p><template v-if="manualItem.previous_fingerprint"><p>原指纹</p><code>{{ manualItem.previous_fingerprint }}</code><p>新指纹（确认重装系统或密钥变更后再允许更新）</p></template><code>{{ manualItem.fingerprint }}</code><label class="management-check"><input v-model="confirmed" type="checkbox" required>我已核对该主机指纹，并允许{{ manualItem.previous_fingerprint ? '更换原指纹' : '保存信任' }}</label></template>
          <div class="management-fields"><label>SSH 用户名<input v-model="credentials.username" maxlength="50" :required="manualItem.status !== 'needs_trust'" autocomplete="off"></label><label>SSH 密码<input v-model="credentials.password" type="password" maxlength="1024" :required="manualItem.status !== 'needs_trust'" autocomplete="new-password" :placeholder="manualItem.status === 'needs_trust' ? '可留空，继续尝试常用或已存凭据' : '请输入此节点密码'"></label></div>
          <p>手动输入仅用于本次节点重试，不加入常用凭据列表。</p><div class="management-inline"><button class="management-primary" :disabled="busy || (manualItem.status === 'needs_trust' && !confirmed)">{{ busy ? '正在提交…' : manualItem.status === 'needs_trust' ? '信任并重新入队' : '使用此凭据重新入队' }}</button><button type="button" :disabled="busy" @click="clearManual">取消输入</button></div>
        </form>
      </template>
    </OperatorGate>
  </main>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useServers } from '../composables/useServers'
import { useOperatorSession } from '../composables/useOperatorSession'
import { parseBatchIds, selectedAgents } from '../utils/batchSelection'
import OperatorGate from '../components/OperatorGate.vue'
import DeploymentLog from '../components/DeploymentLog.vue'
import { createDeploymentFeed } from '../utils/deploymentFeed'
import '../styles/agent-management.css'

const route = useRoute(), router = useRouter(), access = useOperatorSession(), { servers, subscribe } = useServers()
const ids = ref(parseBatchIds(route.query.nodes)), selected = computed(() => selectedAgents(servers.value, ids.value))
const liveLog = ref(null), selectedLogNode = ref(null), feedState = ref(''), updatedAt = ref(0)
const batch = ref(null), recent = ref([]), busy = ref(false), manualId = ref(0), confirmed = ref(false)
const credentials = reactive({ username: '', password: '' })
const manualItem = computed(() => batch.value?.items.find(item => item.server_id === manualId.value && ['needs_credentials', 'needs_trust', 'failed'].includes(item.status)))
const labels = { queued: '等待队列', running: '正在部署', needs_credentials: '等待输入凭据', needs_trust: '等待确认指纹', success: '部署成功', failed: '部署失败', cancelled: '已取消' }
const batchLabels = { running: '队列处理中', waiting_input: '等待人工处理', finished: '批次已结束', stopping: '正在停止后续队列', cancelled: '队列已停止' }
const processed = computed(() => batch.value ? batch.value.total - (batch.value.counts.queued || 0) - (batch.value.counts.running || 0) : 0)
let generation = 0, disposed = false, feed
function stopFeed() { feed?.stop(); feed = null }
function startFeed() {
  stopFeed()
  if (disposed || !access.authenticated || !batch.value) return
  const current = generation, id = batch.value.id, node = selectedLogNode.value
  feed = createDeploymentFeed({
    url: `/api/ops/agent-batches/${encodeURIComponent(id)}/stream${node ? `?log_node=${node}` : ''}`,
    fetchSnapshot: options => api.agentBatchSnapshot(id, node, options),
    isComplete: data => ['finished', 'cancelled'].includes(data.batch.status),
    onState: state => { feedState.value = state },
    onError: error => { if (!disposed && current === generation) access.handleError(error) },
    onSnapshot: data => {
      if (disposed || current !== generation || batch.value?.id !== id) return
      liveLog.value = data.live_log; updatedAt.value = Date.now(); receive(data.batch)
    },
  })
}
function formatTime(seconds) { return new Date(seconds * 1000).toLocaleString('zh-CN', { hour12: false }) }
function clearManual() { manualId.value = 0; confirmed.value = false; credentials.username = ''; credentials.password = '' }
function inputFor(item) { clearManual(); manualId.value = item.server_id; credentials.username = item.username || 'root' }
function receive(data) {
  const before = batch.value?.status
  batch.value = data
  if (!manualItem.value) clearManual()
  if (before !== data.status && ['finished', 'cancelled'].includes(data.status)) window.dispatchEvent(new CustomEvent('servers-refresh'))
}
function loadLogs(item) { if (!busy.value) { selectedLogNode.value = item.server_id; liveLog.value = null; startFeed() } }
function followRunning() { selectedLogNode.value = null; liveLog.value = null; startFeed() }
async function loadRecent() {
  const current = generation
  try { const { data } = await api.agentBatches(); if (!disposed && current === generation && access.authenticated) recent.value = data }
  catch (e) { if (!disposed && current === generation) access.handleError(e) }
}
async function openBatch(id) {
  if (busy.value) return
  const current = ++generation
  busy.value = true; liveLog.value = null; selectedLogNode.value = null; clearManual(); access.error = ''; stopFeed()
  try {
    const { data } = await api.agentBatch(id)
    if (!disposed && current === generation && access.authenticated) { receive(data); router.replace({ path: '/agents/batch-redeploy', query: { batch: data.id } }) }
  } catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { if (current === generation) { busy.value = false; startFeed() } }
}
async function refresh() {
  if (!busy.value && access.authenticated) { startFeed(); await feed?.refresh() }
}
async function start() {
  if (busy.value || !selected.value.length) return
  const current = ++generation; stopFeed(); busy.value = true; access.error = ''
  try {
    const { data } = await api.createAgentBatch(selected.value.map(node => node.id))
    if (!disposed && current === generation && access.authenticated) { receive(data); router.replace({ path: '/agents/batch-redeploy', query: { batch: data.id } }); await loadRecent() }
  } catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { if (current === generation) { busy.value = false; startFeed() } }
}
async function retry(item, body = {}) {
  if (busy.value || !batch.value) return
  const current = ++generation; stopFeed(); busy.value = true; access.error = ''
  try { const { data } = await api.retryAgentBatchNode(batch.value.id, item.server_id, body); if (!disposed && current === generation && access.authenticated) { clearManual(); receive(data) } }
  catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { delete body.password; credentials.password = ''; if (current === generation) { busy.value = false; startFeed() } }
}
async function submitManual() {
  if (busy.value || !manualItem.value) return
  const item = manualItem.value, current = generation, body = {}
  if (credentials.password) { body.username = credentials.username.trim(); body.password = credentials.password }
  if (item.status === 'needs_trust') {
    if (!confirmed.value) return
    busy.value = true
    const confirmation = {}
    if (item.previous_fingerprint) confirmation.previous_fingerprint = item.previous_fingerprint
    if (item.host) confirmation.expected_host = item.host
    if (item.port) confirmation.expected_port = item.port
    try { await api.trustHost(item.server_id, item.fingerprint, confirmation) }
    catch (e) { if (!disposed && current === generation) access.handleError(e); delete body.password; credentials.password = ''; return }
    finally { if (current === generation) busy.value = false }
  }
  if (!disposed && current === generation && access.authenticated) await retry(item, body)
  delete body.password
}
async function retryWaiting() {
  if (busy.value || !batch.value) return
  const current = ++generation; stopFeed(); busy.value = true
  try { const { data } = await api.retryAgentBatchWaiting(batch.value.id); if (!disposed && current === generation && access.authenticated) { clearManual(); receive(data) } }
  catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { if (current === generation) { busy.value = false; startFeed() } }
}
async function stop() {
  if (busy.value || !batch.value) return
  const current = ++generation; stopFeed(); busy.value = true
  try { const { data } = await api.stopAgentBatch(batch.value.id); if (!disposed && current === generation) { clearManual(); receive(data) } }
  catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { if (current === generation) { busy.value = false; startFeed() } }
}
watch(() => JSON.stringify([manualItem.value?.fingerprint, manualItem.value?.previous_fingerprint, manualItem.value?.address]), () => { confirmed.value = false }, { flush: 'sync' })
watch(() => access.authenticated, async authenticated => {
  generation++; stopFeed(); clearManual(); liveLog.value = null; selectedLogNode.value = null; feedState.value = ''; updatedAt.value = 0; batch.value = null; recent.value = []; busy.value = false
  if (!authenticated) return
  await loadRecent()
  if (access.authenticated && route.query.batch) openBatch(String(route.query.batch))
}, { immediate: true })
onMounted(subscribe)
onBeforeUnmount(() => { disposed = true; generation++; stopFeed(); clearManual() })
</script>
