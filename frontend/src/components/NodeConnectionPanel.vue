<template>
  <aside class="connection-panel">
    <div class="panel-caption"><span class="step-number">01</span><div><h2>SSH 连接身份</h2><p>{{ access.node.hostname }}</p></div><span class="mode-chip">{{ access.node.collection_mode === 'agent' ? 'Agent' : 'SSH' }}</span></div>
    <div class="connection-address"><span>目标地址</span><code>{{ access.node.ip_address }}:{{ access.node.ssh_port || 22 }}</code></div>
    <div class="credential-switch" role="group" aria-label="凭据来源"><button :class="{ selected: access.useSaved }" :disabled="!access.node.has_ssh_password || access.busy" @click="access.useSaved = true">已配置凭据</button><button :class="{ selected: !access.useSaved }" :disabled="access.busy" @click="access.useSaved = false">手动输入</button></div>
    <div v-if="access.useSaved" class="stored-credentials"><span class="saved-dot"/>使用 {{ access.node.ssh_user || 'root' }} 的已存密码<small>密码由服务端解密使用，不会返回浏览器。</small></div>
    <div v-else class="manual-credentials"><label>SSH 用户名<input v-model="access.credentials.username" autocomplete="off" maxlength="50" :disabled="access.busy"></label><label>SSH 密码<input v-model="access.credentials.password" type="password" autocomplete="new-password" maxlength="1024" placeholder="输入节点登录密码" :disabled="access.busy"></label><p class="connection-note">测试 SSH 或进入命令行验证成功后，用户名和密码将自动加密同步到节点配置。</p></div>
    <button class="test-button" :disabled="access.busy" @click="access.testConnection">{{ access.testing ? '正在测试…' : '测试 SSH 连通性' }} <span>↗</span></button>
    <div v-if="access.check" class="check-result" :class="{ success: access.check.ok, failure: !access.check.ok }" role="status"><strong>{{ access.check.ok ? '连接成功' : '连接未就绪' }}</strong><p>{{ access.check.message }}</p><p v-if="access.check.credentials_saved">{{ access.check.credentials_message }}</p><small v-if="access.check.latency_ms">连接耗时 {{ access.check.latency_ms }} ms</small><code v-if="access.check.fingerprint">{{ access.check.fingerprint }}</code><template v-if="access.check.code === 'unknown_host'"><label class="trust-checkbox"><input v-model="access.trustConfirmed" type="checkbox" :disabled="access.busy">我已通过服务器控制台核对该指纹</label><button class="secondary-button" :disabled="!access.trustConfirmed || access.busy" @click="access.trustHost">保存信任并重新测试</button></template></div>
    <button v-if="access.check?.code === 'host_key_changed' && access.check.previous_fingerprint" class="secondary-button host-key-reopen" :disabled="access.busy" @click="access.trustDialogOpen = true">核对并更新主机指纹</button>
    <p class="connection-note">SSH 使用密码认证，不创建免密密钥。同步后可直接使用已配置凭据，无需再编辑节点配置。</p>
    <dialog ref="trustDialog" class="host-key-dialog" aria-labelledby="host-key-title" @cancel.prevent="cancel">
      <form @submit.prevent="access.confirmHostChange">
        <h2 id="host-key-title">服务器指纹已变化</h2>
        <p>重装系统或更换 SSH 密钥后，指纹可能改变。请核对目标和新指纹，确认属于这台服务器后再允许更改。</p>
        <dl><dt>目标节点</dt><dd>{{ access.node.hostname }} · #{{ access.node.id }}<code>{{ access.check?.host || access.node.ip_address }}:{{ access.check?.port || access.node.ssh_port || 22 }}</code></dd><dt>原指纹</dt><dd><code>{{ access.check?.previous_fingerprint }}</code></dd><dt>新指纹</dt><dd><code>{{ access.check?.fingerprint }}</code></dd></dl>
        <p class="host-key-warning">若未重装系统或无法确认变更来源，请取消连接。</p>
        <p v-if="access.error" class="operation-error" role="alert">{{ access.error }}</p>
        <footer><button type="button" class="secondary-button" :disabled="access.busy" @click="cancel">取消连接</button><button class="primary-button" :disabled="access.busy">{{ access.testing ? '正在更新并连接…' : '允许更改并继续连接' }}</button></footer>
      </form>
    </dialog>
  </aside>
</template>
<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
const props = defineProps({ access: { type: Object, required: true } })
const trustDialog = ref(null)
function cancel() { if (!props.access.busy) props.access.cancelHostTrust() }
watch(() => props.access.trustDialogOpen, open => {
  if (!trustDialog.value) return
  if (open && !trustDialog.value.open) trustDialog.value.showModal()
  else if (!open && trustDialog.value.open) trustDialog.value.close()
}, { flush: 'post' })
onBeforeUnmount(() => trustDialog.value?.close())
</script>
