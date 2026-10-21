<template>
  <dialog ref="dialog" class="add-node-dialog" aria-labelledby="add-node-title" aria-describedby="add-node-description" @cancel.prevent="closeModal" @click="onBackdropClick" @keydown.tab="trapFocus">
    <form class="add-node-form" @submit.prevent="submit">
      <header class="add-node-header">
        <div class="add-node-symbol" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="4" y="3" width="16" height="7" rx="2"/><rect x="4" y="14" width="16" height="7" rx="2"/><path d="M8 6.5h.01M8 17.5h.01M12 6.5h5M12 17.5h5" stroke-linecap="round"/></svg>
        </div>
        <div class="add-node-heading"><h2 id="add-node-title">增加服务器</h2><p id="add-node-description">接入新的监控节点，集中查看运行状态。</p></div>
        <button type="button" class="add-node-close" aria-label="关闭增加服务器弹窗" :disabled="saving" @click="closeModal"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m5 5 10 10M15 5 5 15" stroke-linecap="round"/></svg></button>
      </header>

      <div class="add-node-body">
        <section aria-labelledby="node-info-heading">
          <div class="add-node-section-heading"><h3 id="node-info-heading">节点信息</h3><span><i>*</i> 为必填项</span></div>
          <div class="add-node-grid">
            <div class="add-node-field"><label for="add-hostname">主机名 <i>*</i></label><input id="add-hostname" v-model="form.hostname" placeholder="例如：计算节点-01" required maxlength="100" autocomplete="off" autofocus :disabled="saving"><small>便于识别的节点名称</small></div>
            <div class="add-node-field"><label for="add-address">IP 地址 <i>*</i></label><input id="add-address" v-model="form.ip_address" placeholder="例如：192.168.1.100" required maxlength="50" spellcheck="false" autocomplete="off" :disabled="saving"><small>填写服务器的 IPv4 或 IPv6 地址</small></div>
            <div class="add-node-field add-node-full"><label for="add-group">所属分组 <span>选填</span></label><input id="add-group" v-model="form.group_name" list="add-node-groups" placeholder="选择已有分组，或输入新分组名称" maxlength="50" autocomplete="off" :disabled="saving"><datalist id="add-node-groups"><option v-for="group in groups" :key="group" :value="group" /></datalist><small>留空将归入「未分组」</small></div>
          </div>
        </section>

        <section class="add-node-connection">
          <div class="add-node-field"><label for="add-mode">采集方式</label><select id="add-mode" v-model="form.collection_mode" :disabled="saving"><option value="agent">节点主动上报（推荐，每5分钟）</option><option value="ssh">SSH 采集（兼容已有节点）</option></select></div>
        </section>
        <section class="add-node-connection" aria-labelledby="node-ssh-heading">
          <div class="add-node-section-heading"><h3 id="node-ssh-heading">SSH 连接</h3><span>{{ form.collection_mode === 'agent' ? '用于安装 Agent 和远程运维' : '用于采集监控数据' }}</span></div>
          <div class="add-node-grid add-node-ssh-grid">
            <div class="add-node-field"><label for="add-ssh-user">用户名</label><input id="add-ssh-user" v-model="form.ssh_user" required maxlength="50" spellcheck="false" autocomplete="off" :disabled="saving"></div>
            <div class="add-node-field"><label for="add-ssh-port">SSH 端口</label><input id="add-ssh-port" v-model.number="form.ssh_port" type="number" min="1" max="65535" step="1" required :disabled="saving"><small>默认 22，可填写实际连接端口</small></div>
            <div v-if="form.collection_mode === 'ssh'" class="add-node-field add-node-full"><label for="add-ssh-password">SSH 密码</label><input id="add-ssh-password" v-model="form.ssh_password" type="password" autocomplete="new-password" required :disabled="saving"><small>通过 HTTPS 提交，加密保存；不使用免密私钥。</small></div>
          </div>
        </section>

        <div class="add-node-tip"><div><strong>接入准备</strong><p v-if="form.collection_mode === 'agent'">添加后进入此新节点的独立部署页，使用用户名和密码一键安装 Agent。无需配置 SSH 免密登录。</p><p v-else>请输入目标服务器的用户名和密码，并由管理员核验 SSH 主机指纹。请使用 HTTPS 页面添加密码。</p></div></div>
        <p v-if="errorMessage" class="add-node-error" role="alert">{{ errorMessage }}</p>
      </div>

      <footer class="add-node-footer"><span class="add-node-footer-hint">{{ form.collection_mode === 'agent' ? '下一步：部署此节点的 Agent' : '添加后可随时编辑节点配置' }}</span><div class="add-node-buttons"><button type="button" class="add-node-cancel" :disabled="saving" @click="closeModal">取消</button><button type="submit" class="add-node-submit" :disabled="saving"><span v-if="saving" class="add-node-spinner" aria-hidden="true"/><span v-else aria-hidden="true" class="add-node-plus">＋</span>{{ saving ? '正在添加…' : form.collection_mode === 'agent' ? '添加并部署 Agent' : '添加服务器' }}</button></div></footer>
    </form>
  </dialog>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'

