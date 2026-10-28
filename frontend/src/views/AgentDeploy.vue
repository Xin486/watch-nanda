<template>
  <NodeAccessLayout :access="access" :title="redeploy ? '修复节点 · 重新部署 Agent' : '新服务器 · 部署 Agent'" :description="redeploy ? '为当前 Agent 节点修复采集服务，保留节点编号和历史数据。' : '为刚添加的服务器安装独立采集服务，完成监控接入。'" :login-purpose="redeploy ? '重新部署此节点的 Agent' : '部署此新节点的 Agent'" :node-label="redeploy ? '已有 Agent 节点' : '新服务器'" :selectable="false">
    <section class="deployment-panel">
      <div class="panel-caption"><span class="step-number">02</span><div><h2>{{ redeploy ? '重新部署独立 Agent' : '部署独立 Agent' }}</h2><p>每 5 分钟主动上报，无需 SSH 免密密钥。</p></div></div>
      <div class="deployment-summary"><div><span>上报入口</span><code>{{ reportEndpoint }}</code></div><div><span>运行方式</span><strong>独立运行包 · systemd 自动启动</strong></div></div>
      <p class="deployment-note">使用左侧用户名和密码连接当前节点，自动检查 sudo、时钟、磁盘、证书与出站 HTTPS。UFW/firewalld 仅补充到上报地址和端口的出站规则，保留其他规则；必须通过服务账户真实上报验证才算部署完成。</p>
      <p class="deployment-note">首次部署和重部署均覆盖安装：先终止旧 Agent、移除旧服务和配置，再安装新版并验证上报；自动处理上次中断的安装记录。保留节点编号、分组和历史，失败时尝试恢复备份。只有点击下方按钮才会开始；旧进程无法终止时会明确报错。</p>
      <p v-if="!eligible" class="operation-error" role="alert">该节点已不再使用 Agent 采集，请返回节点配置核对；此页面不会切换其采集方式。</p>
      <div class="deploy-actions"><span>{{ redeploy ? '固定当前节点，保留 Agent 采集方式。' : '部署成功后自动切换为 Agent 采集。' }}</span><button class="primary-button" :disabled="access.busy || !eligible || (!redeploy && job?.status === 'success')" @click="deploy">{{ job?.status === 'running' ? '正在部署…' : preparing ? '准备部署…' : job?.status === 'success' ? (redeploy ? '再次重新部署 Agent' : '部署完成') : job?.status === 'failed' ? '重试部署 Agent' : redeploy ? '重新部署 Agent' : '一键部署 Agent' }}</button></div>
      <div v-if="job" class="job-result" :class="job.status"><div class="job-caption" role="status"><strong>{{ job.status === 'running' ? '部署进行中' : job.status === 'success' ? '部署完成' : '需要检查' }}</strong><span>{{ job.message }}</span></div><DeploymentLog :key="job.id" :lines="job.logs" :state="feedState" :updated-at="updatedAt" /></div>
      <div class="deployment-complete"><router-link to="/dashboard">{{ job?.status === 'success' ? '完成，返回节点总览' : job?.status === 'running' ? '返回总览，任务继续运行' : '稍后部署，返回节点总览' }} ↗</router-link></div>
      <AgentHealthPanel v-if="redeploy && eligible && access.node" :key="access.node.id" :server-id="access.node.id" hide-redeploy />
    </section>
  </NodeAccessLayout>
</template>
<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { useNodeAccess } from '../composables/useNodeAccess'
import NodeAccessLayout from '../components/NodeAccessLayout.vue'
import AgentHealthPanel from '../components/AgentHealthPanel.vue'
import DeploymentLog from '../components/DeploymentLog.vue'
import { createDeploymentFeed } from '../utils/deploymentFeed'

const props = defineProps({ redeploy: { type: Boolean, default: false } })
const job = ref(null), preparing = ref(false), loading = ref(false)
const activity = computed(() => preparing.value || loading.value || job.value?.status === 'running')
const access = useNodeAccess({ selectable: false, activity })
const eligible = computed(() => !props.redeploy || access.node?.collection_mode === 'agent')
const reportEndpoint = computed(() => access.node?.ip_address?.startsWith('192.168.1.') ? 'https://192.168.1.24/api/agent/report' : 'https://114.212.165.149:59443/api/agent/report')
const feedState = ref(''), updatedAt = ref(0)
let feed, generation = 0, disposed = false

function stopFeed() { feed?.stop(); feed = null }
function startFeed() {
  stopFeed()
  const id = job.value.id, current = generation
  feed = createDeploymentFeed({
    url: `/api/ops/jobs/${encodeURIComponent(id)}/stream`,
    fetchSnapshot: options => api.deploymentJob(id, options),
    isComplete: data => data.status !== 'running',
    onState: state => { feedState.value = state },
    onError: error => { if (!disposed && current === generation) access.handleError(error) },
    onSnapshot: data => {
      if (disposed || current !== generation || job.value?.id !== id) return
      job.value = data; updatedAt.value = Date.now()
      if (data.status !== 'running') window.dispatchEvent(new CustomEvent('servers-refresh'))
    },
  })
}
async function deploy() {
  if (access.busy || !eligible.value || (!props.redeploy && job.value?.status === 'success') || !access.validInput()) return
  const current = generation, id = access.node.id
  preparing.value = true
  access.error = ''
  try {
    if (!await access.testConnection() || disposed || current !== generation) return
    if (!eligible.value) return
    const { data } = await (props.redeploy ? api.redeployAgent(id, access.payload()) : api.deployAgent(id, access.payload()))
    if (disposed || current !== generation) return
    job.value = data
    updatedAt.value = Date.now(); feedState.value = data.status === 'running' ? 'connecting' : 'complete'
    access.credentials.password = ''
    if (data.status === 'running') startFeed()
    else window.dispatchEvent(new CustomEvent('servers-refresh'))
  } catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { if (current === generation) preparing.value = false }
}
watch([() => access.authenticated, () => access.node?.id], async () => {
  const current = ++generation
  stopFeed()
  job.value = null
  feedState.value = ''; updatedAt.value = 0
  preparing.value = loading.value = false
  if (!access.authenticated || !access.node) return
  loading.value = true
  try {
    const { data } = await api.deploymentJobs(access.node.id)
    if (disposed || current !== generation) return
    job.value = data.filter(j => j.status === 'running').at(-1) || data.filter(j => (j.kind || 'deploy') === (props.redeploy ? 'redeploy' : 'deploy')).at(-1) || null
    if (job.value) { updatedAt.value = Date.now(); feedState.value = job.value.status === 'running' ? 'connecting' : 'complete' }
    if (job.value?.status === 'running') startFeed()
  } catch (e) { if (!disposed && current === generation) access.handleError(e) }
  finally { if (current === generation) loading.value = false }
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; generation++; stopFeed() })
</script>
