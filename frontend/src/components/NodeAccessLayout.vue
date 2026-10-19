<template>
  <div class="operations-page">
    <header class="operations-heading">
      <div><router-link to="/dashboard" class="back-link">← 返回节点总览</router-link><h1>{{ title }}</h1><p>{{ description }}</p></div>
      <button v-if="access.authenticated" class="quiet-button" @click="access.logout">退出运维登录</button>
    </header>
    <div v-if="!access.secure" class="transport-notice" role="alert"><strong>请通过 HTTPS 使用此功能</strong><p>用户名、密码和操作数据需要加密传输。</p><a :href="httpsPageUrl(locationHref).href">转到 HTTPS 页面 ↗</a></div>
    <form v-else-if="!access.authenticated" class="operator-login" @submit.prevent="access.login">
      <div class="login-mark">⌘</div><h2>运维登录</h2><p>输入运维口令后继续{{ loginPurpose }}。登录有效期为 1 小时。</p>
      <label for="operator-password">运维口令</label><input id="operator-password" v-model="access.operatorPassword" type="password" autocomplete="current-password" required maxlength="1024" placeholder="输入运维口令">
      <p v-if="access.error" class="operation-error" role="alert">{{ access.error }}</p><button class="primary-button" :disabled="access.loggingIn">{{ access.loggingIn ? '验证中…' : '登录并继续' }}</button>
    </form>
    <template v-else>
      <div v-if="selectable" class="node-selector"><label for="ops-node">操作节点</label><select id="ops-node" v-model="access.selectedId" :disabled="access.busy"><option value="">请选择节点</option><option v-for="s in access.servers" :key="s.id" :value="String(s.id)">{{ s.hostname }} · {{ s.ip_address }}:{{ s.ssh_port || 22 }}</option></select><span v-if="access.node" class="node-status" :class="access.node.status">{{ statusLabel(access.node.status) }}</span></div>
      <div v-else-if="access.node" class="node-selector new-node-summary"><span class="new-node-label">{{ nodeLabel }}</span><strong>{{ access.node.hostname }}</strong><code>{{ access.node.ip_address }}:{{ access.node.ssh_port || 22 }}</code><span class="node-status" :class="access.node.status">{{ statusLabel(access.node.status) }}</span></div>
      <div v-if="access.node" class="operations-grid">
        <NodeConnectionPanel :access="access"/>
        <div class="workspace-panel"><slot/></div>
      </div>
      <div v-else class="select-empty">{{ selectable ? '选择一台节点开始测试 SSH 或进入运维命令行。' : '正在等待节点信息，请确认节点仍存在且已启用。' }}</div>
      <p v-if="access.error" class="operation-error page-error" role="alert">{{ access.error }}</p>
    </template>
  </div>
</template>
<script setup>
import NodeConnectionPanel from './NodeConnectionPanel.vue'
import { statusLabel } from '../utils/serverStatus'
import { httpsPageUrl } from '../utils/transport'
defineProps({ access: { type: Object, required: true }, title: String, description: String, loginPurpose: String, nodeLabel: { type: String, default: '新服务器' }, selectable: { type: Boolean, default: true } })
const locationHref = location.href
</script>
<style src="../styles/nodeOperations.css"></style>