defineProps({ groups: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'created'])
const dialog = ref(null)
const saving = ref(false)
const errorMessage = ref('')
const form = ref({ hostname: '', ip_address: '', group_name: '', ssh_user: 'root', ssh_port: 22, collection_mode: 'agent', ssh_password: '' })

const closeModal = () => { if (!saving.value) emit('close') }
const trapFocus = event => {
  const controls = [...dialog.value.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled)')]
  const first = controls[0], last = controls[controls.length - 1]
  if (!first) { event.preventDefault(); return }
  if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.value)) {
    event.preventDefault(); last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus()
  }
}
const onBackdropClick = event => {
  if (event.target !== dialog.value) return
  const rect = dialog.value.getBoundingClientRect()
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeModal()
}
const submit = async () => {
  if (saving.value) return
  if (form.value.collection_mode === 'ssh' && window.location.protocol !== 'https:') {
    errorMessage.value = '请输入 HTTPS 地址后再配置 SSH 密码。'
    return
  }
  const payload = {
    hostname: form.value.hostname.trim(), ip_address: form.value.ip_address.trim(),
    group_name: form.value.group_name.trim() || '未分组',
    ssh_user: form.value.ssh_user.trim(), ssh_port: Number(form.value.ssh_port),
    collection_mode: form.value.collection_mode,
    ...(form.value.collection_mode === 'ssh' ? { ssh_password: form.value.ssh_password } : {}),
  }
  if (!payload.hostname || !payload.ip_address || !payload.ssh_user) {
    errorMessage.value = '请填写主机名、IP 地址和 SSH 用户名。'
    return
  }
  if (!Number.isInteger(payload.ssh_port) || payload.ssh_port < 1 || payload.ssh_port > 65535) {
    errorMessage.value = 'SSH 端口应为 1–65535 之间的整数。'
    return
  }
  errorMessage.value = ''
  saving.value = true
  try {
    const { data } = await api.addServer(payload)
    if (!data.success) throw new Error('创建未成功')
    form.value.ssh_password = ''
    emit('created', { server_id: data.server_id, collection_mode: payload.collection_mode })
  } catch {
    errorMessage.value = '添加失败，请检查网络或连接信息后重试。已保留当前输入。'
  } finally { saving.value = false }
}
onMounted(() => dialog.value.showModal())
onBeforeUnmount(() => dialog.value?.close())
</script>

