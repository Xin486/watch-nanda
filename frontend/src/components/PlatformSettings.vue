<template>
  <section class="platform-settings">
    <p v-if="notice" role="status" :class="{ error: failed }">{{ notice }}</p>
    <p v-if="!ready" role="status">{{ loading ? '正在读取站点配置…' : '站点配置未就绪' }} <button v-if="!loading" @click="load">重试</button></p>
    <fieldset v-else :disabled="busy">
      <form class="panel" @submit.prevent="saveBranding">
        <h2>站点名称</h2>
        <label>平台名称<input v-model="brand.company_name" maxlength="80" placeholder="Node Monitor"></label>
        <label>浏览器标题<input v-model="brand.page_title" maxlength="100" placeholder="Node Monitor"></label>
        <button :disabled="busy">保存站点名称</button>
      </form>
      <form class="panel" @submit.prevent="saveAgent">
        <h2>Agent 上报入口</h2>
        <p>填写节点能够访问的监控中心地址。保存后用于后续安装或重部署，不会修改已运行节点的配置。</p>
        <label>默认上报地址<input v-model="agent.report_url" required placeholder="https://monitor.example.com:8443/api/agent/report" maxlength="2048"></label>
        <small>可直接填 IP 或域名及端口；省略协议和路径时自动补为 HTTPS 与 /api/agent/report。</small>
        <label>证书来源<select v-model="agent.ca_mode"><option value="site">本站 CA</option><option value="system">系统可信 CA（公网证书）</option><option value="custom">自定义 CA</option></select></label>
        <label v-if="agent.ca_mode === 'custom'">CA 公钥证书（PEM）<textarea v-model="caCertificate" rows="4" :placeholder="agent.has_custom_ca ? '已保存，留空保留原证书' : '粘贴 CA 公钥证书，不能包含私钥'" maxlength="65536" /></label>
        <details><summary>按节点所在网段使用不同入口</summary>
          <p>适用于内外网分流；多个范围重叠时优先采用更具体的网段。</p>
          <div v-for="(route, index) in agent.routes" :key="index" class="route">
            <label>节点网段<input v-model="route.network" placeholder="10.0.0.0/8" required></label>
            <label>上报地址<input v-model="route.url" placeholder="https://monitor.internal:443/api/agent/report" required></label>
            <button type="button" @click="agent.routes.splice(index, 1)">移除</button>
          </div>
          <button type="button" :disabled="agent.routes.length >= 20" @click="agent.routes.push({ network: '', url: '' })">添加网段规则</button>
        </details>
        <button :disabled="busy">保存上报配置</button>
      </form>
      <form class="panel" @submit.prevent="saveHermes">
        <h2>Hermes 助手</h2>
        <p>连接支持 OpenAI 兼容接口的模型服务。关闭助手或更改配置会停止正在执行的助手任务，更改后请新建对话。</p>
        <label class="toggle"><input v-model="hermes.enabled" type="checkbox" :disabled="!hermes.runtime_ready">启用悬浮助手</label>
        <p v-if="!hermes.runtime_ready">助手运行环境尚未安装。管理员可使用 setup.sh --with-hermes 安装后开启。</p>
        <label>API 地址<input v-model="hermes.api_base" placeholder="https://api.example.com/v1" maxlength="2048"></label>
        <label>模型名称<input v-model="hermes.model" placeholder="填写服务商提供的模型 ID" maxlength="200"></label>
        <label>API Key<input v-model="apiKey" type="password" autocomplete="new-password" :placeholder="hermes.has_api_key ? '已配置，留空保留；更换 API 地址需重填' : '填写 API Key'" maxlength="4096"></label>
        <label class="toggle"><input v-model="clearKey" type="checkbox">清除已保存的密钥</label>
        <button :disabled="busy">保存助手配置</button>
      </form>
    </fieldset>
  </section>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { usePlatformSettings } from '../composables/usePlatformSettings'
