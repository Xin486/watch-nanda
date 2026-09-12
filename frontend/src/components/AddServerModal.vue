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

        <section class="add-node-connection" aria-labelledby="node-ssh-heading">
          <div class="add-node-section-heading"><h3 id="node-ssh-heading">SSH 连接</h3><span>用于采集监控数据</span></div>
          <div class="add-node-grid add-node-ssh-grid">
            <div class="add-node-field"><label for="add-ssh-user">用户名</label><input id="add-ssh-user" v-model="form.ssh_user" required maxlength="50" spellcheck="false" autocomplete="off" :disabled="saving"></div>
            <div class="add-node-field"><label for="add-ssh-port">端口</label><input id="add-ssh-port" v-model.number="form.ssh_port" type="number" min="1" max="65535" step="1" required :disabled="saving"></div>
          </div>
        </section>

        <div class="add-node-tip"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="10" cy="10" r="7.3"/><path d="M10 9v5M10 6h.01" stroke-linecap="round"/></svg><div><strong>连接前准备</strong><p>请先配置监控平台到目标服务器的免密 SSH 登录。添加后，平台将在一分钟内尝试采集。</p></div></div>
        <p v-if="errorMessage" class="add-node-error" role="alert">{{ errorMessage }}</p>
      </div>

      <footer class="add-node-footer"><span class="add-node-footer-hint">添加后可随时编辑节点配置</span><div class="add-node-buttons"><button type="button" class="add-node-cancel" :disabled="saving" @click="closeModal">取消</button><button type="submit" class="add-node-submit" :disabled="saving"><span v-if="saving" class="add-node-spinner" aria-hidden="true"/><span v-else aria-hidden="true" class="add-node-plus">＋</span>{{ saving ? '正在添加…' : '添加服务器' }}</button></div></footer>
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
const form = ref({ hostname: '', ip_address: '', group_name: '', ssh_user: 'root', ssh_port: 22 })

const closeModal = () => { if (!saving.value) emit('close') }
const trapFocus = event => {
  const controls = [...dialog.value.querySelectorAll('button:not(:disabled), input:not(:disabled)')]
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
  const payload = {
    hostname: form.value.hostname.trim(), ip_address: form.value.ip_address.trim(),
    group_name: form.value.group_name.trim() || '未分组',
    ssh_user: form.value.ssh_user.trim(), ssh_port: Number(form.value.ssh_port),
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
    emit('created')
  } catch {
    errorMessage.value = '添加失败，请检查网络或连接信息后重试。已保留当前输入。'
  } finally { saving.value = false }
}
onMounted(() => dialog.value.showModal())
onBeforeUnmount(() => dialog.value?.close())
</script>