<style scoped>
.add-node-dialog { padding: 0; border: 1px solid var(--border); border-radius: 18px; width: 640px; max-width: calc(100vw - 32px); max-height: calc(100dvh - 40px); color: var(--text); background:var(--surface); box-shadow: 0 28px 80px #0f172a30, 0 8px 24px #0f172a1a; overflow: auto; }
.add-node-dialog::backdrop { background: var(--backdrop); backdrop-filter: blur(3px); }
.add-node-dialog *, .add-node-dialog *::before, .add-node-dialog *::after { box-sizing: border-box; }
.add-node-form { margin: 0; }
.add-node-header { display: flex; gap: 16px; align-items: center; padding: 26px 28px; background: linear-gradient(115deg, var(--accent-soft), var(--surface) 85%); border-bottom: 1px solid var(--border-soft); }
.add-node-symbol { display: grid; place-items: center; width: 48px; height: 48px; flex-shrink: 0; border: 1px solid var(--accent-border); border-radius: 13px; color: var(--accent); background:var(--surface); box-shadow: 0 3px 8px var(--accent-soft); }
.add-node-symbol svg { width: 26px; height: 26px; }
.add-node-heading { flex: 1; min-width: 0; }
.add-node-heading h2 { margin: 0 0 5px; font-size: 21px; line-height: 1.4; font-weight: 700; letter-spacing: .3px; }
.add-node-heading p { margin: 0; color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.add-node-close { display: grid; place-items: center; width: 32px; height: 32px; padding: 6px; align-self: flex-start; border: 0; border-radius: 8px; background: transparent; color: var(--text-muted); cursor: pointer; }
.add-node-close:hover { background: var(--border-soft); color: var(--text); }
.add-node-body { padding: 24px 28px; }
.add-node-section-heading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.add-node-section-heading h3 { margin: 0; font-size: 14px; font-weight: 650; color: var(--text); }
.add-node-section-heading > span { font-size: 11px; color: var(--text-muted); }
.add-node-dialog i { color: var(--danger); font-style: normal; }
.add-node-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 18px 20px; }
.add-node-field { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.add-node-field label { font-size: 13px; font-weight: 550; color: var(--text-secondary); }
.add-node-field label span { margin-left: 5px; font-size: 11px; font-weight: 400; color: var(--text-muted); }
.add-node-field input { width: 100%; height: 42px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; outline: none; color: var(--text); background:var(--surface); font: inherit; font-size: 13px; transition: border-color .15s, box-shadow .15s; }
.add-node-field input::placeholder { color: var(--text-muted); }
.add-node-field input:hover { border-color: var(--accent-border); }
.add-node-field input:focus { border-color:var(--primary-bg); box-shadow: 0 0 0 3px var(--accent-soft); }
.add-node-field input:disabled { background: var(--surface-soft); color: var(--text-muted); }
.add-node-field select { height: 42px; border: 1px solid var(--border); border-radius: 8px; padding: 8px; background:var(--surface); font: inherit; }
.add-node-field small { font-size: 11px; line-height: 1.4; color: var(--text-muted); }
.add-node-full { grid-column: 1 / -1; }
.add-node-connection { border-top: 1px solid var(--border-soft); margin-top: 24px; padding-top: 22px; }
.add-node-ssh-grid { grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); }
.add-node-tip { display: flex; gap: 10px; margin-top: 22px; padding: 13px 14px; border: 1px solid var(--accent-border); border-radius: 9px; background: var(--accent-soft); color: var(--text-secondary); }
.add-node-tip svg { flex-shrink: 0; width: 18px; height: 18px; margin-top: 1px; color: var(--accent-border); }
.add-node-tip strong { font-size: 12px; font-weight: 600; color: var(--text-secondary); }
.add-node-tip p { margin: 4px 0 0; font-size: 12px; line-height: 1.8; }
.add-node-error { margin: 14px 0 0; padding: 11px 13px; border: 1px solid var(--danger-border); border-radius: 8px; background: var(--danger-bg); color: var(--danger); font-size: 12px; line-height: 1.6; }
.add-node-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 18px 28px; border-top: 1px solid var(--border-soft); background: var(--surface-soft); }
.add-node-footer-hint { color: var(--text-muted); font-size: 11px; }
.add-node-buttons { display: flex; gap: 10px; }
.add-node-buttons button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 40px; padding: 0 18px; border-radius: 8px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; white-space: nowrap; }
.add-node-cancel { border: 1px solid var(--border); background:var(--surface); color: var(--text-muted); }
.add-node-cancel:hover { background: var(--surface-muted); color: var(--text); }
.add-node-submit { border: 1px solid var(--accent); background:var(--primary-bg); color:var(--on-primary); box-shadow: 0 3px 7px var(--accent-soft); }
.add-node-submit:hover { background: var(--primary-hover); }
.add-node-plus { font-size: 19px; font-weight: 400; line-height: 1; }
.add-node-dialog button:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.add-node-spinner { width: 14px; height: 14px; border: 2px solid #ffffff60; border-top-color:var(--on-primary); border-radius: 50%; animation: add-node-spin .7s linear infinite; }
@keyframes add-node-spin { to { transform: rotate(360deg); } }
@media (max-width: 540px) { .add-node-header { padding: 20px; gap: 12px; } .add-node-body { padding: 20px; } .add-node-heading h2 { font-size: 19px; } .add-node-heading p { font-size: 12px; } .add-node-symbol { width: 42px; height: 42px; } .add-node-grid { grid-template-columns: minmax(0, 1fr); gap: 16px; } .add-node-ssh-grid { grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); } .add-node-footer { padding: 16px 20px; justify-content: flex-end; } .add-node-footer-hint { display: none; } }
@media (prefers-reduced-motion: reduce) { .add-node-spinner { animation: none; } .add-node-field input { transition: none; } }
</style>