const { platform, refreshPlatform } = usePlatformSettings()
const ready = ref(false), loading = ref(false), busy = ref(false), notice = ref(''), failed = ref(false)
const brand = ref({ company_name: '', page_title: '' })
const agent = ref({ report_url: '', routes: [], ca_mode: 'site', has_custom_ca: false })
const hermes = ref({ enabled: false, api_base: '', model: '', has_api_key: false, runtime_ready: false })
const apiKey = ref(''), clearKey = ref(false), caCertificate = ref('')
let mounted = true
function error(e) { failed.value = true; notice.value = typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '配置未能保存，请检查连接后重试。' }
async function load() {
  if (loading.value) return
  loading.value = true
  try {
    await api.opsSession()
    const [a, h] = await Promise.all([api.agentSettings(), api.hermesSettings(), refreshPlatform()])
    if (!mounted) return
    agent.value = a.data; hermes.value = h.data
    brand.value = { company_name: '', page_title: '', ...platform.value.branding }
    ready.value = true; failed.value = false; notice.value = ''
  } catch (e) { if (mounted) error(e) }
  finally { if (mounted) loading.value = false }
}
async function save(action) {
  if (!ready.value || busy.value) return
  busy.value = true
  try {
    await action()
    if (mounted) { failed.value = false; notice.value = '配置已保存。'; await refreshPlatform() }
  } catch (e) { if (mounted) error(e) }
  finally { if (mounted) busy.value = false }
}
function saveBranding() { return save(async () => {
  const value = { ...brand.value }; await api.saveBranding(value)
  localStorage.setItem('companyName', value.company_name || 'Node Monitor')
  localStorage.setItem('pageTitle', value.page_title || 'Node Monitor')
  window.dispatchEvent(new Event('storage'))
}) }
function saveAgent() { return save(async () => {
  const payload = { report_url: agent.value.report_url, routes: agent.value.routes.map(r => ({ ...r })), ca_mode: agent.value.ca_mode }
  if (caCertificate.value) payload.ca_certificate = caCertificate.value
  const { data } = await api.saveAgentSettings(payload)
  if (mounted) { agent.value = data; caCertificate.value = '' }
}) }
function saveHermes() { return save(async () => {
  const payload = { enabled: hermes.value.enabled, api_base: hermes.value.api_base, model: hermes.value.model, clear_api_key: clearKey.value }
  const key = apiKey.value
  if (key) payload.api_key = key
  const { data } = await api.saveHermesSettings(payload)
  if (mounted) {
    hermes.value = data
    if (apiKey.value === key) apiKey.value = ''
    clearKey.value = false
  }
}) }
onMounted(load)
onBeforeUnmount(() => { mounted = false; apiKey.value = ''; caCertificate.value = '' })
</script>

<style scoped>
.platform-settings{display:grid;gap:20px;max-width:800px;margin-bottom:20px;color:var(--text)}fieldset{display:grid;gap:20px;min-width:0;border:0;margin:0;padding:0}.panel{padding:25px;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow)}h2{margin:0 0 16px;font-size:18px}p,small{color:var(--text-muted);font-size:13px;line-height:1.7}label{display:flex;flex-direction:column;gap:8px;margin:16px 0;font-size:13px;color:var(--text-secondary)}input,select,textarea{min-width:0;padding:10px;border:1px solid var(--border);border-radius:7px;background:var(--surface-soft);color:var(--text);font:inherit}textarea{resize:vertical}.toggle{flex-direction:row;align-items:center}button{padding:9px 14px;border:1px solid var(--accent-border);border-radius:7px;color:var(--accent);background:var(--accent-soft);cursor:pointer;font:inherit;font-size:13px}button:disabled{opacity:.5;cursor:not-allowed}.route{padding:0 12px 12px;margin:12px 0;border:1px solid var(--border);border-radius:8px}details{margin:18px 0}summary{cursor:pointer;font-size:13px}.error{color:var(--danger)}input:focus-visible,textarea:focus-visible,select:focus-visible,button:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
</style>