<style scoped>
.add-node-dialog { padding: 0; border: 1px solid #e2e8f0; border-radius: 18px; width: 640px; max-width: calc(100vw - 32px); max-height: calc(100dvh - 40px); color: #1e293b; background: #fff; box-shadow: 0 28px 80px #0f172a30, 0 8px 24px #0f172a1a; overflow: auto; }
.add-node-dialog::backdrop { background: #0f172a80; backdrop-filter: blur(3px); }
.add-node-dialog *, .add-node-dialog *::before, .add-node-dialog *::after { box-sizing: border-box; }
.add-node-form { margin: 0; }
.add-node-header { display: flex; gap: 16px; align-items: center; padding: 26px 28px; background: linear-gradient(115deg, #f0f6ff, #fff 85%); border-bottom: 1px solid #e8eef6; }
.add-node-symbol { display: grid; place-items: center; width: 48px; height: 48px; flex-shrink: 0; border: 1px solid #dbeafe; border-radius: 13px; color: #2563eb; background: #fff; box-shadow: 0 3px 8px #2563eb0a; }
.add-node-symbol svg { width: 26px; height: 26px; }
.add-node-heading { flex: 1; min-width: 0; }
.add-node-heading h2 { margin: 0 0 5px; font-size: 21px; line-height: 1.4; font-weight: 700; letter-spacing: .3px; }
.add-node-heading p { margin: 0; color: #64748b; font-size: 13px; line-height: 1.6; }
.add-node-close { display: grid; place-items: center; width: 32px; height: 32px; padding: 6px; align-self: flex-start; border: 0; border-radius: 8px; background: transparent; color: #94a3b8; cursor: pointer; }
.add-node-close:hover { background: #e8eff9; color: #334155; }
.add-node-body { padding: 24px 28px; }
.add-node-section-heading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.add-node-section-heading h3 { margin: 0; font-size: 14px; font-weight: 650; color: #334155; }
.add-node-section-heading > span { font-size: 11px; color: #94a3b8; }
.add-node-dialog i { color: #ef4444; font-style: normal; }
.add-node-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 18px 20px; }
.add-node-field { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.add-node-field label { font-size: 13px; font-weight: 550; color: #475569; }
.add-node-field label span { margin-left: 5px; font-size: 11px; font-weight: 400; color: #94a3b8; }
.add-node-field input { width: 100%; height: 42px; padding: 10px 12px; border: 1px solid #d8e1ed; border-radius: 8px; outline: none; color: #1e293b; background: #fff; font: inherit; font-size: 13px; transition: border-color .15s, box-shadow .15s; }
.add-node-field input::placeholder { color: #9aa9bc; }
.add-node-field input:hover { border-color: #a9bbd1; }
.add-node-field input:focus { border-color: #3b82f6; box-shadow: 0 0 0 3px #3b82f61a; }
.add-node-field input:disabled { background: #f8fafc; color: #64748b; }
.add-node-field small { font-size: 11px; line-height: 1.4; color: #94a3b8; }
.add-node-full { grid-column: 1 / -1; }
.add-node-connection { border-top: 1px solid #edf1f6; margin-top: 24px; padding-top: 22px; }
.add-node-ssh-grid { grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); }
.add-node-tip { display: flex; gap: 10px; margin-top: 22px; padding: 13px 14px; border: 1px solid #e0ecff; border-radius: 9px; background: #f6f9ff; color: #53749e; }
.add-node-tip svg { flex-shrink: 0; width: 18px; height: 18px; margin-top: 1px; color: #6491cb; }
.add-node-tip strong { font-size: 12px; font-weight: 600; color: #41668f; }
.add-node-tip p { margin: 4px 0 0; font-size: 12px; line-height: 1.8; }
.add-node-error { margin: 14px 0 0; padding: 11px 13px; border: 1px solid #fecaca; border-radius: 8px; background: #fef2f2; color: #b91c1c; font-size: 12px; line-height: 1.6; }
.add-node-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 18px 28px; border-top: 1px solid #edf1f6; background: #fafbfd; }
.add-node-footer-hint { color: #94a3b8; font-size: 11px; }
.add-node-buttons { display: flex; gap: 10px; }
.add-node-buttons button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 40px; padding: 0 18px; border-radius: 8px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; white-space: nowrap; }
.add-node-cancel { border: 1px solid #dce3ed; background: #fff; color: #64748b; }
.add-node-cancel:hover { background: #f1f5f9; color: #334155; }
.add-node-submit { border: 1px solid #2563eb; background: #2563eb; color: #fff; box-shadow: 0 3px 7px #2563eb26; }
.add-node-submit:hover { background: #1d4ed8; }
.add-node-plus { font-size: 19px; font-weight: 400; line-height: 1; }
.add-node-dialog button:focus-visible { outline: 3px solid #93c5fd; outline-offset: 2px; }
.add-node-spinner { width: 14px; height: 14px; border: 2px solid #ffffff60; border-top-color: #fff; border-radius: 50%; animation: add-node-spin .7s linear infinite; }
@keyframes add-node-spin { to { transform: rotate(360deg); } }
@media (max-width: 540px) { .add-node-header { padding: 20px; gap: 12px; } .add-node-body { padding: 20px; } .add-node-heading h2 { font-size: 19px; } .add-node-heading p { font-size: 12px; } .add-node-symbol { width: 42px; height: 42px; } .add-node-grid { grid-template-columns: minmax(0, 1fr); gap: 16px; } .add-node-ssh-grid { grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); } .add-node-footer { padding: 16px 20px; justify-content: flex-end; } .add-node-footer-hint { display: none; } }
@media (prefers-reduced-motion: reduce) { .add-node-spinner { animation: none; } .add-node-field input { transition: none; } }
</style>
