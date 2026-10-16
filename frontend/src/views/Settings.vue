<template>
  <div class="settings-page">
    <div class="sync-status" :class="{ failed: syncError }">{{ syncError || (loading ? '正在同步服务器配置…' : '配置已同步 · 修改后点击保存') }}<button v-if="syncError" @click="loadConfigs">重试同步</button></div>
    <div class="settings-grid">
      <!-- 基础设置 -->
      <div class="panel">
        <h2>🖥️ 系统设置</h2>
        <div class="form-group">
          <label>后台标题 (大屏与侧边栏显示)</label>
          <input type="text" v-model="companyName" placeholder="输入公司或集群名称">
        </div>
        <div class="form-group" style="margin-top: 15px;">
          <label>网页标签标题 (浏览器 Tab 栏显示)</label>
          <input type="text" v-model="pageTitle" placeholder="例如: Node Monitor">
        </div>
        <button class="btn btn-primary" @click="saveBaseSettings" style="margin-top: 15px;">保存生效</button>
      </div>

      <!-- 告警规则设置 -->
      <div class="panel" @input="alertDirty = true" @change="alertDirty = true">
        <h2>⚠️ 告警规则配置</h2>
        <p class="desc">设置触发告警的安全阈值，避免因瞬间网络抖动造成的误报。</p>
        <p class="desc">主动上报节点默认每5分钟上报，12分钟标记数据过期，失联告警至少等待16分钟；此处填写更长时间时以更长时间为准。</p>
        
        <div class="form-group row-group">
          <label class="toggle-label">
            <input type="checkbox" v-model="alertForm.enable_email_alert">
            启用邮件告警
          </label>
        </div>
        
        <div class="form-group" v-if="alertForm.enable_email_alert">
          <label>安全阈值 (分钟) <span class="hint">节点失联超过此时间才会发送告警</span></label>
          <input type="number" v-model="alertForm.offline_threshold_minutes" min="1">
        </div>
        
        <div class="form-group" v-if="alertForm.enable_email_alert">
          <label>接收人邮箱 (多个邮箱用逗号分隔)</label>
          <input type="text" v-model="alertForm.alert_recipient" placeholder="admin@example.com">
        </div>
        
        <button class="btn btn-primary" :disabled="loading || !alertLoaded || savingAlert" @click="saveAlertSettings">{{ savingAlert ? '保存中…' : '保存告警规则' }}</button>
      </div>

      <!-- 邮件发件服务器配置 -->
      <div class="panel" @input="emailDirty = true" @change="emailDirty = true">
        <h2>📧 发件服务器 (SMTP)</h2>
        <p class="desc">用于系统发送告警邮件的服务端配置。</p>
        
        <div class="form-grid">
          <div class="form-group"><label>SMTP 服务器</label><input type="text" v-model="emailForm.smtp_server" placeholder="smtp.qq.com"></div>
          <div class="form-group"><label>SMTP 端口</label><input type="number" v-model="emailForm.smtp_port"></div>
          <div class="form-group"><label>发件邮箱账号</label><input type="text" v-model="emailForm.smtp_user"></div>
          <div class="form-group"><label>邮箱密码/授权码</label><input type="password" v-model="emailForm.smtp_password" :placeholder="hasPassword ? '已配置，留空保留原密码' : '输入邮箱授权码'" autocomplete="new-password"></div>
          <div class="form-group"><label>发件人名称</label><input type="text" v-model="emailForm.from_name" placeholder="Node Monitor"></div>
          <div class="form-group"><label>发件人地址</label><input type="text" v-model="emailForm.from_address"></div>
          <div class="form-group row-group">
            <label class="toggle-label"><input type="checkbox" v-model="emailForm.use_tls"> 启用 TLS 安全连接</label>
          </div>
        </div>
        
        <div class="actions">
          <button class="btn btn-primary" :disabled="loading || !emailLoaded || savingEmail" @click="saveEmailSettings">{{ savingEmail ? '保存中…' : '保存发件配置' }}</button>
          <button class="btn btn-secondary" @click="testEmail" :disabled="isTesting || loading || !emailLoaded || !alertLoaded">
            {{ isTesting ? '发送中...' : '发送测试邮件' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// 设置页：系统标题 / 告警规则 / SMTP 发件配置
import { ref, onMounted } from 'vue'
import { api } from '../api'

const companyName = ref(localStorage.getItem('companyName') || '南大仙林')
const pageTitle = ref(localStorage.getItem('pageTitle') || 'Node Monitor')

// ── 缓存键 ────────────────────────────────────────────────
const CACHE_ALERT = 'settings_alert'
const CACHE_EMAIL = 'settings_email'  // 不含密码

// ── 从缓存反序列化（失败则返回 null）────────────────────
function readCache(key) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

// ── 默认值 ────────────────────────────────────────────────
const alertDefault = { enable_email_alert: false, offline_threshold_minutes: 5, alert_recipient: '' }
const emailDefault  = { smtp_server: '', smtp_port: 465, smtp_user: '', smtp_password: '', from_name: 'Node Monitor', from_address: '', use_tls: true }

// ── 初始值优先读缓存，让页面秒开 ─────────────────────────
const cachedAlert = readCache(CACHE_ALERT)
const cachedEmail = readCache(CACHE_EMAIL)

const alertForm = ref(cachedAlert ? { ...alertDefault, ...cachedAlert } : { ...alertDefault })
// 邮件表单：缓存里不含密码，password 先留空，等后台返回后填入
const emailForm = ref(cachedEmail ? { ...emailDefault, ...cachedEmail, smtp_password: '' } : { ...emailDefault })
const isTesting = ref(false)
const alertDirty = ref(false), emailDirty = ref(false)
const loading = ref(false), syncError = ref('')
const alertLoaded = ref(false), emailLoaded = ref(false)
const hasPassword = ref(false)
const savingAlert = ref(false), savingEmail = ref(false)
let loadVersion = 0


// ── 后台静默同步（不阻塞渲染）────────────────────────────
const loadConfigs = async () => {
  const version = ++loadVersion
  loading.value = true
  syncError.value = ''
  const results = await Promise.allSettled([api.getAlertSettings(), api.getEmailSettings()])
  if (version !== loadVersion) return
  const [alertResult, emailResult] = results
  if (alertResult.status === 'fulfilled') {
    alertLoaded.value = true
    const data = { ...alertDefault, ...alertResult.value.data }
    if (!alertDirty.value) alertForm.value = data
    localStorage.setItem(CACHE_ALERT, JSON.stringify(data))
  }
  if (emailResult.status === 'fulfilled') {
    emailLoaded.value = true
    const { smtp_password, has_password, ...data } = emailResult.value.data
    hasPassword.value = !!has_password
    if (!emailDirty.value) emailForm.value = { ...emailDefault, ...data, smtp_password: '' }
    localStorage.setItem(CACHE_EMAIL, JSON.stringify(data))
  }
  if (results.some(r => r.status === 'rejected')) syncError.value = '部分配置同步失败，未同步的配置暂不可保存；当前输入已保留。'
  loading.value = false
}

const saveBaseSettings = () => {
  localStorage.setItem('companyName', companyName.value)
  localStorage.setItem('pageTitle', pageTitle.value)
  document.title = pageTitle.value
  window.dispatchEvent(new Event('storage'))
  alert('✅ 系统设置已保存并生效')
}

const saveAlertSettings = async () => {
  if (loading.value || !alertLoaded.value || savingAlert.value) return
  savingAlert.value = true
  const payload = { ...alertForm.value }
  try {
    await api.saveAlertSettings(payload)
    localStorage.setItem(CACHE_ALERT, JSON.stringify(payload))
    if (JSON.stringify(alertForm.value) === JSON.stringify(payload)) alertDirty.value = false
    alert('✅ 告警规则已保存')
  } catch { alert('保存失败，当前输入已保留') }
  finally { savingAlert.value = false }
}

const saveEmailSettings = async () => {
  if (loading.value || !emailLoaded.value || savingEmail.value) return
  savingEmail.value = true
  const original = { ...emailForm.value }
  const { smtp_password, ...payload } = original
  if (smtp_password) payload.smtp_password = smtp_password
  try {
    await api.saveEmailSettings(payload)
    const { smtp_password: omitted, ...safeEmail } = payload
    localStorage.setItem(CACHE_EMAIL, JSON.stringify(safeEmail))
    if (smtp_password) hasPassword.value = true
    if (JSON.stringify(emailForm.value) === JSON.stringify(original)) {
      emailDirty.value = false
      emailForm.value.smtp_password = ''
    }
    alert('✅ 发件配置已保存')
  } catch { alert('保存失败，当前输入已保留') }
  finally { savingEmail.value = false }
}

const testEmail = async () => {
  if (!emailForm.value.smtp_server || !alertForm.value.alert_recipient) return alert('请先完善并保存SMTP配置和接收人邮箱！')
  isTesting.value = true
  try {
    const res = await api.testEmail()
    if (res.data.success) alert('测试邮件发送成功，请查收！')
    else alert('发送失败: ' + res.data.message)
  } catch (e) {
    alert('发送失败，请检查配置或后台日志')
  } finally {
    isTesting.value = false
  }
}

onMounted(() => {
  // 先用缓存渲染，再异步从后端同步最新值
  loadConfigs()
})
</script>

<style scoped>
.settings-page { padding: 20px; }
.settings-grid { display: grid; gap: 20px; max-width: 800px; }
.panel { background: #fff; padding: 25px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
h2 { margin-top: 0; color: #1e293b; font-size: 18px; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px; }
.desc { color: #64748b; font-size: 13px; margin-bottom: 15px; }
.hint { color: #94a3b8; font-size: 12px; font-weight: normal; margin-left: 10px; }

.form-group { margin-bottom: 15px; display: flex; flex-direction: column; gap: 8px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
.row-group { flex-direction: row; align-items: center; }
.toggle-label { display: flex; align-items: center; gap: 8px; cursor: pointer; font-weight: bold; color: #334155; }

label { font-size: 13px; color: #475569; font-weight: bold; }
input[type="text"], input[type="password"], input[type="number"] { padding: 10px; border: 1px solid #cbd5e1; border-radius: 4px; outline: none; }
input[type="text"]:focus, input[type="password"]:focus, input[type="number"]:focus { border-color: #3b82f6; }

.actions { display: flex; gap: 15px; margin-top: 10px; }
.btn { padding: 10px 16px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; transition: 0.2s; }
.btn-primary { background: #3b82f6; color: #fff; }
.btn-primary:hover { background: #2563eb; }
.btn-secondary { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }
.btn-secondary:hover { background: #e2e8f0; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.sync-status { max-width: 768px; padding: 12px 16px; margin-bottom: 16px; border: 1px solid #bfdbfe; background: #eff6ff; color: #1d4ed8; border-radius: 8px; font-size: 13px; }
.sync-status.failed { border-color: #fde68a; background: #fffbeb; color: #92400e; }
.sync-status button { margin-left: 12px; color: inherit; cursor: pointer; background: transparent; border: 0; text-decoration: underline; }
@media (max-width: 700px) { .form-grid { grid-template-columns: 1fr; } .actions { flex-wrap: wrap; } }
</style>
